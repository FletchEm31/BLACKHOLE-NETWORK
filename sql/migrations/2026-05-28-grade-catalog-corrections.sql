-- master_grade_catalog corrections + reholder schema + RAW/UNKNOWN sentinels.
--
-- Authoritative source: operator-supplied CGC + BGS tables and grader sentinel
-- spec (2026-05-28). Supersedes earlier drafts that incorrectly mapped CGC
-- label colors (the "Green Label" prose was wrong — CGC has no current Green
-- Label for trading cards).
--
-- Five sections, all in one BEGIN/COMMIT:
--   A. CGC tier_label corrections at grades 4–6.5 + AU/AA
--   B. Reholder/crossover support — schema cols + flagged raw_labels
--   C. BGS — separate Black Label and Gold Label rows from Pristine 10
--   D. RAW grader sentinel + UNKNOWN grader sentinel
--   E. Fact-table CHECK constraints accept RAW and UNKNOWN
--
-- CGC label color scheme (authoritative):
--   Gold   = Pristine 10 only
--   Black  = current standard CGC (Gem Mint 10, Mint+ 9.5, grades 9 and below)
--   Blue   = LEGACY (Gem Mint 9.5 only — older naming convention outlier)
--   (Perfect 10 = legacy retired 2023)
--
-- NOT YET APPLIED on LA. Awaiting operator OK.

BEGIN;

-- ============================================================================
-- A. CGC tier_label corrections at grades 4–6.5 + AU/AA
-- ============================================================================
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
-- CGC paid service. Two flagged cases:
--   Gem Mint 9.5 (legacy Blue Label outlier) → Gem Mint 10 (current Black), $5–$10
--   Perfect 10   (legacy retired 2023)        → Pristine 10  (current Gold), fee unknown
--
-- Schema: add three nullable columns so HORIZON can query the target tier
-- and cost without prose parsing.

ALTER TABLE master_grade_catalog
  ADD COLUMN IF NOT EXISTS reholder_eligible        BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS reholder_target_raw_label TEXT,
  ADD COLUMN IF NOT EXISTS reholder_fee_min_usd     NUMERIC(8,2),
  ADD COLUMN IF NOT EXISTS reholder_fee_max_usd     NUMERIC(8,2);

COMMENT ON COLUMN master_grade_catalog.reholder_eligible IS
'TRUE if cards with this raw_label can be reholdered/crossed-over by the grader to a different (typically higher-tier) raw_label. HORIZON arbitrage signal: such slabs may trade at a discount vs the target.';

COMMENT ON COLUMN master_grade_catalog.reholder_target_raw_label IS
'The raw_label this card would carry post-reholder (e.g. ''Pristine 10'' for CGC Perfect 10). NULL when reholder_eligible = FALSE.';

COMMENT ON COLUMN master_grade_catalog.reholder_fee_min_usd IS
'Lower bound of the reholder service fee in USD. NULL when fee unknown or reholder_eligible = FALSE.';

COMMENT ON COLUMN master_grade_catalog.reholder_fee_max_usd IS
'Upper bound of the reholder service fee in USD. Same NULL semantics as the min.';

-- Insert legacy CGC Gem Mint 9.5 (Blue Label outlier) if missing.
INSERT INTO master_grade_catalog
  (grader, raw_label, numeric_grade, tier_label, market_equiv_10, reholder_eligible, reholder_target_raw_label, reholder_fee_min_usd, reholder_fee_max_usd)
SELECT 'CGC', 'Gem Mint 9.5', 9.5, 'Gem Mint', TRUE,
       TRUE, 'Gem Mint 10', 5.00, 10.00
WHERE NOT EXISTS (
  SELECT 1 FROM master_grade_catalog WHERE grader = 'CGC' AND raw_label = 'Gem Mint 9.5'
);

-- Update existing row even if INSERT was skipped (idempotent).
-- Note: market_equiv_10 = TRUE because CGC officially treats Gem Mint 9.5
-- as equivalent to Gem Mint 10 — this is the central pricing signal.
UPDATE master_grade_catalog
   SET reholder_eligible        = TRUE,
       reholder_target_raw_label = 'Gem Mint 10',
       reholder_fee_min_usd     = 5.00,
       reholder_fee_max_usd     = 10.00,
       market_equiv_10          = TRUE
 WHERE grader = 'CGC' AND raw_label = 'Gem Mint 9.5';

