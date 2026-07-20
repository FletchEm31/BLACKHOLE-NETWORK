-- Migration: add weather_silver_asos_running_extremes
-- 2026-07-20
--
-- Built to construct a genuinely fair baseline for the item-4 analog-day
-- matching backtest. The original baseline ("naive climatology" =
-- unconditional same-season mean of the final high/low) uses zero
-- information about the day in progress -- beating it isn't meaningful
-- evidence of skill, since analog matching is conditioned on today's
-- trajectory and climatology isn't conditioned on anything. This table
-- gives the stronger, fairer baseline: the running high/low ALREADY
-- OBSERVED as of each checkpoint hour, from the continuous true-NWS
-- series (trailing 5-min average) -- not the 3-hour trajectory snapshots,
-- which would understate how much a real trader already knows by that
-- hour.
--
-- Same fixed local-standard-time (no DST) convention as items 1/2/4:
-- KLAX = UTC-8 year-round, KMIA = UTC-5 year-round. Same 8 checkpoint
-- hours as the trajectory snapshots (0,3,6,9,12,15,18,21).
--
-- Populated via scripts/weather/asos_running_extremes_extract.py, chunked
-- per station-year (same memory-safety rationale as the other items).
--
-- Run on LA:
--   sudo -u postgres psql eventhorizon -f sql/migrations/2026-07-20-asos-running-extremes-table.sql

CREATE TABLE IF NOT EXISTS weather_silver_asos_running_extremes (
    id                      BIGSERIAL       PRIMARY KEY,
    station_code            TEXT            NOT NULL,
    local_date              DATE            NOT NULL,
    local_hour              SMALLINT        NOT NULL,       -- 0,3,6,9,12,15,18,21 (fixed-LST)
    running_high_so_far_f   NUMERIC         NOT NULL,       -- MAX(true-NWS series) from local midnight through this hour
    running_low_so_far_f    NUMERIC         NOT NULL,       -- MIN(true-NWS series) from local midnight through this hour

    CONSTRAINT asosrun_station_date_hour_unique
        UNIQUE (station_code, local_date, local_hour)
);

CREATE INDEX IF NOT EXISTS asosrun_station_date_idx
    ON weather_silver_asos_running_extremes (station_code, local_date);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_weather_collector') THEN
        GRANT INSERT, SELECT ON weather_silver_asos_running_extremes TO bhn_weather_collector;
        GRANT USAGE, SELECT ON SEQUENCE weather_silver_asos_running_extremes_id_seq TO bhn_weather_collector;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_trader') THEN
        GRANT SELECT, INSERT, UPDATE ON weather_silver_asos_running_extremes TO bhn_trader;
        GRANT USAGE, SELECT ON SEQUENCE weather_silver_asos_running_extremes_id_seq TO bhn_trader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'horizon_agent_reader') THEN
        GRANT SELECT ON weather_silver_asos_running_extremes TO horizon_agent_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader') THEN
        GRANT SELECT ON weather_silver_asos_running_extremes TO grafana_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_reader') THEN
        GRANT SELECT ON weather_silver_asos_running_extremes TO agent_reader;
    END IF;
END $$;
