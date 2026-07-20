-- Migration: add weather_silver_asos_daily_high_low_occurrence
-- 2026-07-20
--
-- Item 2 of the LAX/MIA multivariate probability-model work: time-of-
-- occurrence distribution for the daily high and low, derived directly
-- from the full 1-minute archive (2011-2026) rather than the scarce CLI
-- timestamp field (~40 days/station). This is what tells you, live, how
-- much probability is still on the table at a given hour -- e.g. "the
-- high typically lands by 3pm in summer, so by 4pm most of the
-- uncertainty is already resolved."
--
-- Uses the same reconstructed true-NWS series as item 1 (trailing 5-min
-- average, recomputed every minute -- see
-- weather_silver_asos_degree_crossings) rather than the raw 1-min value,
-- since the crossing table only marks degree BOUNDARIES, not the actual
-- continuous peak/trough between them -- the real daily extremum has to
-- come from scanning the full averaged series per day, not just the
-- crossing points.
--
-- Same fixed local-standard-time (no DST) calendar-day convention as
-- item 1: KLAX = UTC-8 year-round, KMIA = UTC-5 year-round. Working
-- assumption, not yet validated against genuine CLI occurrence times.
--
-- Populated via scripts/weather/asos_daily_high_low_occurrence_extract.py,
-- chunked per station-year (same memory-safety rationale as item 1 --
-- LA is a shared production trading box with limited headroom).
--
-- Run on LA:
--   sudo -u postgres psql eventhorizon -f sql/migrations/2026-07-20-asos-daily-high-low-occurrence-table.sql

CREATE TABLE IF NOT EXISTS weather_silver_asos_daily_high_low_occurrence (
    id                  BIGSERIAL       PRIMARY KEY,
    station_code        TEXT            NOT NULL,
    local_date           DATE            NOT NULL,       -- fixed-LST calendar day
    season              TEXT            NOT NULL,
    high_temp_f          NUMERIC         NOT NULL,        -- true-NWS (rolling-avg) daily high
    high_occurred_at    TIMESTAMPTZ     NOT NULL,        -- UTC
    high_local_minute    SMALLINT        NOT NULL,        -- 0-1439, minutes since local midnight
    low_temp_f           NUMERIC         NOT NULL,
    low_occurred_at      TIMESTAMPTZ     NOT NULL,
    low_local_minute     SMALLINT        NOT NULL,

    CONSTRAINT asoshilo_station_date_unique
        UNIQUE (station_code, local_date)
);

CREATE INDEX IF NOT EXISTS asoshilo_station_season_idx
    ON weather_silver_asos_daily_high_low_occurrence (station_code, season);

-- Permissions -- same reader set as the other new characterization tables.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_weather_collector') THEN
        GRANT INSERT, SELECT ON weather_silver_asos_daily_high_low_occurrence TO bhn_weather_collector;
        GRANT USAGE, SELECT ON SEQUENCE weather_silver_asos_daily_high_low_occurrence_id_seq TO bhn_weather_collector;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'horizon_agent_reader') THEN
        GRANT SELECT ON weather_silver_asos_daily_high_low_occurrence TO horizon_agent_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader') THEN
        GRANT SELECT ON weather_silver_asos_daily_high_low_occurrence TO grafana_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_reader') THEN
        GRANT SELECT ON weather_silver_asos_daily_high_low_occurrence TO agent_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_trader') THEN
        GRANT SELECT, INSERT, UPDATE ON weather_silver_asos_daily_high_low_occurrence TO bhn_trader;
        GRANT USAGE, SELECT ON SEQUENCE weather_silver_asos_daily_high_low_occurrence_id_seq TO bhn_trader;
    END IF;
END $$;
