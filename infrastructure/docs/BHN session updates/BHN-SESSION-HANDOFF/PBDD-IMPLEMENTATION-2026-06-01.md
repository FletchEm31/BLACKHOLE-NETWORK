# PBDD SYSTEM IMPLEMENTATION — Claude Code Session Spec
**Date:** 2026-06-01  
**Prepared by:** Planning chat  
**Execute via:** Claude Code on `D:\GITHUB REPOSITORY\BLACKHOLE-NETWORK`  
**LA node:** `sudo -u postgres psql -d eventhorizon` (peer auth, no -h flag)

---

## WHAT THIS SESSION IS

A major naming and architecture overhaul of the PokemonBHN identifier system. Everything was designed and locked in planning chat. Claude Code executes. This doc is the complete handoff.

**The single rule above all others:** SNAPSHOT BEFORE ANY SCHEMA CHANGE.  
```bash
sudo -u postgres pg_dump eventhorizon > /mnt/eh-nvme-hot/backups/pre-pbdd-$(date +%Y%m%d-%H%M).sql
```

---

## READ THESE FILES FIRST (before touching anything)

1. `infrastructure/docs/pokemonbhn/PokemonBHN-Data Standardization Framework-LEGACY.docx`  
   → The finalized v4 standard. This is the authority for everything in this session.  
   → Read Section I (identifier system), Section III.C-D (grade labels), Section I.G (short codes).

2. `infrastructure/docs/pokemonbhn/BHN-Grade-Label-Reference.md`  
   → Authoritative grade label catalog. All 88 labels, short codes, ambiguity rules.  
   → Read the CGC, PSA, BGS, SGC tables in full before touching master_grade_catalog.

3. `infrastructure/docs/pokemonbhn/collectibles-data-standard.md`  
   → Current live standard. You will be updating this. Read it in full first.

4. Run the verification queries in Phase 2 before writing anything.

---

## THE NAMING CHANGES (locked — apply everywhere)

| Old name | New name | Notes |
|---|---|---|
| PBDS / Pokemon BHN Dewey Decimal System | PBDD / PokemonBHN Dewey Decimal | Drop "System" from full name |
| pbds_code | pbdd_code | The human-readable card variant label |
| slab_code | pbdd_grade_code | The tier identity code (graded or raw) |
| slab_code() | pbdd_grade_code() | The PostgreSQL function |
| bhn_slab_id | pbdd_slab_number | Physical graded slab instance ID |
| card_id | pbdd_card_id | The integer machine join key |

**card_id note:** The PK column on `master_card_catalog` stays as `id`. The FK column on observation tables becomes `pbdd_card_id`. The concept is `pbdd_card_id`.

---

## THE CODE FORMAT (locked)

**pbdd_code format:** `[SETCODE+NUM]-[EDITION]-[VARIANT]`  
- **No year** — dropped entirely  
- **Standard variant is explicit (STN)** — never omitted  
- Example: `TRK014-1E-HOLO`, `BAS004-1E-STN`, `GYC002-1E-STN`, `TRK005-1E-ERR`

**Set codes:** BAS / FOS / JUN / TRK / GYH / GYC / BOG / WBS  
**Edition codes:** 1E / UN / SH / NA  
**Variant codes:** STN / HOLO / ERR / NOSYM / WSTAMP / WIN / JUMBO / PRE / GOLD / RCK / WBM / NP / WOTC / C2000

**pbdd_graded_code format:** `{pbdd_code}-{GRADER}{NUMERIC}{SHORT_CODE}`  
- Example: `TRK014-1E-HOLO-PSA10GM`, `TRK014-1E-HOLO-CGC9.5M+`, `TRK014-1E-HOLO-CGC10PR`  
- SHORT_CODE sourced from `master_grade_catalog.short_code` column (adding this session)

**pbdd_raw_code format:** `{pbdd_code}-RAW[-{CONDITION}]`  
- Condition ∈ {NM, LP, MP, HP, DMG} (TCGplayer 5-tier) when credibly stated  
- Example: `TRK014-1E-HOLO-RAW-NM`, `TRK014-1E-HOLO-RAW` (unknown condition)

---

## PHASE 1 — DIAGNOSTICS (READ-ONLY, no writes)

Run all of these before touching anything. Report findings.

