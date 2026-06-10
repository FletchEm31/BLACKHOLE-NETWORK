-- weather-schema.sql
-- BHN Strategy 9 (BHN-WEATHER-ALPHA) — Phase 1 data-collection schema.
--
-- Phase 1 scope: data collection only. Tables for forecasts, observations,
-- calibration accumulators, and reference series (ENSO, crops). Betting +
-- execution tables (prediction_contracts, weather_bets) ALSO created here
-- in their Phase-1 form (empty placeholders) so the future Phase 3/4 code
-- has a fixed target shape — but no inserts happen until Phase 3.
--
-- Coexists with strategy-5-weather-schema.sql:
--   - weather_forecasts EXISTS from Strat 5 (Polymarket weather sub-strategy).
--     We ALTER it to add Strat 9 ensemble columns. Strat 5's existing column
--     set is preserved unchanged.
--   - All other tables are NEW.
--
-- Apply on LA hub:
--   sudo -u postgres psql -d eventhorizon -f sql/weather-schema.sql
--
-- Pre-req: trading-schema.sql (provides bhn_trader role + grant pattern).
-- Pre-req: strategy-5-weather-schema.sql (optional — provides initial
--          weather_forecasts table; this file is idempotent regardless).

\set ON_ERROR_STOP on

BEGIN;


-- ────────────────────────────────────────────────────────────────────────
-- 1. weather_forecasts — model predictions (extended from Strat 5)
--    Existing Strat 5 columns preserved; new Strat 9 columns added if absent.
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_forecasts (
    id                  BIGSERIAL PRIMARY KEY,
    predicted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    target_date         DATE NOT NULL,
    region              TEXT NOT NULL,
    variable            TEXT NOT NULL,
    predicted_value     NUMERIC,
    predicted_probability NUMERIC,
    confidence          NUMERIC,
    source_model        TEXT NOT NULL,
    raw_payload         JSONB
);

-- Strat 9 additions — ensemble metadata, lead time, station codes. Each
-- ADD COLUMN IF NOT EXISTS is safe on a pre-existing Strat 5 table.
ALTER TABLE weather_forecasts ADD COLUMN IF NOT EXISTS station_code      TEXT;        -- ICAO/ASOS code: 'KNYC', 'KLAX', etc.
ALTER TABLE weather_forecasts ADD COLUMN IF NOT EXISTS lead_time_hours   INTEGER;     -- 0, 24, 48, ..., 384 for medium-range
ALTER TABLE weather_forecasts ADD COLUMN IF NOT EXISTS ensemble_member   INTEGER;     -- 0 = control / mean; 1..N for ensemble members
ALTER TABLE weather_forecasts ADD COLUMN IF NOT EXISTS ensemble_mean     NUMERIC;     -- mean of all members at this (target, lead)
ALTER TABLE weather_forecasts ADD COLUMN IF NOT EXISTS ensemble_std      NUMERIC;     -- stdev of members — spread = uncertainty
ALTER TABLE weather_forecasts ADD COLUMN IF NOT EXISTS bias_correction   NUMERIC;     -- applied additive correction (from model_calibration)
ALTER TABLE weather_forecasts ADD COLUMN IF NOT EXISTS corrected_value   NUMERIC;     -- predicted_value + bias_correction
ALTER TABLE weather_forecasts ADD COLUMN IF NOT EXISTS season            TEXT;        -- 'winter'|'spring'|'summer'|'fall' — for season-specific calibration

CREATE INDEX IF NOT EXISTS weather_forecasts_target_idx
    ON weather_forecasts (target_date, region, variable);
CREATE INDEX IF NOT EXISTS weather_forecasts_predicted_idx
    ON weather_forecasts (predicted_at DESC);
CREATE INDEX IF NOT EXISTS weather_forecasts_region_var_idx
    ON weather_forecasts (region, variable, predicted_at DESC);
