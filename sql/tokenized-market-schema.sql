-- ============================================================================
-- PokemonBHN — Tokenized Market Schema
-- ============================================================================
--
-- Captures the NFT-backed-physical-card market: cards minted as tokens on
-- Courtyard (Polygon) and Collector Crypt (Solana) that represent real graded
-- slabs held in physical custody. Same underlying card-identity model as the
-- ebay_listings / sold_listings streams — the same graded card may appear
-- across both physical (eBay) and tokenized (Courtyard / CC) markets, which
-- is exactly what makes the cross-market arbitrage signal table interesting.
--
-- Authority: infrastructure/docs/pokemonbhn/collectibles-data-standard.md
-- Built against: live eventhorizon DDL of ebay_listings (verified 2026-05-22)
--
-- Design rules applied:
--   * Mirror ebay_listings column-for-column, in original order, with the
--     grade-type drift corrected (TEXT, not NUMERIC — standard §3.5).
--   * Standard-required columns missing from ebay_listings today
--     (card_number, edition, print_variant, sold_price) are added at the end
--     of the mirror block. ebay_listings should converge to this shape.
--   * Tokenized-only additions follow at the very end.
--   * grade is verbatim raw_label TEXT. Soft validate (no FK) — same tier as
--     ebay_listings. New labels must be added to master_grade_catalog first.
--   * edition + print_variant: NOT NULL, controlled vocab via CHECK.
--   * grader: codes-only CHECK ({CGC,PSA,BGS,SGC}) — descriptors rejected.
--   * platform / blockchain / sale_type: CHECK-bounded controlled vocabs.
--   * Per-table CHECK pins (platform,blockchain) to the table's domain.
--   * Idempotency: item_id UNIQUE (mirror ebay_listings).
--   * Money: listed_price/sold_price separate. NULL means absent, 0 means free.
--   * shipping, bid_count, seller_feedback, returns_accepted: kept (mirror)
--     but populate as NULL on tokenized — no physical shipping, no auctions,
--     no reputation system, no returns policy.
--   * Grants:
--       log_shipper   - INSERT on all; UPDATE on listings only (sales immutable)
--       agent_reader  - SELECT
--       grafana_reader- SELECT
--       ehuser        - SELECT
--       n8n_user      - INSERT, UPDATE on tokenized_arbitrage_signals
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. courtyard_listings — active NFT listings on Courtyard (Polygon)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS courtyard_listings (
    -- == ebay_listings mirror block (verbatim order; grade TEXT per §3.5) ==
    id                  SERIAL PRIMARY KEY,
    item_id             TEXT UNIQUE,
    title               TEXT,
    card_name           TEXT,
    grader              TEXT,
    grade               TEXT,
    listed_price        NUMERIC,
    shipping            NUMERIC,
    seller_username     TEXT,
    seller_feedback     INTEGER,
    seller_feedback_pct NUMERIC,
    listing_url         TEXT,
    image_url           TEXT,
    condition           TEXT,
    item_creation_date  TIMESTAMPTZ,
    returns_accepted    BOOLEAN,
    listed_at           TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    current_bid         NUMERIC,
    bid_count           INTEGER,
    currency            TEXT,
    transaction_type    TEXT,
    obo_available       BOOLEAN,
    obo_min_price       NUMERIC,
    set_name            TEXT,
    language            TEXT,
    item_url            TEXT,

    -- == Standard-required identity + price columns (missing from ebay_listings drift) ==
    card_number         TEXT,
    edition             TEXT NOT NULL DEFAULT 'N/A',
    print_variant       TEXT NOT NULL DEFAULT 'Standard',
    sold_price          NUMERIC,

    -- == Tokenized-only additions ==
    platform            TEXT NOT NULL,
    blockchain          TEXT NOT NULL,
    transaction_hash    TEXT,
    sale_type           TEXT,
    seller_address      TEXT,
    buyer_address       TEXT,
    sol_price           DECIMAL(20,9),
    sol_usd_rate        DECIMAL(10,2),
    nft_contract        TEXT,

    -- == Constraints ==
    CONSTRAINT courtyard_listings_edition_chk
        CHECK (edition IN ('1st Edition','Unlimited','Shadowless','N/A')),
    CONSTRAINT courtyard_listings_print_variant_chk
        CHECK (print_variant IN (
            'Standard','Holo','Error','No Symbol','W Stamp','Winner','Jumbo',
            'Prerelease','Gold Border','Red Cheeks','WB Movie','Nintendo Power',
            'WOTC','1999-2000 Copyright')),
    CONSTRAINT courtyard_listings_grader_chk
        CHECK (grader IS NULL OR grader IN ('CGC','PSA','BGS','SGC')),
    CONSTRAINT courtyard_listings_platform_chk
        CHECK (platform = 'courtyard'),
    CONSTRAINT courtyard_listings_blockchain_chk
        CHECK (blockchain = 'polygon'),
    CONSTRAINT courtyard_listings_sale_type_chk
        CHECK (sale_type IS NULL OR sale_type IN ('peer_to_peer','buyback','gacha'))
);

