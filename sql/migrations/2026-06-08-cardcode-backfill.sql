-- 2026-06-08  card_code backfill on ebay_transactions  (Step 5)
-- ============================================================================
-- PENDING FLETCH APPROVAL — do not run until approved (snapshot first).
--
-- Brief proposed re-deriving card_code from card_number+set+edition+print_variant.
-- That is unnecessary: card_id is ALREADY resolved on 19,119/23,606 Bronze rows
-- (the Phase-7 resolver did the hard matching), and master_card_catalog holds a
-- card_code for every row (0 NULL). So card_code comes directly off the existing
-- card_id FK — more reliable than re-parsing card numbers.
--
-- Validated read-only 2026-06-08:
--   fillable rows (card_id present, card_code NULL): 18,030
--   rows that will actually fill (catalog code present): 18,030  (100%)
--   catalog rows missing a code: 0
--   rows that cannot fill (no card_id — need Step 4 title reparse): 4,487
--
-- Idempotent: only touches rows where card_code IS NULL.
-- ============================================================================

-- Snapshot guard (uncomment to capture a before-count into a log table if desired):
-- SELECT count(*) AS before_with_code FROM ebay_transactions WHERE card_code IS NOT NULL;

UPDATE ebay_transactions e
   SET card_code = mc.card_code
  FROM master_card_catalog mc
 WHERE mc.id = e.card_id
   AND e.card_id   IS NOT NULL
   AND e.card_code IS NULL
   AND mc.card_code IS NOT NULL;

-- Expected: UPDATE 18030
-- Verify:
--   SELECT count(*) total, count(card_code) has_code,
--          round(count(card_code)::numeric/count(*)*100,1) pct
--     FROM ebay_transactions;
--   -- expect has_code ~= 19,119 (the card_id-resolved set)
