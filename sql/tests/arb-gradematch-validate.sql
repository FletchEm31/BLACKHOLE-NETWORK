-- Read-only validation of the corrected arbitrage detection logic.
-- Grade-matches courtyard asks to eBay sold comps on (card_id, grader, numeric grade)
-- and runs the estimate_trade_costs() fee model. NO writes.
WITH ca AS (
  SELECT card_id, grader,
         NULLIF(substring(grade from '[0-9]+(\.[0-9]+)?'), '')::numeric AS grade_num,
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
     AND listed_price > 0
     AND COALESCE(listed_at, created_at) > NOW() - INTERVAL '24 hours'
   GROUP BY card_id, grader,
            NULLIF(substring(grade from '[0-9]+(\.[0-9]+)?'), '')::numeric
),
eb AS (
  SELECT card_id, grader,
         NULLIF(substring(grade from '[0-9]+(\.[0-9]+)?'), '')::numeric AS grade_num,
         AVG(sold_price)::numeric(10,2)            AS ebay_90d_avg,
         COUNT(*)                                  AS n_comps
    FROM sold_listings
   WHERE card_id IS NOT NULL
     AND grader IS NOT NULL
     AND grade ~ '[0-9]'
     AND sold_price > 0
     AND inserted_at > NOW() - INTERVAL '90 days'
   GROUP BY card_id, grader,
            NULLIF(substring(grade from '[0-9]+(\.[0-9]+)?'), '')::numeric
  HAVING COUNT(*) >= 3
),
candidates AS (
  SELECT c.card_id, c.grader, c.grade_num, c.card_name, c.grade,
         c.courtyard_ask, e.ebay_90d_avg, e.n_comps,
         ((e.ebay_90d_avg - c.courtyard_ask) / e.ebay_90d_avg * 100)::numeric(6,2) AS spread_pct
    FROM ca c
    JOIN eb e USING (card_id, grader, grade_num)
   WHERE e.ebay_90d_avg > c.courtyard_ask
     AND ((e.ebay_90d_avg - c.courtyard_ask) / e.ebay_90d_avg) > 0.10
)
SELECT count(*) AS n_candidates,
       count(*) FILTER (WHERE tc.is_profitable) AS n_profitable,
       round(avg(spread_pct),1) AS avg_spread,
       round(max(spread_pct),1) AS max_spread
  FROM candidates cd
  CROSS JOIN LATERAL estimate_trade_costs(
         'courtyard','ebay', cd.courtyard_ask, cd.ebay_90d_avg,
         'courtyard_to_ebay','non_store') tc;

\echo '--- top 20 grade-matched candidates by net profit ---'
WITH ca AS (
  SELECT card_id, grader,
         NULLIF(substring(grade from '[0-9]+(\.[0-9]+)?'), '')::numeric AS grade_num,
         MIN(listed_price) AS courtyard_ask, MAX(card_name) AS card_name, MAX(grade) AS grade
    FROM courtyard_listings
   WHERE card_id IS NOT NULL AND grader IS NOT NULL AND grade ~ '[0-9]'
     AND listed_price > 0 AND COALESCE(listed_at, created_at) > NOW() - INTERVAL '24 hours'
   GROUP BY card_id, grader, NULLIF(substring(grade from '[0-9]+(\.[0-9]+)?'), '')::numeric
),
eb AS (
  SELECT card_id, grader,
         NULLIF(substring(grade from '[0-9]+(\.[0-9]+)?'), '')::numeric AS grade_num,
         AVG(sold_price)::numeric(10,2) AS ebay_90d_avg, COUNT(*) AS n_comps
    FROM sold_listings
   WHERE card_id IS NOT NULL AND grader IS NOT NULL AND grade ~ '[0-9]'
     AND sold_price > 0 AND inserted_at > NOW() - INTERVAL '90 days'
   GROUP BY card_id, grader, NULLIF(substring(grade from '[0-9]+(\.[0-9]+)?'), '')::numeric
  HAVING COUNT(*) >= 3
)
SELECT cd.card_name, cd.grader, cd.grade,
       cd.courtyard_ask, eb.ebay_90d_avg, eb.n_comps,
       ((eb.ebay_90d_avg - cd.courtyard_ask)/eb.ebay_90d_avg*100)::numeric(6,2) AS spread_pct,
       tc.total_costs_est, tc.net_profit_est, tc.roi_est_pct, tc.is_profitable
  FROM ca cd
  JOIN eb USING (card_id, grader, grade_num)
  CROSS JOIN LATERAL estimate_trade_costs(
         'courtyard','ebay', cd.courtyard_ask, eb.ebay_90d_avg,
         'courtyard_to_ebay','non_store') tc
 WHERE eb.ebay_90d_avg > cd.courtyard_ask
   AND ((eb.ebay_90d_avg - cd.courtyard_ask)/eb.ebay_90d_avg) > 0.10
 ORDER BY tc.net_profit_est DESC NULLS LAST
 LIMIT 20;

\echo '--- grade-matched candidate count WITHOUT 24h ask window (true opportunity depth) ---'
WITH ca AS (
  SELECT card_id, grader,
         NULLIF(substring(grade from '[0-9]+(\.[0-9]+)?'), '')::numeric AS grade_num,
         MIN(listed_price) AS courtyard_ask
    FROM courtyard_listings
   WHERE card_id IS NOT NULL AND grader IS NOT NULL AND grade ~ '[0-9]' AND listed_price > 0
   GROUP BY card_id, grader, NULLIF(substring(grade from '[0-9]+(\.[0-9]+)?'), '')::numeric
),
eb AS (
  SELECT card_id, grader,
         NULLIF(substring(grade from '[0-9]+(\.[0-9]+)?'), '')::numeric AS grade_num,
         AVG(sold_price)::numeric(10,2) AS ebay_90d_avg, COUNT(*) AS n_comps
    FROM sold_listings
   WHERE card_id IS NOT NULL AND grader IS NOT NULL AND grade ~ '[0-9]' AND sold_price > 0
     AND inserted_at > NOW() - INTERVAL '90 days'
   GROUP BY card_id, grader, NULLIF(substring(grade from '[0-9]+(\.[0-9]+)?'), '')::numeric
  HAVING COUNT(*) >= 3
)
SELECT count(*) AS all_time_asks_matched
  FROM ca JOIN eb USING (card_id, grader, grade_num)
 WHERE eb.ebay_90d_avg > ca.courtyard_ask
   AND ((eb.ebay_90d_avg - ca.courtyard_ask)/eb.ebay_90d_avg) > 0.10;
