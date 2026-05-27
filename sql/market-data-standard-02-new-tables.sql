-- ============================================================================
-- BHN Market Data Standard v2 — Step 02: New tables
-- ============================================================================
-- Depends on Step 01 (renames). Creates the tables the v2 spec adds:
--   ebay_bids, courtyard_bids, collector_crypt_bids, collector_crypt_asks,
--   order_price_history, fee_schedule, arbitrage_positions.
--
-- All CREATE TABLE IF NOT EXISTS. Safe to re-apply.
--
-- Apply on LA hub:
--   cat sql/market-data-standard-02-new-tables.sql \
--     | sudo -u postgres psql -d eventhorizon -v ON_ERROR_STOP=1
-- ============================================================================

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2A. ebay_bids — Best Offer / OBO offers on YOUR eBay listings
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ebay_bids (
    id                  SERIAL PRIMARY KEY,
    -- universal
    card_id             INTEGER REFERENCES master_card_catalog(id),
    card_code           TEXT,
    card_name           TEXT,
    set_name            TEXT,
    card_number         TEXT,
    grader              TEXT,
    grade               TEXT,
    edition             TEXT NOT NULL DEFAULT 'N/A',
    print_variant       TEXT NOT NULL DEFAULT 'Standard',
    platform            TEXT NOT NULL DEFAULT 'ebay',
    currency            TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    raw_payload         JSONB,
    -- offer
    offer_id            TEXT UNIQUE,
    offer_price         DECIMAL(10,2),
    offer_type          TEXT,
    -- lifecycle
    offered_at          TIMESTAMPTZ,
    expires_at          TIMESTAMPTZ,
    is_active           BOOLEAN DEFAULT TRUE,
    status              TEXT,
    -- buyer
    buyer_username      TEXT,

    CONSTRAINT ebay_bids_platform_chk     CHECK (platform = 'ebay'),
    CONSTRAINT ebay_bids_grader_chk       CHECK (grader IS NULL OR grader IN ('CGC','PSA','BGS','SGC')),
    CONSTRAINT ebay_bids_edition_chk      CHECK (edition IN ('1st Edition','Unlimited','Shadowless','N/A')),
    CONSTRAINT ebay_bids_offer_type_chk   CHECK (offer_type IS NULL OR offer_type IN (
                                              'individual','collection','trait','obo'
                                          )),
    CONSTRAINT ebay_bids_status_chk       CHECK (status IS NULL OR status IN (
                                              'open','accepted','declined','expired','cancelled'
                                          ))
);
CREATE INDEX IF NOT EXISTS ebay_bids_card_id_idx ON ebay_bids(card_id);

COMMENT ON TABLE ebay_bids IS
    'Best Offer / OBO offers received on YOUR eBay listings (Trading API). Cannot see offers on other sellers'' listings.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 2B. courtyard_bids — Offers on Courtyard tokens (OpenSea Offers API)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS courtyard_bids (
    id                  SERIAL PRIMARY KEY,
    card_id             INTEGER REFERENCES master_card_catalog(id),
    card_code           TEXT,
    card_name           TEXT,
    set_name            TEXT,
    card_number         TEXT,
    grader              TEXT,
    grade               TEXT,
    edition             TEXT NOT NULL DEFAULT 'N/A',
    print_variant       TEXT NOT NULL DEFAULT 'Standard',
    platform            TEXT NOT NULL DEFAULT 'courtyard',
    currency            TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    raw_payload         JSONB,
    offer_id            TEXT UNIQUE,
    offer_price         DECIMAL(10,2),
    offer_type          TEXT,
    offered_at          TIMESTAMPTZ,
    expires_at          TIMESTAMPTZ,
    is_active           BOOLEAN DEFAULT TRUE,
    status              TEXT,
    buyer_username      TEXT,
    buyer_address       TEXT,
    blockchain          TEXT NOT NULL DEFAULT 'polygon',
    order_hash          TEXT,

    CONSTRAINT courtyard_bids_platform_chk   CHECK (platform = 'courtyard'),
    CONSTRAINT courtyard_bids_blockchain_chk CHECK (blockchain = 'polygon'),
    CONSTRAINT courtyard_bids_grader_chk     CHECK (grader IS NULL OR grader IN ('CGC','PSA','BGS','SGC')),
    CONSTRAINT courtyard_bids_edition_chk    CHECK (edition IN ('1st Edition','Unlimited','Shadowless','N/A')),
    CONSTRAINT courtyard_bids_offer_type_chk CHECK (offer_type IS NULL OR offer_type IN (
                                                'individual','collection','trait','obo'
                                            )),
    CONSTRAINT courtyard_bids_status_chk     CHECK (status IS NULL OR status IN (
                                                'open','accepted','declined','expired','cancelled'
                                            ))
);
CREATE INDEX IF NOT EXISTS courtyard_bids_card_id_idx ON courtyard_bids(card_id);


