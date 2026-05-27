# BHN eBay Sold Comps Scraper — Claude Code Handoff
**Date:** 2026-05-27  
**From:** claude.ai chat session  
**To:** Claude Code  
**Priority:** High — unblock data pipeline for all 8 WOTC sets

---

## What This Is

Build a Node.js eBay sold comps scraper for the PokemonBHN domain. This scraper
collects graded Pokémon card sold listings from eBay search results and loads them
into the `ebay_transactions` table in the `eventhorizon` PostgreSQL database on LA.

This is the eBay price history pipeline — one of the Big 3 fact streams in the
PokemonBHN data architecture alongside CGC pop reports and active eBay listings.

---

## Where Things Live

### Repo
`D:\GITHUB REPOSITORY\BLACKHOLE-NETWORK`

### Scraper destination (new file)
`infrastructure/scrapers/ebay-sold-scrape.js`

### Companion loader (new file)
`infrastructure/scrapers/ebay-sold-load.js`

### Seed CSV (today's manual scrape — load this first)
Upload to LA then load via the loader. See Section 5 below.
File: `bhn_sold_comps.csv` (445 rows, Team Rocket 1st Edition only)

### Reference — existing scrapers to model after
`infrastructure/scrapers/cgc-pop-scrape.js`     — pagination pattern, error handling
`infrastructure/scrapers/cgc-pop-load.js`       — staging table upsert pattern
`infrastructure/scrapers/cgc-pop-scrape-all.js` — multi-set driver pattern
`infrastructure/scrapers/sets.json`             — set registry pattern

### Database
- Host: `10.8.0.1` (WireGuard VPN — must be on VPN to reach)
- Database: `eventhorizon`
- Target table: `ebay_transactions` (back-compat view: `sold_listings`)
- Auth: standard postgres credentials (check `/etc/bhn-trading/env` pattern on LA)

---

## Data Standard — Read This First

Full spec: `infrastructure/docs/pokemonbhn/collectibles-data-standard.md`

Key rules the scraper must enforce:

**set_name** — 8 exact strings only:
`Base Set` · `Fossil` · `Jungle` · `Team Rocket` · `Gym Heroes` · `Gym Challenge` ·
`Wizards Black Star Promos` · `Best of Game`

**card_number** — bare integer as text. Strip everything:
`#14` → `14` · `14/82` → `14` · `014` → `14`

**edition** — exactly one of: `1st Edition` · `Unlimited` · `Shadowless` · `N/A`
Never `1st Ed`, `1E`, `First Edition` in stored data.

**print_variant** — `Standard` (default, omit from pbds code) or `Holo`, `Error` etc.

**grader** — `PSA` · `CGC` · `BGS` · `SGC` only. Never full names.

**grade** — verbatim raw label exactly as grader prints it. Must exist in
`master_grade_catalog`. NULL for ungraded/raw.

**currency** — always record. `USD`, `CAD`, `GBP`. Never assume USD.

**money** — NULL = unknown, 0 = genuinely free. Never conflate these.

**cert_number** — Option A (confirmed by operator): do NOT fetch individual listing
pages for cert numbers. Leave NULL on all rows. Cert enrichment is a future
separate pass. Do not slow the scraper trying to get these.

---

## The PBDS Code System

Every row needs a `pbds_code`. Format:
```
[SET_CODE][CARD#_ZERO_PADDED_3]-[YEAR]-[EDITION_CODE]-[VARIANT_CODE]
```

Set codes:
| Set | Code | Year |
|-----|------|------|
| Base Set | BST | 1999 |
| Fossil | FSL | 1999 |
| Jungle | JGL | 1999 |
| Team Rocket | TRK | 2000 |
| Gym Heroes | GYH | 2000 |
| Gym Challenge | GYC | 2000 |
| Wizards Black Star Promos | WSP | varies |
| Best of Game | BOG | 2002 |

Edition codes: `1E` · `UN` · `SH` · `NA`
Variant codes: omit when Standard · `HOL` · `ERR` · `NOS` etc.

Examples:
- `TRK014-2000-1E-HOL` — Team Rocket #14 Dark Weezing, 1st Ed, Holo
- `BAS004-1999-1E` — Base Set #4 Charizard, 1st Ed, Standard (variant omitted)

