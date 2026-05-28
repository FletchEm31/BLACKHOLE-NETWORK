-- Drop the legacy master_card_catalog.variant column + its trigger-bridge.
--
-- Context: variant was split into (edition, print_variant) on 2026-05-21 per
-- collectibles-data-standard.md §3.3. The column has been retained since with a
-- BEFORE-INSERT/UPDATE trigger (mcc_variant_split_trg → mcc_fill_variant_split)
-- that fills edition/print_variant from variant when either is NULL. Pending
-- consumer migration has now landed: no code reads master_card_catalog.variant
-- today (audited 2026-05-27 across n8n-workflows/, scripts/, infrastructure/scrapers/).
--
-- Pre-apply check (run before this migration, must return zero rows):
--   SELECT COUNT(*) FROM master_card_catalog
--    WHERE variant IS NOT NULL
--      AND (edition IS NULL OR print_variant IS NULL);
-- Verified 2026-05-27: 1354/1354 rows carry valid (edition, print_variant);
-- legacy variant→(edition,print_variant) mapping is consistent.

BEGIN;

DROP TRIGGER  IF EXISTS mcc_variant_split_trg ON master_card_catalog;
DROP FUNCTION IF EXISTS mcc_fill_variant_split();
DROP INDEX    IF EXISTS idx_card_catalog_unique;
ALTER TABLE   master_card_catalog DROP COLUMN IF EXISTS variant;

COMMIT;
