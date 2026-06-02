# =====================================================================
#  BHN System Potential Audit
#  "Am I leaving performance on the table?"  -  Windows 10 / 11
#
#  HOW TO RUN:
#    1. For the full picture (incl. TPM / Secure Boot), open PowerShell
#       as Administrator: Start > type "powershell" > right-click >
#       "Run as administrator".
#    2. If the script is blocked, run this once for the session:
#         Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#    3. Then: cd to this folder and run:  .\BHN-SystemAudit.ps1
#
#  It only READS state. It changes nothing. Safe to re-run anytime.
# =====================================================================

$flags = New-Object System.Collections.Generic.List[string]
function Section($t) { Write-Host "`n[ $t ]" -ForegroundColor Cyan }

Write-Host "`n=== SYSTEM POTENTIAL AUDIT   $(Get-Date -Format 'yyyy-MM-dd HH:mm') ===" -ForegroundColor White

# ---------------- MEMORY (XMP / DOCP) ----------------
Section "MEMORY (XMP/DOCP)"
$mem = Get-CimInstance Win32_PhysicalMemory
foreach ($m in $mem) {
    $rated = $null
    if ($m.PartNumber -match '(\d{4})') {
        $cand = [int]$Matches[1]
        if ($cand -ge 2400 -and $cand -le 8400) { $rated = $cand }
    }
    $line = "  {0,-14} {1}  running {2} MT/s  ({3} GB)" -f $m.DeviceLocator.Trim(), $m.PartNumber.Trim(), $m.ConfiguredClockSpeed, ($m.Capacity/1GB)
    if ($rated) { $line += "   [rated $rated]" }
    Write-Host $line
}
$cfg = ($mem | Measure-Object -Property ConfiguredClockSpeed -Minimum).Minimum
if ($cfg -le 2400) {
    $flags.Add("MEMORY: running at $cfg MT/s -- XMP/DOCP appears OFF. Enable it in BIOS (Tweaker > Extreme Memory Profile > Profile 1) to hit rated speed.")
}

# ---------------- POWER PLAN ----------------
Section "POWER PLAN"
$plan = (powercfg /getactivescheme) -join ' '
Write-Host "  $plan"
if ($plan -match 'Balanced|Power saver') {
    $flags.Add("POWER: active plan looks like Balanced/Power saver -- switch to High Performance (or 'AMD Ryzen High Performance' if installed) for a desktop.")
}

# ---------------- CPU ----------------
Section "CPU"
$cpu = Get-CimInstance Win32_Processor
Write-Host ("  {0}" -f $cpu.Name.Trim())
Write-Host ("  Cores: {0}   Threads: {1}   Max: {2} MHz   Current: {3} MHz" -f $cpu.NumberOfCores, $cpu.NumberOfLogicalProcessors, $cpu.MaxClockSpeed, $cpu.CurrentClockSpeed)
if ($cpu.VirtualizationFirmwareEnabled -eq $false) {
    $flags.Add("CPU: hardware virtualization (SVM) is DISABLED in firmware -- enable 'SVM Mode' in BIOS if you run VMs / hypervisors.")
}

# ---------------- GPU + DISPLAY ----------------
Section "GPU / DISPLAY"
$gpus = Get-CimInstance Win32_VideoController
foreach ($g in $gpus) {
    if (-not $g.Name) { continue }
    Write-Host ("  {0}" -f $g.Name)
    Write-Host ("    Driver: {0}   Resolution: {1}x{2} @ {3} Hz" -f $g.DriverVersion, $g.CurrentHorizontalResolution, $g.CurrentVerticalResolution, $g.CurrentRefreshRate)
    if ($g.CurrentRefreshRate -eq 60 -and $g.Name -notmatch 'Basic|Microsoft') {
        $flags.Add("DISPLAY: '$($g.Name)' is at 60 Hz -- if your monitor is high-refresh (144/165/240), set it under Settings > Display > Advanced display.")
    }
}
# NVIDIA PCIe link width (only if nvidia-smi is present)
try {
    $smi = & nvidia-smi --query-gpu=name,pcie.link.gen.current,pcie.link.gen.max,pcie.link.width.current,pcie.link.width.max --format=csv,noheader 2>$null
    if ($smi) {
        Write-Host "    NVIDIA PCIe (name, genCur, genMax, widthCur, widthMax):" -ForegroundColor DarkGray
        Write-Host "      $smi" -ForegroundColor DarkGray
        $p = ($smi -split ',') | ForEach-Object { $_.Trim() }
        if ($p.Count -ge 5 -and $p[3] -ne $p[4]) {
            $flags.Add("GPU: PCIe link width is $($p[3]) but card supports $($p[4]) -- reseat in the top x16 slot / check riser. (Width drops at idle; re-check under load before acting.)")
        }
    }
} catch { }

# ---------------- STORAGE ----------------
Section "STORAGE"
try {
    Get-PhysicalDisk | Sort-Object DeviceId | ForEach-Object {
        Write-Host ("  {0,-34} {1,-6} {2,8:N0} GB   Health: {3}" -f $_.FriendlyName, $_.BusType, ($_.Size/1GB), $_.HealthStatus)
    }
    Write-Host "  (NVMe: confirm it's in a CPU-direct M.2 slot for full PCIe 4.0; verify real speed with CrystalDiskMark.)" -ForegroundColor DarkGray
} catch { Write-Host "  (Could not enumerate physical disks.)" }

# ---------------- SECURITY / FEATURES (needs admin) ----------------
Section "SECURITY / FEATURES (run as admin for full data)"
try {
    $sb = Confirm-SecureBootUEFI
    Write-Host "  Secure Boot: $sb"
    if (-not $sb) { $flags.Add("SECURITY: Secure Boot is OFF -- needed for BitLocker auto-encryption and the Windows 11 upgrade path.") }
} catch { Write-Host "  Secure Boot: (run as administrator to read)" }
try {
    $tpm = Get-Tpm
    Write-Host ("  TPM Present: {0}   Ready: {1}" -f $tpm.TpmPresent, $tpm.TpmReady)
    if ($tpm.TpmPresent -eq $false -or $tpm.TpmReady -eq $false) {
        $flags.Add("SECURITY: TPM not present/ready -- enable 'AMD fTPM' in BIOS for BitLocker + Win11.")
    }
} catch { Write-Host "  TPM: (run as administrator to read)" }

# ---------------- SUMMARY ----------------
Write-Host "`n=== FLAGS / OPPORTUNITIES ===" -ForegroundColor Yellow
if ($flags.Count -eq 0) {
    Write-Host "  None found -- system looks fully tuned." -ForegroundColor Green
} else {
    $i = 1
    foreach ($f in $flags) { Write-Host ("  {0}. {1}" -f $i, $f) -ForegroundColor Yellow; $i++ }
}
Write-Host ""
