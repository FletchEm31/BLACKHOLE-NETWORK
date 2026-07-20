-- Migration: add weather_bronze_asos_historic_lax_1min / _5min
-- 2026-07-20
--
-- Historical IEM ASOS 1-minute and 5-minute station data for KLAX, staged
-- by the operator as flat CSV-in-.txt files in
-- infrastructure/docs/WeatherBHN/ (ASOS1M-LAX*.txt / ASOS5M-LAX*.txt).
-- Source: https://mesonet.agron.iastate.edu/request/asos/1min.phtml
--
-- Two separate tables, NOT a shared shape -- the 5-minute IEM product is a
-- genuinely reduced field set (no gust/precip/pressure columns exist in
-- that feed at all), confirmed by reading the actual file headers rather
-- than assuming symmetry with the 1-minute file:
--   1-min header: station,station_name,valid(UTC),tmpf,dwpf,sknt,drct,
--                 gust_drct,gust_sknt,ptype,precip,pres1,pres2,pres3
--   5-min header: station,station_name,valid(UTC),tmpf,dwpf,sknt,drct
-- Padding weather_bronze_asos_historic_lax_5min with gust/precip/pressure
-- columns "for symmetry" would leave them permanently NULL and mislead
-- anyone querying the table into thinking that data should exist.
--
-- Missing values in the source files are the literal string "M", not
-- empty/null -- the loader (scripts/weather/asos_historic_lax_loader.py)
-- converts "M" to SQL NULL before insert.
--
-- Natural key (station_code, observed_at) per table, matching the pattern
-- used by weather_bronze_synoptic_asos. station_code is normalized to the
-- ICAO form 'KLAX' at load time even though the source files use IEM's
-- 3-letter 'LAX', to stay consistent with the rest of the bronze layer.
--
-- source_file records provenance (which of the 3 chunked files a row came
-- from) since IEM ships this multi-decade history as several files, not
-- one -- confirmed the real date ranges per file from content, not
-- filenames, before writing this migration (the directory index doc's own
-- filenames didn't even match what's on disk).
--
-- Run on LA:
--   sudo -u postgres psql eventhorizon -f sql/migrations/2026-07-20-asos-historic-lax-tables.sql

CREATE TABLE IF NOT EXISTS weather_bronze_asos_historic_lax_1min (
    id                  BIGSERIAL       PRIMARY KEY,
    station_code        TEXT            NOT NULL,       -- normalized ICAO, 'KLAX'
    observed_at         TIMESTAMPTZ     NOT NULL,        -- UTC, from source valid(UTC)
    air_temp_f          NUMERIC,                         -- tmpf
    dew_point_temp_f    NUMERIC,                         -- dwpf
    wind_speed_kt       NUMERIC,                         -- sknt
    wind_direction_deg  NUMERIC,                         -- drct
    gust_direction_deg  NUMERIC,                         -- gust_drct
    gust_speed_kt       NUMERIC,                         -- gust_sknt
    precip_type_code    TEXT,                            -- ptype (e.g. 'NP', 'R', 'R+')
    precip_in           NUMERIC,                         -- precip (1-min accumulation, inches)
    pressure_1_inhg     NUMERIC,                         -- pres1 (sensor 1 station pressure)
    pressure_2_inhg     NUMERIC,                         -- pres2 (sensor 2 station pressure)
    pressure_3_inhg     NUMERIC,                         -- pres3 (sensor 3 station pressure)
    source_file         TEXT,                            -- which raw file this row was loaded from
    retrieved_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT weather_bronze_asos_historic_lax_1min_unique
        UNIQUE (station_code, observed_at)
);

CREATE INDEX IF NOT EXISTS brasoslax1m_station_observed_idx
    ON weather_bronze_asos_historic_lax_1min (station_code, observed_at DESC);


CREATE TABLE IF NOT EXISTS weather_bronze_asos_historic_lax_5min (
    id                  BIGSERIAL       PRIMARY KEY,
    station_code        TEXT            NOT NULL,       -- normalized ICAO, 'KLAX'
    observed_at         TIMESTAMPTZ     NOT NULL,        -- UTC, from source valid(UTC)
    air_temp_f          NUMERIC,                         -- tmpf
    dew_point_temp_f    NUMERIC,                         -- dwpf
    wind_speed_kt       NUMERIC,                         -- sknt
    wind_direction_deg  NUMERIC,                         -- drct
    source_file         TEXT,                            -- which raw file this row was loaded from
    retrieved_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT weather_bronze_asos_historic_lax_5min_unique
        UNIQUE (station_code, observed_at)
);