---

## Output Schema — ebay_transactions

These are the exact columns, in order:

| Column | Type | Notes |
|--------|------|-------|
| pbds_code | TEXT | Computed per above |
| item_id | TEXT UNIQUE | eBay listing ID — dedup key |
| title | TEXT | Raw listing title |
| card_name | TEXT | Parsed card name |
| set_name | TEXT | Canonical set name |
| card_number | TEXT | Bare integer |
| edition | TEXT | Controlled vocab |
| print_variant | TEXT | Controlled vocab |
| grader | TEXT | PSA/CGC/BGS/SGC or NULL |
| grade | TEXT | Verbatim raw label or NULL |
| sold_price | DECIMAL(10,2) | No $ prefix in DB |
| currency | TEXT | USD/CAD/GBP |
| shipping | DECIMAL(10,2) | NULL if unknown |
| transaction_type | TEXT | Buy It Now/Best Offer/Auction |
| bid_count | INT | NULL for non-auction |
| created_at | TIMESTAMPTZ | Sale close date |
| seller | TEXT | eBay username (rename to seller_username pending) |
| seller_feedback | INT | Feedback count |
| seller_feedback_pct | TEXT | e.g. 99.8% |
| cert_number | TEXT | NULL (Option A — not collected) |
| location | TEXT | Seller country |
| condition | TEXT | eBay condition label |
| returns_accepted | BOOLEAN | |
| obo_min_price | DECIMAL(10,2) | NULL if none |
| current_bid | DECIMAL(10,2) | Auction format only |
| watchers | INT | |
| listing_url | TEXT | https://www.ebay.com/itm/{item_id} |

---

## Scraper Specification

### Source
eBay sold listings search URL pattern:
```
https://www.ebay.com/sch/i.html
  ?_nkw=[SEARCH_QUERY]
  &_sacat=0
  &LH_Sold=1        ← sold listings only
  &LH_Complete=1    ← completed listings
  &_sop=13          ← sort by most recently sold
  &rt=nc
  &_pgn=[PAGE]      ← pagination
```

Search query pattern per card:
```
pokemon [set_name] 1st edition graded PSA CGC [card_name]
```

### What to collect per result
Extract from each search result tile:
- Item ID (from URL or data attribute)
- Title (full raw)
- Sold price (handle $ and currency conversion markers)
- Currency (detect CAD from price display, GBP from £ symbol)
- Shipping cost
- Transaction type (auction vs BIN vs Best Offer — infer from bid count + price label)
- Bid count (auction format)
- Sale date (from "Sold [date]" label)
- Seller username
- Seller feedback count and percentage
- Item location (country)
- Condition label
- Listing URL

Parse from title (regex):
- Card number: `#(\d+)(?:/\d+)?`
- Grader: detect PSA/CGC/BGS/SGC in title
- Grade: detect numeric grade after grader name
- Edition: detect `1st Edition`, `Unlimited`, `Shadowless`
- Holo variant: detect `Holo` in title

### Stealth / rate-limiting requirements — CRITICAL

eBay will ban the scraper IP if it detects automated access. These rules are
non-negotiable:

**Delays:**
- Base delay between requests: 8–15 seconds (randomized, not fixed)
- Longer pause every 10–15 requests: 25–45 seconds (randomized)
- Full break every ~50 requests: 3–5 minutes
- All delays must use `Math.random()` — never a fixed interval

**Headers — rotate these:**
```javascript
const USER_AGENTS = [
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15',
];
```

Pick a random UA per session (not per request — same UA for the whole run).

**Additional headers to send:**
```javascript
{
  'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
  'Accept-Language': 'en-US,en;q=0.5',
  'Accept-Encoding': 'gzip, deflate, br',
  'Connection': 'keep-alive',
  'Upgrade-Insecure-Requests': '1',
  'Sec-Fetch-Dest': 'document',
  'Sec-Fetch-Mode': 'navigate',
  'Sec-Fetch-Site': 'none',
  'Cache-Control': 'max-age=0',
}
```

**Checkpoint / resume:**
- Write a checkpoint file after every 10 cards processed
- Format: `{ lastCard: 'TRK014', lastPage: 2, processedItems: [...item_ids] }`
- On startup, check for checkpoint and resume from there
- This is critical — a 4-hour run should not restart from zero if interrupted

