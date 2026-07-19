-- Migration: add plotly_studio_reader role
-- 2026-07-19
--
-- Dedicated SELECT-only PG role for the upcoming Plotly Studio-generated
-- Dash analytics app on LA. This is what the Dash app's DB connection
-- authenticates as -- deliberately NOT bhn_trader or any role with write
-- access. Scoped to the live weather_* analytics surface (bronze/silver/
-- gold + position/PNL views + calibration/climatology), same "read-only,
-- narrowest useful grant" pattern as horizon_agent_reader/grafana_reader.
--
-- Deliberately excluded:
--   weather_gold_daily_edge_sheet, weather_model_accuracy
--     -- retired/frozen 2026-07-02, see
--        project_weather_gold_edge_sheet_retired_2026-07-02 notes; live
--        table is weather_gold_contract_ledger.
--   weather_partition_backfill_state, weather_table_archive_log,
--   weather_silver_market_archive_log
--     -- internal bookkeeping, not analytics-relevant.
--   weatherbhn_dashboard_journal, weatherbhn_dashboard_notes
--     -- private read/write state for the OTHER dashboard
--        (bhn-weatherbhn-dashboard on :8098), not this one.
--   weather_bronze_kalshi_market_snapshots_* partition children
--     -- SELECT on the parent table covers partition-routed queries in
--        PG 14; no need to grant on each child individually.
--
-- Set the real login password directly on LA after applying this
-- (psql \password plotly_studio_reader) -- never commit it. Record it in
-- Proton Pass as "BHN-PlotlyStudioReader-PG", same convention as every
-- other collector/dashboard credential in this repo.
--
-- Run on LA:
--   sudo -u postgres psql eventhorizon -f sql/migrations/2026-07-19-plotly-studio-reader-role.sql

\set ON_ERROR_STOP on

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'plotly_studio_reader') THEN
        CREATE ROLE plotly_studio_reader LOGIN PASSWORD 'changeme-set-real-password-after-apply';
    END IF;
END $$;

GRANT SELECT ON
    -- Bronze
    weather_bronze_synoptic_asos,
    weather_bronze_nws_actuals,
    weather_bronze_nws_forecast_snapshots,
    weather_bronze_openmeteo_forecast_snapshots,
    weather_bronze_nbm_snapshots,
    weather_bronze_kalshi_market_snapshots,
    weather_bronze_visual_crossing_actuals,
    weather_bronze_era5_kmia,
    weather_bronze_era5_klax,
    weather_bronze_noaa_daily_actuals,
    weather_bronze_noaa_hourly_normals,
    -- Silver
    weather_silver_actuals_conformed,
    weather_silver_forecast_conformed,
    weather_silver_forecast_error,
    weather_silver_market_conformed,
    weather_silver_calibration_training_set,
    -- Gold
    weather_gold_city_day_features,
    weather_gold_contract_ledger,
    weather_gold_contract_ledger_performance,
    weather_gold_calibrated_probabilities,
    -- Position / PNL
    weather_position_exits,
    weather_position_exits_clean,
    weather_open_positions,
    weather_paper_pnl_dashboard,
    weather_paper_trading_summary,
    -- Calibration / climatology / catalog / misc analytics
    weather_model_calibration_daily,
    weather_station_climatology,
    weather_kalshi_contract_catalog,
    weather_commodity_signals,
    weather_bets,
    weather_snapshots,
    weather_observations,
    weather_forecasts
TO plotly_studio_reader;

COMMIT;

\echo 'plotly_studio_reader created/confirmed with SELECT on the live weather_* analytics surface.'
\echo 'REMINDER: set a real password now -- ALTER ROLE plotly_studio_reader PASSWORD <real-password>;'

-- Verification checklist (run before wiring the Dash app's connection string):
--   1. \du plotly_studio_reader                     -- role exists, LOGIN, no superuser/createdb
--   2. SELECT grantee, privilege_type FROM information_schema.role_table_grants
--        WHERE grantee = 'plotly_studio_reader' ORDER BY table_name;
--      -- every row must show only SELECT -- if INSERT/UPDATE/DELETE ever
--         shows up here, something granted it outside this migration; fix it.
--   3. Confirm the password was rotated off the placeholder:
--        SELECT rolpassword IS NOT NULL FROM pg_authid WHERE rolname='plotly_studio_reader';
--      (rolpassword itself isn't human-readable; this is just confirming
--       ALTER ROLE ... PASSWORD was actually run, not a placeholder check)
