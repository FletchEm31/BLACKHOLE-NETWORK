CLAUDE CODE BRIEFING — 2026-06-02
TASK: Formally onboard TAG grader into PokemonBHN pipeline
==========================================================

BACKGROUND
----------
TAG (Trading Card Grader) is a UK-based grading company using a 1-10
numeric scale. We have ~1,040 Bronze rows in ebay_transactions where
grader was coerced to NULL by the V8 loader because TAG wasn't in the
schema. The original TAG grader string is preserved in raw_payload.

This task makes TAG a first-class citizen: seeds the grade catalog,
updates constraints, restores the nulled grader values, and re-runs
promotion so TAG rows flow to Silver automatically.

This has been on the pending list since 2026-05-21.

STEP 1 — INSPECT raw_payload FIRST (read-only)
-----------------------------------------------
Before seeding the catalog, check what grade labels actually appear
in the raw Bronze data so we seed exactly what we have:

  sudo -u postgres psql -d eventhorizon <<'SQL'
  -- What TAG grade labels are in raw_payload?
  SELECT
    raw_payload->>'grader'  AS raw_grader,
    raw_payload->>'grade'   AS raw_grade,
    COUNT(*)                AS rows
  FROM ebay_transactions
  WHERE grader IS NULL
    AND raw_payload IS NOT NULL
    AND raw_payload->>'grader' ILIKE '%TAG%'
  GROUP BY 1, 2
  ORDER BY 3 DESC;

  -- How many TAG rows total?
  SELECT COUNT(*) AS tag_rows_to_restore
  FROM ebay_transactions
  WHERE grader IS NULL
    AND raw_payload IS NOT NULL
    AND raw_payload->>'grader' ILIKE '%TAG%';
  SQL

Report the results before proceeding. The grade labels found in
raw_payload are the exact raw_labels to seed into master_grade_catalog.


STEP 2 — SEED master_grade_catalog WITH TAG GRADES
---------------------------------------------------
Based on TAG's published grading scale, seed the following.
Adjust raw_labels if Step 1 shows different formats in the data.

TAG uses numeric grades 1-10 with half-point increments at 9.5 and 7.5.
Both bare numerics AND descriptor labels may appear in eBay titles.

  sudo -u postgres psql -d eventhorizon <<'SQL'
  BEGIN;

  INSERT INTO master_grade_catalog
    (grader, raw_label, numeric_grade, tier_label, market_equiv_10, is_authentic, short_code)
  VALUES
  -- Bare numeric labels (most common in eBay titles)
  ('TAG', '10',   10.0, 'Gem Mint',              TRUE,  TRUE, 'TAG10GM'),
  ('TAG', '9.5',   9.5, 'Mint Plus',             TRUE,  TRUE, 'TAG9.5M+'),
  ('TAG', '9',     9.0, 'Mint',                  FALSE, TRUE, 'TAG9M'),
  ('TAG', '8.5',   8.5, 'Near Mint-Mint Plus',   FALSE, TRUE, 'TAG8.5NM+'),
  ('TAG', '8',     8.0, 'Near Mint-Mint',        FALSE, TRUE, 'TAG8NM'),
  ('TAG', '7.5',   7.5, 'Near Mint Plus',        FALSE, TRUE, 'TAG7.5NM+'),
  ('TAG', '7',     7.0, 'Near Mint',             FALSE, TRUE, 'TAG7NM'),
  ('TAG', '6',     6.0, 'Excellent-Mint',        FALSE, TRUE, 'TAG6EM'),
  ('TAG', '5',     5.0, 'Excellent',             FALSE, TRUE, 'TAG5EX'),
  ('TAG', '4',     4.0, 'Very Good-Excellent',   FALSE, TRUE, 'TAG4VGE'),
  ('TAG', '3',     3.0, 'Very Good',             FALSE, TRUE, 'TAG3VG'),
  ('TAG', '2',     2.0, 'Good',                  FALSE, TRUE, 'TAG2GD'),
  ('TAG', '1',     1.0, 'Poor',                  FALSE, TRUE, 'TAG1PR'),
  -- Descriptor labels (in case eBay titles use full text)
  ('TAG', 'Gem Mint 10',            10.0, 'Gem Mint',            TRUE,  TRUE, 'TAG10GM'),
  ('TAG', 'Mint Plus 9.5',           9.5, 'Mint Plus',           TRUE,  TRUE, 'TAG9.5M+'),
  ('TAG', 'Mint 9',                  9.0, 'Mint',                FALSE, TRUE, 'TAG9M'),
  ('TAG', 'Near Mint-Mint Plus 8.5', 8.5, 'Near Mint-Mint Plus', FALSE, TRUE, 'TAG8.5NM+'),
  ('TAG', 'Near Mint-Mint 8',        8.0, 'Near Mint-Mint',      FALSE, TRUE, 'TAG8NM'),
  ('TAG', 'Near Mint Plus 7.5',      7.5, 'Near Mint Plus',      FALSE, TRUE, 'TAG7.5NM+'),
  ('TAG', 'Near Mint 7',             7.0, 'Near Mint',           FALSE, TRUE, 'TAG7NM')
  ON CONFLICT (grader, raw_label) DO NOTHING;

  -- Verify count
  SELECT COUNT(*) AS tag_grades_added
  FROM master_grade_catalog
  WHERE grader = 'TAG';

  COMMIT;
  SQL

