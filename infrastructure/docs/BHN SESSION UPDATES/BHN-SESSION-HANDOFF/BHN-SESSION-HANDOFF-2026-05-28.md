# BHN SESSION HANDOFF — 2026-05-27/28
**Operator:** Hayden Harper (Fletch)
**Session Duration:** ~10 hours
**Claude Instance:** Claude Sonnet 4.6 (claude.ai)

---

## SYSTEM STATUS AT CLOSE

### Infrastructure
| Component | Status | Notes |
|-----------|--------|-------|
| LA Node (Vultr) | ✅ Online | 10.8.0.1 |
| Frankfurt Node | ✅ Online | 192.248.187.208, WG reachable |
| Hillsboro Node | ✅ Online | 5.78.94.237, clean IP |
| WireGuard VPN | ✅ Active | Full tunnel confirmed |
| n8n | ✅ Running | Docker container on LA |
| PostgreSQL (eventhorizon) | ✅ Running | LA, peer auth |
| Grafana | ✅ Running | LA |

### Drive Layout (FLETCH-DESKTOP)
| Drive | Label | Type | Status |
|-------|-------|------|--------|
| Disk 3 | CONSTANTINOPLE (C:) | NVMe M.2 | Windows OS — 349GB free |
| Disk 2 | DRESDEN (D:) | SATA M.2 | ✅ WIPED — Ready for Pop!_OS |
| Disk 1 | ROME (R:) | HDD | Primary data drive — repos, vaults |
| Disk 0 | TOKYO (T:) | HDD | Personal files, downloads |

### Repo Location
- **Canonical:** `R:\GITHUB REPOSITORY\BLACKHOLE-NETWORK`
- **Remote:** https://github.com/FletchEm31/BLACKHOLE-NETWORK
- **Branch:** main

---

## COMPLETED THIS SESSION

### 1. System Migrations
- ✅ Dresden (D:) wiped — diskpart clean + convert gpt. Ready for Pop!_OS
- ✅ All data migrated to ROME — GitHub repos, BHN files, EH Backups
- ✅ BHN-BLACKBOX Cryptomator vault moved from D:\ to R:\, repointed and verified
- ✅ Windows default folders redirected to TOKYO
- ✅ Tabby profile (APS 3WAY) updated — working directory R:\GITHUB REPOSITORY\BLACKHOLE-NETWORK
- ✅ GitHub Desktop repointed to ROME

### 2. Git Commits This Session (main branch)
| Commit | Description |
|--------|-------------|
| `e83701b` | fix(pokemonbhn): ebay-sold-load.js normalizes print_variant against catalog |
| `e518273` | docs(pokemonbhn): §10.8 — Courtyard listed_price + shipping intentionally NULL |
| `bf81755` | docs(pokemonbhn): FRA SOCKS5 scrape egress runbook |
| `07c7f95` | feat(sql): market_*_est trigger — populates 6 estimate cols on all 3 market tx tables |
| `6e249e6` | fix(pokemon): COURTYARD-BHN LISTINGS-COLLECTOR parses ev.asset on listings |
| `646fda9` | chore(repo): gitignore scraper data + checkpoints; untrack legacy CSV |
| `723147a` | feat(pokemonbhn): master XLSX export of ebay_transactions per set |
| `28cf42c` | feat(pokemonbhn): scraping work-order Progress tab in master XLSX export |
| `3db6692` | security(pokemonbhn): eBay scraper cold-start warmup + v2 schema mapping fix |
| `11904ea` | feat(sql): migration to drop legacy master_card_catalog.variant |

### 3. Database Changes (Applied to LA)
- ✅ market_*_est trigger — 1083 eBay + 546 Courtyard rows backfilled
- ✅ card_code backfill — 99.8% coverage (1090/1092) on ebay_transactions
- ✅ print_variant normalization — 140 rows corrected
- ✅ Null coercion fix — 580 literal 'null' strings → SQL NULL
- ✅ SOLD-COMPS workflow published — fires every 3 hours
- ✅ Courtyard LISTINGS-COLLECTOR bug fixed — ev.asset→ev.nft patch
- ✅ LA deployment completed — ebay-sold-load.js + scraper v2 schema fixes

