-- master_grade_catalog corrections + reholder support + RAW grader sentinel.
--
-- Combined migration covering three logically-coupled changes:
--   A. CGC tier_label corrections at grades 4–6.5 + AU/AA (live mismatches)
--   B. Reholder/crossover support: add reholder_eligible column + the
--      legacy CGC 'Gem Mint 9.5' raw_label row
--   C. Add 'RAW' as a valid grader value (sentinel for ungraded cards) +
--      update fact-table CHECK constraints to accept it
--
-- NOT YET APPLIED on LA. Awaiting operator OK.
--
-- AUTHORITATIVE SOURCE: operator-provided CGC raw_label table (2026-05-28)
-- and grader-sentinel spec. Supersedes prior label-color assumptions.

BEGIN;

-- ============================================================================
-- A. CGC tier_label corrections
-- ============================================================================
-- Live catalog has tier_labels that disagree with the authoritative spec.
-- Substantive changes at grades 6 and 6.5 (Ex/NM → Excellent/Mint — different
-- intermediate term, not just shorthand expansion) and AU/AA (different
-- meanings). Other rows are shorthand → full expansion for consistency.

UPDATE master_grade_catalog SET tier_label = 'Near Mint/Mint+'      WHERE grader = 'CGC' AND raw_label = '8.5';
UPDATE master_grade_catalog SET tier_label = 'Near Mint/Mint'       WHERE grader = 'CGC' AND raw_label = '8';
UPDATE master_grade_catalog SET tier_label = 'Excellent/Mint+'      WHERE grader = 'CGC' AND raw_label = '6.5';
UPDATE master_grade_catalog SET tier_label = 'Excellent/Mint'       WHERE grader = 'CGC' AND raw_label = '6';
UPDATE master_grade_catalog SET tier_label = 'Very Good/Excellent+' WHERE grader = 'CGC' AND raw_label = '4.5';
UPDATE master_grade_catalog SET tier_label = 'Very Good/Excellent'  WHERE grader = 'CGC' AND raw_label = '4';
UPDATE master_grade_catalog SET tier_label = 'Altered/Ungraded'     WHERE grader = 'CGC' AND raw_label = 'AU';
UPDATE master_grade_catalog SET tier_label = 'Altered/Authentic'    WHERE grader = 'CGC' AND raw_label = 'AA';

-- ============================================================================
-- B. Reholder/crossover support
-- ============================================================================
-- Per operator (2026-05-28): CGC offers a paid reholder service. Legacy CGC
-- 9.5 "Gem Mint" slabs (older Blue Label — outlier, not a separate color
-- tier) can be re-cased as current CGC 10 "Gem Mint" (Blue Label) for ~$10.
-- Market signal: Gem Mint 9.5 trades ≈ Gem Mint 10. Mint+ 9.5 (current
-- standard 9.5) does NOT trade like a 10 — tier_label is the discriminator.

ALTER TABLE master_grade_catalog
  ADD COLUMN IF NOT EXISTS reholder_eligible BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN master_grade_catalog.reholder_eligible IS
'TRUE if cards with this raw_label can be reholdered/crossed-over by the grader to a higher-tier modern label (e.g. legacy CGC Gem Mint 9.5 → current CGC Gem Mint 10, ~$10). Pricing signal: such slabs may trade at a discount vs the target tier.';

-- Insert the legacy CGC 'Gem Mint 9.5' raw_label if missing.
-- This is an OUTLIER row — CGC's current 9.5 tier is Mint+ 9.5; only legacy
-- (older Blue Label) slabs carry the Gem Mint tier at 9.5.
INSERT INTO master_grade_catalog (grader, raw_label, numeric_grade, tier_label, market_equiv_10, reholder_eligible)
SELECT 'CGC', 'Gem Mint 9.5', 9.5, 'Gem Mint', FALSE, TRUE
WHERE NOT EXISTS (
  SELECT 1 FROM master_grade_catalog
   WHERE grader = 'CGC' AND raw_label = 'Gem Mint 9.5'
);

-- Ensure the flag is set whether the row was just inserted or already existed.
UPDATE master_grade_catalog
   SET reholder_eligible = TRUE
 WHERE grader = 'CGC' AND raw_label = 'Gem Mint 9.5';

-- ============================================================================
-- C-pre. BGS Gold Label row
-- ============================================================================
-- Per operator (2026-05-28): BGS Gold Label is a distinct tier from Black
-- Label (currently catalogued as 'Pristine 10') and needs its own row.
-- Existing Black Label = Pristine 10 (all four subgrades = 10, overall 10).
-- Gold Label = 10 overall but with one or more subgrades below 10 — a
-- separate slab colorway. Adding a `Gold 10` raw_label as the canonical
-- representation; operator should confirm exact label-string convention
-- after first observed listing.
INSERT INTO master_grade_catalog (grader, raw_label, numeric_grade, tier_label, market_equiv_10, reholder_eligible)
SELECT 'BGS', 'Gold 10', 10.0, 'Gold', TRUE, FALSE
WHERE NOT EXISTS (
  SELECT 1 FROM master_grade_catalog
   WHERE grader = 'BGS' AND raw_label = 'Gold 10'
);

