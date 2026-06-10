-- 2026-06-08  Arbitrage signal correction
-- ============================================================================
-- This is the corrected query body for the n8n workflow
--   COURTYARD-BHN | ARBITRAGE-SIGNALS   (id gue1SZnC5saDa4Po)
-- It is NOT a schema change. It replaces the single Postgres Execute-Query node.
--
-- Two bugs fixed vs the live query:
--   1. GRADE-BLIND BASELINE.  The old eBay baseline grouped by card_id ONLY, so a
--      CGC 7 Courtyard ask was compared against an average that mixed ALL grades of
--      the card (e.g. CGC 7 Onix ask $14.92 vs a $272 all-grade avg → fake 94.5%).
--      Fix: both sides now group/join on (card_id, grader, numeric grade). Grade
--      text differs by source (Courtyard "8.5 NM-MT+" vs eBay "8.5"), so the numeric
--      grade is extracted with substring(grade FROM '[0-9]+(\.[0-9]+)?')::numeric.
--   2. FEE MODEL NOT WIRED.  est_* / is_profitable_est columns were never populated;
--      all 135 prior signals showed NULL net profit. Fix: CROSS JOIN LATERAL
--      estimate_trade_costs('courtyard','ebay', ask, ebay_avg, 'courtyard_to_ebay')
--      and store the full breakdown. is_profitable_est now reflects true net of
--      buy/sell fees + redemption + shipping + gas (min-profit gate $25 in the fn).
--
-- Dedup is now per (card_id, grader, grade) so different grades of the same card
-- don't suppress each other.
-- ============================================================================

WITH courtyard_asks AS (
  SELECT card_id,
         grader,
         NULLIF(substring(grade FROM '[0-9]+(\.[0-9]+)?'), '')::numeric AS grade_num,
         MIN(listed_price)                         AS courtyard_ask,
         MAX(card_name)                            AS card_name,
         MAX(set_name)                             AS set_name,
         MAX(grade)                                AS grade,
         MAX(edition)                              AS edition,
         MAX(print_variant)                        AS print_variant
    FROM courtyard_listings
   WHERE card_id IS NOT NULL
     AND grader IS NOT NULL
     AND grade ~ '[0-9]'
     AND listed_price IS NOT NULL
     AND listed_price > 0
     AND COALESCE(listed_at, created_at) > NOW() - INTERVAL '24 hours'
   GROUP BY card_id, grader,
            NULLIF(substring(grade FROM '[0-9]+(\.[0-9]+)?'), '')::numeric
),
ebay_baseline AS (
  SELECT card_id,
         grader,
         NULLIF(substring(grade FROM '[0-9]+(\.[0-9]+)?'), '')::numeric AS grade_num,
         AVG(sold_price)::DECIMAL(10,2)            AS ebay_90d_avg,
         COUNT(*)                                  AS n_comps
    FROM sold_listings
   WHERE card_id IS NOT NULL
     AND grader IS NOT NULL
     AND grade ~ '[0-9]'
     AND sold_price IS NOT NULL
     AND sold_price > 0
     AND inserted_at > NOW() - INTERVAL '90 days'
   GROUP BY card_id, grader,
            NULLIF(substring(grade FROM '[0-9]+(\.[0-9]+)?'), '')::numeric
  HAVING COUNT(*) >= 3
),
candidates AS (
  SELECT c.card_id, c.grader, c.grade_num,
         c.card_name, c.set_name, c.grade, c.edition, c.print_variant,
         c.courtyard_ask,
         e.ebay_90d_avg,
         e.n_comps,
         ((e.ebay_90d_avg - c.courtyard_ask) / e.ebay_90d_avg * 100)::DECIMAL(6,2) AS spread_pct
    FROM courtyard_asks c
    JOIN ebay_baseline  e USING (card_id, grader, grade_num)
   WHERE e.ebay_90d_avg > c.courtyard_ask
     AND ((e.ebay_90d_avg - c.courtyard_ask) / e.ebay_90d_avg) > 0.10
)
INSERT INTO tokenized_arbitrage_signals (
  card_id, card_code, card_name, set_name, grader, grade, edition, print_variant,
  courtyard_ask, ebay_90d_avg, spread_pct, estimated_profit, signal_strength,
  est_buy_fees, est_sell_fees, est_shipping, est_redemption, est_tokenization,
  est_gas, est_total_costs, est_net_profit, est_roi_pct, is_profitable_est,
  reviewed, actioned, expires_at, raw_payload
)
SELECT
  cd.card_id,
  mc.card_code,
  cd.card_name,
  cd.set_name,
  cd.grader,
  cd.grade,
  cd.edition,
  cd.print_variant,
  cd.courtyard_ask,
  cd.ebay_90d_avg,
  cd.spread_pct,
  tc.net_profit_est,                       -- estimated_profit now == true net
  CASE
    WHEN cd.spread_pct > 50 THEN 'critical'
    WHEN cd.spread_pct > 35 THEN 'strong'
    WHEN cd.spread_pct > 20 THEN 'moderate'
    ELSE 'weak'
  END AS signal_strength,
  tc.buy_fees_est, tc.sell_fees_est, tc.shipping_est, tc.redemption_est, tc.tokenization_est,
  tc.gas_est, tc.total_costs_est, tc.net_profit_est, tc.roi_est_pct, tc.is_profitable,
  FALSE,
  FALSE,
  NOW() + INTERVAL '7 days',
  jsonb_build_object(
    'courtyard_ask',    cd.courtyard_ask,
    'ebay_90d_avg',     cd.ebay_90d_avg,
    'n_ebay_comps',     cd.n_comps,
    'grade_num',        cd.grade_num,
    'spread_pct',       cd.spread_pct,
    'net_profit_est',   tc.net_profit_est,
    'total_costs_est',  tc.total_costs_est,
    'is_profitable',    tc.is_profitable,
    'pbdd_grade_code',  pbdd_grade_code(mc.card_code, cd.grader, cd.grade)
  )
  FROM candidates cd
  LEFT JOIN master_card_catalog mc ON mc.id = cd.card_id
  CROSS JOIN LATERAL estimate_trade_costs(
         'courtyard', 'ebay', cd.courtyard_ask, cd.ebay_90d_avg,
         'courtyard_to_ebay', 'non_store') tc
 WHERE NOT EXISTS (
   SELECT 1 FROM tokenized_arbitrage_signals s
    WHERE s.card_id = cd.card_id
      AND s.grader  = cd.grader
      AND s.grade   = cd.grade
      AND s.reviewed = FALSE
      AND s.detected_at > NOW() - INTERVAL '24 hours'
 )
RETURNING id, card_code, signal_strength, spread_pct, est_net_profit, is_profitable_est;
