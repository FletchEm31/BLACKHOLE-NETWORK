-- Migration: partial unique index for source_name='gfs_hourly'
-- 2026-07-21
--
-- weather_bronze_nws_forecast_snapshots already stores multiple forecast
-- sources keyed by source_name (nws_gridpoints, nws_hourly), each with its
-- own partial unique index scoped to that source_name. Adding gfs_hourly
-- (Open-Meteo gfs_seamless model, hourly resolution) for the dashboard's
-- NWS+GFS 48h forecast overlay -- reuses this table instead of
-- weather_bronze_openmeteo_forecast_snapshots, which only stores a single
-- generic temperature_2m field and has no dewpoint/RH/cloud/wind/pop
-- columns needed here.
--
-- Run on LA:
--   sudo -u postgres psql eventhorizon -f sql/migrations/2026-07-21-gfs-hourly-unique-index.sql

CREATE UNIQUE INDEX IF NOT EXISTS brnws_gfshourly_unique
    ON weather_bronze_nws_forecast_snapshots (station_code, source_name, forecast_run_time, target_date, hour)
    WHERE source_name = 'gfs_hourly';
