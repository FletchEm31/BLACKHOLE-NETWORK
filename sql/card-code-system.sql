-- card-code-system.sql
-- ⚠️ DEPRECATED 2026-06-01 — SUPERSEDED by the PBDD overhaul. DO NOT RE-RUN.
--   This file populated card_code in the legacy BST-style format
--   (BST-004-1E, hyphenated, Standard variant omitted) and defined slab_code().
--   The PBDD migration `sql/migrations/2026-06-01-pbdd-system.sql` re-points the
--   set_codes to the BAS family and regenerates card_code to the BAS-style format
--   (BAS004-1E-STN, concatenated, STN explicit, no year), and replaces slab_code()
--   with pbdd_grade_code(). Re-running THIS file would overwrite the live card_code
--   back to the obsolete BST format. Kept for history only.
--   Authoritative card_code/pbdd_grade_code definitions: infrastructure/docs/pokemonbhn/collectibles-data-standard.md §2.1–2.2.
--
-- BHN — card_code + slab_code human-readable identifier system. (LEGACY — see banner above.)
-- Spec: infrastructure/docs/BHN session updates/BHN-SESSION-HANDOFF/BHN-CARD-CODE-SPEC-2026-05-27.txt
--
-- Two derived identifiers:
--   card_code   = stored on master_card_catalog (e.g. BST-004-1E)
--                 set_code + zero-padded card_number + edition_code [+ variant_code]
--   slab_code   = derived on demand (e.g. BST-004-1E-PSA-10)
--                 card_code + grader + numeric_grade
--
-- card_id (INTEGER) remains the database join key. card_code is display ONLY.
-- slab_code is NEVER stored — always recomputable from its parts.
--
-- Apply on LA hub (stdin pipe — postgres can't cd into /root):
--   cat sql/card-code-system.sql | sudo -u postgres psql -d eventhorizon -v ON_ERROR_STOP=1
--
-- Idempotent. Depends on card-id-resolver.sql having run first (strips '#'
-- from master_card_catalog.card_number — required for correct zero-padding).

-- ─────────────────────────────────────────────────────────────────────────────
-- STEP 1 — set_code on master_set_catalog (3-letter, UNIQUE)
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE master_set_catalog ADD COLUMN IF NOT EXISTS set_code TEXT UNIQUE;

UPDATE master_set_catalog SET set_code = 'BST' WHERE set_name = 'Base Set';
UPDATE master_set_catalog SET set_code = 'FSL' WHERE set_name = 'Fossil';
UPDATE master_set_catalog SET set_code = 'JGL' WHERE set_name = 'Jungle';
UPDATE master_set_catalog SET set_code = 'TRK' WHERE set_name = 'Team Rocket';
UPDATE master_set_catalog SET set_code = 'GYH' WHERE set_name = 'Gym Heroes';
UPDATE master_set_catalog SET set_code = 'GYC' WHERE set_name = 'Gym Challenge';
UPDATE master_set_catalog SET set_code = 'WSP' WHERE set_name = 'Wizards Black Star Promos';
UPDATE master_set_catalog SET set_code = 'BOG' WHERE set_name = 'Best of Game';


-- ─────────────────────────────────────────────────────────────────────────────
-- STEP 2 — card_code on master_card_catalog (display identifier)
-- Format: SET_CODE-NNN-EDITION[-VARIANT]
-- VARIANT segment is OMITTED when print_variant='Standard' (the default).
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE master_card_catalog ADD COLUMN IF NOT EXISTS card_code TEXT;

UPDATE master_card_catalog mc
   SET card_code = CONCAT(
       ms.set_code,
       '-',
       LPAD(
           regexp_replace(mc.card_number, '^#', '', 'g'),  -- defensive: strip '#' if drift returns
           3, '0'
       ),
       '-',
       CASE mc.edition
           WHEN '1st Edition' THEN '1E'
           WHEN 'Shadowless'  THEN 'SH'
           WHEN 'Unlimited'   THEN 'UN'
           WHEN 'N/A'         THEN 'NA'
       END,
       CASE mc.print_variant
           WHEN 'Standard'            THEN ''
           WHEN 'Holo'                THEN '-HOL'
           WHEN 'Error'               THEN '-ERR'
           WHEN 'No Symbol'           THEN '-NOS'
           WHEN 'W Stamp'             THEN '-WST'
           WHEN 'Winner'              THEN '-WIN'
           WHEN 'Jumbo'               THEN '-JMB'
           WHEN 'Prerelease'          THEN '-PRE'
           WHEN 'Gold Border'         THEN '-GLB'
           WHEN 'Red Cheeks'          THEN '-RCK'
           WHEN 'WB Movie'            THEN '-WBM'
           WHEN 'Nintendo Power'      THEN '-NTP'
           WHEN 'WOTC'                THEN '-WTC'
           WHEN '1999-2000 Copyright' THEN '-C99'
           ELSE ''
       END
   )
  FROM master_set_catalog ms
 WHERE mc.set_name = ms.set_name;

-- UNIQUE constraint (idempotent — drop-if-exists pattern, since DDL ALTER ADD CONSTRAINT
-- IF NOT EXISTS is not supported on constraints in PG 14).
ALTER TABLE master_card_catalog DROP CONSTRAINT IF EXISTS master_card_catalog_card_code_uq;
ALTER TABLE master_card_catalog ADD  CONSTRAINT master_card_catalog_card_code_uq UNIQUE (card_code);


-- ─────────────────────────────────────────────────────────────────────────────
-- STEP 3 — slab_code() function (derived on demand, never stored)
-- Format: CARD_CODE-GRADER-NUMERIC_GRADE   (e.g. BST-004-1E-PSA-10)
-- Returns NULL if (grader, grade) doesn't resolve in master_grade_catalog.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION slab_code(
    p_card_code TEXT,
    p_grader    TEXT,
    p_grade     TEXT   -- raw_label from master_grade_catalog
) RETURNS TEXT AS $$
DECLARE
    v_numeric TEXT;
