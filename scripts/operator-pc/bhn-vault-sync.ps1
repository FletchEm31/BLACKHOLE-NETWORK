<#
.SYNOPSIS
    Pulls fresh BHN backup artifacts from LA into the unlocked BHN-BLACKBOX vault.

.DESCRIPTION
    Triggered automatically by WMI on E:\ arrival (vault unlock).
    Also runnable manually for ad-hoc syncs.

    For each server-produced artifact (manifest at top of script):
      1. SSH to LA, invoke /usr/local/sbin/bhn-backup-produce.sh with a fresh /tmp path
      2. SCP the result back into the vault under the appropriate domain folder
      3. Verify sha256 matches the server-emitted hash
      4. SSH back and rm the tmpfile (server retains no plaintext-at-rest)

    For each local repo (D:\GITHUB REPOSITORY\*):
      1. git bundle --all  ->  vault under the appropriate domain folder

    All operations append to E:\_backup-log\sync-{TS}.log.

.PARAMETER Force
    Bypass the 1-hour debounce. Useful for manual re-runs immediately after a successful sync.

.PARAMETER DryRun
    Log what would be synced, but don't actually pull/bundle anything. Stack with -Force.

.EXAMPLE
    .\bhn-vault-sync.ps1
    Normal automatic sync (1h debounce applies).

.EXAMPLE
    .\bhn-vault-sync.ps1 -Force
    Force a sync even if the last run was recent.

.EXAMPLE
    .\bhn-vault-sync.ps1 -DryRun -Force
    See exactly what the manifest would do, no side effects.

.NOTES
    Repo path     : scripts/operator-pc/bhn-vault-sync.ps1
    Install at    : C:\BHN\bhn-vault-sync.ps1
    Vault root    : E:\  (Cryptomator BHN-BLACKBOX mount)
    SSH alias     : 'la' by default; override via $env:BHN_LA_HOST
    Requires      : Windows PowerShell 5.1+, OpenSSH client (ssh.exe, scp.exe),
                    Git for Windows (git.exe).
    PS edition    : Written for PowerShell 5.1 -- no PS7-only syntax.

    ------------------------------------------------------------------------
    ONE-TIME SETUP ON OPERATOR PC
    ------------------------------------------------------------------------

    1. Create the install directory and copy this script:
         New-Item -ItemType Directory -Path C:\BHN -Force
         Copy-Item .\bhn-vault-sync.ps1 C:\BHN\

    2. Drop the vault-identity sentinel (one-line marker, unlock the vault first):
         "BHN-BLACKBOX vault -- sentinel for bhn-vault-sync -- do not delete" |
             Set-Content E:\.bhn-vault-identity

    3. Ensure SSH alias 'la' is configured in %USERPROFILE%\.ssh\config:
         Host la
             Hostname 10.8.0.1
             User root
             # If WG-to-LA is broken, add: ProxyJump <jump-host>

    4. Register the WMI trigger as a scheduled task (run elevated PowerShell):

         $action = New-ScheduledTaskAction `
             -Execute 'powershell.exe' `
             -Argument '-NoProfile -WindowStyle Hidden -File C:\BHN\bhn-vault-sync.ps1'

         $trigger = New-ScheduledTaskTrigger `
             -AtLogOn `
             -User $env:USERNAME

         # The WMI event subscription itself is registered on logon by the task body;
         # see the WMI-Registration block below -- copy that into a separate
         # C:\BHN\register-vault-trigger.ps1 if you want it to survive reboots cleanly.

         Register-ScheduledTask `
             -TaskName 'BHN-Vault-Sync-Register' `
             -Action $action `
             -Trigger $trigger `
             -RunLevel Highest

    5. WMI subscription (paste into the register-vault-trigger script referenced above,
       or run it manually in your normal user session to test):

         Register-CimIndicationEvent `
             -Query @"
                 SELECT * FROM __InstanceCreationEvent WITHIN 2
                 WHERE TargetInstance ISA 'Win32_LogicalDisk'
                   AND TargetInstance.DeviceID = 'E:'
             "@ `
             -SourceIdentifier 'BHN-Vault-Unlock' `
             -Action {
                 Start-Process powershell.exe -ArgumentList `
                     '-NoProfile','-WindowStyle','Hidden','-File','C:\BHN\bhn-vault-sync.ps1'
             }

    6. Smoke test:
         pwsh C:\BHN\bhn-vault-sync.ps1 -DryRun -Force
         pwsh C:\BHN\bhn-vault-sync.ps1 -Force
#>

