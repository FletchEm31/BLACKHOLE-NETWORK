-- tcgtracking-raw-schema.sql
-- Raw dump tables for all 5 TCGTracking.com games
-- Apply on LA: sudo -u postgres psql -d eventhorizon -f sql/tcgtracking-raw-schema.sql
--
-- 15 tables total — 3 per game (products, pricing, skus)
-- Structure/normalization deferred. Just saving raw JSON for now.
--
-- Game IDs:
--   Magic          = 1  (444 sets)
--   YuGiOh         = 2  (612 sets)
--   Pokemon        = 3  (216 sets)
--   One Piece      = 68 ( 76 sets)
--   Pokemon Japan  = 85 (~216 sets)
--
-- Uniqueness on (set_id, day-of-pull) is enforced via a STORED generated
-- `fetched_date` column. `(fetched_at::DATE)` directly is STABLE (depends on
-- session TZ) so PostgreSQL refuses to index it; pinning the cast to UTC via
-- `(fetched_at AT TIME ZONE 'UTC')::DATE` is IMMUTABLE and indexable.
-- The puller's ON CONFLICT targets `fetched_date` directly.

-- ─────────────────────────────────────────────────────────────────────────────
-- MAGIC
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tcgtracking_magic_products (
    set_id        INTEGER     NOT NULL,
    raw_data      JSONB       NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fetched_date  DATE        GENERATED ALWAYS AS ((fetched_at AT TIME ZONE 'UTC')::DATE) STORED,
    PRIMARY KEY (set_id, fetched_date)
);

CREATE TABLE IF NOT EXISTS tcgtracking_magic_pricing (
    set_id        INTEGER     NOT NULL,
    raw_data      JSONB       NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fetched_date  DATE        GENERATED ALWAYS AS ((fetched_at AT TIME ZONE 'UTC')::DATE) STORED,
    PRIMARY KEY (set_id, fetched_date)
);

CREATE TABLE IF NOT EXISTS tcgtracking_magic_skus (
    set_id        INTEGER     NOT NULL,
    raw_data      JSONB       NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fetched_date  DATE        GENERATED ALWAYS AS ((fetched_at AT TIME ZONE 'UTC')::DATE) STORED,
    PRIMARY KEY (set_id, fetched_date)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- YUGIOH
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tcgtracking_yugioh_products (
    set_id        INTEGER     NOT NULL,
    raw_data      JSONB       NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fetched_date  DATE        GENERATED ALWAYS AS ((fetched_at AT TIME ZONE 'UTC')::DATE) STORED,
    PRIMARY KEY (set_id, fetched_date)
);

CREATE TABLE IF NOT EXISTS tcgtracking_yugioh_pricing (
    set_id        INTEGER     NOT NULL,
    raw_data      JSONB       NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fetched_date  DATE        GENERATED ALWAYS AS ((fetched_at AT TIME ZONE 'UTC')::DATE) STORED,
    PRIMARY KEY (set_id, fetched_date)
);

CREATE TABLE IF NOT EXISTS tcgtracking_yugioh_skus (
    set_id        INTEGER     NOT NULL,
    raw_data      JSONB       NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fetched_date  DATE        GENERATED ALWAYS AS ((fetched_at AT TIME ZONE 'UTC')::DATE) STORED,
    PRIMARY KEY (set_id, fetched_date)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- POKEMON
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tcgtracking_pokemon_products (
    set_id        INTEGER     NOT NULL,
    raw_data      JSONB       NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fetched_date  DATE        GENERATED ALWAYS AS ((fetched_at AT TIME ZONE 'UTC')::DATE) STORED,
    PRIMARY KEY (set_id, fetched_date)
);

CREATE TABLE IF NOT EXISTS tcgtracking_pokemon_pricing (
    set_id        INTEGER     NOT NULL,
    raw_data      JSONB       NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fetched_date  DATE        GENERATED ALWAYS AS ((fetched_at AT TIME ZONE 'UTC')::DATE) STORED,
    PRIMARY KEY (set_id, fetched_date)
);

CREATE TABLE IF NOT EXISTS tcgtracking_pokemon_skus (
    set_id        INTEGER     NOT NULL,
    raw_data      JSONB       NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fetched_date  DATE        GENERATED ALWAYS AS ((fetched_at AT TIME ZONE 'UTC')::DATE) STORED,
    PRIMARY KEY (set_id, fetched_date)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- ONE PIECE
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tcgtracking_onepiece_products (
    set_id        INTEGER     NOT NULL,
    raw_data      JSONB       NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fetched_date  DATE        GENERATED ALWAYS AS ((fetched_at AT TIME ZONE 'UTC')::DATE) STORED,
    PRIMARY KEY (set_id, fetched_date)
);

CREATE TABLE IF NOT EXISTS tcgtracking_onepiece_pricing (
    set_id        INTEGER     NOT NULL,
    raw_data      JSONB       NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fetched_date  DATE        GENERATED ALWAYS AS ((fetched_at AT TIME ZONE 'UTC')::DATE) STORED,
    PRIMARY KEY (set_id, fetched_date)
);

CREATE TABLE IF NOT EXISTS tcgtracking_onepiece_skus (
    set_id        INTEGER     NOT NULL,
    raw_data      JSONB       NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fetched_date  DATE        GENERATED ALWAYS AS ((fetched_at AT TIME ZONE 'UTC')::DATE) STORED,
    PRIMARY KEY (set_id, fetched_date)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- POKEMON JAPAN
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tcgtracking_pokemon_japan_products (
    set_id        INTEGER     NOT NULL,
    raw_data      JSONB       NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fetched_date  DATE        GENERATED ALWAYS AS ((fetched_at AT TIME ZONE 'UTC')::DATE) STORED,
    PRIMARY KEY (set_id, fetched_date)
);

CREATE TABLE IF NOT EXISTS tcgtracking_pokemon_japan_pricing (
    set_id        INTEGER     NOT NULL,
    raw_data      JSONB       NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fetched_date  DATE        GENERATED ALWAYS AS ((fetched_at AT TIME ZONE 'UTC')::DATE) STORED,
    PRIMARY KEY (set_id, fetched_date)
);

CREATE TABLE IF NOT EXISTS tcgtracking_pokemon_japan_skus (
    set_id        INTEGER     NOT NULL,
    raw_data      JSONB       NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fetched_date  DATE        GENERATED ALWAYS AS ((fetched_at AT TIME ZONE 'UTC')::DATE) STORED,
    PRIMARY KEY (set_id, fetched_date)
);
