-- Migration: correct the KDEN 2026-06-11 bronze/silver actuals divergence
-- 2026-07-20
--
-- Root cause investigation (see WEATHERBHN-KDEN-BRONZE-SILVER-DIVERGENCE-
-- SCOPING-2026-07-20.md): exhaustive search (every writer of
-- actual_source='nws_cli', git history around the exact date, search for
-- deleted backfill scripts) found no reproducible bug in currently-
-- deployed code. weather_data_collector.py's fetch_nws_actuals() writes
-- bronze and silver from the SAME parsed value in one atomic call, gated
-- by bronze's ON CONFLICT DO NOTHING is_new flag -- structurally
-- incapable of producing a bronze/silver value mismatch under the
-- current code. This looks like a one-off manual intervention during the
-- project's active buildout (2026-06-12 was a heavy development day per
-- commit c180a58, though that commit's content doesn't touch this path
-- and postdates the divergent row).
--
-- This is therefore a DATA correction, not a code fix. Validated via two
-- independent sources agreeing: weather_bronze_nws_actuals (77.0F,
-- report_issued_at 2026-06-12 07:33 UTC) and NOAA daily actuals (77.0F,
-- station USW00003017). Silver had 90.0F attributed to a report_issued_at
-- (23:29 UTC) with no bronze counterpart.
--
-- This same date/station is one of the two confirmed real settlement-
-- outcome flips found this session (KXHIGHDEN-26JUN11-T83, threshold
-- >83: settled YES using the wrong 90.0F, true value 77.0F settles NO).
--
-- Run on LA:
--   sudo -u postgres psql eventhorizon -f sql/migrations/2026-07-20-fix-kden-0611-silver-divergence.sql

BEGIN;

UPDATE weather_silver_actuals_conformed
SET final_tmax_f = 77.0,
    report_issued_at = '2026-06-12 07:33:00+00'
WHERE station_code = 'KDEN'
  AND target_date = '2026-06-11'
  AND actual_source = 'nws_cli'
  AND final_tmax_f = 90.0;

COMMIT;

-- Verification:
--   SELECT final_tmax_f, report_issued_at FROM weather_silver_actuals_conformed
--     WHERE station_code='KDEN' AND target_date='2026-06-11' AND actual_source='nws_cli';
--   -- expect final_tmax_f = 77.0
