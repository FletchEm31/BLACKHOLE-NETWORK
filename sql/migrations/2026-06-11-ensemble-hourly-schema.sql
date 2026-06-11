-- Migration: 2026-06-11-ensemble-hourly-schema
-- Schema additions for Task 9: Open-Meteo Ensemble + NWS Hourly sources.
--
-- Apply:
--   sudo -u postgres psql -d eventhorizon -P pager=off -f 2026-06-11-ensemble-hourly-schema.sql
--
-- Rollback (order matters):
--   ALTER TABLE weather_gold_daily_edge_sheet       DROP COLUMN IF EXISTS ensemble_spread;
--   ALTER TABLE weather_silver_model_base           DROP COLUMN IF EXISTS ensemble_spread_tmax, DROP COLUMN IF EXISTS ensemble_spread_tmin;
--   ALTER TABLE weather_bronze_openmeteo_forecast_snapshots DROP COLUMN IF EXISTS tmax_f, DROP COLUMN IF EXISTS tmin_f, DROP COLUMN IF EXISTS ensemble_spread_tmax, DROP COLUMN IF EXISTS ensemble_spread_tmin;
--   ALTER TABLE weather_bronze_nws_forecast_snapshots DROP COLUMN IF EXISTS hour, DROP COLUMN IF EXISTS source_name;
--   DROP INDEX IF EXISTS brnws_daily_unique;
--   DROP INDEX IF EXISTS brnws_hourly_unique;
--   ALTER TABLE weather_bronze_nws_forecast_snapshots ADD CONSTRAINT weather_bronze_nws_unique UNIQUE (station_code, forecast_run_time, target_date);

BEGIN;

-- ── NWS forecast: add hour + source_name; replace simple unique with two partial indexes ──
ALTER TABLE weather_bronze_nws_forecast_snapshots
  ADD COLUMN IF NOT EXISTS hour         INTEGER,
  ADD COLUMN IF NOT EXISTS source_name  TEXT DEFAULT 'nws_gridpoints';

-- Backfill existing daily rows
UPDATE weather_bronze_nws_forecast_snapshots
   SET source_name = 'nws_gridpoints'
 WHERE source_name IS NULL;

-- Replace old single unique constraint with source-aware partial indexes
ALTER TABLE weather_bronze_nws_forecast_snapshots
  DROP CONSTRAINT IF EXISTS weather_bronze_nws_unique;

CREATE UNIQUE INDEX IF NOT EXISTS brnws_daily_unique
  ON weather_bronze_nws_forecast_snapshots (station_code, forecast_run_time, target_date)
  WHERE source_name = 'nws_gridpoints';

CREATE UNIQUE INDEX IF NOT EXISTS brnws_hourly_unique
  ON weather_bronze_nws_forecast_snapshots (station_code, source_name, forecast_run_time, target_date, hour)
  WHERE source_name = 'nws_hourly';

-- ── Open-Meteo bronze: add ensemble aggregate columns ──
-- tmax_f / tmin_f store ensemble mean for model='open_meteo_ensemble' rows (hour=-1 sentinel)
ALTER TABLE weather_bronze_openmeteo_forecast_snapshots
  ADD COLUMN IF NOT EXISTS tmax_f                NUMERIC,
  ADD COLUMN IF NOT EXISTS tmin_f                NUMERIC,
  ADD COLUMN IF NOT EXISTS ensemble_spread_tmax  NUMERIC,
  ADD COLUMN IF NOT EXISTS ensemble_spread_tmin  NUMERIC;

-- ── Silver model base: ensemble spread for calibration training ──
ALTER TABLE weather_silver_model_base
  ADD COLUMN IF NOT EXISTS ensemble_spread_tmax  NUMERIC,
  ADD COLUMN IF NOT EXISTS ensemble_spread_tmin  NUMERIC;

-- ── Gold edge sheet: surface ensemble spread for Metabase visibility ──
ALTER TABLE weather_gold_daily_edge_sheet
  ADD COLUMN IF NOT EXISTS ensemble_spread  NUMERIC;

-- ── Grants ──
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_trader') THEN
        -- No new tables; column additions inherit existing table grants
    END IF;
END $$;

COMMIT;
