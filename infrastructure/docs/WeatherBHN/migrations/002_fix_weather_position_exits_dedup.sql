-- WeatherBHN — Fix weather_position_exits deduplication
-- Root cause: ON CONFLICT (contract_ticker, decision_timestamp) DO NOTHING never
-- fired because decision_timestamp = NOW() is always fresh, so a new row
-- accumulated every orchestrator cycle (~5 min per qualifying bucket).
--
-- Fix: replace the per-(ticker, timestamp) uniqueness with a per-ticker uniqueness.
-- exit_audit_logger.py now uses DO UPDATE to refresh signal cols each cycle while
-- a WHERE scored_at IS NULL guard prevents overwriting any row the scorer has
-- already settled.
--
-- Run on LA as a user with ALTER TABLE + DELETE rights on weather_position_exits:
--   psql -U postgres eventhorizon -f 002_fix_weather_position_exits_dedup.sql

BEGIN;

-- 1. Deduplicate existing rows: keep the scored row if one exists,
--    otherwise keep the most recent unsettled signal.
DELETE FROM weather_position_exits
WHERE id NOT IN (
    SELECT DISTINCT ON (contract_ticker) id
    FROM weather_position_exits
    ORDER BY contract_ticker,
             scored_at  NULLS LAST,     -- scored rows survive first
             decision_timestamp DESC     -- then most recent signal
);

-- 2. Drop the old per-cycle constraint.
ALTER TABLE weather_position_exits
    DROP CONSTRAINT IF EXISTS uq_exit_ticker_decision;

-- 3. Enforce one row per contract ticker.
ALTER TABLE weather_position_exits
    ADD CONSTRAINT weather_position_exits_contract_ticker_key
    UNIQUE (contract_ticker);

COMMIT;
