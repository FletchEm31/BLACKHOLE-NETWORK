<#
.SYNOPSIS
    Long-lived watcher: triggers bhn-vault-sync.ps1 when E:\ (BHN-BLACKBOX vault) appears.

.DESCRIPTION
    Owns the WMI CIM indication subscription that listens for
    Win32_LogicalDisk DeviceID='E:' creation events (vault unlock).
    When E:\ appears, launches bhn-vault-sync.ps1 in a hidden background
    PowerShell process and continues watching.

    Designed to be launched by Task Scheduler at user logon. Stays running
    until the user logs off. If killed externally, the next logon re-creates
    the subscription.

    The Action block is intentionally self-contained (no $using: vars, no
    closure over outer scope) -- the runspace it executes in is different
    from the outer scope, and self-contained paths are the most reliable
    pattern.

.NOTES
    Repo path  : scripts/operator-pc/watch-vault.ps1
    Install at : C:\BHN\watch-vault.ps1
    Log        : C:\BHN\watch-vault.log
    Owner SID  : BHN-Vault-Unlock (the CIM event subscription source identifier)

    Requires Windows PowerShell 5.1+. No PS7-only syntax.

    -----------------------------------------------------------------
    SCHEDULED TASK REGISTRATION (run once, elevated PowerShell)
    -----------------------------------------------------------------

      $action = New-ScheduledTaskAction `
          -Execute 'powershell.exe' `
          -Argument '-NoProfile -WindowStyle Hidden -File C:\BHN\watch-vault.ps1'

      $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

      $settings = New-ScheduledTaskSettingsSet `
          -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
          -ExecutionTimeLimit (New-TimeSpan -Days 0) `
          -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

      Register-ScheduledTask `
          -TaskName 'BHN-Vault-Sync-Watcher' `
          -Action $action -Trigger $trigger -Settings $settings `
          -RunLevel Limited -User $env:USERNAME

    -----------------------------------------------------------------
    OPERATIONAL TEST
    -----------------------------------------------------------------

      1. Make sure watcher is running:
           Get-Process -Name 'powershell' | ? { $_.MainWindowTitle -like '*watch-vault*' }
           # or check the log:
           Get-Content C:\BHN\watch-vault.log -Tail 5

      2. Lock the vault in Cryptomator UI -> E: disappears.
      3. Unlock the vault -> E: reappears.
      4. Watch log appends an entry, then C:\BHN\bhn-vault-sync.ps1 fires
         in the background. Tail E:\_backup-log\sync-*.log to see results.
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$LOG       = 'C:\BHN\watch-vault.log'
$SYNC      = 'C:\BHN\bhn-vault-sync.ps1'
$SOURCE_ID = 'BHN-Vault-Unlock'

function Write-WatchLog {
    param([Parameter(Mandatory)][string]$Message)
    $line = '{0}  {1}' -f (Get-Date -Format 'o'), $Message
    Add-Content -Path $LOG -Value $line
}

# Clear any stale subscription from a prior session
Get-EventSubscriber -SourceIdentifier $SOURCE_ID -ErrorAction SilentlyContinue |
    Unregister-Event -ErrorAction SilentlyContinue

Write-WatchLog "watcher starting (PID $PID, user $env:USERNAME)"

# Register the subscription. The Action block runs in a separate runspace and
# is self-contained on purpose - no outer-scope variables.
$query = @"
SELECT * FROM __InstanceCreationEvent WITHIN 2
WHERE TargetInstance ISA 'Win32_LogicalDisk'
  AND TargetInstance.DeviceID = 'E:'
"@

Register-CimIndicationEvent `
    -Query $query `
    -SourceIdentifier $SOURCE_ID `
    -Action {
        # Read the persistent User env var at fire-time and inject it into the
        # child process explicitly. Don't rely on env inheritance - on the first
        # session after [Environment]::SetEnvironmentVariable(...,'User'), in-flight
        # processes haven't picked up the registry change yet.
        $stamp  = (Get-Date -Format 'o')
        $laHost = [Environment]::GetEnvironmentVariable('BHN_LA_HOST','User')
        if (-not $laHost) { $laHost = 'la' }
        Add-Content -Path 'C:\BHN\watch-vault.log' -Value "$stamp  vault unlock detected (LA=$laHost) -> launching sync"
        $cmd = '& { $env:BHN_LA_HOST=''' + $laHost + '''; & ''C:\BHN\bhn-vault-sync.ps1'' }'
        Start-Process -FilePath 'powershell.exe' `
            -ArgumentList @('-NoProfile','-WindowStyle','Hidden','-Command',$cmd) `
            -WindowStyle Hidden
    } | Out-Null

Write-WatchLog "subscribed to Win32_LogicalDisk DeviceID='E:' arrival events"

# Sleep loop keeps the process alive. The Action runspace fires events into this
# process's event queue. Tighter sleeps mean nothing here - WMI events are
# delivered out-of-band.
try {
    while ($true) {
        Start-Sleep -Seconds 60
    }
} finally {
    Write-WatchLog "watcher exiting"
    Get-EventSubscriber -SourceIdentifier $SOURCE_ID -ErrorAction SilentlyContinue |
        Unregister-Event -ErrorAction SilentlyContinue
}