-- Strat 9 access patterns: lookup by station + lead time, by model + target
CREATE INDEX IF NOT EXISTS weather_forecasts_station_lead_idx
    ON weather_forecasts (station_code, variable, lead_time_hours, target_date)
    WHERE station_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS weather_forecasts_model_target_idx
    ON weather_forecasts (source_model, target_date, variable);

COMMENT ON TABLE weather_forecasts IS
    'Unified forecast table — Strat 5 (Polymarket weather arb) + Strat 9 (BHN-WEATHER-ALPHA ensemble model). source_model values: openweathermap, noaa-gfs, ecmwf, gfs-ensemble, nws, bhn-ensemble. Strat 9 fills ensemble_member + lead_time_hours + station_code for the per-city ensemble pipeline.';


-- ────────────────────────────────────────────────────────────────────────
-- 2. weather_observations — historical ground truth from Iowa State ASOS + NWS
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_observations (
    id              BIGSERIAL PRIMARY KEY,
    observed_at     TIMESTAMPTZ NOT NULL,
    station_code    TEXT NOT NULL,
    variable        TEXT NOT NULL,                -- 'tmax_f'|'tmin_f'|'precip_in'|'snow_in'|'wind_mph'
    observed_value  NUMERIC NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('asos', 'nws', 'manual')),
    raw_payload     JSONB,
    UNIQUE (station_code, observed_at, variable, source)
);

CREATE INDEX IF NOT EXISTS weather_observations_station_var_idx
    ON weather_observations (station_code, variable, observed_at DESC);
CREATE INDEX IF NOT EXISTS weather_observations_date_idx
    ON weather_observations (station_code, observed_at);

COMMENT ON TABLE weather_observations IS
    'Ground truth — historical observations from Iowa State ASOS (primary) + NWS (cross-check). UNIQUE constraint prevents duplicate inserts across re-runs. Joined with weather_forecasts for bias-correction calibration in model_calibration.';


-- ────────────────────────────────────────────────────────────────────────
-- 3. model_calibration — bias correction lookup
--    One row per (city, variable, season, lead_time_hours, source_model)
--    holding the rolling bias + skill metrics. Phase 2 rebuilds this table
--    weekly from a JOIN of weather_forecasts × weather_observations.
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS model_calibration (
    id                  BIGSERIAL PRIMARY KEY,
    calibrated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    station_code        TEXT NOT NULL,
    variable            TEXT NOT NULL,
    season              TEXT NOT NULL CHECK (season IN ('winter','spring','summer','fall')),
    lead_time_hours     INTEGER NOT NULL,
    source_model        TEXT NOT NULL,
    sample_size         INTEGER NOT NULL,
    mean_bias           NUMERIC,                  -- observed - predicted (additive correction)
    rmse                NUMERIC,                  -- root mean squared error
    mae                 NUMERIC,                  -- mean absolute error
    crps                NUMERIC,                  -- continuous ranked probability score (ensemble-only)
    reliability_score   NUMERIC,                  -- 0-1, ensemble probabilistic skill (Brier-like)
    UNIQUE (station_code, variable, season, lead_time_hours, source_model)
);

CREATE INDEX IF NOT EXISTS model_calibration_lookup_idx
    ON model_calibration (station_code, variable, season, lead_time_hours, source_model);

COMMENT ON TABLE model_calibration IS
    'Bias-correction lookup table. Phase 2 calibration job rebuilds rows weekly from observed - predicted residuals over a rolling window. Strategy code at prediction time reads this for the matching (city, var, season, lead, model) tuple and adds mean_bias to raw forecast for the bias-corrected value.';