-- Flag CGC Perfect 10 (legacy Gold-era, retired 2023) — reholders to Pristine 10.
-- Fee unknown to operator; left NULL until CGC publishes / operator confirms.
UPDATE master_grade_catalog
   SET reholder_eligible        = TRUE,
       reholder_target_raw_label = 'Pristine 10',
       reholder_fee_min_usd     = NULL,
       reholder_fee_max_usd     = NULL
 WHERE grader = 'CGC' AND raw_label = 'Perfect 10';

-- ============================================================================
-- C. BGS — separate Black Label, Gold Label, and Pristine 10 rows
-- ============================================================================
-- Per operator: BGS has a three-tier top-end. All three are overall-10 slabs
-- but with different subgrade requirements:
--   Black Label (rarest)  — all four subgrades = 10
--   Gold Label  (middle)  — overall 10, subgrades may include 9.5
--   Pristine 10 (lowest)  — overall 10, less-strict subgrade criteria
--
-- Existing master_grade_catalog has only Pristine 10 (which was previously
-- documented as "Black Label maps to Pristine 10" — that mapping is being
-- explicitly broken; Black Label is now its own row).
--
-- ⚠ NAMING — operator to confirm: using 'Black Label 10' / 'Gold Label 10' as
-- the raw_label strings. Alternative is bare 'Black 10' / 'Gold 10'. Match
-- whatever BGS actually prints on the slab label. Easy to rename later if needed.

INSERT INTO master_grade_catalog
  (grader, raw_label, numeric_grade, tier_label, market_equiv_10, reholder_eligible)
SELECT 'BGS', 'Black Label 10', 10.0, 'Black Label', TRUE, FALSE
WHERE NOT EXISTS (
  SELECT 1 FROM master_grade_catalog WHERE grader = 'BGS' AND raw_label = 'Black Label 10'
);

INSERT INTO master_grade_catalog
  (grader, raw_label, numeric_grade, tier_label, market_equiv_10, reholder_eligible)
SELECT 'BGS', 'Gold Label 10', 10.0, 'Gold Label', TRUE, FALSE
WHERE NOT EXISTS (
  SELECT 1 FROM master_grade_catalog WHERE grader = 'BGS' AND raw_label = 'Gold Label 10'
);

-- Cleanup: a prior commit (db93ccf) inserted 'Gold 10' as a placeholder under
-- a different assumption. Remove it if present, since 'Gold Label 10' is the
-- new authoritative naming.
DELETE FROM master_grade_catalog WHERE grader = 'BGS' AND raw_label = 'Gold 10';

-- ============================================================================
-- D. RAW grader sentinel + UNKNOWN grader sentinel
-- ============================================================================
-- grader='RAW'     → card is ungraded/raw (no slab). Lives in the raw_* table
--                    series (see standardization doc §3.5.3). The catalog row
--                    is informational; raw_* tables don't FK-join here.
-- grader='UNKNOWN' → grade could not be parsed from listing title. Distinct
--                    from grader=NULL (which means "data not captured yet").

INSERT INTO master_grade_catalog
  (grader, raw_label, numeric_grade, tier_label, market_equiv_10, reholder_eligible)
SELECT 'RAW', 'Ungraded', NULL, 'Ungraded', FALSE, FALSE
WHERE NOT EXISTS (
  SELECT 1 FROM master_grade_catalog WHERE grader = 'RAW' AND raw_label = 'Ungraded'
);

INSERT INTO master_grade_catalog
  (grader, raw_label, numeric_grade, tier_label, market_equiv_10, reholder_eligible)
SELECT 'UNKNOWN', 'Unparseable', NULL, 'Unparseable', FALSE, FALSE
WHERE NOT EXISTS (
  SELECT 1 FROM master_grade_catalog WHERE grader = 'UNKNOWN' AND raw_label = 'Unparseable'
);

-- ============================================================================
-- E. Fact-table CHECK constraints accept RAW + UNKNOWN
-- ============================================================================
-- ⚠ DECISION DEFERRED: Should graded-table CHECK constraints accept 'RAW'?
-- The operator's spec puts raw cards in their OWN tables (raw_transactions,
-- raw_asks, raw_bids — see standardization doc §3.5.3). Strictly speaking,
-- grader='RAW' should never appear on the graded tables. But leaving 'RAW'
-- in the CHECK is defensive (it means a misrouted insert lands with a clear
-- sentinel rather than being silently rejected). Keeping 'RAW' in the CHECK
-- list below for now; operator can tighten later if desired.