### 4. New Files Created
- `infrastructure/scrapers/export-master.js` — Master XLSX export (265 lines)
- `infrastructure/docs/pokemonbhn/fra-socks5-scrape-runbook.md` — Frankfurt tunnel runbook
- `infrastructure/docs/pokemonbhn/collectibles-data-standard.md` — Updated §10.8 + §3.5.1
- `infrastructure/docs/pokemonbhn/BHN-Grade-Label-Reference.md` — Updated (reholder + CGC corrections)
- `sql/migrations/2026-05-28-market-seller-estimates-trigger.sql` — Applied to LA ✅
- `sql/migrations/2026-05-27-drop-mcc-variant.sql` — Staged, NOT applied
- `sql/migrations/2026-05-28-grade-catalog-corrections.sql` — Staged, NOT applied

---

## STAGED BUT NOT APPLIED TO LA

| Migration File | What It Does | Risk |
|---------------|--------------|------|
| `sql/migrations/2026-05-27-drop-mcc-variant.sql` | Drops legacy variant column from master_card_catalog | Medium — verify 3 consumer files first |
| `sql/migrations/2026-05-28-grade-catalog-corrections.sql` | CGC tier_label fixes + reholder_eligible column + Gem Mint 9.5 legacy row + RAW grader sentinel | Low — additive |

---

## DATA STATUS AT CLOSE

### Row Counts
| Table | Rows | Notes |
|-------|------|-------|
| ebay_transactions | 1,092 | 99.8% card_code populated |
| courtyard_transactions | 546 | Historical backfill only |
| courtyard_asks | 48+ | Live — growing every 15 min |
| courtyard_bids | 0 | Empty |
| ebay_asks | 4.4M | Pre-existing |
| ebay_bids | 0 | Empty |
| master_card_catalog | 968K | 8 WOTC sets |
| tokenized_arbitrage_signals | 0 | Needs courtyard_asks to populate |

### Scraping Progress
| Set | % Complete |
|-----|------------|
| TRK (Team Rocket) | 7.0% |
| BST (Base Set) | 2.6% |
| JGL (Jungle) | 0.7% |
| FSL (Fossil) | 0% |
| GYC (Gym Challenge) | 0% |
| GYH (Gym Heroes) | 0.4% |
| BOG (Best of Game) | 0% |
| WSP (Wizards Promos) | 0% |

### Automated Data Collection Running
| Workflow | Schedule | Status |
|----------|----------|--------|
| n8n POKEMON-BHN SOLD-COMPS | Every 3 hours | ✅ Active — next fire 09:00 UTC |
| Courtyard SALES-COLLECTOR | Every 30 min | ✅ Active |
| Courtyard LISTINGS-COLLECTOR | Every 15 min | ✅ Active — fixed this session |
| Courtyard ARBITRAGE-SIGNALS | Every 30 min | ✅ Active — needs courtyard_asks rows |
| Node.js eBay Scraper | Manual | ❌ OFFLINE — LA IP burned |

---

## SCRAPER STATUS & RESUME INSTRUCTIONS

### Why Scraper Is Down
- LA IP (149.28.91.100) got 403'd by eBay at 01:35 UTC 2026-05-27
- Node fetch() bypasses HTTPS_PROXY — always hit eBay direct from LA
- 24-72hr cooldown minimum

### Frankfurt SOCKS5 Tunnel (Ready — Code Change Still Needed)
Runbook: `infrastructure/docs/pokemonbhn/fra-socks5-scrape-runbook.md`

Start tunnel on LA:
```bash
ssh root@10.8.0.1
screen -S fra-tunnel
ssh -N -D 127.0.0.1:10808 -i /root/.ssh/eh_frankfurt root@10.9.0.2
# Ctrl+A then D to detach
```

IMPORTANT: Node fetch() does NOT auto-route through SOCKS5. undici.Agent wiring needed first — see Priority 1 below.

