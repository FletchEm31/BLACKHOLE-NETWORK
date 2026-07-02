-- 2026-07-02-ledger-exclude-legacy-rows.sql
--
-- Permanently and structurally exclude legacy weather_gold_contract_ledger
-- rows from performance evaluation, per Fletch's decision tonight.
--
-- Two legacy populations identified during tonight's Metabase verification
-- sweep, both predating the current CP1->CP2->CP3->CP4 pipeline
-- (core_trading_orchestrator.py / cp4_kelly_sizer.py):
--   1. 90 BET_YES rows — the current pipeline architecturally can never
--      produce recommended_action='BET_YES' (cp4_kelly_sizer.py only ever
--      writes 'BET_NO' or 'SKIP'). These are from an earlier manual-trading
--      era, before the current action space existed.
--   2. 48 BET_NO rows carrying an identical flat stake_usd=125.0 (zero
--      variance) instead of genuine Kelly sizing — from the June 25/26
--      migration backfill, not the live pipeline (verified: all
--      signal_generated_at dates 2026-06-11 through 2026-06-26, none since).
--
-- Both were confirmed to distort the Overall Scorecard and Edge Tier
-- dashboards (see infrastructure/docs/metabase/CLEAN_QUERIES.sql Query
-- 19/20 notes). Decision: exclude permanently, do not attempt to backfill
-- or reconstruct what current logic "would have" produced for them.
--
-- Verified before writing this migration: both writers
-- (cp4_kelly_sizer.py's _LEDGER_UPSERT, low_side_ledger_populator.py's
-- INSERT) use explicit column lists, not positional VALUES — adding a
-- column with a DEFAULT is safe and requires no writer changes. Neither
-- writer's ON CONFLICT DO UPDATE SET list will reference the new column,
-- so it is never reset on a re-run/upsert of an existing row.
--
-- Deploy: scp this file + apply via psql on LA. No git pull on LA.

BEGIN;

ALTER TABLE weather_gold_contract_ledger
    ADD COLUMN IF NOT EXISTS is_legacy_row BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN weather_gold_contract_ledger.is_legacy_row IS
    'TRUE for rows predating the current CP1-CP4 pipeline''s action space '
    '(BET_YES rows, or BET_NO rows with the flat $125 pre-Kelly stake from '
    'the 2026-06-25/26 migration backfill). Permanently excluded from '
    'performance dashboards via weather_gold_contract_ledger_performance. '
    'Set once 2026-07-02 — do not backfill/reconstruct; new rows default '
    'to FALSE and should stay FALSE under the current pipeline, since it '
    'cannot produce BET_YES and Kelly-sizes every BET_NO individually.';

UPDATE weather_gold_contract_ledger
SET is_legacy_row = TRUE
WHERE recommended_action = 'BET_YES'
   OR (recommended_action = 'BET_NO' AND stake_usd = 125.0);

-- Structural, permanent exclusion — every dashboard should read from this
-- view instead of re-deriving the exclusion condition per-query.
CREATE OR REPLACE VIEW weather_gold_contract_ledger_performance AS
SELECT *
FROM weather_gold_contract_ledger
WHERE is_legacy_row = FALSE;

COMMENT ON VIEW weather_gold_contract_ledger_performance IS
    'weather_gold_contract_ledger with legacy pre-CP4-pipeline rows '
    'permanently excluded (see is_legacy_row column comment). Use this '
    'view, not the base table, for all performance/scorecard dashboards.';

\echo 'Legacy rows flagged:'
\echo 'SELECT COUNT(*) FROM weather_gold_contract_ledger WHERE is_legacy_row;'

COMMIT;