-- ────────────────────────────────────────────────────────────────────────
-- 4. prediction_contracts — Kalshi/Polymarket weather contracts catalog
--    Phase 1 stub (rows arrive in Phase 3 when betting integration lands).
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS prediction_contracts (
    id                  BIGSERIAL PRIMARY KEY,
    discovered_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    exchange            TEXT NOT NULL CHECK (exchange IN ('kalshi', 'polymarket')),
    contract_id         TEXT NOT NULL,
    title               TEXT NOT NULL,
    station_code        TEXT,                     -- mapped from title
    variable            TEXT,
    threshold_value     NUMERIC,
    threshold_op        TEXT CHECK (threshold_op IN ('>', '>=', '<', '<=', 'between', NULL)),
    resolution_date     DATE NOT NULL,
    is_active           BOOLEAN NOT NULL DEFAULT true,
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at         TIMESTAMPTZ,
    resolved_outcome    BOOLEAN,
    raw_payload         JSONB,
    UNIQUE (exchange, contract_id)
);

CREATE INDEX IF NOT EXISTS prediction_contracts_active_idx
    ON prediction_contracts (is_active, resolution_date)
    WHERE is_active = true;
CREATE INDEX IF NOT EXISTS prediction_contracts_station_var_idx
    ON prediction_contracts (station_code, variable, resolution_date)
    WHERE station_code IS NOT NULL AND variable IS NOT NULL;

COMMENT ON TABLE prediction_contracts IS
    'Catalog of discovered weather contracts on Kalshi + Polymarket. Phase 3 betting code parses contract titles for station_code/variable/threshold, links to weather_forecasts for edge calculation. Phase 1 leaves this empty — collector does not yet hit exchange APIs.';


-- ────────────────────────────────────────────────────────────────────────
-- 5. weather_bets — record of placed bets (Phase 3+ insert; Phase 1 stub)
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_bets (
    id                       BIGSERIAL PRIMARY KEY,
    placed_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    contract_id              BIGINT NOT NULL REFERENCES prediction_contracts(id),
    exchange                 TEXT NOT NULL CHECK (exchange IN ('kalshi', 'polymarket')),
    side                     TEXT NOT NULL CHECK (side IN ('yes', 'no')),
    stake_usd                NUMERIC NOT NULL,
    entry_price              NUMERIC NOT NULL,    -- 0-1 fractional implied probability paid
    model_probability        NUMERIC NOT NULL,    -- BHN model's predicted probability
    edge_pct                 NUMERIC NOT NULL,    -- model_prob - entry_price
    kelly_fraction           NUMERIC,             -- half-Kelly applied; stored for audit
    confidence_score         NUMERIC,             -- ensemble confidence at bet time
    status                   TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'won', 'lost', 'voided')),
    exit_at                  TIMESTAMPTZ,
    payout_usd               NUMERIC,
    pnl_usd                  NUMERIC,
    exchange_order_id        TEXT,
    raw_payload              JSONB
);

CREATE INDEX IF NOT EXISTS weather_bets_status_idx
    ON weather_bets (status, placed_at DESC);
CREATE INDEX IF NOT EXISTS weather_bets_contract_idx
    ON weather_bets (contract_id);


-- ────────────────────────────────────────────────────────────────────────
-- 6. weather_commodity_signals — Phase 5 signal log for UNG/CORN/WEAT/SOYB/USO
--    Schema landed in Phase 1 so the data shape is fixed when Phase 5 ships.
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_commodity_signals (
    id                  BIGSERIAL PRIMARY KEY,
    generated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    commodity_etf       TEXT NOT NULL CHECK (commodity_etf IN ('UNG', 'CORN', 'WEAT', 'SOYB', 'USO')),
    direction           TEXT NOT NULL CHECK (direction IN ('long', 'short', 'flat')),
    weather_driver      TEXT NOT NULL,           -- 'heating_degree_days_spike' | 'crop_stress' | 'enso_phase_change' etc.
    confidence          NUMERIC NOT NULL,
    raw_inputs          JSONB,                   -- snapshot of forecasts / observations that produced this signal
    acted_on            BOOLEAN NOT NULL DEFAULT false,
    paper_trade_id      BIGINT REFERENCES paper_trades(id)
);

