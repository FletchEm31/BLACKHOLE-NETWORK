-- BGS/SGC catalog reconciliation (2026-06-01).
-- Scheme (operator-chosen): NUMERIC raw_labels for grades <10, NAMED only at the 10-tiers
-- (matches CGC's pattern + the ~259 existing ebay_transactions BGS/SGC rows). The FK
-- prerequisite is already met by the existing numeric rows; this migration is completion
-- polish only — data-only UPDATEs, no schema change, reversible.
--   A. short_code on numeric BGS rows   B. short_code on numeric SGC rows
--   C. SGC tier_label factual fixes (Gem Mint to 9; Pristine annotation)
--   D. BGS 10-tier tier_label annotation fixes
-- Bare '10' (BGS + SGC) intentionally keeps short_code NULL — ambiguous across the 10-tiers.
-- Short codes sourced from infrastructure/docs/pokemonbhn/BHN-Grade-Label-Reference.md.

BEGIN;

-- ── A. BGS numeric short_codes ───────────────────────────────────────────────
UPDATE master_grade_catalog SET short_code='GM'   WHERE grader='BGS' AND raw_label='9.5';
UPDATE master_grade_catalog SET short_code='M'    WHERE grader='BGS' AND raw_label='9';
UPDATE master_grade_catalog SET short_code='NMM+' WHERE grader='BGS' AND raw_label='8.5';
UPDATE master_grade_catalog SET short_code='NMM'  WHERE grader='BGS' AND raw_label='8';
UPDATE master_grade_catalog SET short_code='NM+'  WHERE grader='BGS' AND raw_label='7.5';
UPDATE master_grade_catalog SET short_code='NM'   WHERE grader='BGS' AND raw_label='7';
UPDATE master_grade_catalog SET short_code='EXM+' WHERE grader='BGS' AND raw_label='6.5';
UPDATE master_grade_catalog SET short_code='EXM'  WHERE grader='BGS' AND raw_label='6';
UPDATE master_grade_catalog SET short_code='EX+'  WHERE grader='BGS' AND raw_label='5.5';
UPDATE master_grade_catalog SET short_code='EX'   WHERE grader='BGS' AND raw_label='5';
UPDATE master_grade_catalog SET short_code='VGE+' WHERE grader='BGS' AND raw_label='4.5';
UPDATE master_grade_catalog SET short_code='VGE'  WHERE grader='BGS' AND raw_label='4';
UPDATE master_grade_catalog SET short_code='VG+'  WHERE grader='BGS' AND raw_label='3.5';
UPDATE master_grade_catalog SET short_code='VG'   WHERE grader='BGS' AND raw_label='3';
UPDATE master_grade_catalog SET short_code='G+'   WHERE grader='BGS' AND raw_label='2.5';
UPDATE master_grade_catalog SET short_code='G'    WHERE grader='BGS' AND raw_label='2';
UPDATE master_grade_catalog SET short_code='FR'   WHERE grader='BGS' AND raw_label='1.5';
UPDATE master_grade_catalog SET short_code='PO'   WHERE grader='BGS' AND raw_label='1';

-- ── B. SGC numeric short_codes (SGC uses Gem Mint to 9; EX/NM tiers per reference) ──
UPDATE master_grade_catalog SET short_code='GM'   WHERE grader='SGC' AND raw_label='9.5';
UPDATE master_grade_catalog SET short_code='GM'   WHERE grader='SGC' AND raw_label='9';
UPDATE master_grade_catalog SET short_code='NMM+' WHERE grader='SGC' AND raw_label='8.5';
UPDATE master_grade_catalog SET short_code='NMM'  WHERE grader='SGC' AND raw_label='8';
UPDATE master_grade_catalog SET short_code='NM+'  WHERE grader='SGC' AND raw_label='7.5';
UPDATE master_grade_catalog SET short_code='NM'   WHERE grader='SGC' AND raw_label='7';
UPDATE master_grade_catalog SET short_code='ENM+' WHERE grader='SGC' AND raw_label='6.5';
UPDATE master_grade_catalog SET short_code='ENM'  WHERE grader='SGC' AND raw_label='6';
UPDATE master_grade_catalog SET short_code='EX+'  WHERE grader='SGC' AND raw_label='5.5';
UPDATE master_grade_catalog SET short_code='EX'   WHERE grader='SGC' AND raw_label='5';
UPDATE master_grade_catalog SET short_code='VGE+' WHERE grader='SGC' AND raw_label='4.5';
UPDATE master_grade_catalog SET short_code='VGE'  WHERE grader='SGC' AND raw_label='4';
UPDATE master_grade_catalog SET short_code='VG+'  WHERE grader='SGC' AND raw_label='3.5';
UPDATE master_grade_catalog SET short_code='VG'   WHERE grader='SGC' AND raw_label='3';
UPDATE master_grade_catalog SET short_code='G+'   WHERE grader='SGC' AND raw_label='2.5';
UPDATE master_grade_catalog SET short_code='G'    WHERE grader='SGC' AND raw_label='2';
UPDATE master_grade_catalog SET short_code='FR'   WHERE grader='SGC' AND raw_label='1.5';
UPDATE master_grade_catalog SET short_code='PO'   WHERE grader='SGC' AND raw_label='1';

-- ── C. SGC tier_label factual fixes ──────────────────────────────────────────
UPDATE master_grade_catalog SET tier_label='Gem Mint' WHERE grader='SGC' AND raw_label='9.5';  -- was 'Mint+'
UPDATE master_grade_catalog SET tier_label='Gem Mint' WHERE grader='SGC' AND raw_label='9';    -- was 'Mint'
UPDATE master_grade_catalog SET tier_label='Pristine' WHERE grader='SGC' AND raw_label='Pristine 10'; -- was 'Pristine (Gold Label)'

-- ── D. BGS 10-tier tier_label annotation fixes (Pristine != Gold/Black Label) ──
UPDATE master_grade_catalog SET tier_label='Black Label' WHERE grader='BGS' AND raw_label='Black Label 10'; -- was 'Pristine (Black Label)'
UPDATE master_grade_catalog SET tier_label='Pristine'    WHERE grader='BGS' AND raw_label='Pristine 10';    -- was 'Pristine (Gold Label)'

-- Verify: only the ambiguous bare '10' rows should remain NULL short_code for BGS/SGC.
SELECT grader, raw_label, numeric_grade, tier_label, short_code
FROM master_grade_catalog
WHERE grader IN ('BGS','SGC') AND short_code IS NULL
ORDER BY grader, raw_label;

COMMIT;
