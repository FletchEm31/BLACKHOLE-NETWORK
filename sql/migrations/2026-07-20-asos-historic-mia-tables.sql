-- Migration: add weather_bronze_asos_historic_mia_1min / _5min
-- 2026-07-20
--
-- Historical IEM ASOS 1-minute and 5-minute station data for KMIA, staged
-- by the operator as flat CSV-in-.txt files in
-- infrastructure/docs/WeatherBHN/ (ASOS1M-MIA*.txt / ASOS5M-MIA26-11-FULL15YR.txt).
-- Source: https://mesonet.agron.iastate.edu/request/asos/1min.phtml
--
-- IMPORTANT STRUCTURAL DIFFERENCE FROM THE LAX TABLES (confirmed from
-- actual file content 2026-07-20, not assumed from the directory index
-- doc, which claimed the same thing for LAX and was checked/found true
-- there but should never be trusted blindly): MIA's 5-minute file is NOT
-- a reduced field set like LAX's was. It carries the full 14-field header
-- identical to the 1-minute file (gust, precip, and all 3 pressure
-- sensors included). So weather_bronze_asos_historic_mia_5min is shaped
-- identically to _1min here, unlike the LAX pair where _5min genuinely
-- has fewer columns. Also: MIA's 5-min data ships as a single file
-- covering the full 2011-2026 range, not three chunked files like LAX.
--
-- Missing values in the source are the literal string "M", not
-- empty/null -- the loader converts "M" to SQL NULL before insert.
--
-- Natural key (station_code, observed_at) per table, matching the LAX and
-- weather_bronze_synoptic_asos pattern. station_code normalized to ICAO
-- 'KMIA' at load time (source files use IEM's 3-letter 'MIA').
--
-- Run on LA:
--   sudo -u postgres psql eventhorizon -f sql/migrations/2026-07-20-asos-historic-mia-tables.sql

CREATE TABLE IF NOT EXISTS weather_bronze_asos_historic_mia_1min (
    id                  BIGSERIAL       PRIMARY KEY,
    station_code        TEXT            NOT NULL,       -- normalized ICAO, 'KMIA'
    observed_at         TIMESTAMPTZ     NOT NULL,        -- UTC, from source valid(UTC)
    air_temp_f          NUMERIC,                         -- tmpf
    dew_point_temp_f    NUMERIC,                         -- dwpf
    wind_speed_kt       NUMERIC,                         -- sknt
    wind_direction_deg  NUMERIC,                         -- drct
    gust_direction_deg  NUMERIC,                         -- gust_drct
    gust_speed_kt       NUMERIC,                         -- gust_sknt
    precip_type_code    TEXT,                            -- ptype (e.g. 'NP', 'R', 'R+')
    precip_in           NUMERIC,                         -- precip (1-min accumulation, inches)
    pressure_1_inhg     NUMERIC,                         -- pres1
    pressure_2_inhg     NUMERIC,                         -- pres2
    pressure_3_inhg     NUMERIC,                         -- pres3
    source_file         TEXT,
    retrieved_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT weather_bronze_asos_historic_mia_1min_unique
        UNIQUE (station_code, observed_at)
);

CREATE INDEX IF NOT EXISTS brasosmia1m_station_observed_idx
    ON weather_bronze_asos_historic_mia_1min (station_code, observed_at DESC);

-- Full field set -- NOT reduced like weather_bronze_asos_historic_lax_5min.
-- See migration comment above: confirmed from the actual MIA 5-min file
-- header, which is identical to the 1-min header.
CREATE TABLE IF NOT EXISTS weather_bronze_asos_historic_mia_5min (
    id                  BIGSERIAL       PRIMARY KEY,
    station_code        TEXT            NOT NULL,
    observed_at         TIMESTAMPTZ     NOT NULL,
    air_temp_f          NUMERIC,
    dew_point_temp_f    NUMERIC,
    wind_speed_kt       NUMERIC,
    wind_direction_deg  NUMERIC,
    gust_direction_deg  NUMERIC,
    gust_speed_kt       NUMERIC,
    precip_type_code    TEXT,
    precip_in           NUMERIC,
    pressure_1_inhg     NUMERIC,
    pressure_2_inhg     NUMERIC,
    pressure_3_inhg     NUMERIC,
    source_file         TEXT,
    retrieved_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT weather_bronze_asos_historic_mia_5min_unique
        UNIQUE (station_code, observed_at)
);

CREATE INDEX IF NOT EXISTS brasosmia5m_station_observed_idx
    ON weather_bronze_asos_historic_mia_5min (station_code, observed_at DESC);

-- Permissions -- same roles as the LAX pair, granted in the same migration
-- as table creation.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_weather_collector') THEN
        GRANT INSERT, SELECT ON weather_bronze_asos_historic_mia_1min TO bhn_weather_collector;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_asos_historic_mia_1min_id_seq TO bhn_weather_collector;
        GRANT INSERT, SELECT ON weather_bronze_asos_historic_mia_5min TO bhn_weather_collector;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_asos_historic_mia_5min_id_seq TO bhn_weather_collector;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'horizon_agent_reader') THEN
        GRANT SELECT ON weather_bronze_asos_historic_mia_1min TO horizon_agent_reader;
        GRANT SELECT ON weather_bronze_asos_historic_mia_5min TO horizon_agent_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader') THEN
        GRANT SELECT ON weather_bronze_asos_historic_mia_1min TO grafana_reader;
        GRANT SELECT ON weather_bronze_asos_historic_mia_5min TO grafana_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_reader') THEN
        GRANT SELECT ON weather_bronze_asos_historic_mia_1min TO agent_reader;
        GRANT SELECT ON weather_bronze_asos_historic_mia_5min TO agent_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'n8n_user') THEN
        GRANT SELECT ON weather_bronze_asos_historic_mia_1min TO n8n_user;
        GRANT SELECT ON weather_bronze_asos_historic_mia_5min TO n8n_user;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_trader') THEN
        GRANT SELECT, INSERT, UPDATE ON weather_bronze_asos_historic_mia_1min TO bhn_trader;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_asos_historic_mia_1min_id_seq TO bhn_trader;
        GRANT SELECT, INSERT, UPDATE ON weather_bronze_asos_historic_mia_5min TO bhn_trader;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_asos_historic_mia_5min_id_seq TO bhn_trader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ehuser') THEN
        GRANT SELECT, INSERT, UPDATE ON weather_bronze_asos_historic_mia_1min TO ehuser;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_asos_historic_mia_1min_id_seq TO ehuser;
        GRANT SELECT, INSERT, UPDATE ON weather_bronze_asos_historic_mia_5min TO ehuser;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_asos_historic_mia_5min_id_seq TO ehuser;
    END IF;
END $$;

-- Verification checklist (run after applying, before calling this done):
--   1. \d weather_bronze_asos_historic_mia_1min
--      \d weather_bronze_asos_historic_mia_5min
--   2. SELECT grantee, privilege_type FROM information_schema.role_table_grants
--        WHERE table_name IN ('weather_bronze_asos_historic_mia_1min',
--                              'weather_bronze_asos_historic_mia_5min');
--   3. python3 scripts/weather/asos_historic_mia_loader.py --resolution 1min --dry-run
--      python3 scripts/weather/asos_historic_mia_loader.py --resolution 5min --dry-run
--   4. After a real load:
--      SELECT COUNT(*), MIN(observed_at), MAX(observed_at) FROM weather_bronze_asos_historic_mia_1min;
--      SELECT COUNT(*), MIN(observed_at), MAX(observed_at) FROM weather_bronze_asos_historic_mia_5min;
