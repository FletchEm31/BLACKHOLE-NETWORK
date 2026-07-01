-- 004_add_real_market_ticker.sql
-- Add real_market_ticker column to weather_position_exits.
-- Also updates contract_ticker on existing unscored rows to the real Kalshi
-- ticker so the ON CONFLICT (contract_ticker) key continues to match correctly
-- after cp4_kelly_sizer.py is updated to write real tickers.
--
-- Run on LA:
--   cat /root/migrations/004_add_real_market_ticker.sql | sudo -u postgres psql eventhorizon

BEGIN;

-- 1. Add column
ALTER TABLE weather_position_exits
    ADD COLUMN IF NOT EXISTS real_market_ticker TEXT;

COMMENT ON COLUMN weather_position_exits.real_market_ticker IS
    'Kalshi-native market_ticker (e.g. KXHIGHLAX-26JUL01-B71.5) looked up from '
    'weather_bronze_kalshi_market_snapshots. Never constructed synthetically. '
    'See WEATHERBHN-TICKER-ARCHITECTURE.md.';

-- 2. Backfill real_market_ticker for existing 9 rows from most recent snapshot
UPDATE weather_position_exits e
SET real_market_ticker = snap.market_ticker
FROM (
    SELECT DISTINCT ON (station_code, bucket_label, target_date)
        station_code, bucket_label, target_date, market_ticker
    FROM weather_bronze_kalshi_market_snapshots
    WHERE market_ticker IS NOT NULL
    ORDER BY station_code, bucket_label, target_date, retrieved_at DESC
) snap
WHERE e.station_code = snap.station_code
  AND e.bucket_label  = snap.bucket_label
  AND e.target_date   = snap.target_date;

-- 3. Update contract_ticker on unscored rows to match the real Kalshi ticker.
--    This re-anchors the UNIQUE constraint so future ON CONFLICT (contract_ticker)
--    cycles match correctly once CP4 writes real tickers going forward.
UPDATE weather_position_exits
SET contract_ticker = real_market_ticker
WHERE scored_at IS NULL
  AND real_market_ticker IS NOT NULL;

-- 4. Verify
SELECT
    station_code,
    target_date,
    bucket_label,
    contract_ticker        AS new_contract_ticker,
    real_market_ticker
FROM weather_position_exits
ORDER BY target_date, station_code, bucket_label;

COMMIT;
