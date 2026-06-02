-- ============================================================================
-- PokemonBHN — Silver Layer
-- silver_ebay_transactions
-- ============================================================================
-- Unified Silver layer for ALL eBay sold comps.
-- Promoted from ebay_transactions (Bronze) via BRONZE_TO_SILVER_EBAY_TRANSACTIONS
-- n8n workflow → promote_bronze_to_silver() PostgreSQL function.
--
-- Promotion gate (row only enters Silver if ALL pass):
--   card_id IS NOT NULL
--   edition IS NOT NULL AND edition != 'N/A' (unless promo set)
--   grader IN ('PSA','CGC','BGS','SGC')
--   grade IS NOT NULL
--   sold_price > 0
--   sale_date IS NOT NULL
--
-- Failures route to grade_reject_log — never silently dropped.
-- Authority: infrastructure/docs/pokemonbhn/pokemonbhn-data-architecture.md
-- ============================================================================

\set ON_ERROR_STOP on
BEGIN;

CREATE TABLE IF NOT EXISTS silver_ebay_transactions (

    id                  BIGSERIAL PRIMARY KEY,

    -- -------------------------------------------------------------------------
    -- PBDD identity
    -- -------------------------------------------------------------------------
    pbdd_code           TEXT NOT NULL,
    pbdd_grade_code     TEXT NOT NULL,
    card_id             INTEGER NOT NULL
                        REFERENCES master_card_catalog(id) ON DELETE RESTRICT,

    -- -------------------------------------------------------------------------
    -- pbdd_code components
    -- -------------------------------------------------------------------------
    set_name            TEXT NOT NULL,
    card_number         TEXT NOT NULL,
    edition             TEXT NOT NULL,
    print_variant       TEXT NOT NULL DEFAULT 'Standard',

    -- -------------------------------------------------------------------------
    -- pbdd_grade_code components
    -- -------------------------------------------------------------------------
    grader              TEXT NOT NULL,
    grade               TEXT NOT NULL,
    grade_numeric       DECIMAL(3,1),
    grade_tier          TEXT NOT NULL,

    -- -------------------------------------------------------------------------
    -- Transaction detail
    -- -------------------------------------------------------------------------
    cert_number         TEXT,
    sold_price          DECIMAL(10,2) NOT NULL,
    shipping_price      DECIMAL(8,2),
    total_price         DECIMAL(10,2)
                        GENERATED ALWAYS AS (
                            sold_price + COALESCE(shipping_price, 0)
                        ) STORED,
    currency            CHAR(3) NOT NULL DEFAULT 'USD',
    ebay_item_id        TEXT NOT NULL,
    sale_date           DATE NOT NULL,
    sale_datetime       TIMESTAMPTZ,
    transaction_type    TEXT,
    listing_url         TEXT,

    -- -------------------------------------------------------------------------
    -- Seller info
    -- -------------------------------------------------------------------------
    seller_username     TEXT,
    seller_feedback     INT,
    seller_location     TEXT,

    -- -------------------------------------------------------------------------
    -- Audit / source
    -- -------------------------------------------------------------------------
    title_raw           TEXT,
    source              TEXT NOT NULL DEFAULT 'ebay',
    bronze_id           BIGINT NOT NULL
                        REFERENCES ebay_transactions(id) ON DELETE RESTRICT,
    promoted_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    promotion_method    TEXT NOT NULL,

    -- -------------------------------------------------------------------------
    -- Constraints
    -- -------------------------------------------------------------------------
    CONSTRAINT chk_grader
        CHECK (grader IN ('PSA','CGC','BGS','SGC')),
    CONSTRAINT chk_edition
        CHECK (edition IN ('1st Edition','Unlimited','Shadowless','N/A')),
    CONSTRAINT chk_transaction_type
        CHECK (transaction_type IS NULL OR
               transaction_type IN ('BIN','auction','OBO')),
    CONSTRAINT chk_promotion_method
        CHECK (promotion_method IN ('exact_match','fuzzy_match','manual')),
    CONSTRAINT chk_currency
        CHECK (currency IN ('USD','GBP','EUR','CAD','AUD')),
    CONSTRAINT chk_sold_price
        CHECK (sold_price > 0),
    CONSTRAINT chk_grade_numeric
        CHECK (grade_numeric IS NULL OR
               (grade_numeric >= 1.0 AND grade_numeric <= 10.0)),

    UNIQUE (pbdd_code, pbdd_grade_code, bronze_id)
);

