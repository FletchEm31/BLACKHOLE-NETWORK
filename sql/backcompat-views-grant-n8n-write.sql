-- ============================================================================
-- Grant n8n write access on the remaining v2 back-compat views
-- ============================================================================
-- Applied on LA hub 2026-05-29 (eventhorizon).
--
-- Companion to sql/ebay-listings-grant-n8n-write.sql. The v2 market-data
-- standard migration (-05-backcompat-views.sql) granted INSERT/UPDATE on every
-- back-compat view only to `log_shipper`. ebay_listings was fixed first (it was
-- actively breaking the 8 vintage workflows); this closes the SAME latent gap
-- on the other four views before any consumer hits it:
--
--   sold_listings         -> ebay_transactions
--   courtyard_listings    -> courtyard_asks
--   courtyard_sales       -> courtyard_transactions
--   collector_crypt_sales -> collector_crypt_transactions
--
-- All four views are owned by `postgres` and are NOT security_invoker, so a
-- write through the view runs base-table/sequence access as the view owner;
-- the invoking role (ehuser / n8n_user) needs privileges only on the VIEW for
-- the current path. The base-table + sequence grants are forward-looking for an
-- eventual repoint of consumers to the real tables.
--
-- Verified with rolled-back INSERTs as ehuser through each view (no
-- "permission denied"; only expected NOT-NULL constraint errors).
--
-- Idempotent: GRANT is safe to re-run.
-- ============================================================================

BEGIN;

-- Views (immediate)
GRANT SELECT, INSERT, UPDATE ON sold_listings, courtyard_listings, courtyard_sales, collector_crypt_sales
  TO ehuser, n8n_user;

-- Base tables (forward-looking, for repoint to real tables)
GRANT SELECT, INSERT, UPDATE ON ebay_transactions, courtyard_asks, courtyard_transactions, collector_crypt_transactions
  TO ehuser, n8n_user;

-- Sequences (needed for direct base INSERT post-repoint)
GRANT USAGE, SELECT ON SEQUENCE
  ebay_transactions_id_seq, courtyard_asks_id_seq, courtyard_transactions_id_seq, collector_crypt_transactions_id_seq
  TO ehuser, n8n_user;

COMMIT;
