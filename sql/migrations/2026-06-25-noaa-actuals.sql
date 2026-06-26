-- Migration: NOAA GHCND daily actuals + hourly normals bronze tables
-- Run from LA: sudo -u postgres psql -d eventhorizon -f sql/migrations/2026-06-25-noaa-actuals.sql
-- Snapshot first: sudo -u postgres pg_dump eventhorizon > /mnt/eh-nvme-hot/backups/pre-noaa-actuals-$(date +%Y%m%d-%H%M).sql
--
-- Source data: NOAA Climate Data Online (CDO), GHCND daily summaries
-- Stations: KMIA, KORD, KDEN, KLAX, KJFK (daily); KMIA, KAFF (hourly normals 1981-2010)
-- Units: °F for temps, inches for precip/snow, mph for wind, % for sunshine
-- Loader: scripts/load-noaa-actuals.py

BEGIN;

-- ============================================================
-- TABLE 1: weather_bronze_noaa_daily_actuals
-- One row per station per calendar date. Historical GHCND data
-- supersedes Visual Crossing for calibration purposes.
-- ============================================================

CREATE TABLE IF NOT EXISTS weather_bronze_noaa_daily_actuals (
    id              BIGSERIAL PRIMARY KEY,

    -- Station identity
    station_id      TEXT NOT NULL,          -- NOAA GHCND station ID e.g. USW00012839
    icao_code       TEXT NOT NULL,          -- ICAO airport code e.g. KMIA
    station_name    TEXT NOT NULL,          -- NAME field from CDO download

    -- Observation date
    date            DATE NOT NULL,

    -- Temperature (°F)
    tmax_f          NUMERIC,                -- TMAX: daily maximum temperature
    tmin_f          NUMERIC,               -- TMIN: daily minimum temperature
    tavg_f          NUMERIC,               -- TAVG: daily average temperature

    -- Precipitation / snow (inches)
    prcp_in         NUMERIC,               -- PRCP: total precipitation (trace = 0.00)
    snow_in         NUMERIC,               -- SNOW: snowfall
    snwd_in         NUMERIC,               -- SNWD: snow depth at observation time

    -- Wind (mph)
    awnd_mph        NUMERIC,               -- AWND: average daily wind speed
    wsf2_mph        NUMERIC,               -- WSF2: fastest 2-minute sustained wind
    wsf5_mph        NUMERIC,               -- WSF5: fastest 5-second wind gust
    wsfg_mph        NUMERIC,               -- WSFG: peak wind gust

    -- Sunshine
    psun_pct        NUMERIC,               -- PSUN: percent of possible sunshine (0-100)

    -- Metadata
    loaded_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT noaa_daily_station_date_unique UNIQUE (station_id, date)
);

CREATE INDEX IF NOT EXISTS noaa_daily_icao_date_idx
    ON weather_bronze_noaa_daily_actuals (icao_code, date);

CREATE INDEX IF NOT EXISTS noaa_daily_date_idx
    ON weather_bronze_noaa_daily_actuals (date DESC);

CREATE INDEX IF NOT EXISTS noaa_daily_icao_date_tmax_idx
    ON weather_bronze_noaa_daily_actuals (icao_code, date, tmax_f, tmin_f)
    WHERE tmax_f IS NOT NULL;


-- ============================================================
-- TABLE 2: weather_bronze_noaa_hourly_normals
-- 1981-2010 climatological hourly normals from CDO.
-- No calendar year — indexed by month/day/hour.
-- Stations: USW00012839 (KMIA), USW00023036 (KAFF / Aurora Buckley AFB)
-- NOTE: Buckley AFB (KAFF) ≠ Denver International (KDEN/USW00003017).
-- ============================================================

