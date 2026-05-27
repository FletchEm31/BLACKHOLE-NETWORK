-- Open item #9 (collectibles-data-standard §3.8): grade three-column system
-- grade_label: tier name parsed from listing title (e.g. "Gem Mint 10"), nullable, never blocks load
ALTER TABLE ebay_transactions ADD COLUMN IF NOT EXISTS grade_label TEXT;
ALTER TABLE ebay_asks         ADD COLUMN IF NOT EXISTS grade_label TEXT;

-- Open item #8 (collectibles-data-standard §2.3): bhn_slab_id
-- 15-char random alphanumeric (A-Z, 0-9) assigned once per unique slab_code
-- Used to resolve ambiguous grades (e.g. CGC 10 Pristine vs Gem Mint)
-- NULL for ungraded rows — never blocks a row from loading
ALTER TABLE ebay_transactions ADD COLUMN IF NOT EXISTS bhn_slab_id TEXT;
ALTER TABLE ebay_asks         ADD COLUMN IF NOT EXISTS bhn_slab_id TEXT;
