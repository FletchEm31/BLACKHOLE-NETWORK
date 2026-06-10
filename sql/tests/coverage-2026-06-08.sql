\echo '===== FRESHNESS: did any new eBay data land since 2026-06-03? ====='
SELECT count(*) AS sold_rows, max(inserted_at)::date AS newest_insert,
       count(*) FILTER (WHERE inserted_at > '2026-06-03') AS since_jun3
  FROM sold_listings;

\echo '===== Q1 SET-LEVEL COVERAGE ====='
SELECT mc.set_name, mc.edition,
       count(DISTINCT mc.id)                                              AS catalog_cards,
       count(DISTINCT sl.card_id)                                        AS cards_with_data,
       round(count(DISTINCT sl.card_id)::numeric/count(DISTINCT mc.id)*100,1) AS cov_pct,
       count(sl.card_id)                                                  AS total_comps,
       min(sl.inserted_at)::date                                         AS earliest,
       max(sl.inserted_at)::date                                         AS latest
  FROM master_card_catalog mc
  LEFT JOIN sold_listings sl ON sl.card_id = mc.id
 GROUP BY mc.set_name, mc.edition
 ORDER BY mc.set_name, mc.edition;

\echo '===== Q2 ZERO-DATA CARDS BY SET ====='
SELECT mc.set_name, count(*) AS zero_data_cards
  FROM master_card_catalog mc
  LEFT JOIN sold_listings sl ON sl.card_id = mc.id
 WHERE sl.card_id IS NULL
 GROUP BY mc.set_name ORDER BY 2 DESC;

\echo '===== Q2b TOTAL zero-data catalog rows ====='
SELECT count(*) AS total_zero_data
  FROM master_card_catalog mc
  LEFT JOIN sold_listings sl ON sl.card_id = mc.id
 WHERE sl.card_id IS NULL;

\echo '===== Q3 GRADER COVERAGE per set/edition ====='
SELECT mc.set_name, mc.edition,
       count(*) FILTER (WHERE sl.grader='PSA') AS psa,
       count(*) FILTER (WHERE sl.grader='CGC') AS cgc,
       count(*) FILTER (WHERE sl.grader='BGS') AS bgs,
       count(*) FILTER (WHERE sl.grader='SGC') AS sgc,
       count(*) FILTER (WHERE sl.grader='TAG') AS tag
  FROM master_card_catalog mc
  JOIN sold_listings sl ON sl.card_id = mc.id
 GROUP BY mc.set_name, mc.edition ORDER BY mc.set_name, mc.edition;

\echo '===== Q4 GRADE DIST on key Base 1E cards (PSA) vs pop ====='
SELECT mc.card_code, mc.card_name,
       count(*) FILTER (WHERE sl.grader='PSA' AND sl.grade='10') AS psa10,
       count(*) FILTER (WHERE sl.grader='PSA' AND sl.grade='9')  AS psa9,
       count(*) FILTER (WHERE sl.grader='PSA' AND sl.grade='8')  AS psa8,
       count(*) FILTER (WHERE sl.grader='PSA' AND sl.grade='7')  AS psa7,
       count(*) AS total_comps
  FROM master_card_catalog mc
  JOIN sold_listings sl ON sl.card_id = mc.id
 WHERE mc.card_code IN ('BAS004-1E-STN','BAS002-1E-STN','BAS015-1E-STN','BAS010-1E-STN','JUN060-1E-STN')
 GROUP BY mc.card_code, mc.card_name ORDER BY mc.card_code;

\echo '===== Q5 TIMING: comps by insert month ====='
SELECT to_char(inserted_at,'YYYY-MM') AS mon, count(*) AS comps
  FROM sold_listings WHERE card_id IS NOT NULL
 GROUP BY 1 ORDER BY 1;

\echo '===== Q6 SHADOWLESS check ====='
SELECT count(*) AS shadowless_comps, count(DISTINCT card_id) AS distinct_cards
  FROM sold_listings WHERE set_name='Base Set' AND edition='Shadowless';

\echo '===== Q7 PROMOS check ====='
SELECT set_name, count(*) AS comps, count(DISTINCT card_id) AS distinct_cards
  FROM sold_listings
 WHERE set_name IN ('Best of Game','Wizards Black Star Promos')
 GROUP BY set_name ORDER BY set_name;