[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ============================================================================
# CONFIGURATION
# ============================================================================

$VAULT_ROOT     = 'E:\'
$SENTINEL       = Join-Path $VAULT_ROOT '.bhn-vault-identity'
$LOG_DIR        = Join-Path $VAULT_ROOT '_backup-log'
$LOCK           = Join-Path $LOG_DIR '.sync-lock'
$LAST_OK        = Join-Path $LOG_DIR 'last-success-timestamp'
$DEBOUNCE_HR    = 6
$LA_HOST        = if ($env:BHN_LA_HOST) { $env:BHN_LA_HOST } else { 'la' }
$PRODUCER_PATH  = '/usr/local/sbin/bhn-backup-produce.sh'
$SSH_TIMEOUT    = 5

$TS = Get-Date -Format 'yyyyMMdd-HHmm'

# ----------------------------------------------------------------------------
# Manifest: server-produced artifacts (SSH -> produce -> scp -> verify)
# ----------------------------------------------------------------------------
# Ordered lightest-first: LA has 1.9 GiB RAM and the n8n container has been
# observed getting SIGKILL'd (exit 137) when pg_dump's memory spike lands
# immediately before the n8n CLI export. Run the lightweight one first.
$SERVER_ARTIFACTS = @(
    @{
        Id       = 'n8n-workflows'
        Dest     = 'BLACKHOLE NETWORK-BACKUP'
        FileName = "n8n-workflows-$TS.tar.zst"
    },
    @{
        Id       = 'pg-eventhorizon'
        Dest     = 'BLACKHOLE NETWORK-BACKUP'
        FileName = "eventhorizon-$TS.dump.zst"
    },
    @{
        Id       = 'matrix-synapse'
        Dest     = 'MatrixBHN\MATRIX-BACKUP'
        FileName = "matrix-synapse-$TS.tar.zst"
    }
    # 'bhn-repo-snapshot' -- enable once /opt/bhn-repo is set up on LA as a server-side mirror clone
)

# Brief pause between server artifacts to let LA's memory settle (1.9 GiB box).
$INTER_ARTIFACT_SLEEP_SEC = 5

# ----------------------------------------------------------------------------
# Manifest: local repos to bundle (git bundle --all, no server roundtrip)
# ----------------------------------------------------------------------------
$LOCAL_REPOS = @(
    @{
        Id     = 'bhn-pc'
        Source = 'D:\GITHUB REPOSITORY\BLACKHOLE-NETWORK'
        Dest   = 'BLACKHOLE NETWORK-BACKUP'
    },
    @{
        Id     = 'bteh'
        Source = 'D:\GITHUB REPOSITORY\BTEH-Beyond-The-EventHorizon'
        # BTEH = Beyond The EventHorizon = audit framework. Confirmed 2026-05-22:
        # SecurityBHN domain (the audit layer for the whole platform), NOT IncubatorBHN.
        # Vault folder will be auto-created at first sync. If a stale
        # IncubatorBHN\BEYOND THE HORIZON-BACKUP\ exists from earlier vault layout,
        # operator can delete it manually -- it's not referenced anywhere.
        Dest   = 'SecurityBHN\BTEH-BACKUP'
    },
    @{
        Id     = 'team-rocket-bhn'
        Source = 'D:\GITHUB REPOSITORY\TEAM ROCKET BHN'
        Dest   = 'PokemonBHN\POKEMON BLACKHOLE-TEAM ROCKET BHN-BACKUP'
    },
    @{
        Id     = 'bhnwave'
        Source = 'D:\GITHUB REPOSITORY\BHNWAVE'
        Dest   = 'StandaloneBHN\BHNwave-BACKUP'
    },
    @{
        Id     = 'blackbox-bidder'
        Source = 'D:\GITHUB REPOSITORY\BLACKBOX-BIDDER'
        Dest   = 'PokemonBHN\BLACKBOX BIDDER-BACKUP'
    }
)

# ============================================================================
# LOGGING
# ============================================================================

if (-not (Test-Path $LOG_DIR)) {
    New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null
}
$LOG_FILE = Join-Path $LOG_DIR ("sync-{0}.log" -f $TS)

function Write-Log {
    param(
        [Parameter(Mandatory)][string]$Message,
        [string]$Level = 'INFO'
    )
    $line = '{0}  [{1,-5}]  {2}' -f (Get-Date -Format 'o'), $Level, $Message
    Add-Content -Path $LOG_FILE -Value $line
    Write-Host $line
}

function Exit-WithError {
    param(
        [Parameter(Mandatory)][string]$Message,
        [int]$Code = 1
    )
    Write-Log -Level 'ERROR' -Message $Message
    if (Test-Path $LOCK) {
        Remove-Item $LOCK -Force -ErrorAction SilentlyContinue
    }
    exit $Code
}

Write-Log "=== bhn-vault-sync starting (DryRun=$($DryRun.IsPresent), Force=$($Force.IsPresent)) ==="
Write-Log "Vault root: $VAULT_ROOT  |  LA host: $LA_HOST  |  Log: $LOG_FILE"

# ============================================================================
# SANITY CHECKS
# ============================================================================

# 1. Required tooling
foreach ($tool in @('ssh', 'scp', 'git')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Exit-WithError "required tool not on PATH: $tool" 5
    }
}

