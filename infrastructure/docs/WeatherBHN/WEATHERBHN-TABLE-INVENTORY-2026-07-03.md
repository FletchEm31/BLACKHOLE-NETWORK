# WeatherBHN — Complete Table Inventory & Reconciliation Audit
**Date:** 2026-07-03
**Branch:** weatherbhn-dev
**Scope:** All `weather_*` tables in `eventhorizon` (public schema) + directly-related tables (`kalshi_positions`, `model_calibration`, `enso_index`, `weather_kalshi_contract_catalog`).
**Method:** Read-only SSH + psql against BHN-LOSANGELES-US1 (10.8.0.1), cross-checked against repo source (writer scripts confirmed via `INSERT INTO` grep, deployed-file hashes verified against repo where noted, systemd timer status via `systemctl`/`journalctl`). No tables altered or dropped.
**Supersedes:** The 6-table Table Inventory in `WeatherBHN - Architecture and Design Overview.docx` §4 (that doc could not be edited directly — see note at bottom).

This document intentionally reports plainly, including findings that suggest duplicated effort or dead code. Nothing here is softened.

---

## Headline finding: a coordinated shutdown on 2026-06-30 at 02:47:47 UTC

Three independent-looking problems traced back to the **same second**:

| Event | Timestamp |
|---|---|
| `bhn-weather-position-monitor.timer` stopped | 2026-06-30 02:47:47 UTC |
| `bhn-weather-edge-calculator.timer` stopped | 2026-06-30 02:47:47 UTC |
| Last write to `weather_gold_calibrated_probabilities` | 2026-06-30 02:47:47.513934 UTC |

Both timers show a clean `Deactivated successfully` / `Stopped` in the journal — not a crash (no `Failed`/`exit-code` around that moment). This is almost certainly **one deliberate action** (a `systemctl stop` on a group of units, or a redeploy script) rather than two unrelated failures. Net effect, still in place today (3 days later):

- **Stop-loss protection has been fully off since 06-30** — `weather_position_monitor.py` never ran again after that second (dry-run or otherwise).
- **`weather_gold_calibrated_probabilities` has been frozen since 06-30** — but it is *not* dead data: `refresh_contract_ledger()` (the nightly settlement function) still does a live `LEFT JOIN LATERAL` against it for `model_delta_flag`, with no recency filter. Since 06-30, every contract ledger refresh has been silently pulling either a 3-day-stale `model_delta_flag` (if the ticker recurs) or `NULL` (for genuinely new tickers) — no error, no log line, nothing visible.

**This needs a direct answer from you: was this shutdown deliberate?** If yes, worth documenting why (and whether it should stay off). If not remembered, it's worth treating as an unplanned outage.

---

## The 4 named pairs

**`weather_gold_calibrated_probabilities` vs. `weather_gold_contract_ledger` / `weather_gold_city_day_features`**
Verdict: **Live & load-bearing, but currently frozen (see above).** This is not a mystery table someone forgot — it's a real, actively-consumed CP3/edge-calculator output (written by `weather_edge_calculator.py`, 5-min timer, plus `calibrate_probabilities.py` with no dedicated timer found — likely manual/ad-hoc invocation). It feeds `weather_gold_contract_ledger.model_delta_flag` via `refresh_contract_ledger()`. It stopped growing at the exact moment the edge-calculator timer was disabled.

**`weather_contract_prices` vs. `weather_silver_market_conformed`**
Verdict: **Live but redundant with `weather_bronze_kalshi_market_snapshots`.** Both are still actively written by the current `weather_data_collector.py` (confirmed: `weather_contract_prices` had a row timestamped seconds before I queried it — 7.06M rows and climbing). But `weather_contract_prices`' `exchange` column is 100% `'kalshi'`, scoped to the same 8 stations WeatherBHN already tracks via the bronze/silver Kalshi tables — it's a second, older, more-generic schema (`contract_id`/`contract_title`/`implied_probability`) tracking the *same underlying market data* as the newer station/bucket-shaped bronze table, in parallel, at a cost of ~15GB + 2.7GB. This looks like a pre-medallion-refactor schema (defined in `sql/weather-schema.sql`, not the newer `weather-bronze-schema.sql`) that was never decommissioned when the Bronze/Silver/Gold rebuild happened — `weather_data_collector.py` just kept dual-writing to both.

**`weather_forecasts` vs. `weather_silver_forecast_conformed`**
Verdict: **Likely legacy predecessor** (not fully confirmed — flagging honestly rather than guessing). `weather_forecasts` is defined in the old `sql/weather-schema.sql`, referenced by older scripts (`strategy_prediction_alpha.py`, `prediction_signal.py`, `weather_calibration.py`) alongside the current ones. I did not get its live freshness timestamp before compiling this — **recommend a follow-up freshness check** before concluding it's fully dead; structurally it looks like the pre-refactor version of the same concept as `weather_silver_forecast_conformed`.

**The bronze sources**
- `weather_bronze_era5_kmia` — Dead-but-harmless. Manually loaded, KMIA-only, explicitly "not yet wired into CP3" per the 2026-06-30 schema doc. Scaffolding for future feature work, not abandoned by accident.
- `weather_bronze_noaa_daily_actuals` — Dead-but-harmless. One-time historical-backfill source; backfill is complete; stale since 2026-06-22 by design.
- `weather_bronze_noaa_hourly_normals` — Dead-but-harmless, but for a different reason: this is a **climatological reference table** (30-year normals by station/month/hour), not a time-series feed. It isn't supposed to get fresher — it's static reference data. Writer script not identified this session.
- `weather_bronze_nbm_snapshots` — Likely live (documented in the current collector topology as a ~30-min NBM-percentiles source), but I did not independently re-verify its freshness this session — flagging as unconfirmed rather than claiming certainty.