### eBay Finding API
- App ID: HaydenHa-TEAMROCK-PRD-8183ec2e3-e8d59599
- Rate limit: 5,000 calls/day, resets ~07:00 UTC

---

## GRADING SYSTEM — CONFIRMED CORRECT STRUCTURE

### CGC Label Colors
| Label Color | Grades |
|-------------|--------|
| Blue | Legacy only — Gem Mint 9.5 era (pre-2023) |
| Black | All current grades (Gem Mint 10, Mint+ 9.5, 9 through 1) |
| Gold | Pristine 10 only |

### CGC Confirmed Tier Hierarchy
```
Perfect 10   | Perfect   | 10.0 | LEGACY retired 2023. Reholders to PRISTINE 10. Blue label.
Pristine 10  | Pristine  | 10.0 | Current top tier. Gold label.
Gem Mint 10  | Gem Mint  | 10.0 | Current standard 10. Black label.
Gem Mint 9.5 | Gem Mint  |  9.5 | LEGACY OUTLIER. Blue label. CGC officially = Gem Mint 10.
                                   Reholder: ~$5-10 to upgrade to Gem Mint 10 black label.
                                   HORIZON signal: grade_label "Gem Mint" at numeric 9.5 = blue label slab.
Mint+ 9.5    | Mint+     |  9.5 | Current standard 9.5. Black label. NOT equivalent to a 10.
9 through 1  | (see doc) |      | Raw label IS the bare number. Black label.
AU           | Altered/Ungraded  | NULL |
AA           | Altered/Authentic | NULL |
```

KEY: Gem Mint 9.5 vs Mint+ 9.5 — same numeric grade, same black/blue distinction, completely different market value. grade_label is how HORIZON tells them apart.

### BGS Confirmed Tier Hierarchy
```
Black Label Pristine 10  | Pristine | 10.0 | ALL four subgrades = 10. Rarest. Highest premium.
Gold Label Pristine 10   | Pristine | 10.0 | Overall 10, subgrades can include 9.5. Separate tier.
Pristine 10              | Pristine | 10.0 | Standard Pristine without Black/Gold label designation.
Gem Mint 9.5             | Gem Mint |  9.5 | Practical top for most slabs.
```
Order: BGS Black Label > BGS Gold Label > BGS Pristine 10

### RAW (Ungraded) — Official Designation
```
grader       = 'RAW'
grade        = NULL (not applicable)
grade_label  = 'Ungraded'
grade_numeric = NULL (not applicable)
```
RAW cards get their own table series: raw_transactions, raw_asks, raw_bids. Never mixed with graded tables.

### NULL Ambiguity Rules
```
grader = 'RAW'     → card is ungraded, no grade applies
grader = NULL      → data was not captured
grader = 'UNKNOWN' → grade could not be parsed from title
grade_label = 'Ungraded' when grader = 'RAW'
```

---

## OPEN QUESTIONS FOR OPERATOR DECISION

### 1. CGC Perfect 10 Reholder — RESOLVED
Perfect 10 reholders to PRISTINE 10 (confirmed from CGC official sources). Migration updated.

### 2. BGS/SGC Crossover Programs
Do BGS or SGC have equivalent ~$10 reholder programs like CGC?
- If yes — add reholder_eligible flags to relevant rows in master_grade_catalog

### 3. ebay_transactions Accounting Columns
22 accounting columns all NULL — Sonnet-designed, not operator-reviewed individually.
Decision deferred until real trade data flows. Do NOT touch until operator reviews PART1 lines 218-274.

### 4. card_code_status Column
Proposed across all market tables:
- 'matched' — card_code populated
- 'unmatched_set' — set not in catalog
- 'unmatched_card' — set in catalog but card not found
- 'pending' — not yet attempted
Not implemented. Confirm design before building.

---

## NEXT SESSION PRIORITIES (IN ORDER)

### Priority 1 — Resume eBay Scraper
1. Wire Frankfurt SOCKS5 into Node fetch() — undici.Agent + socks package in ebay-sold-scrape-all.js
2. Start Frankfurt tunnel in screen on LA
3. Verify eBay returns 200 through tunnel
4. Kick off: `BHN_RUN_ON_LA=1 node ebay-sold-scrape-all.js --sets TRK --no-ui`
5. Monitor first run — cold start warmup 120-180s

