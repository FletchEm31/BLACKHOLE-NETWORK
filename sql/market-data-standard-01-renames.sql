-- ============================================================================
-- BHN Market Data Standard v2 — Step 01: Renames + column extensions
-- ============================================================================
-- Authority: infrastructure/docs/BHN session updates/BHN-SESSION-HANDOFF/
--            BHN-MARKET-DATA-STANDARD-PART{1,2,3}-*.txt   (2026-05-27 v2)
-- Authority doc target: infrastructure/docs/pokemonbhn/collectibles-data-standard.md
--
-- Order (operator rule): renames before new tables; views before n8n changes;
-- fee_schedule seeded immediately. This file does the rename half.
--
-- Strategy (operator-confirmed): ALTER columns IN PLACE first, then
-- ALTER TABLE ... RENAME TO. Preserves data, single transaction per table.
-- All adds are idempotent (ADD COLUMN IF NOT EXISTS / DO blocks).
--
-- Apply on LA hub (project_la_no_repo_checkout — stdin pipe):
--   cat sql/market-data-standard-01-renames.sql \
--     | sudo -u postgres psql -d eventhorizon -v ON_ERROR_STOP=1
-- ============================================================================

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1A. ebay_listings  →  ebay_asks
-- ─────────────────────────────────────────────────────────────────────────────
-- card_id already added by sql/card-id-resolver.sql.

ALTER TABLE ebay_listings ADD COLUMN IF NOT EXISTS card_code           TEXT;
ALTER TABLE ebay_listings ADD COLUMN IF NOT EXISTS card_number         TEXT;
ALTER TABLE ebay_listings ADD COLUMN IF NOT EXISTS edition             TEXT NOT NULL DEFAULT 'N/A';
ALTER TABLE ebay_listings ADD COLUMN IF NOT EXISTS print_variant       TEXT NOT NULL DEFAULT 'Standard';
ALTER TABLE ebay_listings ADD COLUMN IF NOT EXISTS platform            TEXT NOT NULL DEFAULT 'ebay';
ALTER TABLE ebay_listings ADD COLUMN IF NOT EXISTS final_price         DECIMAL(10,2);
ALTER TABLE ebay_listings ADD COLUMN IF NOT EXISTS ended_at            TIMESTAMPTZ;
ALTER TABLE ebay_listings ADD COLUMN IF NOT EXISTS days_listed         INT;
ALTER TABLE ebay_listings ADD COLUMN IF NOT EXISTS outcome             TEXT;
ALTER TABLE ebay_listings ADD COLUMN IF NOT EXISTS views               INT;
ALTER TABLE ebay_listings ADD COLUMN IF NOT EXISTS best_offer_received DECIMAL(10,2);
ALTER TABLE ebay_listings ADD COLUMN IF NOT EXISTS previous_ask_price  DECIMAL(10,2);
ALTER TABLE ebay_listings ADD COLUMN IF NOT EXISTS raw_payload         JSONB;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'ebay_listings_platform_chk'
    ) THEN
        ALTER TABLE ebay_listings
            ADD CONSTRAINT ebay_listings_platform_chk CHECK (platform = 'ebay');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'ebay_listings_outcome_chk'
    ) THEN
        ALTER TABLE ebay_listings
            ADD CONSTRAINT ebay_listings_outcome_chk CHECK (
                outcome IS NULL OR outcome IN (
                    'active','sold_full_price','sold_auction','sold_obo',
                    'expired_no_bids','expired_with_bids','cancelled_seller',
                    'relisted','ended_other'
                )
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'ebay_listings_grader_chk'
    ) THEN
        ALTER TABLE ebay_listings
            ADD CONSTRAINT ebay_listings_grader_chk CHECK (
                grader IS NULL OR grader IN ('CGC','PSA','BGS','SGC')
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'ebay_listings_edition_chk'
    ) THEN
        ALTER TABLE ebay_listings
            ADD CONSTRAINT ebay_listings_edition_chk CHECK (
                edition IN ('1st Edition','Unlimited','Shadowless','N/A')
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'ebay_listings_print_variant_chk'
    ) THEN
        ALTER TABLE ebay_listings
            ADD CONSTRAINT ebay_listings_print_variant_chk CHECK (
                print_variant IN (
                    'Standard','Holo','Error','No Symbol','W Stamp','Winner','Jumbo',
                    'Prerelease','Gold Border','Red Cheeks','WB Movie','Nintendo Power',
                    'WOTC','1999-2000 Copyright'
                )
            );
    END IF;
END$$;

