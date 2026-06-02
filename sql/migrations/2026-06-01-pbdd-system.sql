-- ============================================================================
-- PBDD SYSTEM MIGRATION — Phases 3 / 4 / 5 of PBDD-IMPLEMENTATION-2026-06-01
-- ============================================================================
-- STAGED 2026-06-01 by Claude Code.  *** NOT YET APPLIED ON LA. ***
-- Reviewable single file; run section by section after a snapshot.
--
-- SNAPSHOT FIRST (hard rule):
--   sudo -u postgres pg_dump eventhorizon \
--     > /mnt/eh-nvme-hot/backups/pre-pbdd-$(date +%Y%m%d-%H%M).sql
--
-- Apply on LA (stdin pipe — postgres can't cd into /root):
--   cat sql/migrations/2026-06-01-pbdd-system.sql \
--     | sudo -u postgres psql -d eventhorizon -v ON_ERROR_STOP=1
--
-- ── PREREQUISITE (Phase 3A) ────────────────────────────────────────────────
-- The 2026-05-28 grade-catalog migration is NOT applied on live (confirmed by
-- 2026-06-01 diagnostics: no short_code/reholder cols, no RAW/UNKNOWN sentinels,
-- no Gold Label 10 — only a stray 'Black Label 10'). It is fully idempotent.
-- RUN IT FIRST, in its own transaction:
--   cat sql/migrations/2026-05-28-grade-catalog-corrections.sql \
--     | sudo -u postgres psql -d eventhorizon -v ON_ERROR_STOP=1
-- That adds: reholder cols, RAW + UNKNOWN sentinels, BGS Gold Label 10, CGC
-- tier_label fixes, and CGC reholder flags (so Phase 3E in the spec is already
-- covered — no separate reholder UPDATE needed here).
--
-- ── SET-CODE DECISION ──────────────────────────────────────────────────────
-- Operator approved (2026-06-01) the BAS rename: BST/FSL/JGL/WSP → BAS/FOS/JUN/WBS,
-- hyphenated→concatenated, STN explicit, no year. This SUPERSEDES the BST-style
-- logic in sql/card-code-system.sql (deprecate that file in Phase 6).
-- ============================================================================


-- ════════════════════════════════════════════════════════════════════════════
-- PHASE 3B / 3D — master_grade_catalog: short_code column + populate 88 rows
-- (Run AFTER the 2026-05-28 migration above, so RAW/UNKNOWN/Gold Label 10 exist.)
-- ════════════════════════════════════════════════════════════════════════════

ALTER TABLE master_grade_catalog ADD COLUMN IF NOT EXISTS short_code TEXT;

COMMENT ON COLUMN master_grade_catalog.short_code IS
  'Abbreviated tier label used as the suffix in pbdd_grade_code. Mapped from tier_label. '
  'NULL for parser-fallback rows (bare ambiguous 10, 9.5) and the RAW/UNKNOWN sentinels — '
  'these never get a short_code.';

BEGIN;

-- ── CGC ─────────────────────────────────────────────────────────────────────
UPDATE master_grade_catalog SET short_code = 'PF'    WHERE grader='CGC' AND raw_label='Perfect 10';
UPDATE master_grade_catalog SET short_code = 'PR'    WHERE grader='CGC' AND raw_label='Pristine 10';
UPDATE master_grade_catalog SET short_code = 'GM'    WHERE grader='CGC' AND raw_label='Gem Mint 10';
UPDATE master_grade_catalog SET short_code = 'GM'    WHERE grader='CGC' AND raw_label='Gem Mint 9.5';
UPDATE master_grade_catalog SET short_code = 'M+'    WHERE grader='CGC' AND raw_label='Mint+ 9.5';
UPDATE master_grade_catalog SET short_code = 'M'     WHERE grader='CGC' AND raw_label='9';
UPDATE master_grade_catalog SET short_code = 'NMM+'  WHERE grader='CGC' AND raw_label='8.5';
UPDATE master_grade_catalog SET short_code = 'NMM'   WHERE grader='CGC' AND raw_label='8';
UPDATE master_grade_catalog SET short_code = 'NM+'   WHERE grader='CGC' AND raw_label='7.5';
UPDATE master_grade_catalog SET short_code = 'NM'    WHERE grader='CGC' AND raw_label='7';
UPDATE master_grade_catalog SET short_code = 'EXM+'  WHERE grader='CGC' AND raw_label='6.5';
UPDATE master_grade_catalog SET short_code = 'EXM'   WHERE grader='CGC' AND raw_label='6';
UPDATE master_grade_catalog SET short_code = 'EX+'   WHERE grader='CGC' AND raw_label='5.5';
UPDATE master_grade_catalog SET short_code = 'EX'    WHERE grader='CGC' AND raw_label='5';
UPDATE master_grade_catalog SET short_code = 'VGE+'  WHERE grader='CGC' AND raw_label='4.5';
UPDATE master_grade_catalog SET short_code = 'VGE'   WHERE grader='CGC' AND raw_label='4';
UPDATE master_grade_catalog SET short_code = 'VG+'   WHERE grader='CGC' AND raw_label='3.5';
UPDATE master_grade_catalog SET short_code = 'VG'    WHERE grader='CGC' AND raw_label='3';
UPDATE master_grade_catalog SET short_code = 'G+'    WHERE grader='CGC' AND raw_label='2.5';
UPDATE master_grade_catalog SET short_code = 'G'     WHERE grader='CGC' AND raw_label='2';
UPDATE master_grade_catalog SET short_code = 'FR'    WHERE grader='CGC' AND raw_label='1.5';
UPDATE master_grade_catalog SET short_code = 'PO'    WHERE grader='CGC' AND raw_label='1';
UPDATE master_grade_catalog SET short_code = 'AU'    WHERE grader='CGC' AND raw_label='AU';
UPDATE master_grade_catalog SET short_code = 'AA'    WHERE grader='CGC' AND raw_label='AA';
-- Parser fallbacks: short_code stays NULL (ambiguous — must not resolve)

-- ── PSA ─────────────────────────────────────────────────────────────────────
UPDATE master_grade_catalog SET short_code = 'GM'    WHERE grader='PSA' AND raw_label='10';
UPDATE master_grade_catalog SET short_code = 'M'     WHERE grader='PSA' AND raw_label='9';
UPDATE master_grade_catalog SET short_code = 'NMT+'  WHERE grader='PSA' AND raw_label='8.5';
UPDATE master_grade_catalog SET short_code = 'NMT'   WHERE grader='PSA' AND raw_label='8';
UPDATE master_grade_catalog SET short_code = 'NM+'   WHERE grader='PSA' AND raw_label='7.5';
UPDATE master_grade_catalog SET short_code = 'NM'    WHERE grader='PSA' AND raw_label='7';
UPDATE master_grade_catalog SET short_code = 'EMT+'  WHERE grader='PSA' AND raw_label='6.5';
UPDATE master_grade_catalog SET short_code = 'EMT'   WHERE grader='PSA' AND raw_label='6';
UPDATE master_grade_catalog SET short_code = 'EX+'   WHERE grader='PSA' AND raw_label='5.5';
UPDATE master_grade_catalog SET short_code = 'EX'    WHERE grader='PSA' AND raw_label='5';
UPDATE master_grade_catalog SET short_code = 'VGE+'  WHERE grader='PSA' AND raw_label='4.5';
UPDATE master_grade_catalog SET short_code = 'VGE'   WHERE grader='PSA' AND raw_label='4';
UPDATE master_grade_catalog SET short_code = 'VG+'   WHERE grader='PSA' AND raw_label='3.5';
UPDATE master_grade_catalog SET short_code = 'VG'    WHERE grader='PSA' AND raw_label='3';
UPDATE master_grade_catalog SET short_code = 'G+'    WHERE grader='PSA' AND raw_label='2.5';
UPDATE master_grade_catalog SET short_code = 'G'     WHERE grader='PSA' AND raw_label='2';
UPDATE master_grade_catalog SET short_code = 'FR'    WHERE grader='PSA' AND raw_label='1.5';
UPDATE master_grade_catalog SET short_code = 'FR'    WHERE grader='PSA' AND raw_label='FR';
UPDATE master_grade_catalog SET short_code = 'PO'    WHERE grader='PSA' AND raw_label='1';
UPDATE master_grade_catalog SET short_code = 'AUTH'  WHERE grader='PSA' AND raw_label='Authentic';

-- ── BGS ─────────────────────────────────────────────────────────────────────
UPDATE master_grade_catalog SET short_code = 'BL'    WHERE grader='BGS' AND raw_label='Black Label 10';
UPDATE master_grade_catalog SET short_code = 'GL'    WHERE grader='BGS' AND raw_label='Gold Label 10';
UPDATE master_grade_catalog SET short_code = 'PR'    WHERE grader='BGS' AND raw_label='Pristine 10';
UPDATE master_grade_catalog SET short_code = 'GM'    WHERE grader='BGS' AND raw_label='Gem Mint 9.5';
UPDATE master_grade_catalog SET short_code = 'M'     WHERE grader='BGS' AND raw_label='Mint 9';
UPDATE master_grade_catalog SET short_code = 'NMM+'  WHERE grader='BGS' AND raw_label='Near Mint-Mint+ 8.5';
UPDATE master_grade_catalog SET short_code = 'NMM'   WHERE grader='BGS' AND raw_label='Near Mint-Mint 8';
UPDATE master_grade_catalog SET short_code = 'NM+'   WHERE grader='BGS' AND raw_label='Near Mint+ 7.5';
UPDATE master_grade_catalog SET short_code = 'NM'    WHERE grader='BGS' AND raw_label='Near Mint 7';
UPDATE master_grade_catalog SET short_code = 'EXM+'  WHERE grader='BGS' AND raw_label='Excellent-Mint+ 6.5';
UPDATE master_grade_catalog SET short_code = 'EXM'   WHERE grader='BGS' AND raw_label='Excellent-Mint 6';
UPDATE master_grade_catalog SET short_code = 'EX+'   WHERE grader='BGS' AND raw_label='Excellent+ 5.5';
UPDATE master_grade_catalog SET short_code = 'EX'    WHERE grader='BGS' AND raw_label='Excellent 5';
UPDATE master_grade_catalog SET short_code = 'VGE+'  WHERE grader='BGS' AND raw_label='Very Good-Excellent+ 4.5';
UPDATE master_grade_catalog SET short_code = 'VGE'   WHERE grader='BGS' AND raw_label='Very Good-Excellent 4';
UPDATE master_grade_catalog SET short_code = 'VG+'   WHERE grader='BGS' AND raw_label='Very Good+ 3.5';
UPDATE master_grade_catalog SET short_code = 'VG'    WHERE grader='BGS' AND raw_label='Very Good 3';
UPDATE master_grade_catalog SET short_code = 'G+'    WHERE grader='BGS' AND raw_label='Good+ 2.5';
UPDATE master_grade_catalog SET short_code = 'G'     WHERE grader='BGS' AND raw_label='Good 2';
UPDATE master_grade_catalog SET short_code = 'FR'    WHERE grader='BGS' AND raw_label='Fair 1.5';
UPDATE master_grade_catalog SET short_code = 'PO'    WHERE grader='BGS' AND raw_label='Poor 1';
UPDATE master_grade_catalog SET short_code = 'AUTH'  WHERE grader='BGS' AND raw_label='Authentic';
UPDATE master_grade_catalog SET short_code = 'INC'   WHERE grader='BGS' AND raw_label='BGS 0.5';

-- ── SGC ─────────────────────────────────────────────────────────────────────
UPDATE master_grade_catalog SET short_code = 'PR'    WHERE grader='SGC' AND raw_label='Pristine 10';
UPDATE master_grade_catalog SET short_code = 'GM'    WHERE grader='SGC' AND raw_label='Gem Mint 10';
UPDATE master_grade_catalog SET short_code = 'GM'    WHERE grader='SGC' AND raw_label='Gem Mint 9.5';
UPDATE master_grade_catalog SET short_code = 'GM'    WHERE grader='SGC' AND raw_label='Gem Mint 9';
UPDATE master_grade_catalog SET short_code = 'NMM+'  WHERE grader='SGC' AND raw_label='Near Mint/Mint+ 8.5';
UPDATE master_grade_catalog SET short_code = 'NMM'   WHERE grader='SGC' AND raw_label='Near Mint/Mint 8';
UPDATE master_grade_catalog SET short_code = 'NM+'   WHERE grader='SGC' AND raw_label='Near Mint+ 7.5';
UPDATE master_grade_catalog SET short_code = 'NM'    WHERE grader='SGC' AND raw_label='Near Mint 7';
UPDATE master_grade_catalog SET short_code = 'ENM+'  WHERE grader='SGC' AND raw_label='Excellent/Near Mint+ 6.5';
UPDATE master_grade_catalog SET short_code = 'ENM'   WHERE grader='SGC' AND raw_label='Excellent/Near Mint 6';
UPDATE master_grade_catalog SET short_code = 'EX+'   WHERE grader='SGC' AND raw_label='Excellent+ 5.5';
UPDATE master_grade_catalog SET short_code = 'EX'    WHERE grader='SGC' AND raw_label='Excellent 5';
UPDATE master_grade_catalog SET short_code = 'VGE+'  WHERE grader='SGC' AND raw_label='Very Good/Excellent+ 4.5';
UPDATE master_grade_catalog SET short_code = 'VGE'   WHERE grader='SGC' AND raw_label='Very Good/Excellent 4';
UPDATE master_grade_catalog SET short_code = 'VG+'   WHERE grader='SGC' AND raw_label='Very Good+ 3.5';
UPDATE master_grade_catalog SET short_code = 'VG'    WHERE grader='SGC' AND raw_label='Very Good 3';
UPDATE master_grade_catalog SET short_code = 'G+'    WHERE grader='SGC' AND raw_label='Good+ 2.5';
UPDATE master_grade_catalog SET short_code = 'G'     WHERE grader='SGC' AND raw_label='Good 2';
UPDATE master_grade_catalog SET short_code = 'FR'    WHERE grader='SGC' AND raw_label='Fair 1.5';
UPDATE master_grade_catalog SET short_code = 'PO'    WHERE grader='SGC' AND raw_label='Poor 1.5';
UPDATE master_grade_catalog SET short_code = 'PO'    WHERE grader='SGC' AND raw_label='Poor 1';
UPDATE master_grade_catalog SET short_code = 'AUTH'  WHERE grader='SGC' AND raw_label='Authentic';

-- VERIFY before COMMIT: rows lacking short_code should be ONLY parser-fallbacks
-- (bare ambiguous CGC '10'/'9.5' if present) + the RAW/UNKNOWN sentinels.
SELECT grader, raw_label, short_code
FROM master_grade_catalog
WHERE short_code IS NULL
ORDER BY grader, raw_label;

-- Switch to ROLLBACK; if the NULL set contains anything unexpected.
COMMIT;

-- ── Phase 3F — GRANTs (additive SELECT; verify) ─────────────────────────────
GRANT SELECT ON master_grade_catalog TO ehuser;
GRANT SELECT ON master_grade_catalog TO agent_reader;
-- SELECT grantee, privilege_type FROM information_schema.role_table_grants
--  WHERE table_name = 'master_grade_catalog';


-- ════════════════════════════════════════════════════════════════════════════
-- PHASE 4 — card_code REGENERATION to PBDD/BAS format
-- ⚠ Operator-approved 2026-06-01. SNAPSHOT must already exist (see header).
-- Overwrites all 1,354 card_code rows: BST-004-1E[-HOL] → BAS004-1E-STN / -HOLO.
-- ════════════════════════════════════════════════════════════════════════════

-- 4A — set_code on master_set_catalog → BAS family (overwrites BST family).
-- Column already exists (UNIQUE) from card-code-system.sql; just re-point values.
UPDATE master_set_catalog SET set_code = 'BAS' WHERE set_name = 'Base Set';
UPDATE master_set_catalog SET set_code = 'FOS' WHERE set_name = 'Fossil';
UPDATE master_set_catalog SET set_code = 'JUN' WHERE set_name = 'Jungle';
UPDATE master_set_catalog SET set_code = 'TRK' WHERE set_name = 'Team Rocket';
UPDATE master_set_catalog SET set_code = 'GYH' WHERE set_name = 'Gym Heroes';
UPDATE master_set_catalog SET set_code = 'GYC' WHERE set_name = 'Gym Challenge';
UPDATE master_set_catalog SET set_code = 'BOG' WHERE set_name = 'Best of Game';
UPDATE master_set_catalog SET set_code = 'WBS' WHERE set_name = 'Wizards Black Star Promos';

GRANT SELECT ON master_set_catalog TO ehuser;
GRANT SELECT ON master_set_catalog TO agent_reader;

-- 4B — regenerate card_code: [SETCODE+NUM]-[EDITION]-[VARIANT], STN explicit, no year.
BEGIN;

UPDATE master_card_catalog mcc
SET card_code = (
  SELECT
    msc.set_code
    || LPAD(REGEXP_REPLACE(mcc.card_number, '[^0-9]', '', 'g'), 3, '0')
    || '-'
    || CASE mcc.edition
         WHEN '1st Edition' THEN '1E'
         WHEN 'Unlimited'   THEN 'UN'
         WHEN 'Shadowless'  THEN 'SH'
         WHEN 'N/A'         THEN 'NA'
         ELSE mcc.edition
       END
    || '-'
    || CASE mcc.print_variant
         WHEN 'Standard'            THEN 'STN'
         WHEN 'Holo'                THEN 'HOLO'
         WHEN 'Error'               THEN 'ERR'
         WHEN 'No Symbol'           THEN 'NOSYM'
         WHEN 'W Stamp'             THEN 'WSTAMP'
         WHEN 'Winner'              THEN 'WIN'
         WHEN 'Jumbo'               THEN 'JUMBO'
         WHEN 'Prerelease'          THEN 'PRE'
         WHEN 'Gold Border'         THEN 'GOLD'
         WHEN 'Red Cheeks'          THEN 'RCK'
         WHEN 'WB Movie'            THEN 'WBM'
         WHEN 'Nintendo Power'      THEN 'NP'
         WHEN 'WOTC'                THEN 'WOTC'
         WHEN '1999-2000 Copyright' THEN 'C2000'
         ELSE mcc.print_variant
       END
  FROM master_set_catalog msc
  WHERE msc.set_name = mcc.set_name
);

-- VERIFY sample before COMMIT (expect BAS004-1E-STN style):
SELECT card_code, set_name, card_number, edition, print_variant
FROM master_card_catalog
ORDER BY set_name, card_number::int
LIMIT 20;

-- VERIFY no NULL / suspect values (expect total=with_code, suspect=0):
SELECT COUNT(*) AS total, COUNT(card_code) AS with_code,
       COUNT(CASE WHEN card_code LIKE '%None%' OR card_code LIKE '%null%'
                  OR card_code LIKE '%-%-%-%-%' THEN 1 END) AS suspect
FROM master_card_catalog;

-- If correct: COMMIT.  If anything is wrong: change to ROLLBACK.
COMMIT;


-- ════════════════════════════════════════════════════════════════════════════
-- PHASE 5 — pbdd_grade_code()  (replaces slab_code(); old kept until Phase 6)
-- Graded: {pbdd_code}-{GRADER}{NUMERIC}{SHORT_CODE}   e.g. TRK014-1E-HOLO-PSA10GM
-- Raw:    {pbdd_code}-RAW[-{CONDITION}]               e.g. TRK014-1E-HOLO-RAW-NM
-- ════════════════════════════════════════════════════════════════════════════
-- ⚠ DEVIATION FROM SPEC §5B (deliberate bug fix):
-- The spec's pure-SQL version selects the CASE FROM a (grader,grade) subquery.
-- When p_grade IS NULL (every RAW/UNKNOWN call) that subquery returns zero rows,
-- so the whole SELECT collapses to NULL — the RAW branch never fires. Rewritten
-- in PL/pgSQL so the RAW/UNKNOWN paths actually return. Output is IDENTICAL to
-- the spec for all 6 documented probe cases (5C); only the broken NULL cases fixed.
-- UNKNOWN grader returns NULL (grade unparseable → no tier code can be formed).

CREATE OR REPLACE FUNCTION pbdd_grade_code(
  p_pbdd_code TEXT,
  p_grader    TEXT,
  p_grade     TEXT,
  p_condition TEXT DEFAULT NULL   -- raw condition: NM/LP/MP/HP/DMG (optional)
) RETURNS TEXT AS $$
DECLARE
  v_numeric TEXT;
  v_short   TEXT;
BEGIN
  IF p_pbdd_code IS NULL THEN
    RETURN NULL;
  END IF;

  -- Raw / ungraded card (sentinel RAW, or no grader at all).
  IF p_grader IS NULL OR p_grader = 'RAW' THEN
    IF p_condition IS NOT NULL THEN
      RETURN p_pbdd_code || '-RAW-' || p_condition;
    END IF;
    RETURN p_pbdd_code || '-RAW';
  END IF;

  -- Grade observed but unparseable: no tier code possible.
  IF p_grader = 'UNKNOWN' OR p_grade IS NULL THEN
    RETURN NULL;
  END IF;

  -- Graded: resolve numeric + short_code from the catalog.
  SELECT numeric_grade::TEXT, short_code
    INTO v_numeric, v_short
    FROM master_grade_catalog
   WHERE grader = p_grader AND raw_label = p_grade
   LIMIT 1;

  -- Format numeric without a trailing .0 (standard: '10' not '10.0'; '9.5' stays '9.5').
  -- Only strip when a decimal point is present, so integer grades like '10' aren't mangled.
  IF v_numeric IS NOT NULL AND position('.' IN v_numeric) > 0 THEN
    v_numeric := rtrim(rtrim(v_numeric, '0'), '.');
  END IF;

  RETURN p_pbdd_code || '-' || p_grader
         || COALESCE(v_numeric, p_grade)
         || COALESCE(v_short, '');
END;
$$ LANGUAGE plpgsql STABLE;

-- Carry forward ALL FOUR grantees from the live slab_code() grant (the n8n
-- arbitrage workflow calls it as n8n_user — granting only ehuser/agent_reader
-- as the spec §5B says would break that workflow).
GRANT EXECUTE ON FUNCTION pbdd_grade_code(TEXT, TEXT, TEXT, TEXT)
  TO n8n_user, log_shipper, ehuser, agent_reader;

-- Verify (5C) — expected outputs in comments:
-- SELECT pbdd_grade_code('TRK014-1E-HOLO','PSA','10');            -- TRK014-1E-HOLO-PSA10GM
-- SELECT pbdd_grade_code('TRK014-1E-HOLO','CGC','Pristine 10');   -- TRK014-1E-HOLO-CGC10PR
-- SELECT pbdd_grade_code('TRK014-1E-HOLO','CGC','Mint+ 9.5');     -- TRK014-1E-HOLO-CGC9.5M+
-- SELECT pbdd_grade_code('TRK014-1E-HOLO','CGC','Gem Mint 9.5');  -- TRK014-1E-HOLO-CGC9.5GM
-- SELECT pbdd_grade_code('TRK014-1E-HOLO','RAW',NULL,'NM');       -- TRK014-1E-HOLO-RAW-NM
-- SELECT pbdd_grade_code('TRK014-1E-HOLO','RAW',NULL,NULL);       -- TRK014-1E-HOLO-RAW

-- Old slab_code() is intentionally NOT dropped here. Drop it in Phase 6 AFTER the
-- n8n arbitrage workflow + any SQL callers are migrated to pbdd_grade_code():
--   DROP FUNCTION IF EXISTS slab_code(TEXT, TEXT, TEXT);