# 2. Vault sentinel -- proves we're looking at BHN-BLACKBOX, not some random E:
if (-not (Test-Path $SENTINEL)) {
    Exit-WithError "vault sentinel missing at $SENTINEL -- this drive doesn't look like BHN-BLACKBOX" 2
}

# 3. SSH probe to LA (cheap; bails fast if WG/jump is broken)
Write-Log "Probing SSH to LA ($LA_HOST)..."
$probe = & ssh -o ConnectTimeout=$SSH_TIMEOUT -o BatchMode=yes $LA_HOST 'echo ok' 2>&1
if ($LASTEXITCODE -ne 0 -or ($probe -join '') -notmatch 'ok') {
    Exit-WithError "SSH probe to $LA_HOST failed (exit $LASTEXITCODE): $($probe -join ' | ')" 3
}
Write-Log "SSH to $LA_HOST OK"

# ============================================================================
# DEBOUNCE
# ============================================================================

if ((-not $Force) -and (Test-Path $LAST_OK)) {
    $lastRunRaw = (Get-Content $LAST_OK -Raw -ErrorAction SilentlyContinue)
    if ($lastRunRaw) {
        try {
            $lastRun = [DateTime]::Parse($lastRunRaw.Trim())
            $sinceHours = (New-TimeSpan -Start $lastRun -End (Get-Date)).TotalHours
            if ($sinceHours -lt $DEBOUNCE_HR) {
                Write-Log ("Recent successful sync at {0} ({1:F2}h ago < {2}h debounce) -- skipping. Use -Force to override." -f $lastRun, $sinceHours, $DEBOUNCE_HR)
                exit 0
            }
        } catch {
            Write-Log -Level 'WARN' -Message "couldn't parse last-success-timestamp '$lastRunRaw' -- treating as stale"
        }
    }
}

# ============================================================================
# LOCK (with stale-lock cleanup)
# ============================================================================