---

## Zero-row tables

| Table | Verdict | Evidence |
|---|---|---|
| `weather_commodity_signals` | Dead-but-harmless, **intentional** | Per existing project notes: commodity/degree-day/ENSO data explicitly out of scope for the active stack. |
| `weather_silver_calibration_training_set` | Dead-but-harmless, **planned scaffolding** | Already documented in the 2026-06-30 schema doc as "placeholder, blocked pending spec, no current pipeline dependency." |
| `weather_silver_model_base` | **Dead-and-likely-superseded** (candidate for drop) | Schema is near-identical to `weather_silver_forecast_conformed` (same city/station_code/target_date/forecast_run_time/lead_hours/nws_tmax_f shape). Never populated. Looks like an earlier draft of the same table that shipped under a different name. Recommend confirming with whoever built it before dropping — not doing that here per the read-only rule. |
| `weather_bets` | **Dead-and-likely-superseded** (candidate for drop) | Generic bet-tracking schema (contract_id/exchange/side/stake_usd/entry_price/kelly_fraction) — reads like an early draft of what became `weather_position_exits` + `weather_gold_contract_ledger`. Never populated. |

---

## Everything else (already characterized earlier this session, restated for completeness)

| Table | Rows | Verdict | Writer (timer status) |
|---|---|---|---|
| `weather_bronze_kalshi_market_snapshots` | 6.69M | Live & load-bearing | `weather_data_collector.py`, `bhn-weather-collector.timer` (enabled/active) |
| `weather_bronze_nws_forecast_snapshots` | 84.9K | Live & load-bearing | same collector, Hillsboro node |
| `weather_bronze_openmeteo_forecast_snapshots` | 131K | Live & load-bearing | same collector, Helsinki node |
| `weather_bronze_visual_crossing_actuals` | 4.4K | Live & load-bearing (secondary actuals + precip source) | `weather_vc_backfill.py`, `bhn-vc-backfill.timer` (00:01 UTC daily); known KLAX gap |
| `weather_silver_forecast_conformed` | 57.6K | **Live path, but broken** — 47+ hrs stale | `weather_data_collector.py` silver-populate step; blocked by `permission denied for table weather_silver_forecast_conformed` on Hillsboro/Helsinki since the 2026-07-01 collector-node migration (known, logged, never fixed) |
| `weather_silver_actuals_conformed` | 5.6K | Live & load-bearing, **nws_cli source stalled since 06-29/06-30** | `bhn-weather-settlement-recon` (nws_cli) + `weather_vc_backfill.py` (visual_crossing, still fresh) |
| `weather_silver_forecast_error` | 94.4K | Live & load-bearing | `weather_calibration_build.py`, `weather-calibration.timer` (06:00 UTC daily) |
| `model_calibration` | 192 | Live & load-bearing | same as above |
| `weather_gold_city_day_features` | 7.1K | Live & load-bearing | `weather_gold_builder.py`, `weather-gold-builder.timer` (06:30 UTC daily) |
| `weather_gold_daily_edge_sheet` | 456 | **Live & load-bearing — not retired** (corrects a stale assumption from earlier in the session) | read nightly by `weather_settlement_reconciliation.py`, feeds `weather_model_accuracy` + `weather_gold_contract_ledger` via `refresh_contract_ledger()` |
| `weather_gold_contract_ledger` | 390 | Live & load-bearing, dual-writer | CP4 orchestrator (~5 min) + nightly `refresh_contract_ledger()` |
| `weather_model_accuracy` | 240 | Live & load-bearing | `weather_settlement_reconciliation.py`, nightly |
| `weather_position_exits` | 30 | Live & load-bearing, **stop-loss half dormant, settlement stalled** | `exit_audit_logger.py` (CP4-called + `bhn-exit-audit.timer`); see Task 3 findings — schema mismatch bug in the (currently disabled) stop-loss path |
| `kalshi_positions` | 255K+ | Live & load-bearing, growing continuously | `weather_position_monitor.py` reads it; writer not traced this session (out of WeatherBHN-specific scope) |
| `weather_kalshi_contract_catalog` | 582 | Live (reader confirmed: `weather_position_monitor.py`) | writer not independently confirmed this session |
| `enso_index` | 2,336 | Read by `refresh_contract_ledger()` for ENSO phase/ONI | writer not traced this session |
| `weather_snapshots` | 2,531 | **Out of WeatherBHN-trading scope** | lat/lon current-conditions schema, no station_code/contract fields — looks like a general-purpose weather-lookup utility, unrelated to the trading pipeline |
| `weather_observations` | 797 | Likely legacy/pre-refactor | generic station_code/variable/value store; small and stale relative to the dedicated bronze tables that now cover this |

---

## What this doesn't cover

- Full writer/reader grep for every single table (`kalshi_positions`, `enso_index`, `weather_kalshi_contract_catalog`, `weather_bronze_nbm_snapshots` freshness, `weather_forecasts` freshness) — flagged above as unconfirmed rather than guessed.
- Anything outside `weather_*` / directly-adjacent tables (the full `eventhorizon` database has ~150 tables total, spanning eBay, PokemonBHN, CollectorCrypt, and other strategies — out of scope for this WeatherBHN audit).
- No image/screenshot was actually reviewed by me this session — table list and row counts were independently pulled via direct SQL, not read off any screenshot.

## Note on delivery

I don't have a way to edit `WeatherBHN - Architecture and Design Overview.docx` directly (binary Word format, no docx-editing tool available, and it's currently open/locked). This markdown file is written so it can be copy-pasted into §4 as a replacement for the current 6-table version.