-- ─────────────────────────────────────────────────────────────────────────────
-- 2C. collector_crypt_bids — Bids on CC tokens (Magic Eden Bids API)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS collector_crypt_bids (
    id                  SERIAL PRIMARY KEY,
    card_id             INTEGER REFERENCES master_card_catalog(id),
    card_code           TEXT,
    card_name           TEXT,
    set_name            TEXT,
    card_number         TEXT,
    grader              TEXT,
    grade               TEXT,
    edition             TEXT NOT NULL DEFAULT 'N/A',
    print_variant       TEXT NOT NULL DEFAULT 'Standard',
    platform            TEXT NOT NULL DEFAULT 'collector_crypt',
    currency            TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    raw_payload         JSONB,
    offer_id            TEXT UNIQUE,
    offer_price         DECIMAL(10,2),
    offer_type          TEXT,
    offered_at          TIMESTAMPTZ,
    expires_at          TIMESTAMPTZ,
    is_active           BOOLEAN DEFAULT TRUE,
    status              TEXT,
    buyer_username      TEXT,
    buyer_address       TEXT,
    blockchain          TEXT NOT NULL DEFAULT 'solana',
    order_hash          TEXT,

    CONSTRAINT collector_crypt_bids_platform_chk   CHECK (platform = 'collector_crypt'),
    CONSTRAINT collector_crypt_bids_blockchain_chk CHECK (blockchain = 'solana'),
    CONSTRAINT collector_crypt_bids_grader_chk     CHECK (grader IS NULL OR grader IN ('CGC','PSA','BGS','SGC')),
    CONSTRAINT collector_crypt_bids_edition_chk    CHECK (edition IN ('1st Edition','Unlimited','Shadowless','N/A')),
    CONSTRAINT collector_crypt_bids_offer_type_chk CHECK (offer_type IS NULL OR offer_type IN (
                                                      'individual','collection','trait','obo'
                                                  )),
    CONSTRAINT collector_crypt_bids_status_chk     CHECK (status IS NULL OR status IN (
                                                      'open','accepted','declined','expired','cancelled'
                                                  ))
);
CREATE INDEX IF NOT EXISTS collector_crypt_bids_card_id_idx ON collector_crypt_bids(card_id);