-- ============================================================================
-- Indexes
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_silver_ebay_pbdd
    ON silver_ebay_transactions (pbdd_code, pbdd_grade_code);

CREATE INDEX IF NOT EXISTS idx_silver_ebay_card_id
    ON silver_ebay_transactions (card_id);

CREATE INDEX IF NOT EXISTS idx_silver_ebay_sale_date
    ON silver_ebay_transactions (sale_date DESC);

CREATE INDEX IF NOT EXISTS idx_silver_ebay_set_edition
    ON silver_ebay_transactions (set_name, edition);

CREATE INDEX IF NOT EXISTS idx_silver_ebay_grader_grade
    ON silver_ebay_transactions (grader, grade_numeric);

CREATE INDEX IF NOT EXISTS idx_silver_ebay_cert
    ON silver_ebay_transactions (cert_number)
    WHERE cert_number IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_silver_ebay_bronze_id
    ON silver_ebay_transactions (bronze_id);

CREATE INDEX IF NOT EXISTS idx_silver_ebay_promoted_at
    ON silver_ebay_transactions (promoted_at DESC);

-- ============================================================================
-- Grants
-- ============================================================================

GRANT SELECT ON silver_ebay_transactions TO agent_reader;
GRANT SELECT ON silver_ebay_transactions TO grafana_reader;
GRANT SELECT ON silver_ebay_transactions TO ehuser;
GRANT INSERT, UPDATE ON silver_ebay_transactions TO n8n_user;
GRANT USAGE ON SEQUENCE silver_ebay_transactions_id_seq TO n8n_user;

-- ============================================================================
-- Comments
-- ============================================================================

COMMENT ON TABLE silver_ebay_transactions IS
    'Silver layer — eBay sold comps promoted from ebay_transactions (Bronze). '
    'card_id resolved, PBDD codes computed, grader/grade/edition normalized. '
    'Feeds card_valuations (Gold). Promoted via BRONZE_TO_SILVER_EBAY_TRANSACTIONS n8n workflow.';

COMMENT ON COLUMN silver_ebay_transactions.pbdd_code        IS 'PBDD human card identifier — TRK014-1E-HOLO. Derived, display only.';
COMMENT ON COLUMN silver_ebay_transactions.pbdd_grade_code  IS 'PBDD grade tier code — PSA10GM / CGC9.5M+. Computed via pbdd_grade_code().';
COMMENT ON COLUMN silver_ebay_transactions.card_id          IS 'FK → master_card_catalog.id. Resolved by title re-parser during promotion.';
COMMENT ON COLUMN silver_ebay_transactions.grade            IS 'Verbatim raw_label from source. FK → master_grade_catalog.';
COMMENT ON COLUMN silver_ebay_transactions.total_price      IS 'sold_price + shipping_price. Generated — do not set directly.';
COMMENT ON COLUMN silver_ebay_transactions.title_raw        IS 'Original eBay title preserved for re-parsing audit.';
COMMENT ON COLUMN silver_ebay_transactions.bronze_id        IS 'FK → ebay_transactions.id. Hard provenance link to Bronze source row.';
COMMENT ON COLUMN silver_ebay_transactions.promotion_method IS 'How card_id was resolved: exact_match / fuzzy_match / manual override.';
COMMENT ON COLUMN silver_ebay_transactions.source           IS 'Source platform. Always ebay here — future tables: tcgplayer etc.';

COMMIT;