### 1A — Current card_code format in live DB
```sql
-- See what format the live card_code column actually holds
SELECT card_code, set_name, card_number, edition, print_variant
FROM master_card_catalog
ORDER BY set_name, card_number::int
LIMIT 30;

-- Check whether set codes in the live data are BAS/FOS/JUN or BST/FSL/JGL (FLAGGED CONFLICT)
SELECT DISTINCT SUBSTRING(card_code FROM 1 FOR 3) AS live_set_code, set_name
FROM master_card_catalog
WHERE card_code IS NOT NULL
ORDER BY set_name;
```
**STOP** — report these results to Fletch before proceeding to Phase 4 (card_code regeneration). The set code conflict (BAS vs BST etc.) must be resolved against live data before regenerating 1,354 rows.

### 1B — master_grade_catalog verification
```sql
-- Row counts per grader (expected: CGC 25, PSA 20, BGS 21, SGC 22 = 88 total)
SELECT grader, COUNT(*) AS label_count
FROM master_grade_catalog
GROUP BY grader ORDER BY grader;

-- Full catalog — compare against BHN-Grade-Label-Reference.md
SELECT grader, raw_label, numeric_grade, tier_label, market_equiv_10, is_authentic
FROM master_grade_catalog
ORDER BY grader, numeric_grade DESC NULLS LAST;

-- Check for short_code column (may not exist yet)
SELECT column_name FROM information_schema.columns
WHERE table_name = 'master_grade_catalog' ORDER BY ordinal_position;

-- Check for reholder columns
SELECT column_name FROM information_schema.columns
WHERE table_name = 'master_grade_catalog'
AND column_name LIKE 'reholder%';
```

### 1C — pop_reports grade audit
```sql
-- Orphan check: any grade in pop_reports not in the catalog?
SELECT DISTINCT pr.grader, pr.grade
FROM pop_reports pr
LEFT JOIN master_grade_catalog mgc
  ON pr.grader = mgc.grader AND pr.grade = mgc.raw_label
WHERE mgc.raw_label IS NULL
ORDER BY pr.grader, pr.grade;
-- Expected: 0 rows

-- Grade distribution in pop_reports
SELECT grader, grade, COUNT(*) AS card_count, SUM(population) AS total_graded
FROM pop_reports
GROUP BY grader, grade
ORDER BY grader,
  CASE WHEN grade ~ E'^\\d' THEN grade::numeric ELSE 99 END DESC;
-- Confirm: NO 'Gem Mint 9.5' row for CGC (should only be 'Mint+ 9.5')
-- Confirm: 'Pristine 10' and 'Gem Mint 10' exist as SEPARATE rows for CGC

-- Any bare ambiguous '10' or '9.5' in pop_reports? (should be 0)
SELECT grader, grade, COUNT(*) FROM pop_reports
WHERE grade IN ('10', '9.5') GROUP BY grader, grade;
```

### 1D — slab_code() function — get current definition
```sql
SELECT prosrc FROM pg_proc
WHERE proname = 'slab_code';
-- If no result, the function doesn't exist yet (may be in pending work)
```

### 1E — Check what tables have bhn_slab_id column
```sql
SELECT table_name, column_name
FROM information_schema.columns
WHERE column_name = 'bhn_slab_id'
ORDER BY table_name;
-- If none: pbdd_slab_number doesn't need an ALTER TABLE; it's named correctly when built
```

### 1F — Check existing card_id / pbdd_card_id on observation tables
```sql
SELECT table_name, column_name
FROM information_schema.columns
WHERE column_name IN ('card_id', 'pbdd_card_id')
AND table_schema = 'public'
ORDER BY table_name;
-- Shows which tables already have the join key column (if any)
```

---

## PHASE 2 — UPDATE collectibles-data-standard.md

File: `infrastructure/docs/pokemonbhn/collectibles-data-standard.md`

Make every change below. This is a doc-only update — no DB risk. Do a complete find-and-replace pass first, then targeted structural additions.

### Find-and-replace (apply to entire file)
| Find | Replace |
|---|---|
| `PBDS` | `PBDD` |
| `Pokemon BHN Dewey Decimal System` | `PokemonBHN Dewey Decimal` |
| `pbds_code` | `pbdd_code` |
| `slab_code()` | `pbdd_grade_code()` |
| `slab_code` | `pbdd_grade_code` |
| `bhn_slab_id` | `pbdd_slab_number` |
| `card_id` (as concept/column name) | `pbdd_card_id` |

