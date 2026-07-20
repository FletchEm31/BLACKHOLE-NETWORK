-- Migration: add weather_silver_asos_daily_trajectory_snapshots
-- 2026-07-20
--
-- Foundational table for items 3 (multivariate conditioning) and 4
-- (analog-day matching) of the LAX/MIA multivariate probability-model
-- work. Captures a compact multivariate "trajectory fingerprint" per day:
-- air temp, dewpoint, wind speed/direction, station pressure, and a
-- precip flag at 8 fixed local-standard-time checkpoints per day (00, 03,
-- 06, 09, 12, 15, 18, 21) -- not every 1-minute row, which would make
-- item 4's analog search (comparing "today's trajectory so far" against
-- 15 years of history) unnecessarily expensive for a first pass.
--
-- Same fixed local-standard-time (no DST) convention as items 1/2:
-- KLAX = UTC-8 year-round, KMIA = UTC-5 year-round.
--
-- Populated via scripts/weather/asos_daily_trajectory_snapshots_extract.py,
-- chunked per station-year (same memory-safety rationale as items 1/2).
--
-- Run on LA:
--   sudo -u postgres psql eventhorizon -f sql/migrations/2026-07-20-asos-daily-trajectory-snapshots-table.sql

CREATE TABLE IF NOT EXISTS weather_silver_asos_daily_trajectory_snapshots (
    id                  BIGSERIAL       PRIMARY KEY,
    station_code        TEXT            NOT NULL,
    local_date          DATE            NOT NULL,
    local_hour          SMALLINT        NOT NULL,       -- 0,3,6,9,12,15,18,21 (fixed-LST)
    observed_at         TIMESTAMPTZ     NOT NULL,       -- actual UTC row matched (exact-minute join)
    air_temp_f          NUMERIC,
    dew_point_temp_f    NUMERIC,
    wind_speed_kt       NUMERIC,
    wind_direction_deg  NUMERIC,
    pressure_1_inhg     NUMERIC,
    has_precip          BOOLEAN         NOT NULL,       -- ptype not in ('NP', NULL)

    CONSTRAINT asostraj_station_date_hour_unique
        UNIQUE (station_code, local_date, local_hour)
);

CREATE INDEX IF NOT EXISTS asostraj_station_date_idx
    ON weather_silver_asos_daily_trajectory_snapshots (station_code, local_date);

-- Permissions -- same reader set as the other new characterization tables.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_weather_collector') THEN
        GRANT INSERT, SELECT ON weather_silver_asos_daily_trajectory_snapshots TO bhn_weather_collector;
        GRANT USAGE, SELECT ON SEQUENCE weather_silver_asos_daily_trajectory_snapshots_id_seq TO bhn_weather_collector;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'horizon_agent_reader') THEN
        GRANT SELECT ON weather_silver_asos_daily_trajectory_snapshots TO horizon_agent_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader') THEN
        GRANT SELECT ON weather_silver_asos_daily_trajectory_snapshots TO grafana_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_reader') THEN
        GRANT SELECT ON weather_silver_asos_daily_trajectory_snapshots TO agent_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_trader') THEN
        GRANT SELECT, INSERT, UPDATE ON weather_silver_asos_daily_trajectory_snapshots TO bhn_trader;
        GRANT USAGE, SELECT ON SEQUENCE weather_silver_asos_daily_trajectory_snapshots_id_seq TO bhn_trader;
    END IF;
END $$;
