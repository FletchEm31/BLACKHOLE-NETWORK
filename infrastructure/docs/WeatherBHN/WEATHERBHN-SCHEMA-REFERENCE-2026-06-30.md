# WeatherBHN — Schema Reference
**Date:** 2026-06-30
**Branch:** weatherbhn-dev
**Database:** `eventhorizon` (BHN-LOSANGELES-US1, PostgreSQL)
**Scope:** All `weather_*` tables + `model_calibration` (silver layer, missing prefix — see Naming section)
**Status:** READ ONLY audit — no schema changes made.

---

## Table of Contents
1. [Architecture Overview](#1-architecture-overview)
2. [Bronze Layer](#2-bronze-layer)
3. [Silver Layer](#3-silver-layer)
4. [Gold Layer](#4-gold-layer)
5. [Write Frequency & Collector Health](#5-write-frequency--collector-health)
6. [Naming Standardization Recommendations](#6-naming-standardization-recommendations)
7. [Known Gaps & Anomalies](#7-known-gaps--anomalies)

---

## 1. Architecture Overview

```
Open-Meteo GFS  ─┐
NWS API          ─┤──► BRONZE (raw snapshots, append / upsert)
Kalshi API       ─┤
NOAA / NWS CLI   ─┤         │
ERA5 (manual)   ─┘          ▼
Visual Crossing ─┘    SILVER (conformed actuals, forecast errors, calibration)
                                    │
                                    ▼
                             GOLD (XGBoost features, contract ledger)
                                    │
                                    ▼
                          CP1→CP2→CP3→CP4 orchestrator → Kalshi (DRY_RUN=true)
```

**Tradeable scope (gold layer):** KDEN, KLAX, KMIA — Kalshi KXHIGHDEN / KXHIGHLAX / KXHIGHMIA tmax markets only.

---

## 2. Bronze Layer

### 2.1 `weather_bronze_kalshi_market_snapshots`
**Purpose:** Live Kalshi market price snapshots — one row per (station, date, bucket, poll cycle). Primary market-data source for CP1, CP2, CP4, and gold feature enrichment.

**Source file:** Schema inferred from collector (live on LA) + usage in CP scripts.

| Column | Type | Notes |
|---|---|---|
| `station_code` | TEXT | KDEN / KLAX / KMIA |
| `target_date` | DATE | Settlement date of the contract |
| `retrieved_at` | TIMESTAMPTZ | Timestamp when snapshot was polled |
| `bucket_label` | TEXT | Kalshi label e.g. `"69-70"`, `"T66"`, `"T73"` |
| `bucket_type` | TEXT | `'between'` (range) or `'threshold'` (open-ended tail) |
| `bucket_floor` | NUMERIC | °F lower bound; NULL for threshold-low buckets |
| `bucket_cap` | NUMERIC | °F upper bound; NULL for threshold-high buckets |
| `yes_bid` | NUMERIC | Decimal 0.0–1.0 |
| `yes_ask` | NUMERIC | Decimal 0.0–1.0 |
| `no_bid` | NUMERIC | Decimal 0.0–1.0 |
| `no_ask` | NUMERIC | Decimal 0.0–1.0; **ALWAYS read from DB, never derived** |
| `yes_mid` | NUMERIC | Decimal midpoint |

**Unique constraint (inferred):** `(station_code, target_date, retrieved_at, bucket_label)`
**Indexes (inferred):** `(station_code, target_date, retrieved_at DESC)` for latest-snapshot lookups
**Row count (2026-06-30):** ~5.28M
**Grants:** `bhn_trader` SELECT/INSERT/UPDATE; `ehuser` SELECT/INSERT/UPDATE; `grafana_reader` SELECT
**Volume column:** NOT PRESENT — CP4 defaults to illiquid edge threshold (≥8¢) until added.
**Threshold bucket note:** T66 (≤66°F) and T73 (≥73°F) store `bucket_floor = bucket_cap = threshold_value`. CP4 opens the correct end to −∞/+∞ at query time.

---

### 2.2 `weather_bronze_nws_forecast_snapshots`
**Purpose:** NWS (National Weather Service) tmax/tmin forecast snapshots, polled every ~33 min. Primary forecast source for CP1 (fallback) and CP3 (fallback path when gold row absent).

**Source file:** Schema inferred from CP1 / CP3 usage + status doc.

| Column | Type | Notes |
|---|---|---|
| `station_code` | TEXT | KDEN / KLAX / KMIA (and potentially others) |
| `target_date` | DATE | Forecast valid date |
| `retrieved_at` | TIMESTAMPTZ | Poll timestamp |
| `tmax_f` | NUMERIC | Forecast daily high in °F; nullable |
| `tmin_f` | NUMERIC | Forecast daily low in °F; nullable |

**Unique constraint (inferred):** `(station_code, target_date, retrieved_at)` or equivalent
**Row count (2026-06-30):** ~76,558
**Freshness:** Live — collector running every ~33 min
**Grants (inferred):** `bhn_trader`, `ehuser` SELECT/INSERT; `grafana_reader` SELECT

---

### 2.3 `weather_bronze_openmeteo_forecast_snapshots`
**Purpose:** Open-Meteo GFS Seamless forecast snapshots, polled alongside NWS every ~33 min. Secondary forecast source for CP3 ensemble blend and silver forecast error computation.

**Source file:** Schema inferred from CP3 / gold_builder usage + status doc.

| Column | Type | Notes |
|---|---|---|
| `station_code` | TEXT | KDEN / KLAX / KMIA |
| `target_date` | DATE | Forecast valid date |
| `retrieved_at` | TIMESTAMPTZ | Poll timestamp |
| `tmax_f` | NUMERIC | Forecast daily high in °F; nullable |
| `tmin_f` | NUMERIC | Forecast daily low in °F; nullable |

**Row count (2026-06-30):** ~114,096
**Freshness:** Live — same collector as NWS, every ~33 min
**Grants (inferred):** `bhn_trader`, `ehuser` SELECT/INSERT; `grafana_reader` SELECT
**Source model identifier:** stored as `'open_meteo_gfs_seamless'` in `weather_silver_forecast_error.source_name`

---

### 2.4 `weather_bronze_noaa_daily_actuals`
**Purpose:** NOAA / NWS CLI daily observed actuals (tmax, tmin, precipitation). Used by `weather_historical_backfill.py` as the authoritative ground truth when pairing with Open-Meteo historical forecasts. Covers all 3 tradeable stations plus additional cities.

**Source file:** Schema inferred from `weather_historical_backfill.py`.

| Column | Type | Notes |
|---|---|---|
| `icao_code` | TEXT | ⚠️ Uses `icao_code` not `station_code` — see Naming section |
| `date` | DATE | ⚠️ Uses `date` not `target_date` — see Naming section |
| `tmax_f` | NUMERIC | Observed daily high in °F; nullable |
| `tmin_f` | NUMERIC | Observed daily low in °F; nullable |
| *(additional cols likely)* | | Precipitation, snow, etc. — not used in current pipeline |

**Row count (2026-06-30):** ~127,815
**Freshness:** 2026-06-22 (not live — periodic ingestion)
**Coverage:** KDEN, KLAX, KMIA + additional stations
**Naming anomaly:** `icao_code` / `date` do not match the `station_code` / `target_date` convention used by all other tables. Any cross-table join requires explicit aliasing.

---

### 2.5 `weather_bronze_visual_crossing_actuals`
**Purpose:** Visual Crossing (VC) historical actuals for up to 8 cities, used to build a longer silver actuals baseline. Backfill target: 3 years rolling.

**Source file:** Schema inferred from `bhn-vc-backfill.service` (`weather_vc_backfill.py` on LA).

| Column | Type | Notes |
|---|---|---|
| `station_code` (or `icao_code`?) | TEXT | City identifier — exact column name unverified; VC script not in repo |
| `date` (or `target_date`?) | DATE | Observation date — exact column name unverified |
| `tmax_f` | NUMERIC | VC observed daily high in °F |
| `tmin_f` | NUMERIC | VC observed daily low in °F |
| *(additional VC fields likely)* | | Humidity, precip, cloud cover — not used in current pipeline |

**Row count (2026-06-30):** ~2,000
**Freshness:** 2026-03-25 (KDEN + KMIA only)
**Coverage:** 8-city scope: KMIA, KDEN, KPHX, KLAX, KDFW, KNYC, KORD, KAUS
**Write frequency:** 00:01 UTC daily via `bhn-vc-backfill.timer` — 125 days × 8 cities per run
**Known gap:** KLAX has 0 rows — backfill writes to KLAX are not landing. Under investigation.
**Rate limit:** VC free-tier cap 1,000 records/day → 8 cities × 125 days = 1,000 records per run.

---

### 2.6 `weather_bronze_era5_kmia`
**Purpose:** ERA5 ECMWF reanalysis data for the KMIA bounding box (lat 25.5–26.0, lon −80.5 to −80.0). 2-hourly atmospheric variables for potential ERA5 feature engineering (sea breeze, cloud indicators). Currently loaded manually via `weather_era5_kmia_ingest.py`; not yet used in the live inference pipeline.

**Source file:** `sql/weather-bronze-schema.sql` (authoritative DDL in repo)

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | BIGSERIAL | PRIMARY KEY | |
| `valid_time` | TIMESTAMPTZ | NOT NULL | 2-hourly ERA5 timestamp |
| `latitude` | NUMERIC(7,4) | NOT NULL | Rounded to 4 dp to avoid float drift |
| `longitude` | NUMERIC(7,4) | NOT NULL | |
| `u10` | DOUBLE PRECISION | | 10m u-wind component (m/s) |
| `v10` | DOUBLE PRECISION | | 10m v-wind component (m/s) |
| `d2m` | DOUBLE PRECISION | | 2m dewpoint temperature (K) |
| `t2m` | DOUBLE PRECISION | | 2m temperature (K) |
| `msl` | DOUBLE PRECISION | | Mean sea level pressure (Pa) |
| `sp` | DOUBLE PRECISION | | Surface pressure (Pa) |
| `tp` | DOUBLE PRECISION | | Total precipitation (m, accumulated) |
| `tcc` | DOUBLE PRECISION | | Total cloud cover (0–1) |
| `cbh` | DOUBLE PRECISION | | Cloud base height (m) |
| `mwd` | DOUBLE PRECISION | | Mean wave direction (degrees); NULL over land |
| `mwp` | DOUBLE PRECISION | | Mean wave period (s); NULL over land |
| `sst` | DOUBLE PRECISION | | Sea surface temperature (K); NULL over land |
| `swh` | DOUBLE PRECISION | | Significant height combined wind waves + swell (m); NULL over land |
| `ingested_at` | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | Ingest timestamp |

**Unique constraint:** `weather_bronze_era5_kmia_uq (valid_time, latitude, longitude)`
**Index:** `idx_era5_kmia_valid_time ON (valid_time)`
**Grants:** `bhn_trader` SELECT/INSERT/UPDATE; `ehuser` SELECT/INSERT/UPDATE; `grafana_reader` SELECT; sequence usage granted to `bhn_trader`, `ehuser`
**Row count (2026-06-30):** ~58,158
**Freshness:** 2026-06-23 (Dec 2024 onward; loaded in manual batches)
**Coverage:** KMIA only — ERA5 for KDEN/KLAX not yet loaded
**Pipeline status:** Loaded but not yet wired into CP3 feature vector. Planned for medium-term (ERA5 sea_breeze_flag, cloud indicators).
**Ingest method:** Manual — `python3 weather_era5_kmia_ingest.py --file <grib_or_zip>` on LA; GRIB/ZIP from Copernicus CDS.

---

## 3. Silver Layer

### 3.1 `weather_silver_actuals_conformed`
**Purpose:** Conformed, source-tagged daily observed actuals for all tradeable stations. Canonical ground truth for settlement label assignment and gold feature backfill. Written by settlement reconciliation job (daily at 15:00 UTC via `bhn-weather-settlement-recon`).

**Source file:** Schema inferred from `weather_gold_builder.py` and `cp1_data_sanity.py`.

| Column | Type | Notes |
|---|---|---|
| `station_code` | TEXT | KDEN / KLAX / KMIA |
| `target_date` | DATE | Observation date |
| `actual_source` | TEXT | e.g. `'nws_cli'` |
| `final_tmax_f` | NUMERIC | Observed daily high in °F |
| `final_tmin_f` | NUMERIC | Observed daily low in °F; nullable |
| `settlement_label_high` | TEXT | Kalshi bucket the actual fell in e.g. `"69-70"` |
| `is_final` | BOOLEAN | False = preliminary/amended reading |

**Unique constraint (inferred):** `(station_code, target_date, actual_source)`
**Row count (2026-06-30):** ~3,113
**Freshness:** 2026-06-29
**Write source:** Settlement reconciliation (`bhn-weather-settlement-recon`, 15:00 UTC daily)
**Read by:** `weather_gold_builder.py` (backfills `actual_tmax_f` / `settlement_label_high` in gold); `cp1_data_sanity.py` (is_final check)

---

### 3.2 `weather_silver_forecast_error`
**Purpose:** Per-(station, date, feature, source, lead_time) forecast error records. Powers `model_calibration` bias/RMSE computation and gold feature enrichment. Written by the daily weather-calibration job.

**Source file:** Schema inferred from `weather_calibration_build.py` and `weather_gold_builder.py`.

| Column | Type | Notes |
|---|---|---|
| `station_code` | TEXT | KDEN / KLAX / KMIA |
| `target_date` | DATE | The date being forecast |
| `feature_name` | TEXT | `'tmax_f'` or `'tmin_f'` |
| `source_name` | TEXT | `'nws'` or `'open_meteo_gfs_seamless'` |
| `lead_hours` | INTEGER | Hours before the valid date the forecast was issued (24 used in pipeline) |
| `forecast_value` | NUMERIC | Raw forecast value (°F) |
| `forecast_error_f` | NUMERIC | `forecast_value − actual` (°F); positive = warm bias |

**Unique constraint (inferred):** `(station_code, target_date, feature_name, source_name, lead_hours)` or similar
**Row count (2026-06-30):** ~84,157
**Freshness:** 2026-06-28 (Jun 29 pending next daily run)
**Write source:** Weather calibration job (`weather-calibration.timer`, 06:00 UTC daily)
**Aggregated by:** `weather_calibration_build.py` → `model_calibration`

---

### 3.3 `model_calibration`
**Purpose:** Aggregated bias, RMSE, MAE, and sample size per (station, variable, source_model, season, lead_time). Used by CP3 (emergency fallback calibration), CP4 (sigma / model_rmse), and gold_builder (bias-corrected forecast columns). Refreshed daily by `weather-calibration.timer`.

**Source file:** Schema derived from `weather_calibration_build.py` INSERT SQL and `weather_gold_builder.py` SELECT.

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `station_code` | TEXT | NOT NULL | KDEN / KLAX / KMIA |
| `variable` | TEXT | NOT NULL | `'tmax_f'` or `'tmin_f'` |
| `source_model` | TEXT | NOT NULL | `'nws'` or `'open_meteo_gfs_seamless'` |
| `lead_time_hours` | INTEGER | NOT NULL | 24 in current pipeline |
| `season` | TEXT | NOT NULL | `'winter'` / `'spring'` / `'summer'` / `'fall'` |
| `mean_bias` | NUMERIC | | avg(forecast_error_f) — positive = warm bias |
| `rmse` | NUMERIC | | sqrt(avg(error²)) |
| `mae` | NUMERIC | | avg(abs(error)) |
| `sample_size` | INTEGER | | Rows contributing to this group (min 10 enforced) |
| `calibrated_at` | TIMESTAMPTZ | DEFAULT NOW() | Last upsert timestamp |

**Unique constraint:** `(station_code, variable, season, lead_time_hours, source_model)`
**Row count (2026-06-30):** 192
**Naming anomaly:** Missing `weather_` prefix — does not follow table naming convention. See Naming section.
**Write source:** `weather_calibration_build.py` via `weather-calibration.timer` (06:00 UTC daily), upsert on conflict.
**Current coverage:** 7 lead times × 8 cities × (tmax+tmin) × (nws+om) × seasons = 192 rows (summer season only for the 3 tradeable cities in live pipeline).

---

### 3.4 `weather_silver_calibration_training_set`
**Purpose:** Placeholder — intended to hold a pre-joined training set for calibration model iteration. Not yet populated. Exact schema not yet specified.

**Source file:** No SQL DDL file found; schema unknown.

| Column | Type | Notes |
|---|---|---|
| *(unknown)* | | Table exists but has 0 rows |

**Row count (2026-06-30):** 0
**Status:** Empty placeholder. Blocked pending spec. Does not affect any running component.

---

## 4. Gold Layer

### 4.1 `weather_gold_city_day_features`
**Purpose:** Complete XGBoost feature vector per (station_code, target_date). One row per day per tradeable station — populated by `weather_gold_builder.py` at 06:30 UTC daily, enriched with actuals post-settlement. Also contains `historical_backfill` rows (2020–2026-06-09) for bootstrap training.

**Source file:** `sql/weather-gold-schema.sql` (authoritative DDL in repo)

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | BIGSERIAL | PRIMARY KEY | |
| `station_code` | TEXT | NOT NULL | KDEN / KLAX / KMIA |
| `target_date` | DATE | NOT NULL | |
| `season` | TEXT | NOT NULL, CHECK (winter/spring/summer/fall) | Calendar season of target_date |
| `actual_tmax_f` | NUMERIC | | NULL until settlement; backfilled from `weather_silver_actuals_conformed` |
| `actual_tmin_f` | NUMERIC | | NULL until settlement |
| `settlement_label_high` | TEXT | | Kalshi bucket actual fell in; NULL until settlement |
| `actuals_is_final` | BOOLEAN | | Mirrors `is_final` from silver actuals |
| `nws_tmax_f` | NUMERIC | | NWS 24h-ahead tmax forecast (°F) |
| `nws_tmin_f` | NUMERIC | | NWS 24h-ahead tmin forecast (°F) |
| `om_tmax_f` | NUMERIC | | Open-Meteo GFS Seamless 24h-ahead tmax (°F) |
| `om_tmin_f` | NUMERIC | | Open-Meteo GFS Seamless 24h-ahead tmin (°F) |
| `nws_tmax_mean_bias` | NUMERIC | | From `model_calibration` (24h, matching season) |
| `nws_tmax_rmse` | NUMERIC | | From `model_calibration` |
| `nws_tmin_mean_bias` | NUMERIC | | |
| `nws_tmin_rmse` | NUMERIC | | |
| `om_tmax_mean_bias` | NUMERIC | | |
| `om_tmax_rmse` | NUMERIC | | |
| `om_tmin_mean_bias` | NUMERIC | | |
| `om_tmin_rmse` | NUMERIC | | |
| `nws_tmax_calibrated_f` | NUMERIC | | `nws_tmax_f − nws_tmax_mean_bias` |
| `nws_tmin_calibrated_f` | NUMERIC | | |
| `om_tmax_calibrated_f` | NUMERIC | | `om_tmax_f − om_tmax_mean_bias` |
| `om_tmin_calibrated_f` | NUMERIC | | |
| `kalshi_snapshot_retrieved_at` | TIMESTAMPTZ | | Latest snapshot at or before (target_date − 1 day 20:00 UTC) |
| `kalshi_closest_bucket_label` | TEXT | | Bucket bracketing `nws_tmax_calibrated_f` |
| `kalshi_closest_bucket_floor` | NUMERIC | | |
| `kalshi_closest_bucket_cap` | NUMERIC | | |
| `kalshi_closest_yes_bid` | NUMERIC | | Decimal 0.0–1.0 |
| `kalshi_closest_yes_ask` | NUMERIC | | |
| `kalshi_closest_no_bid` | NUMERIC | | |
| `kalshi_closest_no_ask` | NUMERIC | | Always from DB, never derived |
| `kalshi_closest_yes_mid` | NUMERIC | | |
| `kalshi_implied_prob_yes` | NUMERIC | | `yes_bid` (conservative estimate) |
| `kalshi_bid_ask_spread` | NUMERIC | | `yes_ask − yes_bid` |
| `data_source` | TEXT | NOT NULL DEFAULT 'live', CHECK (live/historical_backfill) | Provenance flag; ON CONFLICT DO NOTHING protects live rows |
| `created_at` | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | |
| `updated_at` | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | |

**Unique constraint:** `weather_gold_uq (station_code, target_date)`
**Indexes:**
- `idx_gold_station_date (station_code, target_date DESC)`
- `gold_data_source_idx (data_source, station_code, target_date)`

**Grants:** `grafana_reader` SELECT; `bhn_trader` SELECT/INSERT/UPDATE; `ehuser` SELECT/INSERT/UPDATE; sequence usage granted to `bhn_trader`, `ehuser`

**Row counts (2026-06-30):**
| `data_source` | Rows | Date range |
|---|---|---|
| `live` | 51 | 2026-06-12 → 2026-06-28 (17/station, no gaps) |
| `historical_backfill` | 7,056 | 2020-01-01 → 2026-06-09 (2,352/station) |

**Write sources:**
- `weather_gold_builder.py` via `weather-gold-builder.timer` (06:30 UTC daily) — live rows, ON CONFLICT DO NOTHING
- `weather_historical_backfill.py` (one-time bootstrap) — historical rows, ON CONFLICT DO NOTHING
- Settlement reconciliation — backfills `actual_tmax_f`, `settlement_label_high`, `actuals_is_final`

**XGBoost feature vector columns** (exact order, used in `cp3_train_model.py` + `cp3_inference.py`):
`nws_tmax_f`, `om_tmax_f`, `nws_tmax_mean_bias`, `om_tmax_mean_bias`, `nws_tmax_rmse`, `om_tmax_rmse`, `nws_tmax_calibrated_f`, `forecast_spread_f` *(derived: nws−om)*, `station_enc` *(derived)*, `season_enc` *(derived)*

---

### 4.2 `weather_gold_contract_ledger`
**Purpose:** CP4 Kelly-sizer trading signals — one row per Kalshi contract ticker (unique). Written by `core_trading_orchestrator.py` every ~5 min via `bhn-weather-orchestrator.timer`. Settlement actuals written by reconciliation job. Currently in DRY_RUN mode (signals logged, no Kalshi order API calls).

**Source file:** Schema derived from the INSERT/UPDATE SQL in `cp4_kelly_sizer.py:323–378`.

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `city` | TEXT | | `'Denver'` / `'Los Angeles'` / `'Miami'` |
| `station_code` | TEXT | | KDEN / KLAX / KMIA |
| `target_date` | DATE | | |
| `contract_side` | TEXT | | `'high'` (tmax; NO-side strategy) |
| `contract_ticker` | TEXT | UNIQUE (PK) | e.g. `KXHIGHLAX-26JUN29-69-70` |
| `bucket_floor` | NUMERIC | | °F; NULL for open-ended tail |
| `bucket_cap` | NUMERIC | | °F; NULL for open-ended tail |
| `bucket_label` | TEXT | | Kalshi label e.g. `"T66"`, `"69-70"` |
| `nws_forecast_f` | NUMERIC | | Raw NWS tmax (°F) at time of signal |
| `gfs_forecast_f` | NUMERIC | | Raw Open-Meteo GFS tmax (°F) at time of signal |
| `calibrated_prob` | NUMERIC | | Model P(NO) in decimal |
| `raw_model_prob` | NUMERIC | | Same as calibrated_prob (future: separate raw/calibrated) |
| `model_delta_f` | NUMERIC | | `predicted_tmax_f − nws_forecast_f` (XGBoost vs NWS divergence) |
| `model_confidence` | TEXT | | `'HIGH'` (<1.5°F delta) / `'MEDIUM'` (<3.0°F) / `'LOW'` (≥3.0°F) |
| `model_delta_flag` | TEXT | | `'DIVERGE'` (≥1.5°F) or `'CONVERGE'` |
| `ensemble_spread` | NUMERIC | | `abs(nws_tmax_f − om_tmax_f)`; nullable |
| `market_implied_prob` | NUMERIC | | `no_ask` (market's implied P(NO)) |
| `market_yes_mid` | NUMERIC | | midpoint of (yes_bid, 1 − no_ask) |
| `edge` | NUMERIC | | `model_prob_no − no_ask` in decimal |
| `edge_pct` | NUMERIC | | `edge / no_ask` |
| `edge_rank` | INTEGER | | 1 = best qualifying bucket by edge; NULL if SKIP |
| `recommended_action` | TEXT | | `'BET_NO'` or `'SKIP'` |
| `signal_strength` | TEXT | | `'STRONG'` (≥15¢) / `'MODERATE'` (≥10¢) / `'WEAK'`; NULL if SKIP |
| `stake_fraction` | NUMERIC | | Half-Kelly fraction (capped at 10% bankroll) |
| `stake_usd` | NUMERIC | | `stake_fraction × bankroll_usd` |
| `skip_reason` | TEXT | | `'INVALID_PRICE'` / `'EDGE_TOO_LOW'`; NULL if BET_NO |
| `is_active` | BOOLEAN | | True = signal still live |
| `signal_generated_at` | TIMESTAMPTZ | | UTC timestamp of signal generation |
| `yes_bid` | NUMERIC | | Decimal 0.0–1.0 |
| `yes_ask` | NUMERIC | | |
| `no_bid` | NUMERIC | | |
| `no_ask` | NUMERIC | | Always from DB — never derived |
| `market_liquidity` | TEXT | | `'ILLIQUID'` (default until volume data available) |
| `ledger_updated_at` | TIMESTAMPTZ | ON CONFLICT UPDATE | Timestamp of last signal refresh |
| `actual_tmax_f` | NUMERIC | | NULL until settled; written by reconciliation job |
| `settled_at` | TIMESTAMPTZ | | NULL until settled |
| `contract_resolved_yes` | BOOLEAN | | Outcome; NULL until settled |
| `paper_pnl` | NUMERIC | | DRY_RUN P&L; NULL until settled |

**Unique constraint:** `(contract_ticker)` — ON CONFLICT DO UPDATE refreshes all signal cols, never touches settlement actuals cols.
**Write sources:**
- `core_trading_orchestrator.py` every ~5 min — signals
- `bhn-weather-settlement-recon` at 15:00 UTC daily — settlement actuals
**Current mode:** DRY_RUN=true (strat9.env) — no Kalshi orders placed.
**Strategy:** NO-side only ("Tail-No"). YES-side extension deferred until ≥60 live ledger entries validate NO-side calibration.
**Edge thresholds:** ≥8¢ illiquid (no volume data yet), ≥5¢ liquid (unused).
**Distribution:** Gaussian (within 2σ); Student-t df=5 (tail buckets >2σ from mean).

---

## 5. Write Frequency & Collector Health

| Table | Writer | Schedule | As-of (2026-06-30) | Health |
|---|---|---|---|---|
| `weather_bronze_kalshi_market_snapshots` | `bhn-weather-collector` | ~33 min | Live ✅ | Active |
| `weather_bronze_nws_forecast_snapshots` | `bhn-weather-collector` | ~33 min | Live ✅ | Active |
| `weather_bronze_openmeteo_forecast_snapshots` | `bhn-weather-collector` | ~33 min | Live ✅ | Active |
| `weather_bronze_noaa_daily_actuals` | Manual / periodic | Ad hoc | 2026-06-22 | Stale — no live ingest |
| `weather_bronze_visual_crossing_actuals` | `bhn-vc-backfill.timer` | 00:01 UTC daily | 2026-03-25 ⚠️ | Active but KLAX gap |
| `weather_bronze_era5_kmia` | Manual `era5_kmia_ingest.py` | Manual batches | 2026-06-23 | No live ingest; on-demand |
| `weather_silver_actuals_conformed` | `bhn-weather-settlement-recon` | 15:00 UTC daily | 2026-06-29 ✅ | Active |
| `weather_silver_forecast_error` | `weather-calibration.timer` | 06:00 UTC daily | 2026-06-28 ✅ | Active |
| `weather_silver_calibration_training_set` | None | N/A | Never written | Empty placeholder |
| `model_calibration` | `weather-calibration.timer` | 06:00 UTC daily | Current ✅ | Active — 192 rows |
| `weather_gold_city_day_features` (live) | `weather-gold-builder.timer` | 06:30 UTC daily | 2026-06-28 ✅ | Active |
| `weather_gold_city_day_features` (hist) | `weather_historical_backfill.py` | One-time | 2026-06-01 (done) | Complete |
| `weather_gold_contract_ledger` (signals) | `bhn-weather-orchestrator.timer` | ~5 min | Live ✅ | Active (DRY_RUN) |
| `weather_gold_contract_ledger` (actuals) | `bhn-weather-settlement-recon` | 15:00 UTC daily | — | Active |

**Flagged timers (from status doc):**
- `bhn-kalshi-portfolio.timer` — no next fire time; likely misconfigured or one-shot. Does not affect schema listed here.

---

## 6. Naming Standardization Recommendations

The following deviations from the `weather_<layer>_<source>_<content>` naming convention were observed. **No changes were made — documented for future work only.**

| Issue | Table / Column | Recommendation |
|---|---|---|
| Missing `weather_` prefix | `model_calibration` | Rename to `weather_silver_model_calibration` |
| Column `icao_code` instead of `station_code` | `weather_bronze_noaa_daily_actuals` | Rename column to `station_code` when schema allows |
| Column `date` instead of `target_date` | `weather_bronze_noaa_daily_actuals` | Rename column to `target_date`; `date` is also a reserved word in PostgreSQL |
| Column name unknown | `weather_bronze_visual_crossing_actuals` | Verify `weather_vc_backfill.py` on LA — likely uses different column names than pipeline convention |
| Empty spec | `weather_silver_calibration_training_set` | Either define schema or DROP if permanently superseded by `model_calibration` |

**Impact of `model_calibration` naming:** CP3, CP4, gold_builder, and the calibration script all reference the bare name `model_calibration`. Rename requires a search-and-replace across all of these plus the DB view/synonym. Low urgency; plan for a maintenance window.

**Impact of NOAA column names:** `weather_historical_backfill.py` already works around this by using `icao_code` and `date` explicitly in its query. Any future script joining NOAA data to other weather tables will need an explicit alias. The fix is a single-column ALTER; low risk, medium urgency.

---

## 7. Known Gaps & Anomalies

| # | Table | Description | Severity |
|---|---|---|---|
| 1 | `weather_bronze_visual_crossing_actuals` | KLAX has 0 rows — backfill writes not landing | Medium — VC actuals not in live pipeline yet; investigate `weather_vc_backfill.py` KLAX path |
| 2 | `weather_silver_calibration_training_set` | Empty placeholder; no spec, no writer | Low — no current pipeline dependency |
| 3 | `weather_bronze_noaa_daily_actuals` | Not on a live ingest schedule; last updated 2026-06-22 | Low — used only for historical backfill (already complete) |
| 4 | `weather_bronze_era5_kmia` | KMIA only; KDEN/KLAX ERA5 not loaded; not yet wired into inference | Low — feature engineering deferred to medium-term roadmap |
| 5 | `weather_bronze_kalshi_market_snapshots` | No volume column — CP4 forced to illiquid threshold (8¢) | Medium — blocks liquid threshold (5¢) strategy activation |
| 6 | `weather_gold_contract_ledger` | Settlement columns (`actual_tmax_f`, `settled_at`, etc.) schema not in any repo SQL file | Low — defined on LA; add DDL to repo |
| 7 | `weather_silver_forecast_error` | Freshness 2026-06-28; Jun 29 pending next daily run | Transient — resolves at next 06:00 UTC run |
| 8 | `model_calibration` | All 192 rows are summer 2026 season only; winter/spring/fall have no rows yet | Medium — CP3/CP4 fallback bias will be 0.0 for non-summer rows until fall data arrives (~Oct 2026) |
| 9 | All silver/gold tables | `weather_silver_calibration_training_set` schema not documented anywhere in repo | Low — document or remove |

---

*Document compiled 2026-06-30. Read-only audit — no INSERT, UPDATE, ALTER, or DROP executed.*
*Branch: weatherbhn-dev | Repo: C:\GITHUB REPOSITORY\BLACKHOLE-NETWORK*
