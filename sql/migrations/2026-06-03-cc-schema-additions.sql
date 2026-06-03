-- 2026-06-03 — collector_crypt_transactions schema additions for CC collector
--
-- Context: collector_crypt_sales is a VIEW on collector_crypt_transactions.
-- All ALTER TABLE targets the base table.
--
-- Decisions locked before this migration:
-- 1. cert_number   — from CC 'Grading ID' trait (grader's cert/serial)
-- 2. grade_label   — tier name only per §3.8 three-column grade system
-- 3. cc_set_name   — raw CC set label; set_name stays NULL for non-WOTC
-- 4. grader CHECK  — expanded to include TAG (onboarded 2026-06-02)
--
-- item_id = Solana transaction signature (not NFT mint address)
-- NFT mint address stored in nft_contract column
--
-- Grade mapping strategy (locked):
--   PSA: GradeNum → bare string ("10", "9", "8.5", ...)
--   CGC 10: map from CC "The Grade" label → "Gem Mint 10" / "Pristine 10" / "Perfect 10"
--   CGC 9.5: map from CC label → "Mint+ 9.5" (current) or "Gem Mint 9.5" (legacy blue)
--   CGC 9 and below: bare GradeNum string ("9", "8.5", "8", ...)
--   BGS 10: bare "10" (can't distinguish Black/Gold/Pristine without subgrades)
--   BGS 9.5 and below: bare GradeNum string ("9.5", "9", ...)
--   SGC 10: map from CC label → "Gem Mint 10" or "Pristine 10"
--   SGC 9.5 and below: bare GradeNum string
--   Unmapped → grade_reject_log intent; insert with grade=NULL
--
-- APPLIED: 2026-06-03

BEGIN;

ALTER TABLE collector_crypt_transactions
  ADD COLUMN IF NOT EXISTS cert_number TEXT;
COMMENT ON COLUMN collector_crypt_transactions.cert_number IS
  'Grader certificate number from CC Grading ID trait.';

ALTER TABLE collector_crypt_transactions
  ADD COLUMN IF NOT EXISTS grade_label TEXT;
COMMENT ON COLUMN collector_crypt_transactions.grade_label IS
  'Tier name only per §3.8 — e.g. Gem Mint, Pristine. Nullable. Derived from CC The Grade trait label. Never blocks load.';

ALTER TABLE collector_crypt_transactions
  ADD COLUMN IF NOT EXISTS cc_set_name TEXT;
COMMENT ON COLUMN collector_crypt_transactions.cc_set_name IS
  'Raw CC set label (e.g. Sword & Shield Vivid Voltage). Preserved for reference; set_name stays NULL for non-WOTC cards.';

ALTER TABLE collector_crypt_transactions
  DROP CONSTRAINT collector_crypt_transactions_grader_chk,
  ADD CONSTRAINT collector_crypt_transactions_grader_chk
    CHECK (grader IS NULL OR grader IN ('CGC','PSA','BGS','SGC','TAG'));

COMMIT;