COMMENT ON TABLE courtyard_listings IS
    'Active NFT listings on Courtyard (Polygon). Mirrors ebay_listings column shape (with grade TEXT per standard) plus tokenized-platform additions. Soft validation tier - no FK to master_grade_catalog.';


-- ----------------------------------------------------------------------------
-- 2. courtyard_sales — completed NFT sales on Courtyard (Polygon)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS courtyard_sales (
    -- == ebay_listings mirror block ==
    id                  SERIAL PRIMARY KEY,
    item_id             TEXT UNIQUE,
    title               TEXT,
    card_name           TEXT,
    grader              TEXT,
    grade               TEXT,
    listed_price        NUMERIC,
    shipping            NUMERIC,
    seller_username     TEXT,
    seller_feedback     INTEGER,
    seller_feedback_pct NUMERIC,
    listing_url         TEXT,
    image_url           TEXT,
    condition           TEXT,
    item_creation_date  TIMESTAMPTZ,
    returns_accepted    BOOLEAN,
    listed_at           TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    current_bid         NUMERIC,
    bid_count           INTEGER,
    currency            TEXT,
    transaction_type    TEXT,
    obo_available       BOOLEAN,
    obo_min_price       NUMERIC,
    set_name            TEXT,
    language            TEXT,
    item_url            TEXT,

    -- == Standard-required identity + price columns ==
    card_number         TEXT,
    edition             TEXT NOT NULL DEFAULT 'N/A',
    print_variant       TEXT NOT NULL DEFAULT 'Standard',
    sold_price          NUMERIC,

    -- == Tokenized-only additions ==
    platform            TEXT NOT NULL,
    blockchain          TEXT NOT NULL,
    transaction_hash    TEXT,
    sale_type           TEXT,
    seller_address      TEXT,
    buyer_address       TEXT,
    sol_price           DECIMAL(20,9),
    sol_usd_rate        DECIMAL(10,2),
    nft_contract        TEXT,

    -- == Constraints ==
    CONSTRAINT courtyard_sales_edition_chk
        CHECK (edition IN ('1st Edition','Unlimited','Shadowless','N/A')),
    CONSTRAINT courtyard_sales_print_variant_chk
        CHECK (print_variant IN (
            'Standard','Holo','Error','No Symbol','W Stamp','Winner','Jumbo',
            'Prerelease','Gold Border','Red Cheeks','WB Movie','Nintendo Power',
            'WOTC','1999-2000 Copyright')),
    CONSTRAINT courtyard_sales_grader_chk
        CHECK (grader IS NULL OR grader IN ('CGC','PSA','BGS','SGC')),
    CONSTRAINT courtyard_sales_platform_chk
        CHECK (platform = 'courtyard'),
    CONSTRAINT courtyard_sales_blockchain_chk
        CHECK (blockchain = 'polygon'),
    CONSTRAINT courtyard_sales_sale_type_chk
        CHECK (sale_type IS NULL OR sale_type IN ('peer_to_peer','buyback','gacha'))
);

COMMENT ON TABLE courtyard_sales IS
    'Completed NFT sales on Courtyard (Polygon). Same shape as courtyard_listings. sold_price + transaction_hash are the primary signal columns; listed_price holds the pre-sale ask if known.';


-- ----------------------------------------------------------------------------
-- 3. collector_crypt_sales — completed sales on Collector Crypt (Solana)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS collector_crypt_sales (
    -- == ebay_listings mirror block ==
    id                  SERIAL PRIMARY KEY,
    item_id             TEXT UNIQUE,
    title               TEXT,
    card_name           TEXT,
    grader              TEXT,
    grade               TEXT,
    listed_price        NUMERIC,
    shipping            NUMERIC,
    seller_username     TEXT,
    seller_feedback     INTEGER,
    seller_feedback_pct NUMERIC,
    listing_url         TEXT,
    image_url           TEXT,
    condition           TEXT,
    item_creation_date  TIMESTAMPTZ,
    returns_accepted    BOOLEAN,
    listed_at           TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    current_bid         NUMERIC,
    bid_count           INTEGER,
    currency            TEXT,
    transaction_type    TEXT,
    obo_available       BOOLEAN,
    obo_min_price       NUMERIC,
    set_name            TEXT,
    language            TEXT,
    item_url            TEXT,

    -- == Standard-required identity + price columns ==
    card_number         TEXT,
    edition             TEXT NOT NULL DEFAULT 'N/A',
    print_variant       TEXT NOT NULL DEFAULT 'Standard',
    sold_price          NUMERIC,

    -- == Tokenized-only additions ==
    platform            TEXT NOT NULL,
    blockchain          TEXT NOT NULL,
    transaction_hash    TEXT,
    sale_type           TEXT,
    seller_address      TEXT,
    buyer_address       TEXT,
    sol_price           DECIMAL(20,9),
    sol_usd_rate        DECIMAL(10,2),
    nft_contract        TEXT,

    -- == Constraints ==
    CONSTRAINT collector_crypt_sales_edition_chk
        CHECK (edition IN ('1st Edition','Unlimited','Shadowless','N/A')),
    CONSTRAINT collector_crypt_sales_print_variant_chk
        CHECK (print_variant IN (
            'Standard','Holo','Error','No Symbol','W Stamp','Winner','Jumbo',
            'Prerelease','Gold Border','Red Cheeks','WB Movie','Nintendo Power',
            'WOTC','1999-2000 Copyright')),
    CONSTRAINT collector_crypt_sales_grader_chk
        CHECK (grader IS NULL OR grader IN ('CGC','PSA','BGS','SGC')),
    CONSTRAINT collector_crypt_sales_platform_chk
        CHECK (platform = 'collector_crypt'),
    CONSTRAINT collector_crypt_sales_blockchain_chk
        CHECK (blockchain = 'solana'),
    CONSTRAINT collector_crypt_sales_sale_type_chk
        CHECK (sale_type IS NULL OR sale_type IN ('peer_to_peer','buyback','gacha'))
);