CREATE TABLE IF NOT EXISTS weather_bronze_noaa_hourly_normals (
    id              BIGSERIAL PRIMARY KEY,

    -- Station identity
    station_id      TEXT NOT NULL,          -- NOAA station ID
    icao_code       TEXT NOT NULL,          -- ICAO code
    station_name    TEXT NOT NULL,

    -- Time index (no year — climatological normals)
    month_day       TEXT NOT NULL,          -- "01-01" through "12-31"
    hour            INTEGER NOT NULL,       -- 1-24 as published by CDO (24 = midnight end)
    normal_date_str TEXT NOT NULL,          -- original CDO value e.g. "01-01T01:00:00"

    -- Temperature normals (°F)
    temp_normal_f   NUMERIC,               -- HLY-TEMP-NORMAL
    temp_10pct_f    NUMERIC,               -- HLY-TEMP-10PCTL
    temp_90pct_f    NUMERIC,               -- HLY-TEMP-90PCTL

    -- Dew point normals (°F)
    dewp_normal_f   NUMERIC,               -- HLY-DEWP-NORMAL
    dewp_10pct_f    NUMERIC,               -- HLY-DEWP-10PCTL
    dewp_90pct_f    NUMERIC,               -- HLY-DEWP-90PCTL

    -- Heat index / wind chill normals (°F)
    hidx_normal_f   NUMERIC,               -- HLY-HIDX-NORMAL (heat index)
    wchl_normal_f   NUMERIC,               -- HLY-WCHL-NORMAL (wind chill)

    -- Pressure normals (tenths of hPa; "1" = insufficient data per CDO encoding)
    pres_normal     NUMERIC,               -- HLY-PRES-NORMAL
    pres_10pct      NUMERIC,               -- HLY-PRES-10PCTL
    pres_90pct      NUMERIC,               -- HLY-PRES-90PCTL

    -- Wind normals (speed in mph; direction in degrees)
    wind_avgspd_mph NUMERIC,               -- HLY-WIND-AVGSPD
    wind_vctdir_deg NUMERIC,               -- HLY-WIND-VCTDIR (vector direction)
    wind_vctspd_mph NUMERIC,               -- HLY-WIND-VCTSPD
    wind_1stdir     INTEGER,               -- HLY-WIND-1STDIR (primary direction degrees)
    wind_1stpct     NUMERIC,               -- HLY-WIND-1STPCT (% from primary direction)
    wind_2nddir     INTEGER,               -- HLY-WIND-2NDDIR
    wind_2ndpct     NUMERIC,               -- HLY-WIND-2NDPCT
    wind_pctclm     NUMERIC,               -- HLY-WIND-PCTCLM (percent calm)

    -- Sky cover (percent in each category)
    clod_pct_clr    NUMERIC,               -- HLY-CLOD-PCTCLR
    clod_pct_few    NUMERIC,               -- HLY-CLOD-PCTFEW
    clod_pct_sct    NUMERIC,               -- HLY-CLOD-PCTSCT
    clod_pct_bkn    NUMERIC,               -- HLY-CLOD-PCTBKN
    clod_pct_ovc    NUMERIC,               -- HLY-CLOD-PCTOVC

    -- Degree-hour accumulations
    cldh_normal     NUMERIC,               -- HLY-CLDH-NORMAL (cooling degree hours)
    htdh_normal     NUMERIC,               -- HLY-HTDH-NORMAL (heating degree hours)

    -- Metadata
    loaded_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT noaa_hourly_normals_unique UNIQUE (station_id, month_day, hour)
);

CREATE INDEX IF NOT EXISTS noaa_hourly_normals_icao_idx
    ON weather_bronze_noaa_hourly_normals (icao_code, month_day, hour);


-- ============================================================
-- GRANTS
-- ============================================================

GRANT SELECT ON weather_bronze_noaa_daily_actuals TO grafana_reader;
GRANT SELECT ON weather_bronze_noaa_daily_actuals TO agent_reader;
GRANT SELECT, INSERT, UPDATE, DELETE ON weather_bronze_noaa_daily_actuals TO ehuser;
GRANT USAGE, SELECT ON SEQUENCE weather_bronze_noaa_daily_actuals_id_seq TO ehuser;

GRANT SELECT ON weather_bronze_noaa_hourly_normals TO grafana_reader;
GRANT SELECT ON weather_bronze_noaa_hourly_normals TO agent_reader;
GRANT SELECT, INSERT, UPDATE, DELETE ON weather_bronze_noaa_hourly_normals TO ehuser;
GRANT USAGE, SELECT ON SEQUENCE weather_bronze_noaa_hourly_normals_id_seq TO ehuser;

COMMIT;

-- ============================================================
-- VERIFY
-- ============================================================
-- \d weather_bronze_noaa_daily_actuals
-- \d weather_bronze_noaa_hourly_normals
-- SELECT grantee, privilege_type FROM information_schema.role_table_grants
--   WHERE table_name IN ('weather_bronze_noaa_daily_actuals', 'weather_bronze_noaa_hourly_normals')
--   ORDER BY table_name, grantee;