-- ============================================================================
-- C. RAW grader sentinel
-- ============================================================================
-- Per operator (2026-05-28): explicit sentinel values needed because NULL is
-- ambiguous between "no data captured" and "not applicable."
--   grader = 'RAW'   → card is ungraded/raw, no grade applies
--   grader = NULL    → grader data was not captured
--   grade = NULL is acceptable when grader = 'RAW' OR when data missing
--   grade_label = 'Ungraded' when grader = 'RAW'

INSERT INTO master_grade_catalog (grader, raw_label, numeric_grade, tier_label, market_equiv_10, reholder_eligible)
SELECT 'RAW', 'Ungraded', NULL, 'Ungraded', FALSE, FALSE
WHERE NOT EXISTS (
  SELECT 1 FROM master_grade_catalog
   WHERE grader = 'RAW' AND raw_label = 'Ungraded'
);

-- Update grader CHECK constraints on every fact table that carries a grader
-- column to include 'RAW' as a valid value. Without this, RAW grader rows
-- will be rejected at INSERT time.
--
-- Affected tables (all have "<original_table_name>_grader_chk" naming because
-- constraints didn't auto-rename during the v2 sold_listings → ebay_transactions
-- rename pass — see BHN-SESSION-HANDOFF-2026-05-27-PT2.txt §"Cosmetic carry-over"):
--   ebay_transactions       constraint: sold_listings_grader_chk
--   ebay_asks               constraint: ebay_listings_grader_chk  (NOTE: column type is numeric on this table; check existence)
--   courtyard_transactions  constraint: courtyard_sales_grader_chk
--   courtyard_asks          constraint: courtyard_listings_grader_chk
--   collector_crypt_transactions  constraint: collector_crypt_sales_grader_chk
--   collector_crypt_asks    constraint: collector_crypt_asks_grader_chk

ALTER TABLE ebay_transactions DROP CONSTRAINT IF EXISTS sold_listings_grader_chk;
ALTER TABLE ebay_transactions ADD CONSTRAINT ebay_transactions_grader_chk
  CHECK (grader IS NULL OR grader = ANY (ARRAY['CGC','PSA','BGS','SGC','RAW']));

ALTER TABLE courtyard_transactions DROP CONSTRAINT IF EXISTS courtyard_sales_grader_chk;
ALTER TABLE courtyard_transactions ADD CONSTRAINT courtyard_transactions_grader_chk
  CHECK (grader IS NULL OR grader = ANY (ARRAY['CGC','PSA','BGS','SGC','RAW']));

ALTER TABLE courtyard_asks DROP CONSTRAINT IF EXISTS courtyard_listings_grader_chk;
ALTER TABLE courtyard_asks ADD CONSTRAINT courtyard_asks_grader_chk
  CHECK (grader IS NULL OR grader = ANY (ARRAY['CGC','PSA','BGS','SGC','RAW']));

ALTER TABLE collector_crypt_transactions DROP CONSTRAINT IF EXISTS collector_crypt_sales_grader_chk;
ALTER TABLE collector_crypt_transactions ADD CONSTRAINT collector_crypt_transactions_grader_chk
  CHECK (grader IS NULL OR grader = ANY (ARRAY['CGC','PSA','BGS','SGC','RAW']));

ALTER TABLE collector_crypt_asks DROP CONSTRAINT IF EXISTS collector_crypt_asks_grader_chk;
ALTER TABLE collector_crypt_asks ADD CONSTRAINT collector_crypt_asks_grader_chk
  CHECK (grader IS NULL OR grader = ANY (ARRAY['CGC','PSA','BGS','SGC','RAW']));

-- ebay_asks may have a different CHECK or none — handle defensively.
-- The pre-v2 ebay_listings table had `grader has descriptors` per §9 of the
-- standard, suggesting no strict CHECK. Skip ebay_asks here; if a CHECK
-- exists it'll need its own ALTER once the operator confirms current state.

COMMIT;

-- ============================================================================
-- Post-apply verification
-- ============================================================================
-- 1. Confirm CGC catalog matches authoritative table:
--    SELECT raw_label, tier_label, numeric_grade, reholder_eligible
--      FROM master_grade_catalog WHERE grader = 'CGC'
--     ORDER BY numeric_grade DESC NULLS LAST, raw_label;
--    Expected 26 rows (was 25 + new Gem Mint 9.5).
--
-- 2. Confirm RAW grader sentinel:
--    SELECT * FROM master_grade_catalog WHERE grader = 'RAW';
--    Expected 1 row: RAW | Ungraded | NULL | Ungraded | FALSE | FALSE
--
-- 3. Confirm grader CHECK constraints accept RAW:
--    INSERT INTO ebay_transactions (item_id, grader, grade, sale_type, platform, currency, raw_payload, edition, print_variant)
--      VALUES ('test-raw-001', 'RAW', NULL, 'fixed_price', 'ebay', 'USD', '{}'::jsonb, 'N/A', 'Standard');
--    -- should succeed; then: DELETE FROM ebay_transactions WHERE item_id = 'test-raw-001';
--
-- 4. Confirm reholder flag:
--    SELECT grader, raw_label, reholder_eligible FROM master_grade_catalog
--     WHERE reholder_eligible = TRUE;
--    Expected 1 row: CGC | Gem Mint 9.5 | TRUE
