-- Migration 002: Add wind_direction_deg to NWS hourly bronze
--               Add 5 derived features to gold edge sheet
-- Applied: 2026-06-13
-- Requires: migration 001 already applied

-- Bronze: add wind_direction_deg for sea breeze detection
ALTER TABLE weather_bronze_nws_forecast_snapshots
    ADD COLUMN IF NOT EXISTS wind_direction_deg NUMERIC;

-- Gold: add 5 hourly-derived feature columns
ALTER TABLE weather_gold_daily_edge_sheet
    ADD COLUMN IF NOT EXISTS peak_hour            INTEGER;

ALTER TABLE weather_gold_daily_edge_sheet
    ADD COLUMN IF NOT EXISTS afternoon_storm_flag  BOOLEAN;

ALTER TABLE weather_gold_daily_edge_sheet
    ADD COLUMN IF NOT EXISTS pre_peak_storm_flag   BOOLEAN;

ALTER TABLE weather_gold_daily_edge_sheet
    ADD COLUMN IF NOT EXISTS cloud_timing_delta    NUMERIC;

ALTER TABLE weather_gold_daily_edge_sheet
    ADD COLUMN IF NOT EXISTS sea_breeze_flag       BOOLEAN;

-- Verify
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'weather_gold_daily_edge_sheet'
  AND column_name IN ('peak_hour','afternoon_storm_flag','pre_peak_storm_flag',
                      'cloud_timing_delta','sea_breeze_flag')
ORDER BY column_name;