CREATE INDEX IF NOT EXISTS brasoslax5m_station_observed_idx
    ON weather_bronze_asos_historic_lax_5min (station_code, observed_at DESC);

-- Permissions -- granted in the same migration as table creation, on
-- purpose (grant-lag has bitten this project repeatedly -- see
-- weather_bronze_synoptic_asos migration history). These are historical
-- bulk-load tables (loader runs manually, not on a systemd timer like the
-- other bronze sources), but bhn_weather_collector still gets INSERT+SELECT
-- for consistency and in case a future incremental refresh reuses that role.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_weather_collector') THEN
        GRANT INSERT, SELECT ON weather_bronze_asos_historic_lax_1min TO bhn_weather_collector;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_asos_historic_lax_1min_id_seq TO bhn_weather_collector;
        GRANT INSERT, SELECT ON weather_bronze_asos_historic_lax_5min TO bhn_weather_collector;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_asos_historic_lax_5min_id_seq TO bhn_weather_collector;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'horizon_agent_reader') THEN
        GRANT SELECT ON weather_bronze_asos_historic_lax_1min TO horizon_agent_reader;
        GRANT SELECT ON weather_bronze_asos_historic_lax_5min TO horizon_agent_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader') THEN
        GRANT SELECT ON weather_bronze_asos_historic_lax_1min TO grafana_reader;
        GRANT SELECT ON weather_bronze_asos_historic_lax_5min TO grafana_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_reader') THEN
        GRANT SELECT ON weather_bronze_asos_historic_lax_1min TO agent_reader;
        GRANT SELECT ON weather_bronze_asos_historic_lax_5min TO agent_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'n8n_user') THEN
        GRANT SELECT ON weather_bronze_asos_historic_lax_1min TO n8n_user;
        GRANT SELECT ON weather_bronze_asos_historic_lax_5min TO n8n_user;
    END IF;
    -- bhn_trader is the role the loader script actually connects as (per
    -- DATABASE_URL in /etc/bhn-trading/env on LA) -- matches the same grant
    -- given to the other bulk-historical bronze tables (visual_crossing,
    -- era5_kmia, era5_klax).
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_trader') THEN
        GRANT SELECT, INSERT, UPDATE ON weather_bronze_asos_historic_lax_1min TO bhn_trader;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_asos_historic_lax_1min_id_seq TO bhn_trader;
        GRANT SELECT, INSERT, UPDATE ON weather_bronze_asos_historic_lax_5min TO bhn_trader;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_asos_historic_lax_5min_id_seq TO bhn_trader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ehuser') THEN
        GRANT SELECT, INSERT, UPDATE ON weather_bronze_asos_historic_lax_1min TO ehuser;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_asos_historic_lax_1min_id_seq TO ehuser;
        GRANT SELECT, INSERT, UPDATE ON weather_bronze_asos_historic_lax_5min TO ehuser;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_asos_historic_lax_5min_id_seq TO ehuser;
    END IF;
END $$;

-- Verification checklist (run after applying, before calling this done):
--   1. \d weather_bronze_asos_historic_lax_1min
--      \d weather_bronze_asos_historic_lax_5min
--   2. SELECT grantee, privilege_type FROM information_schema.role_table_grants
--        WHERE table_name IN ('weather_bronze_asos_historic_lax_1min',
--                              'weather_bronze_asos_historic_lax_5min');
--      -- must show bhn_weather_collector with INSERT + SELECT on both
--   3. python3 scripts/weather/asos_historic_lax_loader.py --resolution 1min --dry-run
--      python3 scripts/weather/asos_historic_lax_loader.py --resolution 5min --dry-run
--   4. After a real (non-dry-run) load:
--      SELECT COUNT(*), MIN(observed_at), MAX(observed_at) FROM weather_bronze_asos_historic_lax_1min;
--      SELECT COUNT(*), MIN(observed_at), MAX(observed_at) FROM weather_bronze_asos_historic_lax_5min;