**Note:** `master_card_catalog.id` stays as `id` — do not rename the PK column reference. Only the concept/FK column name changes.

### Structural additions

**§3.4 grader — add sentinel values:**
```
grader ∈ {PSA, CGC, BGS, SGC, RAW, UNKNOWN}

RAW   — SENTINEL: card is confirmed ungraded (no slab). Lives in raw_* tables only.
UNKNOWN — SENTINEL: grade observed in listing but could not be parsed. Lives in graded tables for operator replay/review.

grader = NULL is a data gap (scraper/parser issue). Never use NULL to mean ungraded.
```

**§3.5 grade — update pbdd_code format:**
```
pbdd_code format: [SETCODE+NUM]-[EDITION]-[VARIANT]
- No year component
- Standard variant is always explicit: STN (never omitted)
- Example: TRK014-1E-HOLO, BAS004-1E-STN, GYC002-1E-STN
```

**Conformance table — update these rows:**
- card_code format: target = `TRK014-1E-HOLO` (no year, concatenated set+num, STN explicit) | current = `TRK-004-1E` (hyphenated, no year, Standard omitted)
- slab_code() → pbdd_grade_code() pending rename
- bhn_slab_id → pbdd_slab_number (not yet built)
- master_grade_catalog.short_code: ⏳ pending ADD COLUMN + population this session

---

## PHASE 3 — master_grade_catalog SCHEMA CHANGES

**SNAPSHOT FIRST.**

### 3A — Apply pending migration (if not already applied)
```bash
sudo -u postgres psql -d eventhorizon -f sql/migrations/2026-05-28-grade-catalog-corrections.sql
```
Verify it ran: check for RAW and UNKNOWN rows in master_grade_catalog, and BGS Black Label 10 / Gold Label 10 rows. If already applied, skip.

### 3B — Add short_code column
```sql
ALTER TABLE master_grade_catalog ADD COLUMN IF NOT EXISTS short_code TEXT;

COMMENT ON COLUMN master_grade_catalog.short_code IS
  'Abbreviated tier label used as the suffix in pbdd_grade_code. Mapped from tier_label. '
  'NULL for parser-fallback rows (bare ambiguous 10, 9.5) — these route to grade_reject_log, never get a short_code.';
```

### 3C — Add reholder columns
```sql
ALTER TABLE master_grade_catalog
  ADD COLUMN IF NOT EXISTS reholder_eligible BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS reholder_target_raw_label TEXT,
  ADD COLUMN IF NOT EXISTS reholder_fee_min_usd NUMERIC,
  ADD COLUMN IF NOT EXISTS reholder_fee_max_usd NUMERIC;

COMMENT ON COLUMN master_grade_catalog.reholder_eligible IS
  'TRUE if CGC offers a reholder/crossover service for this label.';
COMMENT ON COLUMN master_grade_catalog.reholder_target_raw_label IS
  'What the label becomes post-reholder (e.g. Gem Mint 9.5 → Gem Mint 10).';
```

### 3D — Populate short_code for all 88 rows
Run the UPDATE statements below in one transaction. Verify row count after each grader block.

```sql
BEGIN;

-- ── CGC ────────────────────────────────────────────────────────────
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

-- ── PSA ────────────────────────────────────────────────────────────
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

-- ── BGS ────────────────────────────────────────────────────────────
-- Note: Black Label 10 and Gold Label 10 only exist if 2026-05-28 migration was applied
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

-- ── SGC ────────────────────────────────────────────────────────────
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

-- Verify: every authoritative row now has a short_code (parser fallbacks will have NULL — expected)
SELECT grader, raw_label, short_code
FROM master_grade_catalog
WHERE short_code IS NULL
ORDER BY grader, raw_label;

COMMIT;
```

### 3E — Populate reholder fields (CGC only — confirmed cases)
```sql
BEGIN;

UPDATE master_grade_catalog SET
  reholder_eligible = TRUE,
  reholder_target_raw_label = 'Gem Mint 10',
  reholder_fee_min_usd = 5,
  reholder_fee_max_usd = 10
WHERE grader = 'CGC' AND raw_label = 'Gem Mint 9.5';

UPDATE master_grade_catalog SET
  reholder_eligible = TRUE,
  reholder_target_raw_label = 'Pristine 10',
  reholder_fee_min_usd = NULL,  -- fee TBD, operator to confirm
  reholder_fee_max_usd = NULL
WHERE grader = 'CGC' AND raw_label = 'Perfect 10';

COMMIT;
```

