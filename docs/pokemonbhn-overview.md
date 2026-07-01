# PokemonBHN — Graded Card Market Intelligence

**Status:** Pipeline live, scraper rework pending | **Progress:** 50%

## What It Is

A self-contained collectibles intelligence pipeline tracking two market signals for WOTC-era Pokémon cards: **scarcity** (CGC/PSA graded population counts) and **price** (eBay sold comps). Both signals are keyed off a single curated watchlist and normalized against a canonical grade catalog, making it trivially easy to add a new card to monitoring.

This data powers personal investment/collection research and serves as the backend for **Pokemon Blackhole** (repo `TEAM-ROCKET-BHN`) — a GBA-style FireRed/LeafGreen battle interface that renders card market data as Pokémon battles.

---

## Source of Truth — `master_card_catalog`

The shared scraper queue. Every collector reads `WHERE active = true`, so adding a card to the watchlist is a single `INSERT … active = true` and it auto-enrolls across all pipelines.

**Coverage:** 8 WOTC sets (Base Set, Fossil, Jungle, Team Rocket, Gym Heroes, Gym Challenge, Wizards Black Star Promos, Best of Game). Fully audited to canonical completeness against Bulbapedia + pkmncards for the six main sets — every card carries its standard editions (1st Edition + Unlimited; Base Set also Shadowless).

**Size:** 637 distinct cards / 1,354 variant rows

---

## Data Flow

```
master_card_catalog  (active = true → set_name, card_number)
   │
   ├─ CGC pop scraper ─── public JSON API ────────────────────┐
   │   cgc-pop-scrape.js                                       │
   │   LA weekly cron: bhn-cgc-pop-refresh.timer              ├─ cgc-pop-load.js → pop_reports
   │                                                           │   (grader-agnostic upsert)
   ├─ PSA pop scraper ─── stealth browser, runs OFF-LA ───────┘
   │   psa-pop-scrape.js (puppeteer-extra + stealth)
   │   Clears Cloudflare → POST /Pop/GetSetItems → ships JSON to LA
   │
   └─ eBay sold comps ─────────────────────────────────────→ ebay_transactions / sold_listings
       V8 loader: 15,497 rows loaded; card_id recovery at 82.9%
```

---

## Scrapers

**CGC** (`cgc-pop-scrape.js`) — CGC exposes a public population JSON API (no auth, no browser required). Scrapes every tracked set, asserts completeness against the API's `TotalCount`, loads via `cgc-pop-load.js`. Deployed on LA as the `bhn-cgc-pop-refresh.{service,timer}` weekly job.

**PSA** (`psa-pop-scrape.js`) — PSA has no population API and its pages sit behind a Cloudflare managed challenge. Uses a decoupled residential fetch model: a stealth browser clears Cloudflare once, then calls the page's own `POST /Pop/GetSetItems` endpoint so `cf_clearance` rides along. Never runs on LA (datacenter IPs get challenged hardest) — runs on a residential machine, emits CGC-shaped JSON, LA ingests via `cgc-pop-load.js`. Set→PSA heading mapping curated in `psa-sets.json`.

**eBay sold comps** — V8 loader (`ebay-sold-load-v8.js`) with firefox144 TLS impersonation for access. 15,497 rows loaded into `ebay_transactions`. Title-reparse script (`ebay-title-reparse.js`) recovered `card_id` from 3% → 82.9%. eBay's sold/completed listing page still blocks even with impersonation — scraper rework pending.

---

## Tables

| Table | Purpose | Rows |
|---|---|---|
| `master_card_catalog` | Watchlist / scraper queue | 1,354 variants |
| `pop_reports` | Graded population counts per (grader, card, grade) | Live |
| `ebay_transactions` | eBay sold comp bronze table | ~15,497 |
| `silver_ebay_transactions` | Promoted, deduped silver layer | Live |
| `sold_listings` | Legacy sold comps (pre-V8) | 651 |
| `ebay_listings` | Active eBay listings (n8n feed) | Live |
| `master_grade_catalog` | Canonical grade scale per grader (CGC/PSA/BGS/SGC/TAG) | Reference |
| `master_grading_criteria_catalog` | Condition factors per grader, PSA qualifiers | Reference |
| `master_set_catalog` | One row per set; `master_card_catalog.set_name` FK | Reference |

---

## Data Standard

The PokemonBHN domain is governed by `infrastructure/docs/pokemonbhn/collectibles-data-standard.md` — the single source of truth for table/column naming, canonical value vocabularies, the verbatim-`raw_label` grade model, identity model, and enforcement rules. The live DB wins over any doc; the standard doc wins over any chat transcript.

**Core rules:**
- `master_` prefix = reference/source-of-truth tables; plural nouns = observation data
- Surrogate `card_id` is the join key; unique card identity = `(set_name, card_number, edition, print_variant)`
- Grades stored as verbatim `raw_label` (text), FK-constrained to `master_grade_catalog` — unknown labels rejected at insert
- Hard FK enforcement on controlled tables (`sold_listings`, `pop_reports`); soft validate-and-log on live feed (`ebay_listings`)
- `listed_price` (asking) and `sold_price` (actual sale) are distinct columns; valuation uses sold only

**PBDD system:** Grade codes follow the PBDD format (`{grader}-{numeric_grade}-{tier}`), replacing the old PBDS naming. Applied to `master_grade_catalog`, `card_code` columns, and all scraper/n8n workflows.

---

## Known Gaps

1. **eBay scraper rework pending** — sold/completed page (LH_Sold=1) blocks even with firefox144 TLS impersonation. Parser CSS selectors are also stale from the V8 round.
2. **`card_id` backfill at 82.9%** — remaining 17.1% in `ebay_transactions` cannot be matched by title parsing; likely require manual mapping or alternate signal.
3. **PSA coverage gap** — Wizards Black Star Promos fragmented across multiple PSA year-headings; skipped until multi-heading support added.
4. **`silver_ebay_transactions`** — table created and promotion function built (`promote_bronze_to_silver()`); n8n promotion workflow not yet built.

---

## Roadmap

1. Fix eBay sold-comps scraper (CSS parser update + sold page access approach)
2. Complete `card_id` backfill on remaining 17.1% of `ebay_transactions`
3. Build n8n workflow to run `promote_bronze_to_silver()` on schedule
4. Multi-heading PSA support for Wizards Black Star Promos
5. BGS/SGC pop scraper (CGC-shaped JSON, same load path)
