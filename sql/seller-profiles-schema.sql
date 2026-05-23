-- ============================================================================
-- PokemonBHN — Seller Profiles Schema
-- ============================================================================
--
-- One unified cross-platform seller dimension. Holds enriched per-seller
-- metrics derived from the observation streams (ebay_listings,
-- courtyard_listings, courtyard_sales, collector_crypt_sales). A given
-- real-world seller may appear on multiple platforms under different
-- usernames; cross-platform identity is asserted via the self-referencing
-- linked_seller_id pointer rather than fused into a single row.
--
-- Authority: infrastructure/docs/pokemonbhn/collectibles-data-standard.md
-- Operator spec received: 2026-05-22 23:34 PT
--
-- Type note: linked_seller_id is INT per the operator spec; id is BIGSERIAL
-- (== BIGINT). Postgres accepts this FK with an implicit cast at lookup
-- time; promote to BIGINT later if seller count ever approaches 2.1B
-- (almost certainly never).
--
-- Grants:
--   log_shipper   - INSERT, UPDATE (scraper-side enrichment)
--   n8n_user      - INSERT, UPDATE (HORIZON workflow enrichment)
--   agent_reader  - SELECT
--   grafana_reader- SELECT
--   ehuser        - SELECT
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS seller_profiles (
    id                      BIGSERIAL PRIMARY KEY,
    seller_username         TEXT NOT NULL,
    platform                TEXT NOT NULL CHECK (platform IN ('ebay','courtyard','collector_crypt')),
    linked_seller_id        INT REFERENCES seller_profiles(id),
    seller_feedback_score   INT,
    seller_feedback_pct     DECIMAL(5,2),
    total_listings_seen     INT DEFAULT 0,
    total_sold              INT DEFAULT 0,
    sell_through_rate       DECIMAL(5,2),
    avg_days_to_sell        DECIMAL(6,1),
    avg_price_cut_pct       DECIMAL(5,2),
    relist_frequency        DECIMAL(5,2),
    active_listings         INT DEFAULT 0,
    active_listings_value   DECIMAL(10,2),
    avg_listing_age_days    DECIMAL(6,1),
    last_seen_at            TIMESTAMPTZ,
    first_seen_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_dealer               BOOLEAN DEFAULT FALSE,
    is_flagged              BOOLEAN DEFAULT FALSE,
    notes                   TEXT,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (seller_username, platform)
);

COMMENT ON TABLE seller_profiles IS
    'Cross-platform seller dimension. One row per (seller_username, platform). Same real-world seller across platforms is linked via linked_seller_id (operator/HORIZON assertion, not auto-derived).';

COMMENT ON COLUMN seller_profiles.linked_seller_id IS
    'Self-ref to another seller_profiles.id when the same real-world seller is known to operate under different usernames on different platforms. Type is INT per operator spec; references BIGSERIAL id with implicit cast.';

COMMENT ON COLUMN seller_profiles.is_dealer IS
    'Operator/HORIZON-asserted: this seller operates as a professional reseller (consistent inventory, repeat behavior). Used to weight signal quality.';

COMMENT ON COLUMN seller_profiles.is_flagged IS
    'Operator/HORIZON-asserted: this seller is flagged for review (suspect pricing, suspect grading, prior bad transaction, etc.). Distinct from is_dealer.';

-- ============================================================================
-- Grants
-- ============================================================================

GRANT INSERT, UPDATE ON seller_profiles            TO log_shipper;
GRANT INSERT, UPDATE ON seller_profiles            TO n8n_user;
GRANT USAGE ON SEQUENCE seller_profiles_id_seq     TO log_shipper, n8n_user;
GRANT SELECT           ON seller_profiles          TO agent_reader, grafana_reader, ehuser;

COMMIT;
