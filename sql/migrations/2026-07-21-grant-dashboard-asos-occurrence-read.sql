-- Migration: grant weatherbhn_dashboard read access to
-- weather_silver_asos_daily_high_low_occurrence
-- 2026-07-21
--
-- The WeatherBHN dashboard app (app/db.py) runs as the weatherbhn_dashboard
-- role, which per its own docstring only has SELECT on
-- weather_bronze_kalshi_market_snapshots / weather_position_exits_clean /
-- weather_station_climatology / weather_gold_city_day_features. None of
-- those have per-day time-of-occurrence data for the new LAX/MIA
-- climatology page -- that lives only in
-- weather_silver_asos_daily_high_low_occurrence (built earlier this
-- session from the full 1min ASOS archive, 5,796 rows KLAX / 5,478 rows
-- KMIA, 2010-2026). SELECT-only grant, same pattern as every other
-- reader-role grant this session.
--
-- Run on LA:
--   sudo -u postgres psql eventhorizon -f sql/migrations/2026-07-21-grant-dashboard-asos-occurrence-read.sql

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'weatherbhn_dashboard') THEN
        GRANT SELECT ON weather_silver_asos_daily_high_low_occurrence TO weatherbhn_dashboard;
    END IF;
END $$;