ALTER TABLE ebay_listings RENAME TO ebay_asks;
-- Rename inherited identity objects (idempotent — guarded by current name lookup).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'ebay_listings_id_seq') THEN
        ALTER SEQUENCE ebay_listings_id_seq RENAME TO ebay_asks_id_seq;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'ebay_listings_pkey') THEN
        ALTER INDEX ebay_listings_pkey RENAME TO ebay_asks_pkey;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'ebay_listings_card_id_idx') THEN
        ALTER INDEX ebay_listings_card_id_idx RENAME TO ebay_asks_card_id_idx;
    END IF;
END$$;


-- ─────────────────────────────────────────────────────────────────────────────
-- 1B. sold_listings  →  ebay_transactions
-- ─────────────────────────────────────────────────────────────────────────────
-- card_id already added by sql/card-id-resolver.sql.

ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS card_code               TEXT;
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS card_number             TEXT;
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS edition                 TEXT NOT NULL DEFAULT 'N/A';
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS print_variant           TEXT NOT NULL DEFAULT 'Standard';
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS platform                TEXT NOT NULL DEFAULT 'ebay';
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS currency                TEXT;
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS raw_payload             JSONB;
-- sale details
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS listed_price            DECIMAL(10,2);
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS sold_at                 TIMESTAMPTZ;
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS sale_type               TEXT;
-- parties
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS buyer_username          TEXT;
-- actual fees
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS platform_fee_pct        DECIMAL(6,4);
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS platform_fee_amt        DECIMAL(10,2);
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS royalty_fee_pct         DECIMAL(6,4);
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS royalty_fee_amt         DECIMAL(10,2);
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS payment_processing_pct  DECIMAL(6,4);
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS payment_processing_amt  DECIMAL(10,2);
-- shipping & handling
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS shipping_cost           DECIMAL(10,2);
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS insurance_cost          DECIMAL(10,2);
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS packaging_cost          DECIMAL(10,2);
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS authentication_fee      DECIMAL(10,2);
-- taxes
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS sales_tax_collected     DECIMAL(10,2);
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS sales_tax_rate          DECIMAL(6,4);
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS income_tax_liability    DECIMAL(10,2);
-- cost basis (YOUR trades only)
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS acquisition_price       DECIMAL(10,2);
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS acquisition_market      TEXT;
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS acquisition_date        TIMESTAMPTZ;
-- totals (YOUR trades only)
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS total_fees              DECIMAL(10,2);
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS total_cost_basis        DECIMAL(10,2);
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS gross_profit            DECIMAL(10,2);
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS net_profit              DECIMAL(10,2);
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS profit_margin_pct       DECIMAL(6,2);
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS roi_pct                 DECIMAL(6,2);
-- market-rate estimates (all sales)
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS market_platform_fee_est   DECIMAL(10,2);
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS market_processing_fee_est DECIMAL(10,2);
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS market_shipping_est       DECIMAL(10,2);
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS market_auth_fee_est       DECIMAL(10,2);
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS market_total_costs_est    DECIMAL(10,2);
ALTER TABLE sold_listings ADD COLUMN IF NOT EXISTS market_net_to_seller_est  DECIMAL(10,2);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sold_listings_platform_chk') THEN
        ALTER TABLE sold_listings ADD CONSTRAINT sold_listings_platform_chk
            CHECK (platform = 'ebay');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sold_listings_sale_type_chk') THEN
        ALTER TABLE sold_listings ADD CONSTRAINT sold_listings_sale_type_chk
            CHECK (sale_type IS NULL OR sale_type IN (
                'fixed_price','auction','offer_accepted','buyback','peer_to_peer'
            ));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sold_listings_grader_chk') THEN
        ALTER TABLE sold_listings ADD CONSTRAINT sold_listings_grader_chk
            CHECK (grader IS NULL OR grader IN ('CGC','PSA','BGS','SGC'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sold_listings_edition_chk') THEN
        ALTER TABLE sold_listings ADD CONSTRAINT sold_listings_edition_chk
            CHECK (edition IN ('1st Edition','Unlimited','Shadowless','N/A'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sold_listings_print_variant_chk') THEN
        ALTER TABLE sold_listings ADD CONSTRAINT sold_listings_print_variant_chk
            CHECK (print_variant IN (
                'Standard','Holo','Error','No Symbol','W Stamp','Winner','Jumbo',
                'Prerelease','Gold Border','Red Cheeks','WB Movie','Nintendo Power',
                'WOTC','1999-2000 Copyright'
            ));
    END IF;
END$$;

ALTER TABLE sold_listings RENAME TO ebay_transactions;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'sold_listings_id_seq') THEN
        ALTER SEQUENCE sold_listings_id_seq RENAME TO ebay_transactions_id_seq;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'sold_listings_pkey') THEN
        ALTER INDEX sold_listings_pkey RENAME TO ebay_transactions_pkey;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'sold_listings_card_id_idx') THEN
        ALTER INDEX sold_listings_card_id_idx RENAME TO ebay_transactions_card_id_idx;
    END IF;
END$$;


-- ─────────────────────────────────────────────────────────────────────────────
-- 1C. courtyard_listings  →  courtyard_asks
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE courtyard_listings ADD COLUMN IF NOT EXISTS card_code           TEXT;
ALTER TABLE courtyard_listings ADD COLUMN IF NOT EXISTS final_price         DECIMAL(10,2);
ALTER TABLE courtyard_listings ADD COLUMN IF NOT EXISTS ended_at            TIMESTAMPTZ;
ALTER TABLE courtyard_listings ADD COLUMN IF NOT EXISTS days_listed         INT;
ALTER TABLE courtyard_listings ADD COLUMN IF NOT EXISTS outcome             TEXT;
ALTER TABLE courtyard_listings ADD COLUMN IF NOT EXISTS watchers            INT;
ALTER TABLE courtyard_listings ADD COLUMN IF NOT EXISTS views               INT;
ALTER TABLE courtyard_listings ADD COLUMN IF NOT EXISTS best_offer_received DECIMAL(10,2);
ALTER TABLE courtyard_listings ADD COLUMN IF NOT EXISTS relist_count        INT DEFAULT 0;
ALTER TABLE courtyard_listings ADD COLUMN IF NOT EXISTS original_item_id    TEXT;
ALTER TABLE courtyard_listings ADD COLUMN IF NOT EXISTS previous_ask_price  DECIMAL(10,2);
ALTER TABLE courtyard_listings ADD COLUMN IF NOT EXISTS last_seen_at        TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'courtyard_listings_outcome_chk') THEN
        ALTER TABLE courtyard_listings ADD CONSTRAINT courtyard_listings_outcome_chk
            CHECK (outcome IS NULL OR outcome IN (
                'active','sold','delisted','price_reduced','expired'
            ));
    END IF;
