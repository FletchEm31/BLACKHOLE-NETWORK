-- tokenized-sales-unified.sql
-- Unified materialized view across all tokenized card market platforms.
-- Architecture: split tables per platform (courtyard_sales, collector_crypt_sales, ...)
-- as raw ingestion layer; this view normalizes for cross-platform arbitrage + pricing.
--
-- Adding a new platform = one more UNION ALL block.
-- Refresh: REFRESH MATERIALIZED VIEW CONCURRENTLY tokenized_sales_unified
-- (requires the unique index below; run hourly via n8n)
--
-- Only card_id-resolved rows appear (card_id IS NOT NULL) — unresolved rows
-- still land in the source tables for future re-resolution.

CREATE MATERIALIZED VIEW tokenized_sales_unified AS

SELECT
  'courtyard'        AS platform,
  'polygon'          AS blockchain,
  transaction_hash,
  card_id,
  grader,
  grade,
  sold_price         AS sold_price_usd,
  NULL::numeric      AS sold_price_native,
  'USDC'             AS native_currency,
  created_at         AS sold_at,
  buyer_address,
  seller_address,
  language,
  item_id
FROM courtyard_sales
WHERE card_id IS NOT NULL
  AND sold_price IS NOT NULL

UNION ALL

SELECT
  'collector_crypt'  AS platform,
  'solana'           AS blockchain,
  transaction_hash,
  card_id,
  grader,
  grade,
  sold_price         AS sold_price_usd,
  sol_price          AS sold_price_native,
  'SOL'              AS native_currency,
  created_at         AS sold_at,
  buyer_address,
  seller_address,
  language,
  item_id
FROM collector_crypt_sales
WHERE card_id IS NOT NULL
  AND sold_price IS NOT NULL;

-- Unique index required for REFRESH CONCURRENTLY
CREATE UNIQUE INDEX ON tokenized_sales_unified (platform, item_id);

-- Query indexes
CREATE INDEX ON tokenized_sales_unified (card_id);
CREATE INDEX ON tokenized_sales_unified (sold_at DESC);
CREATE INDEX ON tokenized_sales_unified (platform, grader, grade);

GRANT SELECT ON tokenized_sales_unified TO ehuser, agent_reader, n8n_user, grafana_reader;

-- Verification query:
-- SELECT platform, blockchain, COUNT(*) AS sales,
--        ROUND(AVG(sold_price_usd)::numeric,2) AS avg_usd
-- FROM tokenized_sales_unified
-- GROUP BY platform, blockchain ORDER BY platform;