**Rate limit detection:**
- If response is 429, 503, or contains CAPTCHA indicators: back off 10–20 minutes
- Log the event, do not crash — resume after backoff
- If blocked 3 times in a row: stop and alert (write to error log)

**Search query construction:**
- Search one card at a time, not multi-card bulk queries
- Multi-card queries were tried and returned 0 results for commons
- Pattern: `pokemon team rocket 1st edition graded PSA CGC [card_name]`
- For cards with generic names add card number: `pokemon team rocket 1st edition #14 dark weezing graded`

**Pagination:**
- Collect up to 3 pages per card (eBay returns ~60 results per page = ~180 comps max)
- Stop early if page returns 0 results
- Stop early if page returns only lot/multi-card sales (no individual graded slabs)

---

## Multi-Set Driver

Build a driver script `ebay-sold-scrape-all.js` that reads from `master_card_catalog`
(or a local sets config) and runs the scraper across all 8 sets:

```javascript
// Query master_card_catalog for active cards
SELECT card_name, card_number, set_name, edition, print_variant
FROM master_card_catalog
WHERE active = true
ORDER BY set_name, card_number::int
```

Run sets in this order (smallest to largest, test with Team Rocket first):
1. Best of Game (9 cards)
2. Wizards Black Star Promos (~51)
3. Fossil (62)
4. Jungle (64)
5. Team Rocket (83) ← already have 445 rows, skip or supplement
6. Base Set (102)
7. Gym Heroes (132)
8. Gym Challenge (132)

---

## Loading the Seed CSV

The seed CSV (`bhn_sold_comps.csv`, 445 rows) from today's manual scrape needs to
be loaded into `ebay_transactions` first.

**Steps:**
1. SCP the CSV to LA: `scp bhn_sold_comps.csv root@10.8.0.1:/tmp/`
2. The loader script should:
   - Read the CSV
   - Strip `$` from sold_price before inserting
   - Parse currency from the currency column (USD/CAD)
   - Use staging table + upsert on `item_id` (same pattern as `cgc-pop-load.js`)
   - Skip rows where item_id already exists
   - Report: rows inserted, rows skipped (dupes), rows failed (grade FK violations)

**Known data quality issues in the CSV to handle:**
- `sold_price` column has `$` prefix — strip before inserting
- Some `grade` values may not match `master_grade_catalog.raw_label` exactly —
  log these to a `grade_reject_log` table rather than failing the whole batch
  (grade_reject_log schema: `grader TEXT, raw_label TEXT, item_id TEXT, created_at TIMESTAMPTZ`)
- `bid_count` is sometimes empty string — coerce to NULL
- `shipping` is sometimes empty string — coerce to NULL

---

## Known Gaps in Today's Scrape

64 of 83 Team Rocket card numbers covered. Missing 19:

Cards with real graded sales (scraper should get these):
`#13 Dark Vileplume` · `#23 Dark Electrode (STD)` · `#24 Dark Golbat (STD)` ·
`#25 Dark Gyarados (STD)` · `#27 Dark Jolteon` · `#32 Dark Charmeleon` ·
`#36 Dark Flareon (v2)` · `#40 Dark Kadabra (v2)` · `#48 Full Heal Energy` ·
`#78 Rocket's Sneak Attack (STD)`