if (Test-Path $LOCK) {
    $stalePid = (Get-Content $LOCK -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($stalePid) {
        $running = Get-Process -Id $stalePid -ErrorAction SilentlyContinue
        if ($running) {
            Exit-WithError "sync already running (PID $stalePid)" 4
        } else {
            Write-Log -Level 'WARN' -Message "stale lock (PID $stalePid not alive) -- reclaiming"
        }
    }
}
$PID | Set-Content $LOCK

# ============================================================================
# MAIN
# ============================================================================

$serverOk = 0; $serverFail = 0
$localOk  = 0; $localFail  = 0
$localSkip = 0

try {

    # ------------------------------------------------------------------------
    # SERVER-PULLED ARTIFACTS
    # ------------------------------------------------------------------------
    foreach ($a in $SERVER_ARTIFACTS) {

        $destFolder = Join-Path $VAULT_ROOT $a.Dest
        $finalPath  = Join-Path $destFolder $a.FileName

        if (-not (Test-Path $destFolder)) {
            Write-Log -Level 'WARN' -Message "vault folder missing -- creating: $destFolder"
            if (-not $DryRun) {
                New-Item -ItemType Directory -Path $destFolder -Force | Out-Null
            }
        }

        if ($DryRun) {
            Write-Log "[dry-run] would produce '$($a.Id)' on $LA_HOST -> pull -> $finalPath"
            continue
        }

        # Random tmpfile path on LA
        $remoteTmp = "/tmp/bhn-backup-$([guid]::NewGuid().ToString('N')).tmp"
        Write-Log "Producing '$($a.Id)' on $LA_HOST -> $remoteTmp"

        # Capture stdout only (one line: the sha256 hex). Do NOT use 2>&1 here -
        # PowerShell 5.1 wraps native-exe stderr as NativeCommandError under
        # ErrorActionPreference=Stop and aborts the script, even on exit 0.
        # Producer diagnostics flow through ssh.exe's own stderr to the console,
        # which is fine for smoke; they aren't needed by the script logic.
        $stdout = & ssh $LA_HOST "$PRODUCER_PATH $($a.Id) $remoteTmp"
        $producerExit = $LASTEXITCODE

        # stdout has exactly one line: the sha256 hash. Defensive regex filter.
        $remoteSha = $stdout |
            ForEach-Object { "$_" } |
            Where-Object { $_ -match '^[a-f0-9]{64}$' } |
            Select-Object -Last 1

        if ($producerExit -ne 0 -or -not $remoteSha) {
            Write-Log -Level 'ERROR' -Message "producer failed for '$($a.Id)' (exit $producerExit) - see ssh stderr above for diagnostics"
            & ssh $LA_HOST "rm -f $remoteTmp" 2>$null | Out-Null
            $serverFail++
            continue
        }
        Write-Log "Producer OK. remote sha256=$remoteSha. Pulling..."

        # Pull via scp. Same stderr-rule applies - don't merge streams.
        & scp -q "${LA_HOST}:${remoteTmp}" "$finalPath"
        $scpExit = $LASTEXITCODE
        if ($scpExit -ne 0 -or -not (Test-Path $finalPath)) {
            Write-Log -Level 'ERROR' -Message "scp failed for '$($a.Id)' (exit $scpExit)"
            & ssh $LA_HOST "rm -f $remoteTmp" 2>$null | Out-Null
            $serverFail++
            continue
        }

        # Verify sha256 locally
        $localSha = (Get-FileHash $finalPath -Algorithm SHA256).Hash.ToLower()
        if ($localSha -ne $remoteSha) {
            Write-Log -Level 'ERROR' -Message "sha256 MISMATCH for '$($a.Id)': remote=$remoteSha local=$localSha -- deleting corrupt local"
            Remove-Item $finalPath -Force -ErrorAction SilentlyContinue
            & ssh $LA_HOST "rm -f $remoteTmp" 2>$null | Out-Null
            $serverFail++
            continue
        }

        # All good -- clean up server-side
        & ssh $LA_HOST "rm -f $remoteTmp" 2>$null | Out-Null
        $sizeMB = [Math]::Round(((Get-Item $finalPath).Length / 1MB), 1)
        Write-Log "OK   $($a.Id) -> $finalPath ($sizeMB MB, sha256=$localSha)"
        $serverOk++

        # Settle the box before the next artifact (LA RAM is tight)
        if ($INTER_ARTIFACT_SLEEP_SEC -gt 0) {
            Start-Sleep -Seconds $INTER_ARTIFACT_SLEEP_SEC
        }
    }

    # ------------------------------------------------------------------------
    # LOCAL REPO BUNDLES
    # ------------------------------------------------------------------------
    foreach ($r in $LOCAL_REPOS) {

        if (-not (Test-Path $r.Source)) {
            Write-Log "skip $($r.Id) -- not present on this PC ($($r.Source))"
            $localSkip++
            continue
        }
        if (-not (Test-Path (Join-Path $r.Source '.git'))) {
            Write-Log -Level 'WARN' -Message "skip $($r.Id) -- source exists but is not a git repo ($($r.Source))"
            $localSkip++
            continue
        }

        $destFolder = Join-Path $VAULT_ROOT $r.Dest
        if (-not (Test-Path $destFolder)) {
            Write-Log -Level 'WARN' -Message "vault folder missing -- creating: $destFolder"
            if (-not $DryRun) {
                New-Item -ItemType Directory -Path $destFolder -Force | Out-Null
            }
        }

        $bundleName = "$($r.Id)-$TS.bundle"
        $bundlePath = Join-Path $destFolder $bundleName

        if ($DryRun) {
            Write-Log "[dry-run] would bundle $($r.Source) -> $bundlePath"
            continue
        }

        Push-Location $r.Source
        try {
            $bundleOut = & git bundle create $bundlePath --all 2>&1
            $bundleExit = $LASTEXITCODE
            if ($bundleExit -ne 0 -or -not (Test-Path $bundlePath)) {
                Write-Log -Level 'ERROR' -Message ("git bundle failed for '$($r.Id)' (exit $bundleExit): " + ($bundleOut -join ' | '))
                $localFail++
                continue
            }
            $sizeMB = [Math]::Round(((Get-Item $bundlePath).Length / 1MB), 1)
            Write-Log "OK   $($r.Id) -> $bundlePath ($sizeMB MB)"
            $localOk++
        } finally {
            Pop-Location
        }
    }

    # ------------------------------------------------------------------------
    # Success marker (only if nothing failed)
    # ------------------------------------------------------------------------
    if ((-not $DryRun) -and $serverFail -eq 0 -and $localFail -eq 0) {
        (Get-Date -Format 'o') | Set-Content $LAST_OK
    }

    Write-Log ("=== bhn-vault-sync done -- server: {0} OK / {1} fail  |  local: {2} OK / {3} skip / {4} fail ===" -f $serverOk, $serverFail, $localOk, $localSkip, $localFail)

    if ($serverFail -gt 0 -or $localFail -gt 0) {
        exit 10
    }

} finally {
    if (Test-Path $LOCK) {
        Remove-Item $LOCK -Force -ErrorAction SilentlyContinue
    }
}