### Priority 2 — Fresh Residential Proxies
- ProxySeller proxies dead — all timed out
- ~$30-40/mo rotating residential proxies needed
- Add to ebay_scraper_config.json proxies array when acquired

### Priority 3 — card_code on Courtyard Tables
- courtyard_asks + courtyard_bids: add card_code auto-matching on insert
- card_code_status column across all market tables
- Backfill courtyard_transactions (22 WOTC rows parseable)

### Priority 4 — Grade Doc Rewrite (Authorized)
- CGC label color cheatsheet (Blue/Black/Gold)
- CGC 9.5 naming history (Gem Mint legacy → Mint+ current)
- BGS Gold Label as separate tier — add row to master_grade_catalog
- RAW section added to BHN-Grade-Label-Reference.md
- Delete duplicate .txt file — keep .md canonical
- Rename Word doc to LEGACY-PokemonBHN-Data Standardization Framework.docx

### Priority 5 — Apply Staged Migrations
- `2026-05-27-drop-mcc-variant.sql` — verify 3 consumer files first
- `2026-05-28-grade-catalog-corrections.sql` — low risk, apply when ready

### Priority 6 — PokemonPriceTracker API
- $9.99/mo at pokemonpricetracker.com
- PSA/CGC/BGS data, 50,000+ cards, modern sets included
- No scraping risk — clean API
- Build n8n workflow or standalone script

### Priority 7 — Neo Series Expansion
Confirm codes before adding:
- NEG — Neo Genesis
- NED — Neo Discovery
- NER — Neo Revelation
- NDS — Neo Destiny

### Priority 8 — Pop!_OS Dual Boot
Everything ready — just needs execution:
- DRESDEN (D:) wiped + GPT ✅
- ISO at T:\Downloads\pop-os_24.04_amd64_generic_24 ✅
- Balena Etcher installed ✅
- SANDISKJUMP (X:) available ✅

Steps:
1. Etcher → Flash ISO to SANDISKJUMP
2. Reboot → DEL for BIOS
3. Disable Secure Boot
4. Boot USB → Install to DRESDEN
5. Set DRESDEN as primary boot

---

## IMPORTANT NOTES FOR NEXT SESSION

### eBay IP Status
- 149.28.91.100 (LA) — BURNED. No scraping for 24-72hrs from 01:35 UTC 2026-05-27
- 5.78.94.237 (Hillsboro) — CLEAN. n8n/curl only — Node fetch does NOT route here
- 192.248.187.208 (Frankfurt) — CLEAN. Use for scraping via SOCKS5

### Frankfurt Decommission Warning
Frankfurt is scheduled for decommission. Use it for scraping BEFORE taking it down. Have residential proxies ready first.

### Cryptomator Vaults
| Vault | Location | Status |
|-------|----------|--------|
| OPERATION ROMEO | R:\OPERATION ROMEO | ✅ |
| OPERATION TANGO | T:\OPERATION TANGO | ✅ |
| BHN-BLACKBOX | R:\BHN-BLACKBOX\BHN-BLACKBOX | ✅ Moved from Dresden |

### Sensitive Files — Move to OPERATION TANGO
Currently unencrypted on T:\ root:
- Alpha Vantage API Key
- PROXYSELLER
- VULTR VPS SERVER
- BHN DOMAIN AND ORG

---

## EXPORT TOOL

```powershell
cd "R:\GITHUB REPOSITORY\BLACKHOLE-NETWORK"
node infrastructure/scrapers/export-master.js
```
Output: `infrastructure/scrapers/data/BHN_SOLD_COMPS_MASTER.xlsx`
Tabs: {SET} Master, {SET} {DATE}, {SET} Progress, Summary

---

*Session closed: 2026-05-28 ~06:00 UTC*
*Next session: Wire Frankfurt SOCKS5 → resume scraper → Linux install*