-- ─────────────────────────────────────────────────────────────────────────────
-- 2D. collector_crypt_asks — CC sell listings (Magic Eden Listings API)
-- ─────────────────────────────────────────────────────────────────────────────
-- New table. The 2026-05-22 schema only built collector_crypt_sales (now
-- collector_crypt_transactions); the live-listings side was deferred until
-- the Magic Eden Listings API was wired. v2 brings it into the standard.
CREATE TABLE IF NOT EXISTS collector_crypt_asks (
    id                  SERIAL PRIMARY KEY,
    card_id             INTEGER REFERENCES master_card_catalog(id),
    card_code           TEXT,
    card_name           TEXT,
    set_name            TEXT,
    card_number         TEXT,
    grader              TEXT,
    grade               TEXT,
    edition             TEXT NOT NULL DEFAULT 'N/A',
    print_variant       TEXT NOT NULL DEFAULT 'Standard',
    platform            TEXT NOT NULL DEFAULT 'collector_crypt',
    currency            TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    raw_payload         JSONB,
    -- listing
    item_id             TEXT UNIQUE,
    listed_price        DECIMAL(10,2),
    final_price         DECIMAL(10,2),
    listed_at           TIMESTAMPTZ,
    ended_at            TIMESTAMPTZ,
    days_listed         INT,
    outcome             TEXT,
    -- demand
    watchers            INT,
    views               INT,
    bid_count           INT,
    best_offer_received DECIMAL(10,2),
    -- relist tracking
    relist_count        INT DEFAULT 0,
    original_item_id    TEXT,
    previous_ask_price  DECIMAL(10,2),
    -- seller
    seller_username     TEXT,
    seller_address      TEXT,
    seller_feedback     INT,
    seller_feedback_pct NUMERIC,
    -- listing meta
    listing_url         TEXT,
    image_url           TEXT,
    condition           TEXT,
    transaction_type    TEXT,
    last_seen_at        TIMESTAMPTZ,
    -- tokenized
    blockchain          TEXT NOT NULL DEFAULT 'solana',
    nft_contract        TEXT,

    CONSTRAINT collector_crypt_asks_platform_chk   CHECK (platform = 'collector_crypt'),
    CONSTRAINT collector_crypt_asks_blockchain_chk CHECK (blockchain = 'solana'),
    CONSTRAINT collector_crypt_asks_grader_chk     CHECK (grader IS NULL OR grader IN ('CGC','PSA','BGS','SGC')),
    CONSTRAINT collector_crypt_asks_edition_chk    CHECK (edition IN ('1st Edition','Unlimited','Shadowless','N/A')),
    CONSTRAINT collector_crypt_asks_print_variant_chk CHECK (print_variant IN (
        'Standard','Holo','Error','No Symbol','W Stamp','Winner','Jumbo',
        'Prerelease','Gold Border','Red Cheeks','WB Movie','Nintendo Power',
        'WOTC','1999-2000 Copyright'
    )),
    CONSTRAINT collector_crypt_asks_outcome_chk    CHECK (outcome IS NULL OR outcome IN (
        'active','sold','delisted','price_reduced','expired','buyback'
    ))
);
CREATE INDEX IF NOT EXISTS collector_crypt_asks_card_id_idx ON collector_crypt_asks(card_id);


-- ─────────────────────────────────────────────────────────────────────────────
-- 2E. order_price_history — bid + ask price change tracking
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS order_price_history (
    id                  SERIAL PRIMARY KEY,
    market              TEXT NOT NULL,
    order_side          TEXT NOT NULL,
    item_id             TEXT NOT NULL,
    card_id             INTEGER REFERENCES master_card_catalog(id),
    card_code           TEXT,
    old_price           DECIMAL(10,2),
    new_price           DECIMAL(10,2),
    change_pct          DECIMAL(6,2),
    change_direction    TEXT NOT NULL,
    recorded_at         TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT order_price_history_market_chk      CHECK (market IN ('ebay','courtyard','collector_crypt')),
    CONSTRAINT order_price_history_order_side_chk  CHECK (order_side IN ('bid','ask')),
    CONSTRAINT order_price_history_direction_chk   CHECK (change_direction IN ('increase','decrease'))
);
CREATE INDEX IF NOT EXISTS order_price_history_card_id_idx     ON order_price_history(card_id);
CREATE INDEX IF NOT EXISTS order_price_history_market_side_idx ON order_price_history(market, order_side);
CREATE INDEX IF NOT EXISTS order_price_history_recorded_idx    ON order_price_history(recorded_at);


-- ─────────────────────────────────────────────────────────────────────────────
-- 2F. fee_schedule — platform fee reference table (seed in Step 03)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS fee_schedule (
    id                  SERIAL PRIMARY KEY,
    market              TEXT NOT NULL,
    fee_name            TEXT NOT NULL,
    fee_type            TEXT NOT NULL,
    rate                DECIMAL(10,4),
    applies_to          TEXT,
    min_threshold       DECIMAL(10,2),
    max_threshold       DECIMAL(10,2),
    is_promotional      BOOLEAN DEFAULT FALSE,
    promo_expires_at    TIMESTAMPTZ,
    effective_date      DATE NOT NULL,
    notes               TEXT,
    verified_source     TEXT,
    updated_at          TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT fee_schedule_market_chk     CHECK (market IN ('ebay','courtyard','collector_crypt')),
    CONSTRAINT fee_schedule_fee_type_chk   CHECK (fee_type IN (
        'platform_pct','platform_flat','payment_pct','payment_flat',
        'royalty_pct','shipping_flat','shipping_pct','authentication_flat',
        'redemption_flat','tokenization_flat','gas_flat','tax_pct'
    )),
    CONSTRAINT fee_schedule_applies_chk    CHECK (applies_to IS NULL OR applies_to IN ('buyer','seller','both')),
    CONSTRAINT fee_schedule_unique         UNIQUE (market, fee_name, effective_date)
);
CREATE INDEX IF NOT EXISTS fee_schedule_market_idx     ON fee_schedule(market);
CREATE INDEX IF NOT EXISTS fee_schedule_effective_idx  ON fee_schedule(effective_date);


