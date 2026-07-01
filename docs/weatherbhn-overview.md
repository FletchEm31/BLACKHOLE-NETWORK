# WeatherBHN — Kalshi Temperature Prediction Market Trading

**Status:** Live, DRY_RUN mode (paper P&L only) | **Progress:** 80%

## What It Is

A systematic, model-driven strategy trading daily high temperature contracts on [Kalshi](https://kalshi.com), the U.S.-regulated prediction market exchange. The core thesis: NWS probabilistic forecast data, processed through an ensemble modeling layer and calibrated against historical actuals, produces probability estimates that diverge measurably from Kalshi's market-implied probabilities. That divergence is the tradeable signal.

**Active markets:** KXHIGHDEN (Denver), KXHIGHLAX (Los Angeles), KXHIGHMIA (Miami) — tmax only.

**Strategy:** NO-side only ("Tail-No"). Kalshi weather markets systematically overprice extreme temperature buckets. YES-side extension deferred until ≥60 live ledger entries validate NO-side calibration.

---

## Pipeline Architecture

Four checkpoints run in sequence every ~5 minutes via `core_trading_orchestrator.py`:

### CP1 — Data Sanity Gate
Validates that all required data exists and is coherent before any trade is considered:
- NWS tmax forecast exists and is within plausible range (−20°F to 130°F)
- Kalshi market snapshot exists for the target station/date/bucket
- `yes_bid` and `no_ask` are non-null and non-zero
- Bucket geometry is valid for `between` type buckets
- If a NWS CLI actual exists, `is_final = TRUE` (no preliminary readings)

### CP2 — Structural Arb Check
Scans all open Kalshi buckets for the station/date and flags any structural arbitrage (`yes_bid + no_ask < 1.00` by more than 1¢). Logged but non-blocking — CP3/CP4 proceed regardless. `no_ask` is always read directly from the database; never derived as `1 − yes_price`.

### CP3 — XGBoost tmax Inference
Loads the trained model and returns a tmax prediction for the (station, target_date). Emergency fallback returns `nws_tmax_calibrated_f` from the calibration table if the model file is missing or corrupt.

**Model performance (2026-06-30):**
- Train RMSE: 1.49°F (7,096 rows)
- Test RMSE: 2.13°F (live rows only — honest validation)
- Calibrated NWS baseline: 2.42°F
- Edge vs baseline: +0.29°F improvement

**Feature vector (10 features):** `nws_tmax_f`, `om_tmax_f`, `nws_tmax_mean_bias`, `om_tmax_mean_bias`, `nws_tmax_rmse`, `om_tmax_rmse`, `nws_tmax_calibrated_f`, `forecast_spread_f` (nws−om), `station_enc`, `season_enc`

### CP4 — Half-Kelly Position Sizer
For each Kalshi bucket, computes model P(YES) via Gaussian CDF (center buckets, within 2σ) or Student-t df=5 (tail buckets, >2σ). Derives P(NO) edge and applies half-Kelly sizing.

**Key parameters:**
- Strategy: NO side only
- Edge threshold: ≥8¢ illiquid (default; volume data not yet in snapshot table)
- Bankroll cap: 10% per single contract
- Kelly fraction: half-Kelly
- Sigma: time-decayed from model RMSE (`σ × √(hours_remaining/24)`), floor at 20% of base

**Settlement UTC offsets:** KLAX = 00:00 (midnight, next day); KDEN = 22:00; KMIA = 20:00

---

## Data Pipeline (Medallion Architecture)

### Bronze Layer — Raw Ingestion

| Table | Source | Frequency | Rows (2026-06-30) |
|---|---|---|---|
| `weather_bronze_kalshi_market_snapshots` | Kalshi API | ~33 min | ~5.28M |
| `weather_bronze_nws_forecast_snapshots` | NWS Gridpoint API | ~33 min | ~76,558 |
| `weather_bronze_openmeteo_forecast_snapshots` | Open-Meteo GFS Seamless | ~33 min | ~114,096 |
| `weather_bronze_noaa_daily_actuals` | NOAA GHCND | Manual/periodic | ~127,815 |
| `weather_bronze_visual_crossing_actuals` | Visual Crossing | 00:01 UTC daily | ~2,000 |
| `weather_bronze_era5_kmia` | ECMWF ERA5 (manual load) | On-demand | ~58,158 |

Known gap: `weather_bronze_visual_crossing_actuals` has 0 rows for KLAX — under investigation.

### Silver Layer — Conformed & Calibrated

| Table | Purpose | Rows |
|---|---|---|
| `weather_silver_actuals_conformed` | Source-tagged observed actuals | ~3,113 |
| `weather_silver_forecast_error` | Per-(station, date, model, lead_time) forecast errors | ~84,157 |
| `model_calibration` | Bias/RMSE by (station, variable, model, season, lead_hours) | 192 |
| `weather_silver_calibration_training_set` | Empty placeholder — not yet spec'd | 0 |

### Gold Layer — Features & Signals

| Table | Purpose | Rows |
|---|---|---|
| `weather_gold_city_day_features` | XGBoost feature vector per (station, date) | 7,107 |
| `weather_gold_contract_ledger` | CP4 trading signals per contract ticker | Live |

Row breakdown for `weather_gold_city_day_features`:
- `live`: 51 rows (2026-06-12 → 2026-06-28, 17 per station, no gaps)
- `historical_backfill`: 7,056 rows (2020-01-01 → 2026-06-09, bootstrap training)

Live rows weighted 3.0× in training. Test set = most recent 20% of live rows only. `ON CONFLICT DO NOTHING` ensures historical rows never overwrite live data.

---

## Active Timers (LA Hub)

| Timer | Schedule | Purpose |
|---|---|---|
| `bhn-weather-collector` | ~33 min | NWS + Open-Meteo + Kalshi snapshot collector |
| `weather-calibration` | 06:00 UTC daily | Build `model_calibration` from forecast error table |
| `weather-gold-builder` | 06:30 UTC daily | Populate `weather_gold_city_day_features` |
| `bhn-vc-backfill` | 00:01 UTC daily | Visual Crossing historical actuals backfill |
| `bhn-weather-train` | Sunday 02:00 UTC | Retrain XGBoost model |
| `bhn-weather-orchestrator` | ~5 min | CP1→CP2→CP3→CP4→ledger cycle |
| `bhn-weather-settlement-recon` | 15:00 UTC daily | Backfill settlement actuals into gold + ledger |

---

## Roadmap

**Near term (~Oct 2026, ~150 live rows):** Flip `DRY_RUN=false` when NO-side calibration passes. Multi-season data arrives. Add CatBoost as second model; blend by recent 30-day RMSE.

**Medium term (~Jan 2027, ~300 rows):** Per-station models. Add volume data to Kalshi snapshot table; activate liquid edge threshold (5¢).

**Maturity (~Summer 2027, ~1,100 rows):** First complete annual cycle. Raise Kelly fractions / lower edge thresholds as model earns trust.

**Scope lock:** KXHIGHDEN, KXHIGHLAX, KXHIGHMIA (tmax only). No tmin markets on Kalshi. No additional cities without explicit scope change.

---

Full schema reference: [`infrastructure/docs/WeatherBHN/WEATHERBHN-SCHEMA-REFERENCE-2026-06-30.md`](../infrastructure/docs/WeatherBHN/WEATHERBHN-SCHEMA-REFERENCE-2026-06-30.md)
