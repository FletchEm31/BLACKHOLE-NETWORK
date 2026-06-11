-- BHN Strategy 9 — Bronze/Silver/Gold Table Creation (self-contained)
-- Creates all 14 BSG tables: 5 bronze + 6 silver + 3 gold + 1 catalog
-- Reference schema files: sql/weather-bronze-schema.sql, weather-silver-schema.sql, weather-gold-schema.sql
--
-- Deploy:
--   scp sql/migrations/2026-06-11-weather-bsg-tables.sql root@10.8.0.1:/tmp/
--   ssh root@10.8.0.1 "sudo -u postgres psql -d eventhorizon -f /tmp/2026-06-11-weather-bsg-tables.sql"
--
-- All tables use CREATE TABLE IF NOT EXISTS — safe to re-run.
-- Old tables are NOT modified or dropped here.

BEGIN;

-- ═════════════════════════════════════════════════════════════════════════════
-- BRONZE LAYER
-- ═════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS weather_bronze_nws_forecast_snapshots (
    id                  BIGSERIAL       PRIMARY KEY,
    city                TEXT            NOT NULL,
    station_code        TEXT            NOT NULL,
    nws_office          TEXT,
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
    weather_text        TEXT,
    hazards             TEXT,
    source_payload_json JSONB,
    retrieved_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT weather_bronze_nws_unique
        UNIQUE (station_code, forecast_run_time, target_date)
);

CREATE INDEX IF NOT EXISTS brnws_station_date_idx
    ON weather_bronze_nws_forecast_snapshots (station_code, target_date, forecast_run_time DESC);

CREATE INDEX IF NOT EXISTS brnws_retrieved_idx
    ON weather_bronze_nws_forecast_snapshots (retrieved_at DESC);


CREATE TABLE IF NOT EXISTS weather_bronze_openmeteo_forecast_snapshots (
    id                      BIGSERIAL   PRIMARY KEY,
    city                    TEXT        NOT NULL,
    station_code            TEXT        NOT NULL,
    lat                     NUMERIC,
    lon                     NUMERIC,
    model                   TEXT        NOT NULL,
    forecast_run_time       TIMESTAMPTZ NOT NULL,
    target_date             DATE        NOT NULL,
    hour                    INTEGER,
    temperature_2m          NUMERIC,
    dewpoint_2m             NUMERIC,
    relative_humidity_2m    NUMERIC,
    cloud_cover             NUMERIC,
    precipitation           NUMERIC,
    rain                    NUMERIC,
    snowfall                NUMERIC,
    wind_speed_10m          NUMERIC,
    wind_gusts_10m          NUMERIC,
    surface_pressure        NUMERIC,
    weather_code            INTEGER,
    source_payload_json     JSONB,
    retrieved_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT weather_bronze_openmeteo_unique
        UNIQUE (station_code, model, forecast_run_time, target_date, hour)
);

CREATE INDEX IF NOT EXISTS brom_station_date_idx
    ON weather_bronze_openmeteo_forecast_snapshots (station_code, target_date, model, forecast_run_time DESC);

CREATE INDEX IF NOT EXISTS brom_model_run_idx
    ON weather_bronze_openmeteo_forecast_snapshots (model, forecast_run_time DESC, station_code);