Genuinely thin market (may return 0-1 results — that's fine):
`#49 Goop Gas Attack` · `#52 Imposter Oak's Invention` · `#63 Ponyta` ·
`#66 Rattata` · `#73 Devastation` · `#76 Potion Energy` · `#82 Trash Exchange`

---

## Deployment

Once built and tested:
- Deploy alongside CGC scraper at `/opt/bhn/ebay-sold-scraper/`
- Run on-demand (not on a timer) — eBay data goes stale but doesn't need
  daily refresh. Weekly or per-acquisition-research cadence.
- Log output to `/var/lib/bhn-ebay-sold/`
- No systemd timer needed initially — operator triggers manually

---

## Reference: Existing Scraper Architecture

The CGC scraper (`cgc-pop-scrape.js`) is the gold standard for this repo's
scraper pattern. Follow its structure:
- CLI flags with `--name`, `--out` etc.
- `module.exports = { scrapeSet }` for use by the all-sets driver
- Completeness assertion at end of each set
- Structured JSON output, one file per set
- Loader as separate script piped into psql

The key difference: CGC hits a clean JSON API. This scraper hits HTML search
results pages, so it needs `node-fetch` + `cheerio` for HTML parsing instead of
`response.json()`.

Dependencies needed:
```
npm install node-fetch cheerio
```
(Both already standard in Node.js scraper projects — no exotic deps.)

---

## Summary Checklist for Claude Code

- [ ] `ebay-sold-scrape.js` — core scraper, single set/card, HTML parsing
- [ ] `ebay-sold-scrape-all.js` — multi-set driver, reads master_card_catalog
- [ ] `ebay-sold-load.js` — CSV/JSON → postgres loader with upsert + reject log
- [ ] Load seed CSV (`bhn_sold_comps.csv`) into `ebay_transactions`
- [ ] Checkpoint/resume logic built in
- [ ] Stealth delays + UA rotation implemented
- [ ] Rate limit backoff implemented
- [ ] PBDS code generation matches data standard
- [ ] Grade FK violations → grade_reject_log (not hard fail)
- [ ] Test against Team Rocket first, then expand to all 8 sets


---

## Terminal UI Requirements (Operator-Facing)

The operator needs to be able to see, manage, and alter the scrape process in
real time. Build a terminal dashboard UI using the `blessed` or `blessed-contrib`
Node.js library (or Python equivalent with `rich` or `curses` if preferred).

### Required UI panels:

**Header bar:**
- Current set being scraped
- Overall progress: X cards complete / Y total
- Elapsed time + estimated time remaining
- Current status: SCRAPING / PAUSED / RATE LIMITED / COMPLETE

**Live scrape feed (scrolling log):**
- Each card as it completes: `[TRK014] Dark Weezing — 23 results | PSA10: $838 | PSA9: $280 | CGC10: $399`
- Rate limit events: `[BACKOFF] 429 detected — pausing 12m 34s`
- Checkpoint saves: `[CHECKPOINT] Saved at TRK031 — 217 rows captured`

**Stats panel (live updating):**
- Rows captured this run
- Rows skipped (dupes)
- Grade FK failures (rejected rows)
- Cards with 0 results
- Requests made / requests per minute

**Controls (keyboard shortcuts):**
- `P` — Pause / Resume scraping
- `S` — Skip current card, move to next
- `+` / `-` — Increase / decrease delay multiplier (e.g. 1.0x → 1.5x → 2.0x)
- `Q` — Graceful quit (saves checkpoint, flushes buffer to DB)
- `R` — Show/hide rate limit log
- `C` — Show current config (delays, UA, set queue)

**Config panel (editable at runtime):**
Operator should be able to adjust these without restarting:
- Base delay range (min/max seconds)
- Long pause interval (every N requests)
- Break interval (every N requests)
- Max pages per card (1–5)
- Grade filter (e.g. PSA 7+ only, or all grades)

### Python alternative (if Node UI is complex):
If building the UI in Python is cleaner, the scraper can be Python with:
- `requests` + `beautifulsoup4` for fetching/parsing
- `rich` library for the terminal UI (progress bars, live tables, panels)
- PostgreSQL connection via `psycopg2`
- Config via a simple `scraper_config.json` that the UI can read/write

Either language is fine — pick whichever produces the cleanest operator experience.
The scraper logic matters more than the language.

### Config file (`ebay_scraper_config.json`):
```json
{
  "delay_min_sec": 8,
  "delay_max_sec": 15,
  "long_pause_every_n": 12,
  "long_pause_min_sec": 25,
  "long_pause_max_sec": 45,
  "break_every_n": 50,
  "break_min_min": 3,
  "break_max_min": 5,
  "max_pages_per_card": 3,
  "min_grade": 7,
  "sets_to_run": ["Team Rocket", "Base Set", "Fossil", "Jungle", "Gym Heroes", "Gym Challenge"],
  "checkpoint_every_n_cards": 10
}
```

This file should be editable directly and hot-reloaded by the scraper between
cards (not mid-card) so the operator can tweak settings without restarting.

