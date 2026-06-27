-- migration_004_phase2_features.sql
-- Adds 5 Phase 2 hourly-derived feature columns to weather_gold_city_day_features.
-- These are computed by weather_edge_calculator.py from NWS hourly data per city/date
-- and consumed by the Phase 2 gradient boosting calibration model.
--
-- Run on LA: psql -U postgres -d eventhorizon -f migration_004_phase2_features.sql
-- Safe to run more than once (IF NOT EXISTS).

ALTER TABLE weather_gold_city_day_features
    ADD COLUMN IF NOT EXISTS peak_hour            INTEGER,
    ADD COLUMN IF NOT EXISTS afternoon_storm_flag BOOLEAN,
    ADD COLUMN IF NOT EXISTS pre_peak_storm_flag  BOOLEAN,
    ADD COLUMN IF NOT EXISTS cloud_timing_delta   NUMERIC,
    ADD COLUMN IF NOT EXISTS sea_breeze_flag      BOOLEAN;

COMMENT ON COLUMN weather_gold_city_day_features.peak_hour IS
    'Hour (6-20) with highest hourly tmax_f in NWS hourly forecast';
COMMENT ON COLUMN weather_gold_city_day_features.afternoon_storm_flag IS
    'TRUE if any hour 12-17 has pop_pct > 20% (caps observed high below NWS forecast)';
COMMENT ON COLUMN weather_gold_city_day_features.pre_peak_storm_flag IS
    'TRUE if any hour in [12, peak_hour) has pop_pct > 20% (strongest overforecast signal)';
COMMENT ON COLUMN weather_gold_city_day_features.cloud_timing_delta IS
    'Hours: (hour of max cloud_cover_pct, 10-20) minus peak_hour; negative = clouds before heat peak';
COMMENT ON COLUMN weather_gold_city_day_features.sea_breeze_flag IS
    'TRUE if onshore wind > 5 mph during hours 12-17; coastal cities only (KMIA/KLAX/KNYC); NULL for inland';