ALTER TABLE ebay_transactions DROP CONSTRAINT IF EXISTS sold_listings_grader_chk;
ALTER TABLE ebay_transactions DROP CONSTRAINT IF EXISTS ebay_transactions_grader_chk;
ALTER TABLE ebay_transactions ADD CONSTRAINT ebay_transactions_grader_chk
  CHECK (grader IS NULL OR grader = ANY (ARRAY['CGC','PSA','BGS','SGC','RAW','UNKNOWN']));

ALTER TABLE courtyard_transactions DROP CONSTRAINT IF EXISTS courtyard_sales_grader_chk;
ALTER TABLE courtyard_transactions DROP CONSTRAINT IF EXISTS courtyard_transactions_grader_chk;
ALTER TABLE courtyard_transactions ADD CONSTRAINT courtyard_transactions_grader_chk
  CHECK (grader IS NULL OR grader = ANY (ARRAY['CGC','PSA','BGS','SGC','RAW','UNKNOWN']));

ALTER TABLE courtyard_asks DROP CONSTRAINT IF EXISTS courtyard_listings_grader_chk;
ALTER TABLE courtyard_asks DROP CONSTRAINT IF EXISTS courtyard_asks_grader_chk;
ALTER TABLE courtyard_asks ADD CONSTRAINT courtyard_asks_grader_chk
  CHECK (grader IS NULL OR grader = ANY (ARRAY['CGC','PSA','BGS','SGC','RAW','UNKNOWN']));

ALTER TABLE collector_crypt_transactions DROP CONSTRAINT IF EXISTS collector_crypt_sales_grader_chk;
ALTER TABLE collector_crypt_transactions DROP CONSTRAINT IF EXISTS collector_crypt_transactions_grader_chk;
ALTER TABLE collector_crypt_transactions ADD CONSTRAINT collector_crypt_transactions_grader_chk
  CHECK (grader IS NULL OR grader = ANY (ARRAY['CGC','PSA','BGS','SGC','RAW','UNKNOWN']));

ALTER TABLE collector_crypt_asks DROP CONSTRAINT IF EXISTS collector_crypt_asks_grader_chk;
ALTER TABLE collector_crypt_asks ADD CONSTRAINT collector_crypt_asks_grader_chk
  CHECK (grader IS NULL OR grader = ANY (ARRAY['CGC','PSA','BGS','SGC','RAW','UNKNOWN']));

COMMIT;

-- ============================================================================
-- Post-apply verification
-- ============================================================================
-- 1. CGC catalog matches authoritative table (26 rows: 24 + Gem Mint 9.5 + Perfect 10 updates):
--    SELECT raw_label, tier_label, numeric_grade, reholder_eligible,
--           reholder_target_raw_label, reholder_fee_min_usd, reholder_fee_max_usd
--      FROM master_grade_catalog WHERE grader = 'CGC'
--     ORDER BY numeric_grade DESC NULLS LAST, raw_label;
--
-- 2. BGS three-tier top end:
--    SELECT raw_label, tier_label FROM master_grade_catalog
--     WHERE grader = 'BGS' AND numeric_grade = 10 ORDER BY raw_label;
--    Expected 3 rows: Black Label 10 / Gold Label 10 / Pristine 10.
--
-- 3. RAW + UNKNOWN sentinels:
--    SELECT grader, raw_label, tier_label FROM master_grade_catalog
--     WHERE grader IN ('RAW','UNKNOWN');
--
-- 4. Reholder eligibility summary:
--    SELECT grader, raw_label, reholder_target_raw_label, reholder_fee_min_usd, reholder_fee_max_usd
--      FROM master_grade_catalog WHERE reholder_eligible = TRUE;
--    Expected 2 rows: CGC Gem Mint 9.5 → Gem Mint 10 ($5–$10),
--                     CGC Perfect 10   → Pristine 10  (NULL–NULL).
--
-- 5. Fact-table CHECK constraints accept new sentinels:
--    INSERT INTO ebay_transactions (item_id, grader, edition, print_variant, platform, currency, raw_payload)
--      VALUES ('test-raw-001', 'RAW', 'N/A', 'Standard', 'ebay', 'USD', '{}'::jsonb);
--    INSERT INTO ebay_transactions (item_id, grader, edition, print_variant, platform, currency, raw_payload)
--      VALUES ('test-unk-001', 'UNKNOWN', 'N/A', 'Standard', 'ebay', 'USD', '{}'::jsonb);
--    DELETE FROM ebay_transactions WHERE item_id IN ('test-raw-001','test-unk-001');