END$$;

ALTER TABLE courtyard_listings RENAME TO courtyard_asks;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'courtyard_listings_id_seq') THEN
        ALTER SEQUENCE courtyard_listings_id_seq RENAME TO courtyard_asks_id_seq;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'courtyard_listings_pkey') THEN
        ALTER INDEX courtyard_listings_pkey RENAME TO courtyard_asks_pkey;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'courtyard_listings_card_id_idx') THEN
        ALTER INDEX courtyard_listings_card_id_idx RENAME TO courtyard_asks_card_id_idx;
    END IF;
END$$;


-- ─────────────────────────────────────────────────────────────────────────────
-- 1D. courtyard_sales  →  courtyard_transactions
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS card_code               TEXT;
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS buyer_username          TEXT;
-- actual fees
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS platform_fee_pct        DECIMAL(6,4);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS platform_fee_amt        DECIMAL(10,2);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS royalty_fee_pct         DECIMAL(6,4);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS royalty_fee_amt         DECIMAL(10,2);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS payment_processing_pct  DECIMAL(6,4);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS payment_processing_amt  DECIMAL(10,2);
-- shipping & handling
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS shipping_cost           DECIMAL(10,2);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS insurance_cost          DECIMAL(10,2);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS packaging_cost          DECIMAL(10,2);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS authentication_fee      DECIMAL(10,2);
-- vault / redemption
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS redemption_fee          DECIMAL(10,2);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS vault_storage_fee       DECIMAL(10,2);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS tokenization_fee        DECIMAL(10,2);
-- blockchain
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS gas_fee                 DECIMAL(10,6);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS gas_fee_usd             DECIMAL(10,4);
-- taxes
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS sales_tax_collected     DECIMAL(10,2);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS sales_tax_rate          DECIMAL(6,4);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS income_tax_liability    DECIMAL(10,2);
-- cost basis
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS acquisition_price       DECIMAL(10,2);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS acquisition_market      TEXT;
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS acquisition_date        TIMESTAMPTZ;
-- totals
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS total_fees              DECIMAL(10,2);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS total_cost_basis        DECIMAL(10,2);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS gross_profit            DECIMAL(10,2);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS net_profit              DECIMAL(10,2);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS profit_margin_pct       DECIMAL(6,2);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS roi_pct                 DECIMAL(6,2);
-- market-rate estimates
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS market_platform_fee_est   DECIMAL(10,2);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS market_processing_fee_est DECIMAL(10,2);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS market_shipping_est       DECIMAL(10,2);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS market_auth_fee_est       DECIMAL(10,2);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS market_total_costs_est    DECIMAL(10,2);
ALTER TABLE courtyard_sales ADD COLUMN IF NOT EXISTS market_net_to_seller_est  DECIMAL(10,2);