### 3F — Update GRANTs
```sql
GRANT SELECT ON master_grade_catalog TO ehuser;
GRANT SELECT ON master_grade_catalog TO agent_reader;
-- Verify no other roles need adjustment:
SELECT grantee, privilege_type FROM information_schema.role_table_grants
WHERE table_name = 'master_grade_catalog';
```

### 3G — Verify n8n workflows that read master_grade_catalog
Check if any n8n workflow nodes query master_grade_catalog directly. If so, note them — they should still work (SELECT only, additive columns). No changes needed to n8n for this phase.

---

## PHASE 4 — card_code REGENERATION

**⚠ DO NOT RUN until Phase 1A diagnostic results are reviewed with Fletch.**  
The set code format in the live DB must be confirmed (BAS vs BST conflict) before overwriting 1,354 rows.

Once confirmed, this is the target transformation:
- Old: `TRK-004-1E` (hyphenated, no year, Standard omitted)
- New: `TRK014-1E-HOLO` or `TRK014-1E-STN` (concatenated, no year, Standard explicit)

### 4A — Add set_code to master_set_catalog (if not present)
```sql
ALTER TABLE master_set_catalog ADD COLUMN IF NOT EXISTS set_code TEXT;

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
```

### 4B — Regenerate card_code (SNAPSHOT FIRST)
```sql
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
         WHEN 'Standard'          THEN 'STN'
         WHEN 'Holo'              THEN 'HOLO'
         WHEN 'Error'             THEN 'ERR'
         WHEN 'No Symbol'         THEN 'NOSYM'
         WHEN 'W Stamp'           THEN 'WSTAMP'
         WHEN 'Winner'            THEN 'WIN'
         WHEN 'Jumbo'             THEN 'JUMBO'
         WHEN 'Prerelease'        THEN 'PRE'
         WHEN 'Gold Border'       THEN 'GOLD'
         WHEN 'Red Cheeks'        THEN 'RCK'
         WHEN 'WB Movie'          THEN 'WBM'
         WHEN 'Nintendo Power'    THEN 'NP'
         WHEN 'WOTC'              THEN 'WOTC'
         WHEN '1999-2000 Copyright' THEN 'C2000'
         ELSE mcc.print_variant
       END
  FROM master_set_catalog msc
  WHERE msc.set_name = mcc.set_name
);

-- Verify sample before committing:
SELECT card_code, set_name, card_number, edition, print_variant
FROM master_card_catalog
ORDER BY set_name, card_number
LIMIT 20;

-- Verify no NULLs or unexpected values:
SELECT COUNT(*) AS total, COUNT(card_code) AS with_code,
       COUNT(CASE WHEN card_code LIKE '%None%' OR card_code LIKE '%null%' THEN 1 END) AS suspect
FROM master_card_catalog;

-- If all looks correct:
COMMIT;
-- If anything is wrong:
-- ROLLBACK;
```

---

## PHASE 5 — RENAME slab_code() → pbdd_grade_code()

### 5A — Get current function definition
```sql
\df+ slab_code
SELECT prosrc, proargtypes::text FROM pg_proc WHERE proname = 'slab_code';
```

### 5B — Create new pbdd_grade_code() function
Replace the logic with the updated format: graded returns `{pbdd_code}-{GRADER}{NUMERIC}{SHORT_CODE}` (joining master_grade_catalog for the short_code). Raw returns `{pbdd_code}-RAW[-{CONDITION}]`.

