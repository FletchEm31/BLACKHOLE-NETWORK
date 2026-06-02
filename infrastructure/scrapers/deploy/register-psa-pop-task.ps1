# Register the weekly PSA pop-refresh in Windows Task Scheduler (operator PC).
# Run this AFTER a successful supervised first run of bhn-psa-pop-refresh.ps1
# (so _psa-profile holds a valid Cloudflare cookie).
#
# Headful Chrome needs an interactive, logged-on session, so the task is configured
# "run only when user is logged on" (Interactive logon). If you need fully-unattended
# runs, set $env:PSA_HEADLESS='true' in the wrapper instead (less reliable vs Cloudflare).
$ErrorActionPreference = 'Stop'

$wrapper = Join-Path $PSScriptRoot 'bhn-psa-pop-refresh.ps1'
if (-not (Test-Path $wrapper)) { throw "wrapper not found: $wrapper" }

$action    = New-ScheduledTaskAction -Execute 'powershell.exe' `
               -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$wrapper`""
# Sunday 04:00 local — staggered after CGC's Sun 03:00 UTC LA refresh.
$trigger   = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 4:00AM
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable `
               -ExecutionTimeLimit (New-TimeSpan -Hours 1) -RestartCount 1 -RestartInterval (New-TimeSpan -Minutes 15)

Register-ScheduledTask -TaskName 'BHN-PSA-Pop-Refresh' -Action $action -Trigger $trigger `
  -Principal $principal -Settings $settings -Force `
  -Description 'Weekly PSA population-report scrape (headful Chrome, residential) -> ship to LA -> load into pop_reports'

Write-Host "Registered 'BHN-PSA-Pop-Refresh' (Sundays 04:00, only when logged on)."
Write-Host "Test now : Start-ScheduledTask -TaskName 'BHN-PSA-Pop-Refresh'"
Write-Host "Remove   : Unregister-ScheduledTask -TaskName 'BHN-PSA-Pop-Refresh' -Confirm:`$false"
