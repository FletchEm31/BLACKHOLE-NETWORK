# BHN SESSION HANDOFF — 2026-06-30

## Status: COMPLETE

---

## COMPLETED THIS SESSION

1. **WEATHERBHN SCHEMA AUDIT**
   - Read-only audit of all 12 `weather_*` tables in `eventhorizon`
   - Produced full schema reference: `infrastructure/docs/WeatherBHN/WEATHERBHN-SCHEMA-REFERENCE-2026-06-30.md`
   - Documented columns, constraints, indexes, grants, write frequency, and collector health per table
   - Key findings:
     - `model_calibration` missing `weather_` prefix → future rename to `weather_silver_model_calibration`
     - `weather_bronze_noaa_daily_actuals` uses `icao_code` / `date` instead of `station_code` / `target_date` — naming inconsistency, any cross-join needs explicit alias
     - `weather_gold_contract_ledger` had no DDL file in repo (fixed this session)
     - `weather_bronze_kalshi_market_snapshots` has no volume column → CP4 pinned to illiquid threshold (8¢)
     - `weather_bronze_visual_crossing_actuals` still 0 rows for KLAX — unresolved
     - `model_calibration` 192 rows are summer 2026 only; non-summer bias defaults to 0.0 until fall data arrives (~Oct 2026)

2. **CONTRACT LEDGER DDL ADDED TO REPO**
   - `weather_gold_contract_ledger` schema appended to `sql/weather-gold-schema.sql`
   - Settlement columns (`actual_tmax_f`, `settled_at`, `contract_resolved_yes`, `paper_pnl`) documented separately from signal columns with a comment block explaining they are written by `bhn-weather-settlement-recon` only and intentionally excluded from the orchestrator's `ON CONFLICT DO UPDATE`
   - Three indexes added: station/date, BET_NO partial, unsettled-active partial

3. **README FULL REWRITE**
   - Removed: software stack table, HORIZON AI agent section, entire BLACKHOLE-NETWORK roadmap (was HORIZON phases), AI agent roadmap, pgvector/Redis/ElevenLabs/Twilio mentions, Alpaca paper account IDs, hardcoded bootstrap credentials, Services map section (ports/webhook URLs), Console terminology section (port specifics), AI category from PostgreSQL schema
   - Rewrote Five-Phase Build Plan to reflect actual current state (Foundation / Data Platform / Trading / Collectibles / Resilience)
   - Updated WeatherBHN description: KLAX added as third city, half-Kelly not quarter-Kelly, XGBoost RMSE numbers, CP1-CP4 live
   - Domain order changed to: FinancialBHN, WeatherBHN, SecurityBHN, PokemonBHN

4. **README RESTRUCTURED TO FRONT PAGE + FOUR DOMAIN DOCS**
   - README stripped to: one intro paragraph + four domain blurbs (2-3 sentences + progress % + link) + license
   - No ports, addresses, service names, credentials, or internal architecture anywhere in README
   - Four detail docs created:
     - `docs/weatherbhn-overview.md` — CP1-CP4 pipeline, medallion table reference, timers, model performance, roadmap
     - `docs/financialbhn-overview.md` — strategy roster, Grafana dashboards, data collectors, activation roadmap
     - `docs/securitybhn-overview.md` — security stack by layer, telemetry tables, node mesh, BTEH
     - `docs/pokemonbhn-overview.md` — scrapers, data flow, tables, PBDD system, known gaps, roadmap

5. **BRANCH CLEANUP — STALE EBAY BRANCHES**
   - `fix/ebay-scraper-impers-rework` (326 commits ahead of main): all real work (PBDD, silver layer, card_id recovery, firefox144, V8 loader) was already in main via a separate merge path. No unique content.
   - `fix/ebay-listings-n8n-write-and-filter-rejections` (2 unique commits not in main): cherry-picked into `pokemonbhn-dev`:
     - `d1c9af6` — restore n8n writes to `ebay_listings` + fix filter rejection runoff (`message` column) across all 8 vintage workflow JSONs + sync sold-comps throttle
     - `aab8336` — close write-permission gap on remaining v2 back-compat views (`sold_listings`, `courtyard_listings`, `courtyard_sales`, `collector_crypt_sales`)
   - Both stale branches deleted from remote.

6. **NEW BRANCHES CREATED**
   - `financialbhn-dev` — off main, empty, ready for FinancialBHN work
   - `pokemonbhn-dev` — off main + 2 cherry-picked eBay fixes

---

## MERGE HOLD

**`weatherbhn-dev` → `main`: DO NOT MERGE YET**

Hold condition: 30+ scored paper trades (settled contracts with actual vs predicted outcomes recorded in `weather_gold_contract_ledger`) must validate the pipeline before merging. Currently at 0 settled live trades.