BEGIN
    IF p_card_code IS NULL OR p_grader IS NULL OR p_grade IS NULL THEN
        RETURN NULL;
    END IF;

    SELECT numeric_grade::TEXT INTO v_numeric
      FROM master_grade_catalog
     WHERE grader = p_grader
       AND raw_label = p_grade
     LIMIT 1;

    IF v_numeric IS NULL THEN
        RETURN NULL;
    END IF;

    RETURN p_card_code || '-' || p_grader || '-' || v_numeric;
END;
$$ LANGUAGE plpgsql STABLE;

GRANT EXECUTE ON FUNCTION slab_code(TEXT, TEXT, TEXT)
    TO n8n_user, log_shipper, ehuser, agent_reader;


-- ─────────────────────────────────────────────────────────────────────────────
-- STEP 4 — card_code on tokenized_arbitrage_signals (display in HORIZON alerts)
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE tokenized_arbitrage_signals
    ADD COLUMN IF NOT EXISTS card_code TEXT;


-- ─────────────────────────────────────────────────────────────────────────────
-- STEP 5 — Verification queries (run after apply)
-- ─────────────────────────────────────────────────────────────────────────────

-- 5a. Every master_card_catalog row should have a card_code now.
SELECT COUNT(*) AS rows_missing_card_code FROM master_card_catalog WHERE card_code IS NULL;
-- Expected: 0

-- 5b. Spot-check format — should look like 'BST-001-UN', 'TRK-004-1E', etc.
SELECT card_code, set_name, card_number, edition, print_variant, card_name
  FROM master_card_catalog
 ORDER BY card_code
 LIMIT 12;

-- 5c. slab_code probe — should produce 'BST-004-1E-PSA-10' style output.
SELECT slab_code(
         (SELECT card_code FROM master_card_catalog WHERE set_name = 'Base Set' AND card_number = '4' AND edition = '1st Edition' LIMIT 1),
         'PSA',
         'PSA 10'
       ) AS slab_check;
