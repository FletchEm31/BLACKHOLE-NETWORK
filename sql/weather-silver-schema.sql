-- BHN Strategy 9 — Silver Layer Schema
-- Cleaned, standardized, and deduplicated data.
-- Populated inline by the collector after each bronze write.
-- is_latest_run / is_latest_snapshot flags managed by the collector
-- (reset previous, set new) — never manually set.
--
-- Apply via: sql/migrations/2026-06-11-weather-bsg-tables.sql

-- ─────────────────────────────────────────────────────────────────────────────
-- 1) weather_silver_forecast_conformed
--    One row per (station, source, run_time, target_date).
--    is_latest_run = TRUE on the most recent run per (station, source, target_date).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_silver_forecast_conformed (
    id                  BIGSERIAL       PRIMARY KEY,
    city                TEXT            NOT NULL,
    station_code        TEXT            NOT NULL,
    source_name         TEXT            NOT NULL,   -- 'nws', 'open_meteo_gfs', 'open_meteo_ecmwf'
    forecast_run_time   TIMESTAMPTZ     NOT NULL,
    target_date         DATE            NOT NULL,
    lead_hours          INTEGER,
    tmax_f              NUMERIC,
    tmin_f              NUMERIC,
    dewpoint_f          NUMERIC,
    rh_pct              NUMERIC,
    wind_speed_mph      NUMERIC,
    wind_gust_mph       NUMERIC,
    cloud_cover_pct     NUMERIC,
    pop_pct             NUMERIC,
    qpf_in              NUMERIC,
    snowfall_in         NUMERIC,
    is_latest_run       BOOLEAN         NOT NULL DEFAULT FALSE,
    is_valid            BOOLEAN         NOT NULL DEFAULT TRUE,
    quality_flag        TEXT,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT silver_forecast_unique
        UNIQUE (station_code, source_name, forecast_run_time, target_date)
);

CREATE INDEX IF NOT EXISTS sfcfc_latest_idx
    ON weather_silver_forecast_conformed (station_code, target_date, is_latest_run)
    WHERE is_latest_run = TRUE;

CREATE INDEX IF NOT EXISTS sfcfc_source_date_idx
    ON weather_silver_forecast_conformed (station_code, source_name, target_date, forecast_run_time DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- 2) weather_silver_market_conformed
--    One row per (market_ticker, snapshot_time).
--    is_latest_snapshot = TRUE on the most recent snapshot per market_ticker.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_silver_market_conformed (
    id                      BIGSERIAL   PRIMARY KEY,
    market_ticker           TEXT        NOT NULL,
    series_ticker           TEXT,
    city                    TEXT        NOT NULL,
    station_code            TEXT        NOT NULL,
    contract_side           TEXT        NOT NULL,   -- 'high' or 'low'
    bucket_floor            NUMERIC,
    bucket_cap              NUMERIC,
    bucket_type             TEXT,
    bucket_label            TEXT,
    target_date             DATE        NOT NULL,
    snapshot_time           TIMESTAMPTZ NOT NULL,
    yes_mid                 NUMERIC,
    yes_bid                 NUMERIC,
    yes_ask                 NUMERIC,
    implied_prob            NUMERIC,    -- same as yes_mid, explicit alias
    volume                  NUMERIC,
    open_interest           NUMERIC,
    market_status           TEXT,
    market_liquidity_flag   TEXT,       -- 'liquid' >1000, 'thin' >100, 'illiquid' else
    is_latest_snapshot      BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT silver_market_unique
        UNIQUE (market_ticker, snapshot_time)
);

CREATE INDEX IF NOT EXISTS smktc_latest_idx
    ON weather_silver_market_conformed (market_ticker, is_latest_snapshot)
    WHERE is_latest_snapshot = TRUE;

CREATE INDEX IF NOT EXISTS smktc_station_date_idx
    ON weather_silver_market_conformed (station_code, target_date, is_latest_snapshot);

CREATE INDEX IF NOT EXISTS smktc_series_date_idx
    ON weather_silver_market_conformed (series_ticker, target_date)
    WHERE series_ticker IS NOT NULL;


-- ─────────────────────────────────────────────────────────────────────────────
-- 3) weather_silver_actuals_conformed
--    Settlement truth from NWS CLI.
--    UNIQUE per (station, date, source) — multiple sources possible in future.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_silver_actuals_conformed (
    id                      BIGSERIAL   PRIMARY KEY,
    city                    TEXT        NOT NULL,
    station_code            TEXT        NOT NULL,
    target_date             DATE        NOT NULL,
    final_tmax_f            NUMERIC,
    final_tmin_f            NUMERIC,
    actual_source           TEXT        NOT NULL DEFAULT 'nws_cli',
    report_issued_at        TIMESTAMPTZ,
    settlement_label_high   TEXT,       -- which Kalshi bucket the actual fell in, e.g. '88-89'
    settlement_label_low    TEXT,
    is_final                BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT silver_actuals_unique
        UNIQUE (station_code, target_date, actual_source)
);

CREATE INDEX IF NOT EXISTS sact_station_date_idx
    ON weather_silver_actuals_conformed (station_code, target_date DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- 4) weather_silver_forecast_error
