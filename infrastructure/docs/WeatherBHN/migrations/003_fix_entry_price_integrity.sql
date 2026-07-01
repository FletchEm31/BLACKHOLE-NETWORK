-- 003_fix_entry_price_integrity.sql
-- Add entry_no_ask_cents and entry_captured_at to weather_position_exits.
--
-- These columns lock in the true first-capture price and timestamp for each
-- position. They are set on INSERT and excluded from the ON CONFLICT DO UPDATE
-- SET list in exit_audit_logger.py — meaning subsequent orchestrator cycles
-- can refresh the live signal fields (no_ask_cents, edge_cents, etc.) without
-- ever overwriting what we paid to enter.
--
-- Background: migration 002 deduped rows using ORDER BY decision_timestamp DESC
-- (kept most recent) instead of ASC (keep oldest). The original first-capture
-- data is gone. This migration backfills entry_no_ask_cents / entry_captured_at
-- from the earliest qualifying snapshot in weather_bronze_kalshi_market_snapshots
-- where no_ask was in a tradeable range (≥3¢, ≤92¢).
--
-- Run on LA:
--   cat /root/migrations/003_fix_entry_price_integrity.sql | sudo -u postgres psql eventhorizon

BEGIN;

-- ── 1. Add columns ────────────────────────────────────────────────────────────
ALTER TABLE weather_position_exits
    ADD COLUMN IF NOT EXISTS entry_no_ask_cents NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS entry_captured_at  TIMESTAMPTZ;

COMMENT ON COLUMN weather_position_exits.entry_no_ask_cents IS
    'no_ask price (cents) at first signal capture — never overwritten by subsequent orchestrator cycles';
COMMENT ON COLUMN weather_position_exits.entry_captured_at IS
    'timestamp of first signal capture — never overwritten by subsequent orchestrator cycles';

-- ── 2. Backfill existing 9 open rows from earliest qualifying snapshot ────────
-- "Qualifying" = no_ask in tradeable range (≥3¢ above thin floor, ≤92¢ below
-- near-certain). This is the earliest point in time at which our model would
-- have considered the contract worth tracking.
UPDATE weather_position_exits e
SET
    entry_no_ask_cents = round(snap.no_ask * 100, 2),
    entry_captured_at  = snap.retrieved_at
FROM (
    SELECT DISTINCT ON (s.station_code, s.bucket_label, s.target_date)
        s.station_code,
        s.bucket_label,
        s.target_date,
        s.no_ask,
        s.retrieved_at
    FROM weather_bronze_kalshi_market_snapshots s
    WHERE s.no_ask IS NOT NULL
      AND s.no_ask >= 0.03   -- above MIN_NO_ASK_CENTS floor
      AND s.no_ask <= 0.92   -- below near-certain threshold
    ORDER BY s.station_code, s.bucket_label, s.target_date, s.retrieved_at ASC
) snap
WHERE e.station_code = snap.station_code
  AND e.bucket_label  = snap.bucket_label
  AND e.target_date   = snap.target_date
  AND e.scored_at IS NULL;

-- ── 3. Verify backfill ────────────────────────────────────────────────────────
SELECT
    station_code,
    target_date,
    bucket_label,
    no_ask_cents          AS current_no_ask_cents,
    entry_no_ask_cents,
    entry_captured_at,
    decision_timestamp    AS last_updated_at
FROM weather_position_exits
WHERE scored_at IS NULL
ORDER BY target_date, station_code, bucket_label;

COMMIT;
