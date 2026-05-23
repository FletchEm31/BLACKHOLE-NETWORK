-- ============================================================================
-- PokemonBHN - ebay_listings + sold_listings column additions
-- ============================================================================
--
-- Adds enriched-observation columns to the two eBay-source fact tables:
-- temporal markers (auction_end_time, first_seen_at, last_seen_at,
-- original_listed_at), relist tracking (relist_count, original_item_id),
-- best-offer floor (obo_min_price), slab identity (cert_number),
-- geography (location), and demand signal (watchers).
--
-- File name per operator spec 2026-05-22 23:34 PT.
-- Applied against live state captured 2026-05-22 23:35 PT.
--
-- Idempotency: ADD COLUMN IF NOT EXISTS - safe to re-run.
--
-- DRIFT NOTES (flagged for operator awareness, not fixed here):
--   ebay_listings.obo_min_price already exists as NUMERIC (no precision).
--   Operator spec called for DECIMAL(10,2). Operationally equivalent
--   (any value the operator would store fits both); kept existing column
--   to avoid a full table rewrite. To converge: ALTER COLUMN obo_min_price
--   TYPE DECIMAL(10,2) - holds a table-rewrite lock proportional to row
--   count, so worth scheduling rather than dropping in mid-day.
--
--   sold_listings inherits the column as DECIMAL(10,2) (fresh).
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- ebay_listings: 9 new columns
-- (obo_min_price already exists as NUMERIC - intentionally NOT re-added)
-- ----------------------------------------------------------------------------
ALTER TABLE ebay_listings
    ADD COLUMN IF NOT EXISTS auction_end_time    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS first_seen_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_seen_at        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS original_item_id    TEXT,
    ADD COLUMN IF NOT EXISTS relist_count        INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS original_listed_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS cert_number         TEXT,
    ADD COLUMN IF NOT EXISTS location            TEXT,
    ADD COLUMN IF NOT EXISTS watchers            INT;

COMMENT ON COLUMN ebay_listings.auction_end_time   IS 'When the eBay auction closes (auction format only; NULL on Buy-It-Now)';
COMMENT ON COLUMN ebay_listings.first_seen_at      IS 'Scraper first captured this listing (distinct from listed_at which is eBay-reported)';
COMMENT ON COLUMN ebay_listings.last_seen_at       IS 'Scraper most-recently confirmed this listing still active';
COMMENT ON COLUMN ebay_listings.original_item_id   IS 'First item_id observed for this card/seller combo - tracks relists under new item_ids';
COMMENT ON COLUMN ebay_listings.relist_count       IS 'Number of times this seller has relisted this card (per original_item_id grouping)';
COMMENT ON COLUMN ebay_listings.original_listed_at IS 'When we first EVER saw this card from this seller (across relists)';
COMMENT ON COLUMN ebay_listings.cert_number        IS 'PSA/CGC cert number on the slab - was noted in standard section 1 as not-stored-today, now stored';
COMMENT ON COLUMN ebay_listings.location           IS 'Seller location/country (eBay item.location)';
COMMENT ON COLUMN ebay_listings.watchers           IS 'Number of eBay watchers - demand signal';

-- ----------------------------------------------------------------------------
-- sold_listings: all 10 new columns (none exist today)
-- ----------------------------------------------------------------------------
ALTER TABLE sold_listings
    ADD COLUMN IF NOT EXISTS auction_end_time    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS first_seen_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_seen_at        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS original_item_id    TEXT,
    ADD COLUMN IF NOT EXISTS relist_count        INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS original_listed_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS obo_min_price       DECIMAL(10,2),
    ADD COLUMN IF NOT EXISTS cert_number         TEXT,
    ADD COLUMN IF NOT EXISTS location            TEXT,
    ADD COLUMN IF NOT EXISTS watchers            INT;

COMMENT ON COLUMN sold_listings.auction_end_time   IS 'When the eBay auction closed (NULL on BIN sales)';
COMMENT ON COLUMN sold_listings.first_seen_at      IS 'Scraper first captured this listing (before it sold)';
COMMENT ON COLUMN sold_listings.last_seen_at       IS 'Scraper last confirmed this listing active before the sale';
COMMENT ON COLUMN sold_listings.original_item_id   IS 'First item_id observed for this card/seller combo - tracks pre-sale relists';
COMMENT ON COLUMN sold_listings.relist_count       IS 'Times the seller relisted before the sale finalized';
COMMENT ON COLUMN sold_listings.original_listed_at IS 'When we first EVER saw this card from this seller';
COMMENT ON COLUMN sold_listings.obo_min_price      IS 'Best-offer floor at time of sale (DECIMAL(10,2) here; ebay_listings has the same column as legacy NUMERIC)';
COMMENT ON COLUMN sold_listings.cert_number        IS 'PSA/CGC cert number on the slab';
COMMENT ON COLUMN sold_listings.location           IS 'Seller location/country at time of sale';
COMMENT ON COLUMN sold_listings.watchers           IS 'Number of watchers at sale time - demand signal';

COMMIT;