CREATE TABLE IF NOT EXISTS weather_bronze_kalshi_market_snapshots (
    id                  BIGSERIAL       PRIMARY KEY,
    market_ticker       TEXT            NOT NULL,
    event_ticker        TEXT,
    series_ticker       TEXT,
    city                TEXT,
    station_code        TEXT,
    contract_side       TEXT,
    bucket_type         TEXT,
    bucket_floor        NUMERIC,
    bucket_cap          NUMERIC,
    bucket_label        TEXT,
    target_date         DATE,
    yes_bid             NUMERIC,
    yes_ask             NUMERIC,
    no_bid              NUMERIC,
    no_ask              NUMERIC,
    yes_mid             NUMERIC,
    last_price          NUMERIC,
    volume              NUMERIC,
    open_interest       NUMERIC,
    market_status       TEXT,
    source_payload_json JSONB,
    retrieved_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS brkm_ticker_time_idx
    ON weather_bronze_kalshi_market_snapshots (market_ticker, retrieved_at DESC);

CREATE INDEX IF NOT EXISTS brkm_station_date_idx
    ON weather_bronze_kalshi_market_snapshots (station_code, target_date, retrieved_at DESC)
    WHERE station_code IS NOT NULL;

CREATE INDEX IF NOT EXISTS brkm_series_date_idx
    ON weather_bronze_kalshi_market_snapshots (series_ticker, target_date)
    WHERE series_ticker IS NOT NULL;


CREATE TABLE IF NOT EXISTS weather_bronze_nws_actuals (
    id                  BIGSERIAL       PRIMARY KEY,
    city                TEXT            NOT NULL,
    station_code        TEXT            NOT NULL,
    cli_location        TEXT,
    target_date         DATE            NOT NULL,
    final_tmax_f        NUMERIC,
    final_tmin_f        NUMERIC,
    report_issued_at    TIMESTAMPTZ,
    source_payload_json JSONB,
    retrieved_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT weather_bronze_actuals_unique
        UNIQUE (station_code, target_date)
);

CREATE INDEX IF NOT EXISTS bract_station_date_idx
    ON weather_bronze_nws_actuals (station_code, target_date DESC);


CREATE TABLE IF NOT EXISTS weather_kalshi_contract_catalog (
    id                  BIGSERIAL       PRIMARY KEY,
    market_ticker       TEXT            NOT NULL UNIQUE,
    event_ticker        TEXT,
    series_ticker       TEXT,
    city                TEXT,
    station_code        TEXT,
    contract_side       TEXT,
    bucket_type         TEXT,
    bucket_floor        NUMERIC,
    bucket_cap          NUMERIC,
    bucket_label        TEXT,
    target_date         DATE,
    market_status       TEXT,
    is_active           BOOLEAN         NOT NULL DEFAULT TRUE,
    first_seen_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    last_seen_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    source_payload_json JSONB
);

CREATE INDEX IF NOT EXISTS catalog_station_date_idx
    ON weather_kalshi_contract_catalog (station_code, target_date, is_active)
    WHERE station_code IS NOT NULL;

CREATE INDEX IF NOT EXISTS catalog_active_date_idx
    ON weather_kalshi_contract_catalog (target_date, is_active)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS catalog_series_idx
    ON weather_kalshi_contract_catalog (series_ticker, target_date)
    WHERE series_ticker IS NOT NULL;


-- ═════════════════════════════════════════════════════════════════════════════
-- SILVER LAYER
-- ═════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS weather_silver_forecast_conformed (
    id                  BIGSERIAL       PRIMARY KEY,
    city                TEXT            NOT NULL,
    station_code        TEXT            NOT NULL,
    source_name         TEXT            NOT NULL,
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


CREATE TABLE IF NOT EXISTS weather_silver_market_conformed (
    id                      BIGSERIAL   PRIMARY KEY,
    market_ticker           TEXT        NOT NULL,
    series_ticker           TEXT,
    city                    TEXT        NOT NULL,
    station_code            TEXT        NOT NULL,
    contract_side           TEXT        NOT NULL,
    bucket_floor            NUMERIC,
    bucket_cap              NUMERIC,
    bucket_type             TEXT,
    bucket_label            TEXT,
    target_date             DATE        NOT NULL,
    snapshot_time           TIMESTAMPTZ NOT NULL,
    yes_mid                 NUMERIC,
    yes_bid                 NUMERIC,
    yes_ask                 NUMERIC,
    implied_prob            NUMERIC,
    volume                  NUMERIC,
    open_interest           NUMERIC,
    market_status           TEXT,
    market_liquidity_flag   TEXT,
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


CREATE TABLE IF NOT EXISTS weather_silver_actuals_conformed (
    id                      BIGSERIAL   PRIMARY KEY,
    city                    TEXT        NOT NULL,
    station_code            TEXT        NOT NULL,
    target_date             DATE        NOT NULL,
    final_tmax_f            NUMERIC,
    final_tmin_f            NUMERIC,
    actual_source           TEXT        NOT NULL DEFAULT 'nws_cli',
    report_issued_at        TIMESTAMPTZ,
    settlement_label_high   TEXT,
    settlement_label_low    TEXT,
    is_final                BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT silver_actuals_unique
        UNIQUE (station_code, target_date, actual_source)
);

CREATE INDEX IF NOT EXISTS sact_station_date_idx
    ON weather_silver_actuals_conformed (station_code, target_date DESC);


CREATE TABLE IF NOT EXISTS weather_silver_forecast_error (
    id                  BIGSERIAL       PRIMARY KEY,
    city                TEXT            NOT NULL,
    station_code        TEXT            NOT NULL,
    target_date         DATE            NOT NULL,
    feature_name        TEXT            NOT NULL,
    source_name         TEXT            NOT NULL,
    forecast_run_time   TIMESTAMPTZ,
    lead_hours          INTEGER,
    forecast_value      NUMERIC,
    actual_value        NUMERIC,
    forecast_error_f    NUMERIC,
    error_sign          TEXT,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT silver_fcerr_unique
        UNIQUE (station_code, target_date, feature_name, source_name, forecast_run_time)
);

CREATE INDEX IF NOT EXISTS sfce_station_feature_idx
    ON weather_silver_forecast_error (station_code, feature_name, target_date DESC);

CREATE INDEX IF NOT EXISTS sfce_station_source_idx
    ON weather_silver_forecast_error (station_code, source_name, target_date DESC);


CREATE TABLE IF NOT EXISTS weather_silver_calibration_training_set (
    id                          BIGSERIAL   PRIMARY KEY,
    city                        TEXT        NOT NULL,
    station_code                TEXT        NOT NULL,
    contract_side               TEXT        NOT NULL,
    market_ticker               TEXT,
    target_date                 DATE        NOT NULL,
    forecast_run_time           TIMESTAMPTZ,
    lead_hours                  INTEGER,
    raw_prob                    NUMERIC,
    market_price                NUMERIC,
    actual_outcome              INTEGER,
    actual_tmax_f               NUMERIC,
    forecast_error_f            NUMERIC,
    season                      TEXT,
    calibration_window_flag     BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS scal_station_side_idx
    ON weather_silver_calibration_training_set (station_code, contract_side, target_date DESC);


CREATE TABLE IF NOT EXISTS weather_silver_model_base (
    id                      BIGSERIAL   PRIMARY KEY,
    city                    TEXT        NOT NULL,
    station_code            TEXT        NOT NULL,
    target_date             DATE        NOT NULL,
    contract_side           TEXT        NOT NULL,
    forecast_run_time       TIMESTAMPTZ NOT NULL,
    lead_hours              INTEGER,
    nws_tmax_f              NUMERIC,
    nws_tmin_f              NUMERIC,
    nws_dewpoint_f          NUMERIC,
    nws_rh_pct              NUMERIC,
    nws_wind_speed_mph      NUMERIC,
    nws_cloud_cover_pct     NUMERIC,
    nws_pop_pct             NUMERIC,
    gfs_tmax_f              NUMERIC,
    gfs_tmin_f              NUMERIC,
    gfs_dewpoint_f          NUMERIC,
    gfs_rh_pct              NUMERIC,
    gfs_wind_speed          NUMERIC,
    gfs_cloud_cover         NUMERIC,
    nws_gfs_tmax_delta      NUMERIC,
    nws_gfs_tmin_delta      NUMERIC,
    market_ticker           TEXT,
    kalshi_yes_mid          NUMERIC,
    kalshi_implied_prob     NUMERIC,
    kalshi_volume           NUMERIC,
    kalshi_snapshot_time    TIMESTAMPTZ,
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


-- ═════════════════════════════════════════════════════════════════════════════
-- GOLD LAYER
-- ═════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS weather_gold_city_day_features (
    id                      BIGSERIAL   PRIMARY KEY,
    city                    TEXT        NOT NULL,
    station_code            TEXT        NOT NULL,
    target_date             DATE        NOT NULL,
    contract_side           TEXT        NOT NULL,
    latest_nws_tmax_f       NUMERIC,
    latest_nws_tmin_f       NUMERIC,
    latest_gfs_tmax_f       NUMERIC,
    latest_gfs_tmin_f       NUMERIC,
    forecast_spread         NUMERIC,
    dewpoint_spread         NUMERIC,
    humidity_spread         NUMERIC,
    cloud_cover_change      NUMERIC,
    wind_shift_flag         BOOLEAN,
    lead_time_hours         INTEGER,
    season                  TEXT,
    historical_bias_7d      NUMERIC,
    historical_bias_30d     NUMERIC,
    historical_mae_30d      NUMERIC,
    market_yes_mid          NUMERIC,
    market_implied_prob     NUMERIC,
    target_label            NUMERIC,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT gold_features_unique
        UNIQUE (station_code, target_date, contract_side)
);

CREATE INDEX IF NOT EXISTS gfeat_station_date_idx
    ON weather_gold_city_day_features (station_code, target_date DESC, contract_side);


CREATE TABLE IF NOT EXISTS weather_gold_calibrated_probabilities (
    id                      BIGSERIAL   PRIMARY KEY,
    city                    TEXT        NOT NULL,
    station_code            TEXT        NOT NULL,
    target_date             DATE        NOT NULL,
    contract_side           TEXT        NOT NULL,
    market_ticker           TEXT        NOT NULL,
    bucket_floor            NUMERIC,
    bucket_cap              NUMERIC,
    bucket_label            TEXT,
    raw_model_prob          NUMERIC,
    calibrated_prob         NUMERIC,
    market_implied_prob     NUMERIC,
    edge                    NUMERIC,
    edge_rank               INTEGER,
    trade_flag              TEXT,
    confidence              TEXT,
    model_delta_flag        TEXT,
    calibrator_version      TEXT        DEFAULT 'v0_passthrough',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS gcprob_station_date_edge_idx
    ON weather_gold_calibrated_probabilities (station_code, target_date, contract_side, edge DESC);

CREATE INDEX IF NOT EXISTS gcprob_trade_date_idx
    ON weather_gold_calibrated_probabilities (trade_flag, target_date)
    WHERE trade_flag != 'SKIP';


CREATE TABLE IF NOT EXISTS weather_gold_daily_edge_sheet (
    id                      BIGSERIAL   PRIMARY KEY,
    city                    TEXT        NOT NULL,
    station_code            TEXT        NOT NULL,
    target_date             DATE        NOT NULL,
    contract_side           TEXT        NOT NULL,
    contract_ticker         TEXT        NOT NULL,
    bucket_floor            NUMERIC,
    bucket_cap              NUMERIC,
    bucket_label            TEXT,
    sheet_date              DATE        NOT NULL DEFAULT CURRENT_DATE,
    raw_forecast_f          NUMERIC,
    gfs_forecast_f          NUMERIC,
    model_delta_f           NUMERIC,
    model_confidence        TEXT,
    calibrated_prob         NUMERIC,
    raw_model_prob          NUMERIC,
    market_implied_prob     NUMERIC,
    market_yes_mid          NUMERIC,
    market_volume           NUMERIC,
    market_liquidity        TEXT,
    edge                    NUMERIC,
    edge_pct                NUMERIC,
    edge_rank               INTEGER,
    recommended_action      TEXT,
    stake_fraction          NUMERIC,
    stake_usd               NUMERIC,
    skip_reason             TEXT,
    last_updated            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    calibrator_version      TEXT        DEFAULT 'v0_passthrough',
    is_active               BOOLEAN     NOT NULL DEFAULT TRUE,

    CONSTRAINT gold_edge_sheet_unique
        UNIQUE (contract_ticker, sheet_date)
);

CREATE INDEX IF NOT EXISTS ges_target_action_edge_idx
    ON weather_gold_daily_edge_sheet (target_date, recommended_action, edge DESC);

CREATE INDEX IF NOT EXISTS ges_station_date_idx
    ON weather_gold_daily_edge_sheet (station_code, target_date);

CREATE INDEX IF NOT EXISTS ges_sheet_date_idx
    ON weather_gold_daily_edge_sheet (sheet_date DESC, recommended_action);


-- ═════════════════════════════════════════════════════════════════════════════
-- PERMISSIONS
-- ═════════════════════════════════════════════════════════════════════════════

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'horizon_agent_reader') THEN
        GRANT SELECT ON
            weather_bronze_nws_forecast_snapshots,
            weather_bronze_openmeteo_forecast_snapshots,
            weather_bronze_kalshi_market_snapshots,
            weather_bronze_nws_actuals,
            weather_kalshi_contract_catalog,
            weather_silver_forecast_conformed,
            weather_silver_market_conformed,
            weather_silver_actuals_conformed,
            weather_silver_forecast_error,
            weather_silver_calibration_training_set,
            weather_silver_model_base,
            weather_gold_city_day_features,
            weather_gold_calibrated_probabilities,
            weather_gold_daily_edge_sheet
        TO horizon_agent_reader;
    END IF;
END $$;

COMMIT;

\echo ''
\echo '=== BSG tables created. Verify with:'
\echo "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'weather_%' ORDER BY 1;"