-- Broaden sale_type vocab from the 2026-05-22 (peer_to_peer,buyback,gacha)
-- to the v2 spec (fixed_price,auction,offer_accepted,buyback,peer_to_peer).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'courtyard_sales_sale_type_chk') THEN
        ALTER TABLE courtyard_sales DROP CONSTRAINT courtyard_sales_sale_type_chk;
    END IF;
    ALTER TABLE courtyard_sales ADD CONSTRAINT courtyard_sales_sale_type_chk
        CHECK (sale_type IS NULL OR sale_type IN (
            'fixed_price','auction','offer_accepted','buyback','peer_to_peer'
        ));
END$$;

ALTER TABLE courtyard_sales RENAME TO courtyard_transactions;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'courtyard_sales_id_seq') THEN
        ALTER SEQUENCE courtyard_sales_id_seq RENAME TO courtyard_transactions_id_seq;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'courtyard_sales_pkey') THEN
        ALTER INDEX courtyard_sales_pkey RENAME TO courtyard_transactions_pkey;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'courtyard_sales_card_id_idx') THEN
        ALTER INDEX courtyard_sales_card_id_idx RENAME TO courtyard_transactions_card_id_idx;
    END IF;
END$$;


-- ─────────────────────────────────────────────────────────────────────────────
-- 1E. collector_crypt_sales  →  collector_crypt_transactions
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS card_code               TEXT;
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS buyer_username          TEXT;
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS platform_fee_pct        DECIMAL(6,4);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS platform_fee_amt        DECIMAL(10,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS royalty_fee_pct         DECIMAL(6,4);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS royalty_fee_amt         DECIMAL(10,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS payment_processing_pct  DECIMAL(6,4);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS payment_processing_amt  DECIMAL(10,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS shipping_cost           DECIMAL(10,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS insurance_cost          DECIMAL(10,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS packaging_cost          DECIMAL(10,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS authentication_fee      DECIMAL(10,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS redemption_fee          DECIMAL(10,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS vault_storage_fee       DECIMAL(10,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS tokenization_fee        DECIMAL(10,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS gas_fee                 DECIMAL(10,6);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS gas_fee_usd             DECIMAL(10,4);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS sales_tax_collected     DECIMAL(10,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS sales_tax_rate          DECIMAL(6,4);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS income_tax_liability    DECIMAL(10,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS acquisition_price       DECIMAL(10,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS acquisition_market      TEXT;
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS acquisition_date        TIMESTAMPTZ;
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS total_fees              DECIMAL(10,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS total_cost_basis        DECIMAL(10,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS gross_profit            DECIMAL(10,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS net_profit              DECIMAL(10,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS profit_margin_pct       DECIMAL(6,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS roi_pct                 DECIMAL(6,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS market_platform_fee_est   DECIMAL(10,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS market_processing_fee_est DECIMAL(10,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS market_shipping_est       DECIMAL(10,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS market_auth_fee_est       DECIMAL(10,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS market_total_costs_est    DECIMAL(10,2);
ALTER TABLE collector_crypt_sales ADD COLUMN IF NOT EXISTS market_net_to_seller_est  DECIMAL(10,2);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'collector_crypt_sales_sale_type_chk') THEN
        ALTER TABLE collector_crypt_sales DROP CONSTRAINT collector_crypt_sales_sale_type_chk;
    END IF;
    ALTER TABLE collector_crypt_sales ADD CONSTRAINT collector_crypt_sales_sale_type_chk
        CHECK (sale_type IS NULL OR sale_type IN (
            'fixed_price','auction','offer_accepted','buyback','peer_to_peer'
        ));
END$$;

ALTER TABLE collector_crypt_sales RENAME TO collector_crypt_transactions;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'collector_crypt_sales_id_seq') THEN
        ALTER SEQUENCE collector_crypt_sales_id_seq RENAME TO collector_crypt_transactions_id_seq;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'collector_crypt_sales_pkey') THEN
        ALTER INDEX collector_crypt_sales_pkey RENAME TO collector_crypt_transactions_pkey;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'collector_crypt_sales_card_id_idx') THEN
        ALTER INDEX collector_crypt_sales_card_id_idx RENAME TO collector_crypt_transactions_card_id_idx;
    END IF;
END$$;

COMMIT;