-- ─────────────────────────────────────────────────────────────────────────────
-- 2G. arbitrage_positions — full trade lifecycle + P&L
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS arbitrage_positions (
    -- identity
    id                      SERIAL PRIMARY KEY,
    card_id                 INTEGER REFERENCES master_card_catalog(id),
    card_code               TEXT,
    slab_code               TEXT,
    card_name               TEXT,
    set_name                TEXT,
    grader                  TEXT,
    grade                   TEXT,
    -- signal
    signal_id               INTEGER REFERENCES tokenized_arbitrage_signals(id),
    signal_strength         TEXT,
    signal_spread_pct       DECIMAL(6,2),
    signal_detected_at      TIMESTAMPTZ,
    -- status
    status                  TEXT NOT NULL,
    status_updated_at       TIMESTAMPTZ DEFAULT NOW(),
    -- direction
    direction               TEXT NOT NULL,
    -- buy side
    buy_market              TEXT,
    buy_item_id             TEXT,
    buy_price               DECIMAL(10,2),
    bought_at               TIMESTAMPTZ,
    buy_tx_hash             TEXT,
    buy_platform_fee        DECIMAL(10,2),
    buy_processing_fee      DECIMAL(10,2),
    buy_gas                 DECIMAL(10,6),
    buy_total_cost          DECIMAL(10,2),
    -- sell side
    sell_market             TEXT,
    sell_item_id            TEXT,
    sell_listed_price       DECIMAL(10,2),
    sell_final_price        DECIMAL(10,2),
    listed_at               TIMESTAMPTZ,
    sold_at                 TIMESTAMPTZ,
    sell_tx_hash            TEXT,
    sell_platform_fee       DECIMAL(10,2),
    sell_processing_fee     DECIMAL(10,2),
    sell_royalty_fee        DECIMAL(10,2),
    sell_auth_fee           DECIMAL(10,2),
    sell_shipping           DECIMAL(10,2),
    sell_gas                DECIMAL(10,6),
    sell_total_deductions   DECIMAL(10,2),
    sell_net_proceeds       DECIMAL(10,2),
    -- physical movement
    redemption_requested_at TIMESTAMPTZ,
    redemption_completed_at TIMESTAMPTZ,
    redemption_fee          DECIMAL(10,2),
    shipping_tracking       TEXT,
    shipping_cost           DECIMAL(10,2),
    shipping_carrier        TEXT,
    -- vault / tokenization
    vault_submitted_at      TIMESTAMPTZ,
    vault_authenticated_at  TIMESTAMPTZ,
    token_minted_at         TIMESTAMPTZ,
    tokenization_fee        DECIMAL(10,2),
    -- 3-way cost comparison (market / est / actual)
    market_platform_fee     DECIMAL(10,2),
    est_platform_fee        DECIMAL(10,2),
    actual_platform_fee     DECIMAL(10,2),
    market_processing_fee   DECIMAL(10,2),
    est_processing_fee      DECIMAL(10,2),
    actual_processing_fee   DECIMAL(10,2),
    market_shipping         DECIMAL(10,2),
    est_shipping            DECIMAL(10,2),
    actual_shipping         DECIMAL(10,2),
    market_auth_fee         DECIMAL(10,2),
    est_auth_fee            DECIMAL(10,2),
    actual_auth_fee         DECIMAL(10,2),
    market_redemption_fee   DECIMAL(10,2),
    est_redemption_fee      DECIMAL(10,2),
    actual_redemption_fee   DECIMAL(10,2),
    market_gas_fee          DECIMAL(10,2),
    est_gas_fee             DECIMAL(10,2),
    actual_gas_fee          DECIMAL(10,2),
    -- rollups
    market_total_costs      DECIMAL(10,2),
    est_total_costs         DECIMAL(10,2),
    actual_total_costs      DECIMAL(10,2),
    -- deltas
    delta_market_vs_est     DECIMAL(10,2),
    delta_est_vs_actual     DECIMAL(10,2),
    delta_market_vs_actual  DECIMAL(10,2),
    -- profit (3 views)
    market_net_profit       DECIMAL(10,2),
    est_net_profit          DECIMAL(10,2),
    actual_net_profit       DECIMAL(10,2),
    -- return metrics
    est_roi_pct             DECIMAL(6,2),
    actual_roi_pct          DECIMAL(6,2),
    roi_variance_pct        DECIMAL(6,2),
    days_held               INT,
    annualized_return_pct   DECIMAL(8,2),
    -- risk mgmt
    max_loss_threshold      DECIMAL(10,2),
    target_profit           DECIMAL(10,2),
    price_at_risk           DECIMAL(10,2),
    unrealized_pnl          DECIMAL(10,2),
    -- meta
    notes                   TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT arb_pos_status_chk     CHECK (status IN (
        'signal_detected','buy_pending','bought','relist_pending','listed',
        'sale_pending','sold','redeemed','shipped','cancelled','expired'
    )),
    CONSTRAINT arb_pos_direction_chk  CHECK (direction IN (
        'courtyard_to_courtyard','courtyard_to_ebay','courtyard_to_cc',
        'ebay_to_courtyard','ebay_to_cc',
        'cc_to_courtyard','cc_to_ebay','cc_to_cc',
        'within_opensea'
    )),
    CONSTRAINT arb_pos_strength_chk   CHECK (signal_strength IS NULL OR signal_strength IN (
        'weak','moderate','strong','critical'
    )),
    CONSTRAINT arb_pos_grader_chk     CHECK (grader IS NULL OR grader IN ('CGC','PSA','BGS','SGC'))
);
CREATE INDEX IF NOT EXISTS arb_pos_card_id_idx    ON arbitrage_positions(card_id);
CREATE INDEX IF NOT EXISTS arb_pos_status_idx     ON arbitrage_positions(status);
CREATE INDEX IF NOT EXISTS arb_pos_direction_idx  ON arbitrage_positions(direction);
CREATE INDEX IF NOT EXISTS arb_pos_open_idx       ON arbitrage_positions(status)
    WHERE status IN ('bought','listed','relist_pending');


