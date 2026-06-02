-- Backfill ebay_transactions.sold_date from sold_at (the actual sale timestamp).
--
-- Legacy-loaded rows (ebay-sold-load.js) populated sold_at (created_at→sold_at) but left
-- sold_date NULL, blocking Silver promotion (gate requires sold_date NOT NULL) on otherwise
-- fully-resolved, graded rows. sold_at IS the sale time, so sold_date := sold_at::date is a
-- correct derivation (not a fabricated date). The original date is NOT in raw_payload.
--
-- Idempotent (only touches NULL sold_date). Rows with no sold_at remain NULL (unrecoverable).

\set ON_ERROR_STOP on
BEGIN;

UPDATE ebay_transactions
   SET sold_date = sold_at::date
 WHERE sold_date IS NULL
   AND sold_at  IS NOT NULL;

COMMIT;

-- Verify: remaining NULL sold_date (should be only rows that also lack sold_at).
SELECT COUNT(*) FILTER (WHERE sold_date IS NULL)                       AS still_null_date,
       COUNT(*) FILTER (WHERE sold_date IS NULL AND sold_at IS NULL)   AS null_date_no_sold_at
FROM ebay_transactions;
