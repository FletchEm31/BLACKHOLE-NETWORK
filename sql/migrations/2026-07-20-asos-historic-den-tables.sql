-- Migration: add weather_bronze_asos_historic_den_1min / _5min
-- 2026-07-20
--
-- Denver was the one Kalshi-tradeable station with no historical 1-min/
-- 5-min ASOS archive all session (flagged repeatedly as "the open
-- question" -- LAX and MIA were sourced from operator-staged files, but
-- Denver's files were never provided until the operator found IEM's
-- direct asos1min.py endpoint, documented in "ASOS 1M-5M Automation Curl
-- Data Reqauests.txt").
--
-- Sourced via scripts/weather/asos_monthly_fetch.sh -- one station, one
-- month, one resolution at a time (per that doc's own safety guidance,
-- and NOAA/IEM's general guidance against single giant requests for
-- high-frequency station data), then loaded via
-- scripts/weather/asos_historic_monthly_loader.py.
--
-- Same 14-column shape as the LAX/MIA tables EXCEPT this source format
-- also includes lat/lon (GIS) columns per-row, which the LAX/MIA
-- operator-staged files did not have -- confirmed from actual file
-- content 2026-07-20, not assumed from the doc's format description.
-- station_code normalized to ICAO 'KDEN' at load time (source uses IEM's
-- 3-letter 'DEN').
--
-- Run on LA:
--   sudo -u postgres psql eventhorizon -f sql/migrations/2026-07-20-asos-historic-den-tables.sql

CREATE TABLE IF NOT EXISTS weather_bronze_asos_historic_den_1min (
    id                  BIGSERIAL       PRIMARY KEY,
    station_code        TEXT            NOT NULL,       -- normalized ICAO, 'KDEN'
    observed_at         TIMESTAMPTZ     NOT NULL,        -- UTC, from source valid(UTC)
    latitude            NUMERIC,
    longitude           NUMERIC,
    air_temp_f          NUMERIC,                         -- tmpf
    dew_point_temp_f    NUMERIC,                         -- dwpf
    wind_speed_kt        NUMERIC,                         -- sknt
    wind_direction_deg  NUMERIC,                         -- drct
    gust_direction_deg  NUMERIC,                         -- gust_drct
    gust_speed_kt        NUMERIC,                         -- gust_sknt
    precip_type_code    TEXT,                            -- ptype
    precip_in           NUMERIC,                         -- precip
    pressure_1_inhg     NUMERIC,                         -- pres1
    pressure_2_inhg     NUMERIC,                         -- pres2
    pressure_3_inhg     NUMERIC,                         -- pres3
    source_file         TEXT,
    retrieved_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT weather_bronze_asos_historic_den_1min_unique
        UNIQUE (station_code, observed_at)
);

CREATE INDEX IF NOT EXISTS brasosden1m_station_observed_idx
    ON weather_bronze_asos_historic_den_1min (station_code, observed_at DESC);

CREATE TABLE IF NOT EXISTS weather_bronze_asos_historic_den_5min (
    id                  BIGSERIAL       PRIMARY KEY,
    station_code        TEXT            NOT NULL,
    observed_at         TIMESTAMPTZ     NOT NULL,
    latitude            NUMERIC,
    longitude           NUMERIC,
    air_temp_f          NUMERIC,
    dew_point_temp_f    NUMERIC,
    wind_speed_kt        NUMERIC,
    wind_direction_deg  NUMERIC,
    gust_direction_deg  NUMERIC,
    gust_speed_kt        NUMERIC,
    precip_type_code    TEXT,
    precip_in           NUMERIC,
    pressure_1_inhg     NUMERIC,
    pressure_2_inhg     NUMERIC,
    pressure_3_inhg     NUMERIC,
    source_file         TEXT,
    retrieved_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT weather_bronze_asos_historic_den_5min_unique
        UNIQUE (station_code, observed_at)
);

CREATE INDEX IF NOT EXISTS brasosden5m_station_observed_idx
    ON weather_bronze_asos_historic_den_5min (station_code, observed_at DESC);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_weather_collector') THEN
        GRANT INSERT, SELECT ON weather_bronze_asos_historic_den_1min TO bhn_weather_collector;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_asos_historic_den_1min_id_seq TO bhn_weather_collector;
        GRANT INSERT, SELECT ON weather_bronze_asos_historic_den_5min TO bhn_weather_collector;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_asos_historic_den_5min_id_seq TO bhn_weather_collector;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_trader') THEN
        GRANT SELECT, INSERT, UPDATE ON weather_bronze_asos_historic_den_1min TO bhn_trader;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_asos_historic_den_1min_id_seq TO bhn_trader;
        GRANT SELECT, INSERT, UPDATE ON weather_bronze_asos_historic_den_5min TO bhn_trader;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_asos_historic_den_5min_id_seq TO bhn_trader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'horizon_agent_reader') THEN
        GRANT SELECT ON weather_bronze_asos_historic_den_1min TO horizon_agent_reader;
        GRANT SELECT ON weather_bronze_asos_historic_den_5min TO horizon_agent_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader') THEN
        GRANT SELECT ON weather_bronze_asos_historic_den_1min TO grafana_reader;
        GRANT SELECT ON weather_bronze_asos_historic_den_5min TO grafana_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_reader') THEN
        GRANT SELECT ON weather_bronze_asos_historic_den_1min TO agent_reader;
        GRANT SELECT ON weather_bronze_asos_historic_den_5min TO agent_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'n8n_user') THEN
        GRANT SELECT ON weather_bronze_asos_historic_den_1min TO n8n_user;
        GRANT SELECT ON weather_bronze_asos_historic_den_5min TO n8n_user;
    END IF;
END $$;
