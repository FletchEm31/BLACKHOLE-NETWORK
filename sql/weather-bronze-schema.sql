-- BHN Strategy 9 — Bronze Layer Schema
-- Raw ingestion tables. Nothing is transformed here.
-- Every source API payload is preserved in source_payload_json.
-- These tables are append-only where possible; use ON CONFLICT DO NOTHING.
--
-- Apply via: sql/migrations/2026-06-11-weather-bsg-tables.sql

-- ─────────────────────────────────────────────────────────────────────────────
-- 1) weather_bronze_nws_forecast_snapshots
--    Raw NWS gridpoints forecast — one row per (station, run_time, target_date).
--    source_model always 'nws_gridpoints'.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_bronze_nws_forecast_snapshots (
    id                  BIGSERIAL       PRIMARY KEY,
    city                TEXT            NOT NULL,           -- human name e.g. 'Miami'
    station_code        TEXT            NOT NULL,           -- ICAO e.g. 'KMIA'
    nws_office          TEXT,                               -- WFO e.g. 'MFL'
    forecast_run_time   TIMESTAMPTZ     NOT NULL,           -- when NWS run was retrieved
    target_date         DATE            NOT NULL,
    lead_hours          INTEGER,                            -- target_date - forecast_run_time in hours
    tmax_f              NUMERIC,
    tmin_f              NUMERIC,
    dewpoint_f          NUMERIC,
    rh_pct              NUMERIC,
    wind_speed_mph      NUMERIC,
    wind_gust_mph       NUMERIC,
    cloud_cover_pct     NUMERIC,
    pop_pct             NUMERIC,        -- probability of precipitation
    qpf_in              NUMERIC,        -- quantitative precip forecast (inches)
    snowfall_in         NUMERIC,
    weather_text        TEXT,
    hazards             TEXT,
    source_payload_json JSONB,          -- raw period/gridpoints API response
    retrieved_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT weather_bronze_nws_unique
        UNIQUE (station_code, forecast_run_time, target_date)
);

CREATE INDEX IF NOT EXISTS brnws_station_date_idx
    ON weather_bronze_nws_forecast_snapshots (station_code, target_date, forecast_run_time DESC);

CREATE INDEX IF NOT EXISTS brnws_retrieved_idx
    ON weather_bronze_nws_forecast_snapshots (retrieved_at DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- 2) weather_bronze_openmeteo_forecast_snapshots
--    Raw Open-Meteo hourly forecast — one row per (station, model, run, date, hour).
--    forecast_run_time is rounded DOWN to nearest 6h GFS cycle boundary.
--    hour = NULL when populated from daily aggregate migration.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_bronze_openmeteo_forecast_snapshots (
    id                      BIGSERIAL   PRIMARY KEY,
    city                    TEXT        NOT NULL,
    station_code            TEXT        NOT NULL,
    lat                     NUMERIC,
    lon                     NUMERIC,
    model                   TEXT        NOT NULL,   -- 'gfs_seamless', 'ecmwf_ifs04', etc.
    forecast_run_time       TIMESTAMPTZ NOT NULL,   -- rounded to 6h GFS cycle
    target_date             DATE        NOT NULL,
    hour                    INTEGER,                -- 0-23 local hour; NULL if daily-only row
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


-- ─────────────────────────────────────────────────────────────────────────────
-- 3) weather_bronze_kalshi_market_snapshots
--    Append-only price snapshots. Every poll is a new row.
--    No unique constraint — all snapshots are valuable.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_bronze_kalshi_market_snapshots (
    id                  BIGSERIAL       PRIMARY KEY,
    market_ticker       TEXT            NOT NULL,
    event_ticker        TEXT,
    series_ticker       TEXT,
    city                TEXT,
    station_code        TEXT,
    contract_side       TEXT,           -- 'high' or 'low'
    bucket_type         TEXT,           -- 'between', 'above', 'below'
    bucket_floor        NUMERIC,
    bucket_cap          NUMERIC,
    bucket_label        TEXT,           -- e.g. '88-89', '>92', '<85'
    target_date         DATE,
    yes_bid             NUMERIC,
    yes_ask             NUMERIC,
    no_bid              NUMERIC,
    no_ask              NUMERIC,
    yes_mid             NUMERIC,        -- (yes_bid + yes_ask) / 2 when both present
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


-- ─────────────────────────────────────────────────────────────────────────────
-- 4) weather_bronze_nws_actuals
--    Official NWS CLI (Daily Climate Report) settlement values.
--    This is the source Kalshi uses for contract settlement.
--    UNIQUE per station + date — actuals are immutable once issued.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_bronze_nws_actuals (
    id                  BIGSERIAL       PRIMARY KEY,
    city                TEXT            NOT NULL,
    station_code        TEXT            NOT NULL,
    cli_location        TEXT,           -- 3-char NWS CLI location code e.g. 'MIA'
    target_date         DATE            NOT NULL,
    final_tmax_f        NUMERIC,
    final_tmin_f        NUMERIC,
    report_issued_at    TIMESTAMPTZ,
    source_payload_json JSONB,          -- raw CLI product text + metadata
    retrieved_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT weather_bronze_actuals_unique
        UNIQUE (station_code, target_date)
);

CREATE INDEX IF NOT EXISTS bract_station_date_idx
    ON weather_bronze_nws_actuals (station_code, target_date DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- 5) weather_kalshi_contract_catalog
--    Static catalog of all known Kalshi weather contracts.
--    Upserted by the price poller on every cycle.
--    Read by the edge calculator.
--    Replaces prediction_contracts conceptually (old table kept in parallel).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_kalshi_contract_catalog (
    id                  BIGSERIAL       PRIMARY KEY,
    market_ticker       TEXT            NOT NULL UNIQUE,
    event_ticker        TEXT,
    series_ticker       TEXT,
    city                TEXT,
    station_code        TEXT,
    contract_side       TEXT,           -- 'high' or 'low'
    bucket_type         TEXT,           -- 'between', 'above', 'below'
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

-- Permissions
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'horizon_agent_reader') THEN
        GRANT SELECT ON weather_bronze_nws_forecast_snapshots TO horizon_agent_reader;
        GRANT SELECT ON weather_bronze_openmeteo_forecast_snapshots TO horizon_agent_reader;
        GRANT SELECT ON weather_bronze_kalshi_market_snapshots TO horizon_agent_reader;
        GRANT SELECT ON weather_bronze_nws_actuals TO horizon_agent_reader;
        GRANT SELECT ON weather_kalshi_contract_catalog TO horizon_agent_reader;
    END IF;
END $$;