All `weatherbhn-dev` commits are pushed and stable on the remote. Resume from this branch when the hold condition clears.

---

## PENDING (CARRY FORWARD)

1. **WeatherBHN: flip DRY_RUN=false** — after NO-side calibration passes (≥60 live ledger entries with outcomes). Coordinate as a trading gate session.
2. **KLAX missing from `weather_bronze_visual_crossing_actuals`** — investigate `weather_vc_backfill.py` KLAX path; VC actuals not in live pipeline yet but will matter when YES-side trades begin
3. **`weather_silver_calibration_training_set`** — empty placeholder with no spec; either define schema or DROP
4. **`model_calibration` rename** — `weather_silver_model_calibration`; low urgency, plan for a maintenance window; requires search-and-replace across CP3, CP4, gold_builder, calibration_build scripts
5. **NOAA column rename** — `icao_code` → `station_code`, `date` → `target_date` in `weather_bronze_noaa_daily_actuals`; low risk ALTER; historical backfill is complete so timing is flexible
6. **`weather_gold_contract_ledger` settlement cols DDL** — the C: version of `weather-gold-schema.sql` was the old v0 file; the correct live DDL (XGBoost feature table + ledger) is on `weatherbhn-dev` remote but C: local copy gets clobbered by Proton. Reconcile SQL files in a dedicated maintenance session.
7. **eBay scraper rework (pokemonbhn-dev)** — sold/completed page (LH_Sold=1) still 403s with firefox144; CSS selectors also stale from V8 round. Blocked pending new scraping approach.
8. **`card_id` backfill** — 17.1% of `ebay_transactions` still unmatched (82.9% recovered by title-reparse); remaining rows need manual mapping or alternate signal.
9. **`promote_bronze_to_silver()` n8n workflow** — function exists on LA, automation workflow not built yet.
10. **PSA Wizards Black Star Promos** — fragmented across multiple year-headings; skipped until multi-heading support added.
11. **Justin Probst WireGuard config delivery** — send via Signal (carried from prior sessions)
12. **PSA lost cards claim** — Sabrina's Gastly cert 154271366, Sabrina's Psyduck cert 154271367 (carried from prior sessions)

---

## BRANCH STATE

| Branch | Base | Purpose | Status |
|---|---|---|---|
| `main` | — | Production | ✅ Current — Helsinki EU1 node |
| `weatherbhn-dev` | main | WeatherBHN pipeline + docs | ✅ Pushed — merge hold (≥30 scored trades) |
| `financialbhn-dev` | main | FinancialBHN work | 🆕 Empty, ready |
| `pokemonbhn-dev` | main | PokemonBHN work + eBay fixes | 🆕 2 cherry-picks applied |

---

## NODE STATUS

| Node | Status | Notes |
|---|---|---|
| BHN-LOSANGELES-US1 | ✅ Operational | WeatherBHN pipeline live, CP1-CP4 running, DRY_RUN=true |
| BHN-NEWJERSEY-US2 | ✅ Operational | Strat 13 active (operational test) |
| BHN-HILLSBORO-US3 | ✅ Operational | Tor relay BHNHeliosUS3 active |
| BHN-HELSINKI-EU1 | ✅ Operational | Commissioned 2026-06-27; Tor relay BHNAuroraEU1 active |
| Frankfurt EU1 | ⛔ Decommissioned | May 2026; archived |

---

## WEATHERBHN PIPELINE STATUS (as of 2026-06-30)

| Component | Status | Notes |
|---|---|---|
| CP1 data sanity | ✅ Live | |
| CP2 structural arb | ✅ Live | |
| CP3 XGBoost inference | ✅ Live | Test RMSE 2.13°F vs 2.42°F NWS baseline |
| CP4 Kelly sizer | ✅ Live | DRY_RUN=true; NO-side only |
| Model training | ✅ Weekly | Sunday 02:00 UTC; 7,096 rows (51 live + 7,056 historical) |
| Gold builder | ✅ Daily | 06:30 UTC; live rows only; ON CONFLICT DO NOTHING |
| Calibration | ✅ Daily | 06:00 UTC; 192 rows, summer only |
| Settlement recon | ✅ Daily | 15:00 UTC; 0 settled live contracts to date |
| Kalshi collector | ✅ Live | ~33 min; 5.28M rows |
| VC backfill | ✅ Daily | 00:01 UTC; KLAX gap persists |
| CP4 volume threshold | ⚠️ Pinned illiquid | No volume col in snapshot table; 8¢ threshold |
| KLAX VC actuals | ⚠️ 0 rows | Backfill writes not landing |
| model_calibration coverage | ⚠️ Summer only | Non-summer bias = 0.0 until Oct 2026 |