NOTE: If Step 1 reveals additional label formats (e.g. 'TAG 10' with a
space, or different descriptor phrasing), add those rows before
committing. Match exactly what raw_payload contains.


STEP 3 — UPDATE CHECK CONSTRAINTS TO ADD TAG
--------------------------------------------
Every grader CHECK constraint needs TAG added.

  sudo -u postgres psql -d eventhorizon <<'SQL'
  BEGIN;

  -- silver_ebay_transactions
  ALTER TABLE silver_ebay_transactions
    DROP CONSTRAINT IF EXISTS chk_grader;
  ALTER TABLE silver_ebay_transactions
    ADD CONSTRAINT chk_grader
    CHECK (grader IN ('PSA','CGC','BGS','SGC','TAG'));

  -- ebay_transactions (Bronze) — check if a grader constraint exists first
  SELECT conname, pg_get_constraintdef(oid)
  FROM pg_constraint
  WHERE conrelid = 'ebay_transactions'::regclass
    AND contype = 'c'
    AND pg_get_constraintdef(oid) ILIKE '%grader%';
  -- If a grader CHECK exists on Bronze, drop and recreate with TAG included.
  -- If none exists, skip — Bronze uses soft validation.

  -- ebay_asks (if it has a grader CHECK)
  SELECT conname, pg_get_constraintdef(oid)
  FROM pg_constraint
  WHERE conrelid = 'ebay_asks'::regclass
    AND contype = 'c'
    AND pg_get_constraintdef(oid) ILIKE '%grader%';

  COMMIT;
  SQL

Also update collectibles-data-standard.md §3.4 to read:
  `{CGC, PSA, BGS, SGC, TAG}`


STEP 4 — RESTORE grader=NULL ROWS IN BRONZE
--------------------------------------------
Restore the TAG grader value on Bronze rows where it was coerced
to NULL. The original is preserved in raw_payload.

  sudo -u postgres psql -d eventhorizon <<'SQL'
  BEGIN;

  UPDATE ebay_transactions
  SET grader = 'TAG'
  WHERE grader IS NULL
    AND raw_payload IS NOT NULL
    AND raw_payload->>'grader' ILIKE '%TAG%';

  -- Verify
  SELECT grader, COUNT(*) FROM ebay_transactions
  WHERE grader = 'TAG'
  GROUP BY 1;

  COMMIT;
  SQL


STEP 5 — RE-RUN TITLE RE-PARSER AND PROMOTION
----------------------------------------------
Now that TAG rows have grader restored, run the re-parser to
attempt card_id resolution, then promote to Silver.

  -- On LA:
  node infrastructure/scrapers/ebay-title-reparse.js

  -- Then promote
  sudo -u postgres psql -d eventhorizon -c \
    "SELECT promoted, rejected FROM promote_bronze_to_silver();"

  -- Check how many TAG rows made it to Silver
  SELECT grader, COUNT(*)
  FROM silver_ebay_transactions
  WHERE grader = 'TAG'
  GROUP BY 1;


VERIFICATION
------------
  -- master_grade_catalog TAG row count
  SELECT COUNT(*) FROM master_grade_catalog WHERE grader = 'TAG';
  -- Expected: 20 rows (13 bare + 7 descriptor labels)

  -- Bronze TAG rows restored
  SELECT COUNT(*) FROM ebay_transactions WHERE grader = 'TAG';
  -- Expected: ~320 (the two TAG files loaded today)

  -- Silver TAG rows promoted
  SELECT COUNT(*) FROM silver_ebay_transactions WHERE grader = 'TAG';
  -- Depends on card_id resolution rate


COMMIT
------
  Summary: feat: onboard TAG grader — catalog, constraints, Bronze restore, Silver promotion
  Description:
    Seeds TAG grading scale (13 bare numeric + 7 descriptor raw_labels) into
    master_grade_catalog. Updates chk_grader CHECK constraint on
    silver_ebay_transactions to include TAG. Restores grader=NULL on ~320 Bronze
    ebay_transactions rows where V8 loader had coerced TAG to NULL (original
    preserved in raw_payload). Re-runs title re-parser and promote_bronze_to_silver()
    to flow recovered TAG rows into Silver. Updates standard §3.4 grader code set.
    Closes long-pending item from 2026-05-21 handoff.

==========================================================
