# PSA population-report refresh — runs on the OPERATOR PC (residential IP + real Chrome).
# Decoupled model (mirrors CGC): scrape -> JSON -> ship to LA -> load via cgc-pop-load.js.
# PSA sits behind Cloudflare, so this MUST run with a headful Chrome on a residential IP;
# do NOT run on the LA datacenter VPS.
#
# First run must be SUPERVISED (watch Chrome clear the Cloudflare challenge once — it seeds
# infrastructure/scrapers/_psa-profile with the cf_clearance cookie for subsequent runs).
# Do not run concurrently with other heavy Chrome automation (e.g. the eBay MCP scrape).
$ErrorActionPreference = 'Stop'

$ScraperDir = Split-Path $PSScriptRoot -Parent           # infrastructure/scrapers
$OutDir     = Join-Path $env:LOCALAPPDATA 'bhn-psa-pop'
$LaHost     = 'root@10.8.0.1'
# 7 fully-mapped WOTC sets. Wizards Black Star Promos is pending multi-heading mapping (psa-sets.json).
$Sets       = 'Base Set,Fossil,Jungle,Team Rocket,Gym Heroes,Gym Challenge,Best of Game'

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Get-ChildItem $OutDir -Filter 'psa-*.json' -ErrorAction SilentlyContinue | Remove-Item -Force
Set-Location $ScraperDir

Write-Host "[psa-pop] scraping 7 WOTC sets (headful Chrome; clears Cloudflare)..."
# --no-filter: capture full set population (pop_reports is full-scale) and avoid needing a DB
# password in the scrape step. The load step is the only thing that touches the DB.
& node psa-pop-scrape.js --no-filter --sets "$Sets" --out-dir $OutDir
if ($LASTEXITCODE -ne 0) { Write-Warning "[psa-pop] scrape exit code $LASTEXITCODE — loading whatever JSON was produced" }

$jsons = Get-ChildItem $OutDir -Filter 'psa-*.json'
if (-not $jsons) { throw "[psa-pop] no PSA JSON produced — aborting (Cloudflare not cleared?)" }

Write-Host "[psa-pop] shipping $($jsons.Count) JSON files to LA..."
& ssh -o BatchMode=yes $LaHost 'mkdir -p /tmp/psa-pop && rm -f /tmp/psa-pop/*.json'
& scp -o BatchMode=yes @($jsons.FullName) "${LaHost}:/tmp/psa-pop/"
if ($LASTEXITCODE -ne 0) { throw "[psa-pop] scp failed" }

Write-Host "[psa-pop] loading into pop_reports on LA (cgc-pop-load.js | psql)..."
# node (as root) emits SQL; psql (as postgres via sudo socket) applies it. Same loader as CGC.
& ssh -o BatchMode=yes $LaHost 'node /opt/bhn/cgc-pop-scraper/cgc-pop-load.js /tmp/psa-pop/*.json | sudo -u postgres psql -d eventhorizon -v ON_ERROR_STOP=1 -q -P pager=off; rm -f /tmp/psa-pop/*.json'
if ($LASTEXITCODE -ne 0) { throw "[psa-pop] LA load failed" }

Write-Host "[psa-pop] done."
