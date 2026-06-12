-- Backfill last_price, volume, open_interest from source_payload_json for
-- rows where those columns are NULL.
--
-- IMPORTANT: yes_bid / yes_ask are NOT in the Kalshi /markets list response.
-- They come from the orderbook endpoint, which was not stored in the original
-- snapshots. Those columns cannot be backfilled here; they will be populated
-- for all new snapshots by the updated poller.
--
-- Kalshi v2 API: bid/ask are integer cents (1–99); volume and open_interest
-- are plain contract-count integers.  last_price is integer cents.
--
-- Deploy:
--   scp sql/migrations/2026-06-12-backfill-kalshi-bronze-prices.sql root@10.8.0.1:/tmp/
--   sudo -u postgres psql -d eventhorizon -f /tmp/2026-06-12-backfill-kalshi-bronze-prices.sql

BEGIN;

-- ── Diagnostic: show what the API actually stored ─────────────────────────────
\echo ''
\echo '=== Source payload field sample (most recent 3 rows with payload) ==='
SELECT
    market_ticker,
    source_payload_json->>'yes_bid'        AS raw_yes_bid,
    source_payload_json->>'yes_ask'        AS raw_yes_ask,
    source_payload_json->>'last_price'     AS raw_last_price,
    source_payload_json->>'volume'         AS raw_volume,
    source_payload_json->>'volume_24h'     AS raw_volume_24h,
    source_payload_json->>'open_interest'  AS raw_open_interest
FROM weather_bronze_kalshi_market_snapshots
WHERE source_payload_json IS NOT NULL
ORDER BY id DESC
LIMIT 3;

-- ── Backfill last_price, volume, open_interest ────────────────────────────────
UPDATE weather_bronze_kalshi_market_snapshots
SET
    last_price = CASE
        WHEN (source_payload_json->>'last_price')::numeric > 0
        THEN (source_payload_json->>'last_price')::numeric / 100.0
        ELSE NULL
    END,

    -- volume: prefer volume_24h key; fall back to volume
    volume = COALESCE(
        (NULLIF(source_payload_json->>'volume_24h', ''))::numeric,
        (NULLIF(source_payload_json->>'volume',     ''))::numeric
    ),

    open_interest = (NULLIF(source_payload_json->>'open_interest', ''))::numeric,

    -- yes_mid: set from last_price only when bid/ask are missing
    yes_mid = CASE
        WHEN yes_mid IS NULL
             AND (source_payload_json->>'last_price')::numeric > 0
        THEN (source_payload_json->>'last_price')::numeric / 100.0
        ELSE yes_mid
    END

WHERE source_payload_json IS NOT NULL
  AND (last_price IS NULL OR volume IS NULL OR open_interest IS NULL);

-- ── Summary ───────────────────────────────────────────────────────────────────
\echo ''
\echo '=== Post-backfill null counts ==='
SELECT
    COUNT(*)                                      AS total_rows,
    COUNT(*) FILTER (WHERE yes_bid IS NULL)       AS yes_bid_null,
    COUNT(*) FILTER (WHERE yes_ask IS NULL)       AS yes_ask_null,
    COUNT(*) FILTER (WHERE last_price IS NULL)    AS last_price_null,
    COUNT(*) FILTER (WHERE volume IS NULL)        AS volume_null,
    COUNT(*) FILTER (WHERE open_interest IS NULL) AS oi_null,
    COUNT(*) FILTER (WHERE yes_mid IS NULL)       AS yes_mid_null
FROM weather_bronze_kalshi_market_snapshots;

COMMIT;

\echo ''
\echo 'Backfill complete.'
\echo 'yes_bid / yes_ask: populated only by new poller rows (orderbook-derived).'
