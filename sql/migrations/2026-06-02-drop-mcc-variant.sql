-- Drop the legacy master_card_catalog.variant column + trigger-bridge + back-compat view dep.
--
-- Supersedes sql/migrations/2026-05-27-drop-mcc-variant.sql, which FAILED on apply because the
-- `card_catalog` back-compat view (SELECT ... variant ... FROM master_card_catalog) depends on
-- the column. That migration was never successfully applied (confirmed 2026-06-02: variant +
-- mcc_variant_split_trg + mcc_fill_variant_split() + idx_card_catalog_unique all still live).
--
-- variant was split into (edition, print_variant) 2026-05-21 (§3.3). All 1,354 rows carry valid
-- (edition, print_variant); the trigger only back-filled them from variant when NULL. Repo audit
-- 2026-06-02: zero code consumers of `variant` or of the `card_catalog` view (grep clean).
--
-- This migration: drop the view, the column, its trigger/function, and the variant unique index,
-- then RECREATE card_catalog WITHOUT variant (preserving the back-compat alias + grafana_reader
-- grant). idx_mcc_identity (edition, print_variant) remains the identity uniqueness index.
--
-- Pre-apply check (must be 0): SELECT COUNT(*) FROM master_card_catalog
--   WHERE variant IS NOT NULL AND (edition IS NULL OR print_variant IS NULL);  -- verified 0.

\set ON_ERROR_STOP on
BEGIN;

DROP VIEW     IF EXISTS card_catalog;
DROP TRIGGER  IF EXISTS mcc_variant_split_trg ON master_card_catalog;
DROP FUNCTION IF EXISTS mcc_fill_variant_split();
DROP INDEX    IF EXISTS idx_card_catalog_unique;
ALTER TABLE   master_card_catalog DROP COLUMN IF EXISTS variant;

-- Recreate the back-compat alias without the dropped legacy column.
CREATE VIEW card_catalog AS
    SELECT id, card_number, card_name, set_name,
           ungraded_price, grade_9_price, psa_10_price, last_updated, active
      FROM master_card_catalog;

GRANT SELECT ON card_catalog TO grafana_reader;

COMMENT ON VIEW card_catalog IS
    'Back-compat alias for master_card_catalog (legacy name). Legacy variant column removed '
    '2026-06-02 — use edition + print_variant on master_card_catalog instead.';

COMMIT;