-- ─────────────────────────────────────────────────────────────────────────────
-- Grants
-- ─────────────────────────────────────────────────────────────────────────────
GRANT INSERT, UPDATE ON ebay_bids               TO log_shipper, n8n_user;
GRANT INSERT, UPDATE ON courtyard_bids          TO log_shipper, n8n_user;
GRANT INSERT, UPDATE ON collector_crypt_bids    TO log_shipper, n8n_user;
GRANT INSERT, UPDATE ON collector_crypt_asks    TO log_shipper, n8n_user;
GRANT INSERT          ON order_price_history    TO log_shipper, n8n_user;
GRANT INSERT, UPDATE  ON fee_schedule           TO n8n_user;
GRANT INSERT, UPDATE  ON arbitrage_positions    TO n8n_user, ehuser;

GRANT USAGE ON SEQUENCE ebay_bids_id_seq             TO log_shipper, n8n_user;
GRANT USAGE ON SEQUENCE courtyard_bids_id_seq        TO log_shipper, n8n_user;
GRANT USAGE ON SEQUENCE collector_crypt_bids_id_seq  TO log_shipper, n8n_user;
GRANT USAGE ON SEQUENCE collector_crypt_asks_id_seq  TO log_shipper, n8n_user;
GRANT USAGE ON SEQUENCE order_price_history_id_seq   TO log_shipper, n8n_user;
GRANT USAGE ON SEQUENCE fee_schedule_id_seq          TO n8n_user;
GRANT USAGE ON SEQUENCE arbitrage_positions_id_seq   TO n8n_user, ehuser;

GRANT SELECT ON ebay_bids, courtyard_bids, collector_crypt_bids,
                collector_crypt_asks, order_price_history,
                fee_schedule, arbitrage_positions
    TO agent_reader, grafana_reader, ehuser;

COMMIT;