```sql
CREATE OR REPLACE FUNCTION pbdd_grade_code(
  p_pbdd_code TEXT,
  p_grader    TEXT,
  p_grade     TEXT,
  p_condition TEXT DEFAULT NULL  -- optional: NM/LP/MP/HP/DMG for raw cards
)
RETURNS TEXT
LANGUAGE sql
STABLE
AS $$
  SELECT
    CASE
      -- Graded card: append GRADER + NUMERIC + SHORT_CODE
      WHEN p_grader IS NOT NULL AND p_grader NOT IN ('RAW', 'UNKNOWN') AND p_grade IS NOT NULL THEN
        p_pbdd_code || '-' || p_grader
          || COALESCE(mgc.numeric_grade::text, p_grade)
          || COALESCE(mgc.short_code, '')
      -- Raw card with known condition
      WHEN p_grader = 'RAW' AND p_condition IS NOT NULL THEN
        p_pbdd_code || '-RAW-' || p_condition
      -- Raw card with unknown condition
      ELSE
        p_pbdd_code || '-RAW'
    END
  FROM (
    SELECT numeric_grade, short_code
    FROM master_grade_catalog
    WHERE grader = p_grader AND raw_label = p_grade
    LIMIT 1
  ) mgc
$$;

-- Drop old function once new one verified:
-- DROP FUNCTION IF EXISTS slab_code(TEXT, TEXT, TEXT);

GRANT EXECUTE ON FUNCTION pbdd_grade_code(TEXT, TEXT, TEXT, TEXT) TO ehuser;
GRANT EXECUTE ON FUNCTION pbdd_grade_code(TEXT, TEXT, TEXT, TEXT) TO agent_reader;
```

### 5C — Verify the function works
```sql
-- Should return TRK014-1E-HOLO-PSA10GM
SELECT pbdd_grade_code('TRK014-1E-HOLO', 'PSA', '10');

-- Should return TRK014-1E-HOLO-CGC10PR  
SELECT pbdd_grade_code('TRK014-1E-HOLO', 'CGC', 'Pristine 10');

-- Should return TRK014-1E-HOLO-CGC9.5M+
SELECT pbdd_grade_code('TRK014-1E-HOLO', 'CGC', 'Mint+ 9.5');

-- Should return TRK014-1E-HOLO-CGC9.5GM (Blue Label legacy)
SELECT pbdd_grade_code('TRK014-1E-HOLO', 'CGC', 'Gem Mint 9.5');

-- Should return TRK014-1E-HOLO-RAW-NM
SELECT pbdd_grade_code('TRK014-1E-HOLO', 'RAW', NULL, 'NM');

-- Should return TRK014-1E-HOLO-RAW
SELECT pbdd_grade_code('TRK014-1E-HOLO', 'RAW', NULL, NULL);
```

---

## PHASE 6 — GREP AND UPDATE ALL CODE FILES

Run from repo root (`D:\GITHUB REPOSITORY\BLACKHOLE-NETWORK`):

```bash
# Find all files referencing old names
grep -r "pbds_code\|PBDS\|slab_code\|bhn_slab_id\|pbds" \
  --include="*.js" --include="*.py" --include="*.json" \
  --include="*.sql" --include="*.md" --include="*.txt" \
  -l

# Then for each file found, review and update:
# pbds_code → pbdd_code
# PBDS → PBDD  
# slab_code → pbdd_grade_code
# bhn_slab_id → pbdd_slab_number
```

**Key files to check:**
- `infrastructure/scrapers/cgc-pop-scrape.js` — check for any PBDS variable names
- `infrastructure/scrapers/psa-pop-scrape.js` — same
- `infrastructure/scrapers/cgc-pop-load.js` — same
- `scripts/operator-pc/clone-vintage-workflow.js` — check for pbds/slab references
- `n8n-workflows/pokemon/` — check all workflow JSONs for slab_code, pbds_code
- Any SQL files in `sql/` that reference old names
- `infrastructure/docs/BHN session updates/` — update any handoff docs

**n8n workflows — IMPORTANT:**
If any n8n workflow calls `slab_code()`, update the SQL node to call `pbdd_grade_code()`.  
Saved AND Published — both required or the workflow silently won't run.  
Snapshot n8n database.sqlite before any workflow changes.

---

## PHASE 7 — card_id RESOLVER (DIAGNOSTIC ONLY — NO WRITES)

**⚠ Do NOT write any pbdd_card_id values. Report results to Fletch first.**

This phase runs the confidence-scored resolver as a dry-run and reports match rates. Fletch reviews before any UPDATE is authorized.

