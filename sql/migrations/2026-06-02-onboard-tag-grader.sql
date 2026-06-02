-- ============================================================================
-- PokemonBHN — Onboard TAG grader (Trading Card Grader, UK, 1–10 scale)
-- ============================================================================
-- Briefing: infrastructure/docs/BHN session updates/CLAUDE_CODE_BRIEFING_TAG_GRADER.md
-- STEP 1 recon (2026-06-02) corrected two briefing assumptions:
--   (a) TAG signal lives in title_raw (160) + raw_payload->>'original_grader' (61) = 167 rows,
--       NOT raw_payload->>'grader' (0).
--   (b) grades in data are bare numerics incl 6.5/4.5/3.5/2.5 (briefing omitted these) and NO 9.5.
-- This migration seeds the catalog, adds TAG to grader CHECKs, and restores grader+grade on the
-- 167 Bronze rows (grade set FK-safely — only to a seeded TAG raw_label, else NULL).
--
-- ACE grader intentionally SKIPPED — no ACE data exists.
-- ============================================================================

\set ON_ERROR_STOP on
BEGIN;

-- ── STEP 2 — seed master_grade_catalog with TAG grades ───────────────────────
-- Bare numerics cover every grade observed in title_raw (10..2.5) + briefing's 9.5/2/1.
-- Half-grade tier_labels below 7.5 (6.5/4.5/3.5/2.5) are interpolated from TAG's scale
-- (exact published names unconfirmed; only raw_label matters for the FK).
INSERT INTO master_grade_catalog
  (grader, raw_label, numeric_grade, tier_label, market_equiv_10, is_authentic, short_code)
VALUES
  ('TAG','10',  10.0,'Gem Mint',               TRUE,  TRUE,'TAG10GM'),
  ('TAG','9.5',  9.5,'Mint Plus',              TRUE,  TRUE,'TAG9.5M+'),
  ('TAG','9',    9.0,'Mint',                   FALSE, TRUE,'TAG9M'),
  ('TAG','8.5',  8.5,'Near Mint-Mint Plus',    FALSE, TRUE,'TAG8.5NM+'),
  ('TAG','8',    8.0,'Near Mint-Mint',         FALSE, TRUE,'TAG8NM'),
  ('TAG','7.5',  7.5,'Near Mint Plus',         FALSE, TRUE,'TAG7.5NM+'),
  ('TAG','7',    7.0,'Near Mint',              FALSE, TRUE,'TAG7NM'),
  ('TAG','6.5',  6.5,'Excellent-Mint Plus',    FALSE, TRUE,'TAG6.5EM+'),
  ('TAG','6',    6.0,'Excellent-Mint',         FALSE, TRUE,'TAG6EM'),
  ('TAG','5',    5.0,'Excellent',              FALSE, TRUE,'TAG5EX'),
  ('TAG','4.5',  4.5,'Very Good-Excellent Plus',FALSE,TRUE,'TAG4.5VGE+'),
  ('TAG','4',    4.0,'Very Good-Excellent',    FALSE, TRUE,'TAG4VGE'),
  ('TAG','3.5',  3.5,'Very Good Plus',         FALSE, TRUE,'TAG3.5VG+'),
  ('TAG','3',    3.0,'Very Good',              FALSE, TRUE,'TAG3VG'),
  ('TAG','2.5',  2.5,'Good Plus',              FALSE, TRUE,'TAG2.5GD+'),
  ('TAG','2',    2.0,'Good',                   FALSE, TRUE,'TAG2GD'),
  ('TAG','1',    1.0,'Poor',                   FALSE, TRUE,'TAG1PR'),
  -- Descriptor labels (briefing — none seen in data yet, harmless for future titles)
  ('TAG','Gem Mint 10',            10.0,'Gem Mint',            TRUE,  TRUE,'TAG10GM'),
  ('TAG','Mint Plus 9.5',           9.5,'Mint Plus',           TRUE,  TRUE,'TAG9.5M+'),
  ('TAG','Mint 9',                  9.0,'Mint',                FALSE, TRUE,'TAG9M'),
  ('TAG','Near Mint-Mint Plus 8.5', 8.5,'Near Mint-Mint Plus', FALSE, TRUE,'TAG8.5NM+'),
  ('TAG','Near Mint-Mint 8',        8.0,'Near Mint-Mint',      FALSE, TRUE,'TAG8NM'),
  ('TAG','Near Mint Plus 7.5',      7.5,'Near Mint Plus',      FALSE, TRUE,'TAG7.5NM+'),
  ('TAG','Near Mint 7',             7.0,'Near Mint',           FALSE, TRUE,'TAG7NM')
ON CONFLICT (grader, raw_label) DO NOTHING;

-- ── STEP 3 — add TAG to grader CHECK constraints ─────────────────────────────
ALTER TABLE ebay_transactions DROP CONSTRAINT IF EXISTS ebay_transactions_grader_chk;
ALTER TABLE ebay_transactions ADD  CONSTRAINT ebay_transactions_grader_chk
  CHECK (grader IS NULL OR grader = ANY (ARRAY['CGC','PSA','BGS','SGC','RAW','UNKNOWN','TAG']));

ALTER TABLE ebay_asks DROP CONSTRAINT IF EXISTS ebay_listings_grader_chk;
ALTER TABLE ebay_asks ADD  CONSTRAINT ebay_listings_grader_chk
  CHECK (grader IS NULL OR grader = ANY (ARRAY['CGC','PSA','BGS','SGC','TAG']));

ALTER TABLE silver_ebay_transactions DROP CONSTRAINT IF EXISTS chk_grader;
ALTER TABLE silver_ebay_transactions ADD  CONSTRAINT chk_grader
  CHECK (grader = ANY (ARRAY['PSA','CGC','BGS','SGC','TAG']));

-- ── STEP 4 — restore grader (+grade) on the 167 TAG Bronze rows ──────────────
-- grade is set ONLY to a value that exists as a TAG raw_label (FK-safe); otherwise NULL.
UPDATE ebay_transactions e
SET grader = 'TAG',
    grade  = (
      SELECT mgc.raw_label FROM master_grade_catalog mgc
      WHERE mgc.grader = 'TAG'
        AND mgc.raw_label = COALESCE(
              e.grade,
              substring(e.title_raw from '(?i)\mtag[ -]?([0-9]+\.?[0-9]*)'))
    )
WHERE e.grader IS NULL
  AND ( e.title_raw ~* '\mtag[ -]?[0-9]'
        OR e.raw_payload->>'original_grader' ILIKE '%TAG%' );

COMMIT;

-- ── Post-apply verification ──────────────────────────────────────────────────
SELECT 'tag_grades_in_catalog' AS k, COUNT(*) FROM master_grade_catalog WHERE grader='TAG';
SELECT 'bronze_tag_rows' AS k, COUNT(*) AS rows, COUNT(grade) AS with_grade FROM ebay_transactions WHERE grader='TAG';
