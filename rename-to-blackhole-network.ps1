#requires -version 5.1

<#
.SYNOPSIS
    Renames the repo folder from EVENT HORIZON VPN DASHBOARD to BLACKHOLE-NETWORK
    and preserves the Claude Code auto-memory directory.

.DESCRIPTION
    BEFORE RUNNING:
      1. Close all Claude Code sessions
      2. Close any terminal, editor, or File Explorer window pointing at the source folder
      3. Open a NEW PowerShell window with cwd OUTSIDE the source folder
         (e.g., cd C:\Users\fletc\ first)
      4. Optionally copy this script to your Desktop and run it from there

    To run:
      powershell -ExecutionPolicy Bypass -File .\rename-to-blackhole-network.ps1
      powershell -ExecutionPolicy Bypass -File .\rename-to-blackhole-network.ps1 -DryRun

.PARAMETER DryRun
    Print what would happen without making any changes.
#>

[CmdletBinding()]
param([switch]$DryRun)

$ErrorActionPreference = 'Stop'

$src    = 'D:\GITHUB REPOSITORY\EVENT HORIZON VPN\EVENT HORIZON VPN DASHBOARD'
$dst    = 'D:\GITHUB REPOSITORY\BLACKHOLE-NETWORK'
$parent = 'D:\GITHUB REPOSITORY\EVENT HORIZON VPN'
$memSrc = 'C:\Users\fletc\.claude\projects\D--GITHUB-REPOSITORY-EVENT-HORIZON-VPN-EVENT-HORIZON-VPN-DASHBOARD'
$memDst = 'C:\Users\fletc\.claude\projects\D--GITHUB-REPOSITORY-BLACKHOLE-NETWORK'

function Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function OK($m)   { Write-Host "    OK: $m" -ForegroundColor Green }
function Warn($m) { Write-Host "    WARN: $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "    ERROR: $m" -ForegroundColor Red; exit 1 }

# Refuse to run if cwd is inside the source folder (would prevent the move)
$cwd = (Get-Location).Path
if ($cwd -like "$src*") {
    Fail "Current directory is inside source folder.`n      Run from outside, e.g.:  cd C:\Users\fletc\; then re-run this script"
}

# Source must exist
if (-not (Test-Path -LiteralPath $src)) { Fail "Source not found: $src" }

# Destination must not exist
if (Test-Path -LiteralPath $dst) { Fail "Destination already exists: $dst`n      Move or delete it before running this script." }

# --- Move the folder ---
Step "Moving folder"
Write-Host "    src: $src"
Write-Host "    dst: $dst"
if ($DryRun) {
    Warn "DRY RUN: skipped"
} else {
    try {
        Move-Item -LiteralPath $src -Destination $dst
        OK "Moved"
    } catch {
        Fail "Move failed: $($_.Exception.Message)`n      Close any terminals, editors, or File Explorer windows showing the source folder, then retry."
    }
}

# --- Copy Claude auto-memory ---
Step "Copying Claude Code auto-memory"
Write-Host "    src: $memSrc"
Write-Host "    dst: $memDst"
if (-not (Test-Path -LiteralPath $memSrc)) {
    Warn "Source memory dir not found (skipping)"
} elseif (Test-Path -LiteralPath $memDst) {
    Warn "Destination memory dir already exists (skipping)"
} elseif ($DryRun) {
    Warn "DRY RUN: skipped"
} else {
    Copy-Item -LiteralPath $memSrc -Destination $memDst -Recurse
    OK "Memory copied"
}

# --- Report on empty parent folder ---
Step "Checking parent folder"
Write-Host "    parent: $parent"
if (Test-Path -LiteralPath $parent) {
    $items = @(Get-ChildItem -LiteralPath $parent -Force -ErrorAction SilentlyContinue)
    if ($items.Count -eq 0) {
        Warn "Parent is empty. Remove manually with:  Remove-Item -LiteralPath '$parent'"
    } else {
        Warn "Parent still contains $($items.Count) item(s); leaving in place"
    }
} else {
    OK "Parent folder no longer exists"
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host " RENAME COMPLETE" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host "  New repo location:    $dst"
Write-Host "  New memory location:  $memDst"
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  cd '$dst'"
Write-Host "  git status        # confirm repo is intact"
Write-Host "  claude            # reopen Claude Code in the new location"