```sql
-- Field inventory: which observation tables exist, which columns are populated
SELECT
  t.table_name,
  COUNT(DISTINCT c.column_name) AS columns,
  (SELECT COUNT(*) FROM information_schema.columns c2
   WHERE c2.table_name = t.table_name AND c2.column_name = 'pbdd_card_id') AS has_pbdd_card_id
FROM information_schema.tables t
JOIN information_schema.columns c ON t.table_name = c.table_name
WHERE t.table_schema = 'public'
AND t.table_name IN ('ebay_transactions','sold_listings','ebay_asks',
                     'ebay_listings','pop_reports','courtyard_asks',
                     'courtyard_listings','courtyard_transactions',
                     'collector_crypt_transactions')
GROUP BY t.table_name
ORDER BY t.table_name;

-- Resolver dry-run: match sold_listings rows to master_card_catalog
-- Returns confidence tiers without writing anything
WITH normalized AS (
  SELECT
    id,
    REGEXP_REPLACE(card_number, '[^0-9]', '', 'g') AS norm_num,
    set_name, edition, print_variant
  FROM sold_listings
  WHERE card_number IS NOT NULL
),
matched AS (
  SELECT
    n.id AS sold_id,
    mcc.id AS pbdd_card_id,
    CASE
      WHEN mcc.id IS NOT NULL
        AND n.edition = mcc.edition
        AND n.print_variant = mcc.print_variant THEN 'HIGH'
      WHEN mcc.id IS NOT NULL THEN 'MED'
      ELSE 'UNMATCHED'
    END AS confidence
  FROM normalized n
  LEFT JOIN master_card_catalog mcc
    ON mcc.set_name = n.set_name
    AND REGEXP_REPLACE(mcc.card_number, '[^0-9]', '', 'g') = n.norm_num
    AND mcc.edition = n.edition
)
SELECT
  confidence,
  COUNT(*) AS row_count,
  ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 2) AS pct
FROM matched
GROUP BY confidence
ORDER BY confidence;
```

Report these numbers to Fletch. Do not proceed to writing pbdd_card_id until he approves the match rates and reviews a stratified sample.

---

## PHASE 8 — COMMIT

Every commit needs Summary AND Description. Use two commits — one for docs, one for DB/code.

**Commit 1 — Documentation**  
```
Summary: docs: update PokemonBHN standard to PBDD system (v4)

Description:
Rename PBDS → PBDD (PokemonBHN Dewey Decimal) throughout.
Update collectibles-data-standard.md with new naming, code format,
sentinel grader values (RAW/UNKNOWN), and pbdd_grade_code concept.
No year in pbdd_code; Standard variant always explicit (STN).
pbdd_card_id (machine) / pbdd_code (human) distinction documented.
pbdd_graded_code and pbdd_raw_code defined as separate concepts.
```

**Commit 2 — Schema and code**  
```
Summary: feat: implement PBDD system in master_grade_catalog and card_code

Description:
Add short_code and reholder columns to master_grade_catalog.
Populate all 88 short_codes (CGC/PSA/BGS/SGC) from grade label reference.
Set reholder fields for CGC Gem Mint 9.5 (→ Gem Mint 10, $5-10)
and Perfect 10 (→ Pristine 10, fee TBD).
Add set_code column to master_set_catalog.
Regenerate master_card_catalog.card_code to PBDD format:
[SETCODE+NUM]-[EDITION]-[VARIANT] (no year, STN explicit).
Rename slab_code() → pbdd_grade_code() with tier short_code logic.
Apply 2026-05-28 grade catalog corrections migration if pending.
Update scraper/workflow files to remove PBDS references.
```

---

## HARD RULES — DO NOT DEVIATE

- NJ SSH is always port 2222
- PostgreSQL: `sudo -u postgres psql -d eventhorizon` — no `-h` flag for superuser
- SNAPSHOT before every schema change
- Every schema change: update GRANTs for all affected roles, then verify
- Every n8n change: Saved AND Published (draft = silently won't run)
- Snapshot n8n database.sqlite before any workflow changes
- GitHub commits: both Summary AND Description — no exceptions
- card_id resolver (Phase 7): DIAGNOSTIC ONLY, no writes without Fletch approval
- card_code regeneration (Phase 4): STOP after Phase 1A dump, report to Fletch first
- BGS Black Label / Gold Label naming: confirm raw_label text against physical slab before inserting
- Consult Fletch before any structural change NOT listed in this spec

---

## OPEN ITEMS AFTER THIS SESSION (not in scope today)

- grade_reject_log table build (Open Item #1)
- ebay_asks schema alignment (Open Item #2)
- pop_reports.card_set rename (Open Item #3)
- card_id resolver writes — after diagnostic approval (Open Item #8)
- eBay → condition normalization map (Open Item #11)
- pbdd_slab_number implementation on ebay_transactions/ebay_asks (Open Item #13)
- card_valuations materialized view (Open Item #14)
- PostgreSQL 14→18 migration (deferred, separate session)