--    Forecast vs actual pairs for calibration training.
--    Error convention: forecast_error_f = actual - forecast
--      positive = NWS ran cold (forecast too low)
--      negative = NWS ran hot (forecast too high)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_silver_forecast_error (
    id                  BIGSERIAL       PRIMARY KEY,
    city                TEXT            NOT NULL,
    station_code        TEXT            NOT NULL,
    target_date         DATE            NOT NULL,
    feature_name        TEXT            NOT NULL,   -- 'tmax_f' or 'tmin_f'
    source_name         TEXT            NOT NULL,
    forecast_run_time   TIMESTAMPTZ,
    lead_hours          INTEGER,
    forecast_value      NUMERIC,
    actual_value        NUMERIC,
    forecast_error_f    NUMERIC,        -- actual - forecast
    error_sign          TEXT,           -- 'cold', 'hot', 'exact'
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT silver_fcerr_unique
        UNIQUE (station_code, target_date, feature_name, source_name, forecast_run_time)
);

CREATE INDEX IF NOT EXISTS sfce_station_feature_idx
    ON weather_silver_forecast_error (station_code, feature_name, target_date DESC);

CREATE INDEX IF NOT EXISTS sfce_station_source_idx
    ON weather_silver_forecast_error (station_code, source_name, target_date DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- 5) weather_silver_calibration_training_set
--    Per-bucket training rows. Populated after actuals arrive.
--    actual_outcome = 1 if the actual temp fell in the bucket, else 0.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_silver_calibration_training_set (
    id                          BIGSERIAL   PRIMARY KEY,
    city                        TEXT        NOT NULL,
    station_code                TEXT        NOT NULL,
    contract_side               TEXT        NOT NULL,   -- 'high' or 'low'
    market_ticker               TEXT,
    target_date                 DATE        NOT NULL,
    forecast_run_time           TIMESTAMPTZ,
    lead_hours                  INTEGER,
    raw_prob                    NUMERIC,    -- model raw probability for this bucket
    market_price                NUMERIC,    -- kalshi yes_mid at time of signal
    actual_outcome              INTEGER,    -- 1 = bucket hit, 0 = miss
    actual_tmax_f               NUMERIC,
    forecast_error_f            NUMERIC,
    season                      TEXT,       -- 'spring','summer','fall','winter'
    calibration_window_flag     BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS scal_station_side_idx
    ON weather_silver_calibration_training_set (station_code, contract_side, target_date DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- 6) weather_silver_model_base
--    One clean row per (station, date, contract_side, forecast_run_time).
--    Joins NWS + GFS + Kalshi + actuals in one place.
--    Updated on new forecasts; actual fields populated after settlement.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_silver_model_base (
    id                      BIGSERIAL   PRIMARY KEY,
    city                    TEXT        NOT NULL,
    station_code            TEXT        NOT NULL,
    target_date             DATE        NOT NULL,
    contract_side           TEXT        NOT NULL,   -- 'high' or 'low'
    forecast_run_time       TIMESTAMPTZ NOT NULL,
    lead_hours              INTEGER,

    -- NWS forecast
    nws_tmax_f              NUMERIC,
    nws_tmin_f              NUMERIC,
    nws_dewpoint_f          NUMERIC,
    nws_rh_pct              NUMERIC,
    nws_wind_speed_mph      NUMERIC,
    nws_cloud_cover_pct     NUMERIC,
    nws_pop_pct             NUMERIC,

    -- Open-Meteo GFS forecast
    gfs_tmax_f              NUMERIC,
    gfs_tmin_f              NUMERIC,
    gfs_dewpoint_f          NUMERIC,
    gfs_rh_pct              NUMERIC,
    gfs_wind_speed          NUMERIC,
    gfs_cloud_cover         NUMERIC,

    -- NWS vs GFS disagreement
    nws_gfs_tmax_delta      NUMERIC,    -- nws_tmax_f - gfs_tmax_f
    nws_gfs_tmin_delta      NUMERIC,

    -- Kalshi market at time of signal (latest snapshot when row written)
    market_ticker           TEXT,
    kalshi_yes_mid          NUMERIC,
    kalshi_implied_prob     NUMERIC,
    kalshi_volume           NUMERIC,
    kalshi_snapshot_time    TIMESTAMPTZ,

    -- Actual settlement (NULL until CLI report arrives)
    actual_tmax_f           NUMERIC,
    actual_tmin_f           NUMERIC,
    is_settled              BOOLEAN     NOT NULL DEFAULT FALSE,

    is_valid                BOOLEAN     NOT NULL DEFAULT TRUE,
    quality_flag            TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT silver_model_base_unique
        UNIQUE (station_code, target_date, contract_side, forecast_run_time)
);

CREATE INDEX IF NOT EXISTS smbase_station_date_idx
    ON weather_silver_model_base (station_code, target_date DESC, contract_side);

CREATE INDEX IF NOT EXISTS smbase_unsettled_idx
    ON weather_silver_model_base (target_date, station_code)
    WHERE is_settled = FALSE;

-- Permissions
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'horizon_agent_reader') THEN
        GRANT SELECT ON weather_silver_forecast_conformed TO horizon_agent_reader;
        GRANT SELECT ON weather_silver_market_conformed TO horizon_agent_reader;
        GRANT SELECT ON weather_silver_actuals_conformed TO horizon_agent_reader;
        GRANT SELECT ON weather_silver_forecast_error TO horizon_agent_reader;
        GRANT SELECT ON weather_silver_calibration_training_set TO horizon_agent_reader;
        GRANT SELECT ON weather_silver_model_base TO horizon_agent_reader;
    END IF;
END $$;
