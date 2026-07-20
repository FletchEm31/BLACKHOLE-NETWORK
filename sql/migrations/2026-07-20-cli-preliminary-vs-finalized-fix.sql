-- Migration: fix CLI preliminary-vs-finalized report handling (thread 2 of 2)
-- 2026-07-20
--
-- See WEATHERBHN-CLI-PRELIMINARY-VS-FINALIZED-SCOPING-2026-07-20.md.
-- fetch_nws_actuals() set is_final=TRUE unconditionally in silver;
-- confirmed via raw product text that 95% of KLAX's genuine CLI rows
-- were preliminary same-day reports ("VALID TODAY AS OF 0500 PM LOCAL
-- TIME"), not the true finalized report NWS issues ~1:40am the following
-- morning.
--
-- Adds is_final to bronze (didn't exist there at all -- only silver had
-- it) so a preliminary report can be safely upgraded to a finalized one
-- once it's actually fetched, instead of bronze's ON CONFLICT DO NOTHING
-- permanently locking in whichever report happened to be captured first.
--
-- Existing rows default to TRUE (preserves current read behavior for
-- historical data we can't retroactively re-classify without re-fetching
-- NWS's product archive -- that's a separate backfill decision, not made
-- here) -- going forward, new writes set this properly via product-text
-- detection.
--
-- Run on LA:
--   sudo -u postgres psql eventhorizon -f sql/migrations/2026-07-20-cli-preliminary-vs-finalized-fix.sql

ALTER TABLE weather_bronze_nws_actuals
    ADD COLUMN IF NOT EXISTS is_final BOOLEAN NOT NULL DEFAULT TRUE;

-- Verification:
--   \d weather_bronze_nws_actuals  -- confirm is_final column exists

-- Grant UPDATE on weather_bronze_nws_actuals to bhn_weather_collector --
-- the old ON CONFLICT DO NOTHING never needed it (never modified an
-- existing row); the new ON CONFLICT DO UPDATE (to allow upgrading a
-- preliminary report to finalized) does. Found by testing live on
-- Hillsboro immediately after deploying -- caught before it could sit
-- silently broken (bronze/silver writes failing with permission denied,
-- same failure class flagged as a standing risk in this project's
-- commit-before-deploy rule).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_weather_collector') THEN
        GRANT UPDATE ON weather_bronze_nws_actuals TO bhn_weather_collector;
    END IF;
END $$;