CREATE INDEX IF NOT EXISTS weather_commodity_signals_etf_idx
    ON weather_commodity_signals (commodity_etf, generated_at DESC);


-- ────────────────────────────────────────────────────────────────────────
-- 7. degree_days — heating + cooling degree days per city per day
--    Derived from weather_observations.tmin_f + tmax_f. Direct input to UNG
--    (natural gas) signal. Computed by the collector at end of each cycle.
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS degree_days (
    id                  BIGSERIAL PRIMARY KEY,
    station_code        TEXT NOT NULL,
    target_date         DATE NOT NULL,
    tmin_f              NUMERIC NOT NULL,
    tmax_f              NUMERIC NOT NULL,
    mean_temp_f         NUMERIC NOT NULL,        -- (tmin + tmax) / 2
    hdd                 NUMERIC NOT NULL,        -- max(0, 65 - mean_temp)
    cdd                 NUMERIC NOT NULL,        -- max(0, mean_temp - 65)
    is_forecast         BOOLEAN NOT NULL,        -- true if from weather_forecasts, false if from observations
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (station_code, target_date, is_forecast)
);

CREATE INDEX IF NOT EXISTS degree_days_station_date_idx
    ON degree_days (station_code, target_date);
CREATE INDEX IF NOT EXISTS degree_days_actual_idx
    ON degree_days (target_date)
    WHERE is_forecast = false;


-- ────────────────────────────────────────────────────────────────────────
-- 8. enso_index — NOAA CPC weekly ENSO state
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS enso_index (
    id                  BIGSERIAL PRIMARY KEY,
    week_ending         DATE NOT NULL UNIQUE,
    oni_value           NUMERIC,                 -- Oceanic Niño Index, 3-month rolling SST anomaly °C
    phase               TEXT CHECK (phase IN ('el_nino_strong', 'el_nino', 'neutral', 'la_nina', 'la_nina_strong')),
    nino34_sst_anomaly  NUMERIC,                 -- raw weekly Niño 3.4 SST anomaly
    source              TEXT NOT NULL DEFAULT 'noaa_cpc',
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_payload         JSONB
);


-- ────────────────────────────────────────────────────────────────────────
-- 5b. weather_bets — GFS-window capture audit columns
--     Added 2026-05-13 to support the aggressive post-GFS-update polling
--     loop. Records which poll # captured each opportunity + which polling
--     phase + actual API round-trip latency.
-- ────────────────────────────────────────────────────────────────────────
ALTER TABLE weather_bets ADD COLUMN IF NOT EXISTS gfs_window_poll_number INTEGER;
ALTER TABLE weather_bets ADD COLUMN IF NOT EXISTS gfs_window_phase       TEXT;
ALTER TABLE weather_bets ADD COLUMN IF NOT EXISTS kalshi_api_latency_ms  INTEGER;

COMMENT ON COLUMN weather_bets.gfs_window_poll_number IS
    'Which poll # within the GFS update window captured this opportunity. '
    'Low numbers = caught lag early; high numbers = caught a slow-to-reprice contract.';
COMMENT ON COLUMN weather_bets.gfs_window_phase IS
    'burst (0-5min) | active (5-15min) | wind_down (15-30min) | idle | manual';
COMMENT ON COLUMN weather_bets.kalshi_api_latency_ms IS
    'Round-trip latency to Kalshi at the moment we observed the lagging price. '
    'Used to calibrate poll intervals: high latency = increase intervals.';