COMMENT ON TABLE collector_crypt_sales IS
    'Completed sales on Collector Crypt (Solana). Same shape as the Courtyard tables. sol_price + sol_usd_rate carry the native-currency view; sold_price is the USD-pegged number used for cross-market comparison.';


-- ----------------------------------------------------------------------------
-- 4. tokenized_arbitrage_signals — cross-market opportunity flags
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tokenized_arbitrage_signals (
    id                  BIGSERIAL PRIMARY KEY,
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    card_name           TEXT NOT NULL,
    set_name            TEXT,
    grader              TEXT,
    grade               TEXT,
    edition             TEXT,
    print_variant       TEXT,
    ebay_item_id        TEXT,            -- soft ref to ebay_listings.item_id
    ebay_listed_price   DECIMAL(10,2),
    ebay_90d_avg        DECIMAL(10,2),
    courtyard_ask       DECIMAL(10,2),
    collector_crypt_ask DECIMAL(10,2),
    tokenized_90d_avg   DECIMAL(10,2),
    buyback_floor       DECIMAL(10,2),
    spread_pct          DECIMAL(6,2),
    estimated_profit    DECIMAL(10,2),
    signal_strength     TEXT,
    reviewed            BOOLEAN DEFAULT FALSE,
    actioned            BOOLEAN DEFAULT FALSE,
    notes               TEXT,
    expires_at          TIMESTAMPTZ,
    raw_payload         JSONB,

    CONSTRAINT tokenized_arbitrage_signals_strength_chk
        CHECK (signal_strength IS NULL OR signal_strength IN ('weak','moderate','strong','critical')),
    CONSTRAINT tokenized_arbitrage_signals_grader_chk
        CHECK (grader IS NULL OR grader IN ('CGC','PSA','BGS','SGC')),
    CONSTRAINT tokenized_arbitrage_signals_edition_chk
        CHECK (edition IS NULL OR edition IN ('1st Edition','Unlimited','Shadowless','N/A')),
    CONSTRAINT tokenized_arbitrage_signals_print_variant_chk
        CHECK (print_variant IS NULL OR print_variant IN (
            'Standard','Holo','Error','No Symbol','W Stamp','Winner','Jumbo',
            'Prerelease','Gold Border','Red Cheeks','WB Movie','Nintendo Power',
            'WOTC','1999-2000 Copyright'))
);

COMMENT ON TABLE tokenized_arbitrage_signals IS
    'Cross-market opportunity flags: when the same graded card is meaningfully cheaper on one market vs. another. Sources are referenced softly (ebay_item_id is a text reference, not a hard FK) so signal records survive churn in the source streams.';


-- ============================================================================
-- Grants
-- ============================================================================

-- log_shipper: INSERT everywhere + UPDATE on listings only (sales immutable)
GRANT INSERT, UPDATE ON courtyard_listings    TO log_shipper;
GRANT INSERT          ON courtyard_sales      TO log_shipper;
GRANT INSERT          ON collector_crypt_sales TO log_shipper;
GRANT USAGE ON SEQUENCE courtyard_listings_id_seq    TO log_shipper;
GRANT USAGE ON SEQUENCE courtyard_sales_id_seq       TO log_shipper;
GRANT USAGE ON SEQUENCE collector_crypt_sales_id_seq TO log_shipper;

-- n8n_user: INSERT + UPDATE on the signal table
GRANT INSERT, UPDATE ON tokenized_arbitrage_signals  TO n8n_user;
GRANT USAGE ON SEQUENCE tokenized_arbitrage_signals_id_seq TO n8n_user;

-- Read access for HORIZON, Grafana, and the operator role
GRANT SELECT ON courtyard_listings           TO agent_reader, grafana_reader, ehuser;
GRANT SELECT ON courtyard_sales              TO agent_reader, grafana_reader, ehuser;
GRANT SELECT ON collector_crypt_sales        TO agent_reader, grafana_reader, ehuser;
GRANT SELECT ON tokenized_arbitrage_signals  TO agent_reader, grafana_reader, ehuser;

COMMIT;
