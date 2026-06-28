-- Migration: add weather_bronze_era5_kmia
-- 2026-06-28
--
-- ERA5 reanalysis bronze table for KMIA (Miami International).
-- Dataset: reanalysis-era5-single-levels, 13 variables, 2025-2026.
-- Bounding box: lat 25.5-26 / lon -80.5 to -80.
-- One row per (valid_time, latitude, longitude).
-- Data format: GRIB, downloaded via cdsapi once CDS job completes.
-- Ingestion: scripts/weather/weather_era5_kmia_ingest.py --file <grib/zip>
--
-- APPLIED: 2026-06-28 (DDL applied directly on LA before this file was committed)
-- Run on LA (<BHN_WG_LA_IP>):
--   sudo -u postgres psql -d eventhorizon -f sql/migrations/2026-06-28-era5-kmia-bronze.sql

CREATE TABLE IF NOT EXISTS weather_bronze_era5_kmia (
    id              BIGSERIAL PRIMARY KEY,
    valid_time      TIMESTAMPTZ NOT NULL,
    latitude        NUMERIC(7,4) NOT NULL,
    longitude       NUMERIC(7,4) NOT NULL,

    -- Atmospheric variables (cfgrib short names)
    u10             DOUBLE PRECISION,   -- 10m u-wind component (m/s)
    v10             DOUBLE PRECISION,   -- 10m v-wind component (m/s)
    d2m             DOUBLE PRECISION,   -- 2m dewpoint temperature (K)
    t2m             DOUBLE PRECISION,   -- 2m temperature (K)
    msl             DOUBLE PRECISION,   -- mean sea level pressure (Pa)
    sp              DOUBLE PRECISION,   -- surface pressure (Pa)
    tp              DOUBLE PRECISION,   -- total precipitation (m, accumulated)
    tcc             DOUBLE PRECISION,   -- total cloud cover (0-1)
    cbh             DOUBLE PRECISION,   -- cloud base height (m)

    -- Ocean/wave variables (NULL where land)
    mwd             DOUBLE PRECISION,   -- mean wave direction (degrees)
    mwp             DOUBLE PRECISION,   -- mean wave period (s)
    sst             DOUBLE PRECISION,   -- sea surface temperature (K)
    swh             DOUBLE PRECISION,   -- significant height combined wind waves + swell (m)

    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT weather_bronze_era5_kmia_uq UNIQUE (valid_time, latitude, longitude)
);

CREATE INDEX IF NOT EXISTS idx_era5_kmia_valid_time
    ON weather_bronze_era5_kmia (valid_time);

-- Permissions
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_trader') THEN
        GRANT SELECT, INSERT, UPDATE ON weather_bronze_era5_kmia TO bhn_trader;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_era5_kmia_id_seq TO bhn_trader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ehuser') THEN
        GRANT SELECT, INSERT, UPDATE ON weather_bronze_era5_kmia TO ehuser;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_era5_kmia_id_seq TO ehuser;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader') THEN
        GRANT SELECT ON weather_bronze_era5_kmia TO grafana_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'horizon_agent_reader') THEN
        GRANT SELECT ON weather_bronze_era5_kmia TO horizon_agent_reader;
    END IF;
END $$;
