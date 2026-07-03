-- 2026-07-03-fix-weather-collector-silver-grants.sql
--
-- URGENT FIX (per Fletch, top priority after tonight's table audit):
-- bhn_weather_collector (the role Hillsboro/Helsinki use for weather_data_collector.py)
-- was granted INSERT+SELECT on exactly 6 bronze/misc tables during the 2026-07-01
-- collector-node split migration, but was never granted access to the 3 silver
-- tables its own silver-populate helpers write to. Confirmed via
-- information_schema.role_table_grants: zero grants existed on any of the three
-- tables below before this migration.
--
-- Impact confirmed tonight:
--   - weather_silver_forecast_conformed: nws + open_meteo_gfs_seamless sources
--     stalled since 2026-07-01 ~06:0x UTC (47+ hrs at time of writing). Feeds
--     weather_edge_calculator.py, low_side_ledger_populator.py,
--     weather_vc_backfill.py, and the Austin/NYC/Chicago calibration countdown.
--   - weather_silver_actuals_conformed: nws_cli actual_source stalled since
--     2026-06-29/06-30 (same _populate_silver_actuals() call, same role).
--   - weather_silver_forecast_error: same function inserts here immediately
--     after actuals, same failure mode, not yet independently confirmed stale
--     but almost certainly affected — included preemptively.
--
-- All three failures are non-fatal by design (weather_data_collector.py logs a
-- warning and continues; bronze writes always succeed) — which is exactly why
-- this went unnoticed for 2-3 days: no crash, no alert, just silently missing
-- silver rows.
--
-- Run on LA:
--   psql -U postgres eventhorizon -f sql/migrations/2026-07-03-fix-weather-collector-silver-grants.sql

\set ON_ERROR_STOP on

BEGIN;

GRANT INSERT, UPDATE, SELECT ON weather_silver_forecast_conformed TO bhn_weather_collector;
GRANT INSERT, UPDATE, SELECT ON weather_silver_actuals_conformed  TO bhn_weather_collector;
GRANT INSERT, UPDATE, SELECT ON weather_silver_forecast_error     TO bhn_weather_collector;

GRANT USAGE ON SEQUENCE weather_silver_forecast_conformed_id_seq TO bhn_weather_collector;
GRANT USAGE ON SEQUENCE weather_silver_actuals_conformed_id_seq  TO bhn_weather_collector;
GRANT USAGE ON SEQUENCE weather_silver_forecast_error_id_seq     TO bhn_weather_collector;

\echo 'bhn_weather_collector granted INSERT/UPDATE/SELECT on 3 silver tables + sequence USAGE.'

COMMIT;
