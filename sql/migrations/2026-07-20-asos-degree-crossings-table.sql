-- Migration: add weather_silver_asos_degree_crossings
-- 2026-07-20
--
-- Part of the LAX/MIA multivariate probability-model work. Per NWS's own
-- ASOS documentation (weather.gov/asos, weather.gov/lox/asostemperature),
-- the official temperature series is a trailing 5-minute average of the
-- raw sensor reading, recomputed every minute -- not the raw 1-min
-- instantaneous value, and not the 5-min subsample (already confirmed
-- 2026-07-20 to be a pure snapshot of the raw reading, zero averaging).
-- This table logs every whole-degree transition of that reconstructed
-- rolling-average series, giving an empirical rate-of-change dataset:
-- how fast temperature typically moves once it starts climbing or
-- falling, by season/hour-of-day, across the full 2011-2026 archive.
--
-- Calendar-day/hour bucketing uses FIXED local standard time (no DST
-- adjustment) per the NWS climate-day convention: KLAX = UTC-8 year-round,
-- KMIA = UTC-5 year-round. This is a working assumption for this
-- characterization pass, not yet formally validated against genuine CLI
-- occurrence times -- that validation is a separate, lower-priority
-- follow-up (see WEATHERBHN-ASOS-MULTIVARIATE-PROBABILITY-MODEL-SCOPING
-- and the queued ASOS-to-CLI algorithm write-up).
--
-- Populated via scripts/weather/asos_degree_crossings_extract.py, chunked
-- per station-year to keep memory pressure low on LA (shared with the
-- live trading pipeline -- confirmed low headroom before running this).
--
-- Run on LA:
--   sudo -u postgres psql eventhorizon -f sql/migrations/2026-07-20-asos-degree-crossings-table.sql

CREATE TABLE IF NOT EXISTS weather_silver_asos_degree_crossings (
    id                      BIGSERIAL       PRIMARY KEY,
    station_code            TEXT            NOT NULL,
    crossing_at             TIMESTAMPTZ     NOT NULL,       -- UTC, the 1-min row's observed_at
    rolling_avg_temp_f       NUMERIC         NOT NULL,       -- trailing 5-min avg at this row
    prev_rolling_avg_temp_f  NUMERIC         NOT NULL,       -- trailing 5-min avg at the prior row
    direction               TEXT            NOT NULL,       -- 'rising' or 'falling'
    crossed_degree_f        INTEGER         NOT NULL,       -- whole-degree F threshold crossed
    local_date              DATE            NOT NULL,       -- fixed-LST calendar day
    local_hour              SMALLINT        NOT NULL,       -- 0-23, fixed-LST
    season                  TEXT            NOT NULL,

    CONSTRAINT asoscross_direction_check
        CHECK (direction IN ('rising', 'falling'))
);

CREATE INDEX IF NOT EXISTS asoscross_station_date_idx
    ON weather_silver_asos_degree_crossings (station_code, local_date);

CREATE INDEX IF NOT EXISTS asoscross_station_season_hour_idx
    ON weather_silver_asos_degree_crossings (station_code, season, local_hour);

-- Permissions -- same reader set as the historic ASOS bronze tables.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_weather_collector') THEN
        GRANT INSERT, SELECT ON weather_silver_asos_degree_crossings TO bhn_weather_collector;
        GRANT USAGE, SELECT ON SEQUENCE weather_silver_asos_degree_crossings_id_seq TO bhn_weather_collector;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'horizon_agent_reader') THEN
        GRANT SELECT ON weather_silver_asos_degree_crossings TO horizon_agent_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader') THEN
        GRANT SELECT ON weather_silver_asos_degree_crossings TO grafana_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_reader') THEN
        GRANT SELECT ON weather_silver_asos_degree_crossings TO agent_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_trader') THEN
        GRANT SELECT, INSERT, UPDATE ON weather_silver_asos_degree_crossings TO bhn_trader;
        GRANT USAGE, SELECT ON SEQUENCE weather_silver_asos_degree_crossings_id_seq TO bhn_trader;
    END IF;
END $$;
