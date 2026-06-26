# PSA pop-report scraper — production deploy (operator PC)

PSA's population report sits behind a Cloudflare managed challenge, so — unlike CGC (clean JSON
API, runs on LA) — the PSA scraper must run with a **headful real Chrome on a residential IP**.
LA's datacenter IP gets challenged/flagged, so PSA runs on the **operator PC** and ships JSON to
LA for loading (the same decoupled model CGC uses).

```
operator PC:  psa-pop-scrape.js (headful Chrome, residential)  ->  psa-*.json
              -> scp to LA:/tmp/psa-pop/
LA:           cgc-pop-load.js /tmp/psa-pop/*.json | psql  ->  pop_reports   (same loader as CGC)
```

## Prerequisites (verified present on this PC, 2026-06-01)
- node + `infrastructure/scrapers/node_modules` (puppeteer-extra, stealth, pg, csv-parse) ✅
- System Chrome + puppeteer bundled Chromium ✅
- `psa-sets.json` with 7 mapped WOTC sets ✅
- SSH key auth to `root@<BHN_WG_LA_IP>` (WireGuard) ✅
- LA has `/opt/bhn/cgc-pop-scraper/cgc-pop-load.js` (reused) ✅

## Sets
Deploys the **7 fully-mapped** WOTC sets: Base Set, Fossil, Jungle, Team Rocket, Gym Heroes,
Gym Challenge, Best of Game. **Wizards Black Star Promos is intentionally excluded** — PSA
fragments it across 4 year-headings (2000/2001/2003/2006) and `psa-sets.json` has
`headingID: null, verify: true`. Map those headings, then add it to `$Sets` in the wrapper.

## First run — SUPERVISED (do this once, when NOT running the eBay MCP scrape)
The first run opens a visible Chrome to clear Cloudflare and seeds
`infrastructure/scrapers/_psa-profile` with the `cf_clearance` cookie.

```powershell
& "infrastructure\scrapers\deploy\bhn-psa-pop-refresh.ps1"
```
Watch Chrome clear "Just a moment…", then confirm the LA load report. Verify:
```
ssh root@<BHN_WG_LA_IP> "sudo -u postgres psql -d eventhorizon -c \"SELECT card_set, COUNT(*) FROM pop_reports WHERE grader='PSA' GROUP BY card_set ORDER BY card_set\""
```

## Schedule it (after a clean supervised run)
```powershell
& "infrastructure\scrapers\deploy\register-psa-pop-task.ps1"
```
Registers `BHN-PSA-Pop-Refresh`, Sundays 04:00, **only when logged on** (headful needs an
interactive session). Manual trigger: `Start-ScheduledTask -TaskName 'BHN-PSA-Pop-Refresh'`.

## Caveats
- **Do not run concurrently** with the eBay Chrome-MCP scrape — competing browser automation.
- Headful ⇒ "run only when logged on." For unattended runs set `$env:PSA_HEADLESS='true'` (less
  reliable vs Cloudflare).
- `--no-filter` is used so the scrape needs no DB password and captures full-set population.
- pop_reports has a HARD grade FK; PSA grades (`1`–`10`, `Authentic`) all exist in the catalog,
  so no FK rejects expected. (`cgc-pop-load.js` is all-or-nothing — a novel grade would abort the
  batch; watch the first load.)