-- ────────────────────────────────────────────────────────────────────────
-- 10. gfs_window_stats — one row per GFS-update polling cycle
--     The most important addition for tuning the polling strategy. After
--     ~30 days of windows we can read this table to determine:
--       - which cities reprice slowest (target them harder)
--       - how often we hit rate limits (calibrate aggression)
--       - average lag (minutes after GFS update) at which we catch edge
--       - whether polling frequency is optimal vs over- or under-aggressive
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gfs_window_stats (
    id                       BIGSERIAL PRIMARY KEY,
    gfs_run_at               TIMESTAMPTZ NOT NULL,        -- which GFS cycle triggered this
    gfs_run_hour             INTEGER NOT NULL CHECK (gfs_run_hour IN (0, 6, 12, 18)),
    window_start             TIMESTAMPTZ NOT NULL,
    window_end               TIMESTAMPTZ NOT NULL,
    total_polls              INTEGER NOT NULL DEFAULT 0,
    rate_limit_hits          INTEGER NOT NULL DEFAULT 0,
    opportunities_found      INTEGER NOT NULL DEFAULT 0,
    bets_placed              INTEGER NOT NULL DEFAULT 0,
    total_edge_captured      NUMERIC,                     -- sum of (model_prob - entry_price) × stake, in dollars
    avg_lag_minutes          NUMERIC,                     -- avg minutes after GFS we captured edge
    fastest_capture_minutes  NUMERIC,
    slowest_capture_minutes  NUMERIC,
    notes                    TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS gfs_window_stats_run_idx
    ON gfs_window_stats (gfs_run_at DESC);
CREATE INDEX IF NOT EXISTS gfs_window_stats_hour_idx
    ON gfs_window_stats (gfs_run_hour, gfs_run_at DESC);

COMMENT ON TABLE gfs_window_stats IS
    'One row per GFS-update polling window. Phase 1: written by '
    'kalshi_client.poll_weather_prices_aggressive() at window completion. '
    'After ~30 days, query for slowest-reprice cities / optimal poll cadence / '
    'rate-limit calibration. Each GFS cycle (0/6/12/18 UTC) gets its own row.';


-- ────────────────────────────────────────────────────────────────────────
-- 9. crop_conditions — USDA NASS weekly crop progress + conditions
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS crop_conditions (
    id                  BIGSERIAL PRIMARY KEY,
    week_ending         DATE NOT NULL,
    state_code          TEXT NOT NULL,           -- 'IA', 'IL', 'NE', etc.; 'US' for national rollup
    commodity           TEXT NOT NULL CHECK (commodity IN ('CORN', 'SOYBEANS', 'WHEAT', 'COTTON', 'RICE')),
    condition_category  TEXT NOT NULL CHECK (condition_category IN ('VERY POOR', 'POOR', 'FAIR', 'GOOD', 'EXCELLENT')),
    pct_in_category     NUMERIC NOT NULL,        -- 0-100
    source              TEXT NOT NULL DEFAULT 'usda_nass',
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_payload         JSONB,
    UNIQUE (week_ending, state_code, commodity, condition_category)
);

CREATE INDEX IF NOT EXISTS crop_conditions_commodity_week_idx
    ON crop_conditions (commodity, week_ending DESC, state_code);


-- ────────────────────────────────────────────────────────────────────────
-- 11. weather_contract_prices — live Kalshi/Polymarket price snapshots
--     One row per (exchange, contract_id, captured_at) inserted by the
--     weather collector every 30 minutes. strategy_prediction_alpha reads
--     the most-recent snapshot per contract for edge calculation.
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_contract_prices (
    id                   BIGSERIAL PRIMARY KEY,
    captured_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    exchange             TEXT NOT NULL CHECK (exchange IN ('kalshi', 'polymarket')),
    contract_id          TEXT NOT NULL,            -- Kalshi ticker e.g. 'KXHIGHMIA-26JUN10-T92'
    contract_title       TEXT NOT NULL,
    implied_probability  NUMERIC NOT NULL CHECK (implied_probability >= 0 AND implied_probability <= 1),
    yes_price            NUMERIC,                  -- best YES ask (0-1 fractional)
    no_price             NUMERIC,                  -- best NO ask (0-1 fractional)
    volume_24h           NUMERIC,
    open_interest        NUMERIC,
    resolution_date      DATE,
    region               TEXT,                     -- ICAO station code mapped from ticker
    variable             TEXT,                     -- 'tmax_f' | 'tmin_f' etc.
    raw_payload          JSONB
);

CREATE INDEX IF NOT EXISTS weather_contract_prices_contract_time_idx
    ON weather_contract_prices (contract_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS weather_contract_prices_region_var_idx
    ON weather_contract_prices (region, variable, captured_at DESC)
    WHERE region IS NOT NULL AND variable IS NOT NULL;
CREATE INDEX IF NOT EXISTS weather_contract_prices_resolution_idx
    ON weather_contract_prices (resolution_date, captured_at DESC);

COMMENT ON TABLE weather_contract_prices IS
    'Live price snapshots from Kalshi (and future Polymarket) weather contracts. '
    'Inserted every 30 min by fetch_kalshi_markets(). strategy_prediction_alpha '
    'reads latest snapshot per contract to compute edge vs BHN model probability.';


-- ────────────────────────────────────────────────────────────────────────
-- Grants — match the pattern from trading-schema.sql
-- ────────────────────────────────────────────────────────────────────────

-- bhn_trader (NJ strategy code) — full RW on Strat 9 tables
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_trader') THEN
        GRANT SELECT, INSERT, UPDATE ON
            weather_forecasts, weather_observations, model_calibration,
            prediction_contracts, weather_bets, weather_commodity_signals,
            degree_days, enso_index, crop_conditions, gfs_window_stats,
            weather_contract_prices
            TO bhn_trader;
        GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO bhn_trader;
    END IF;
END $$;

-- log_shipper — INSERT-only on the collector-targeted tables
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'log_shipper') THEN
        GRANT INSERT ON
            weather_forecasts, weather_observations,
            degree_days, enso_index, crop_conditions,
            weather_contract_prices
            TO log_shipper;
        GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO log_shipper;
    END IF;
END $$;

-- agent_reader (HORIZON) — SELECT on everything weather-related
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_reader') THEN
        GRANT SELECT ON
            weather_forecasts, weather_observations, model_calibration,
            prediction_contracts, weather_bets, weather_commodity_signals,
            degree_days, enso_index, crop_conditions, gfs_window_stats,
            weather_contract_prices
            TO agent_reader;
    END IF;
END $$;

-- DEFAULT PRIVILEGES: any future tables created in public are accessible
-- to bhn_trader and PUBLIC automatically — prevents permission drift when
-- new tables are added without re-running the full grant block.
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO PUBLIC;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE ON TABLES TO PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO PUBLIC;

COMMIT;

-- ────────────────────────────────────────────────────────────────────────
-- Post-deploy verification queries
-- ────────────────────────────────────────────────────────────────────────
-- Verify table set:
--   SELECT table_name FROM information_schema.tables
--    WHERE table_schema='public' AND table_name IN (
--      'weather_forecasts', 'weather_observations', 'model_calibration',
--      'prediction_contracts', 'weather_bets', 'weather_commodity_signals',
--      'degree_days', 'enso_index', 'crop_conditions'
--    ) ORDER BY table_name;
--
-- Verify weather_forecasts Strat 9 columns landed:
--   SELECT column_name FROM information_schema.columns
--    WHERE table_name='weather_forecasts'
--      AND column_name IN ('station_code', 'lead_time_hours', 'ensemble_member',
--                          'ensemble_mean', 'ensemble_std', 'season');
--
-- Verify grants:
--   SELECT grantee, privilege_type, count(*)
--   FROM information_schema.role_table_grants
--   WHERE table_name IN ('weather_forecasts', 'weather_observations',
--                        'model_calibration', 'prediction_contracts',
--                        'weather_bets', 'weather_commodity_signals',
--                        'degree_days', 'enso_index', 'crop_conditions')
--   GROUP BY grantee, privilege_type ORDER BY grantee, privilege_type;
