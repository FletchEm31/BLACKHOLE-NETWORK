-- migration_003_raw_payload.sql
-- Adds raw_payload JSONB column to two NWS bronze tables.
-- Purpose: preserve the full API response for future re-parsing
--          without re-fetching from NWS (useful when schema changes
--          or new fields need to be extracted retroactively).
--
-- Run on LA: psql -U ehuser -d bhn -f migration_003_raw_payload.sql
-- Safe to run more than once (IF NOT EXISTS).

ALTER TABLE weather_bronze_nws_hourly
    ADD COLUMN IF NOT EXISTS raw_payload JSONB;

ALTER TABLE weather_bronze_nws_forecasts
    ADD COLUMN IF NOT EXISTS raw_payload JSONB;
