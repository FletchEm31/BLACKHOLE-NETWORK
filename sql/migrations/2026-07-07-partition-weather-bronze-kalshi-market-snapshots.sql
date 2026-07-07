-- ─────────────────────────────────────────────────────────────────────────
-- Partition weather_bronze_kalshi_market_snapshots (range on retrieved_at)
--
-- Root-cause context: this table (and its since-removed duplicate
-- weather_contract_prices) caused the 2026-07-06 disk-exhaustion outage.
-- weather_contract_prices has been archived+dropped; this migration adds
-- a real retention policy for the actual, legitimate, still-growing table
-- so the same wall doesn't get hit again. 90-day hot/cold split: partitions
-- older than 90 days get relocated (ALTER TABLE ... SET TABLESPACE) to the
-- eh_cold_ts tablespace by the monthly bhn-weather-partition-maintenance
-- timer -- see scripts/bhn-weather-partition-maintenance.sh. This
-- migration only creates the structure; nothing is old enough to move to
-- cold storage yet (data starts 2026-06-10, cutoff is ~90 days back).
--
-- Cutover is a separate, manual, gated step -- see
-- scripts/bhn-weather-partition-cutover.sh. This file only creates the
-- new (empty) partitioned table under a _new suffix; backfill and the
-- rename-swap happen afterward.
-- ─────────────────────────────────────────────────────────────────────────

-- eh_cold_ts tablespace (LOCATION /mnt/eh-hdd-cold/pg-tablespaces/eh_cold_ts) is
-- created out-of-band by the operator/deploy step since CREATE TABLESPACE has
-- no IF NOT EXISTS form. Verify with: SELECT spcname FROM pg_tablespace;

CREATE TABLE IF NOT EXISTS weather_bronze_kalshi_market_snapshots_new (
    id                  BIGINT          NOT NULL
                        DEFAULT nextval('weather_bronze_kalshi_market_snapshots_id_seq'),
    market_ticker       TEXT            NOT NULL,
    event_ticker        TEXT,
    series_ticker       TEXT,
    city                TEXT,
    station_code        TEXT,
    contract_side       TEXT,
    bucket_type         TEXT,
    bucket_floor        NUMERIC,
    bucket_cap          NUMERIC,
    bucket_label        TEXT,
    target_date         DATE,
    yes_bid             NUMERIC,
    yes_ask             NUMERIC,
    no_bid              NUMERIC,
    no_ask              NUMERIC,
    yes_mid             NUMERIC,
    last_price          NUMERIC,
    volume              NUMERIC,
    open_interest       NUMERIC,
    market_status       TEXT,
    source_payload_json JSONB,
    retrieved_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, retrieved_at)
) PARTITION BY RANGE (retrieved_at);

-- Data starts 2026-06-10; partition from there through current+1 buffer month.
-- All on the default (hot) tablespace for now -- nothing is >90 days old yet.
CREATE TABLE IF NOT EXISTS weather_bronze_kalshi_market_snapshots_p2026_06
    PARTITION OF weather_bronze_kalshi_market_snapshots_new
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE TABLE IF NOT EXISTS weather_bronze_kalshi_market_snapshots_p2026_07
    PARTITION OF weather_bronze_kalshi_market_snapshots_new
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');

CREATE TABLE IF NOT EXISTS weather_bronze_kalshi_market_snapshots_p2026_08
    PARTITION OF weather_bronze_kalshi_market_snapshots_new
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

-- Safety net: any row landing outside the ranges above (clock skew, a
-- collector node with a wrong clock, etc.) goes here loudly instead of
-- the INSERT failing outright. bhn-weather-partition-maintenance.sh
-- alerts if this ever has rows.
CREATE TABLE IF NOT EXISTS weather_bronze_kalshi_market_snapshots_default
    PARTITION OF weather_bronze_kalshi_market_snapshots_new DEFAULT;

-- Indexes are deliberately NOT created here -- built after the backfill
-- completes (see bhn-weather-partition-backfill.sh), since bulk-loading
-- into an unindexed target is dramatically faster.

COMMENT ON TABLE weather_bronze_kalshi_market_snapshots_new IS
    'Partitioned replacement for weather_bronze_kalshi_market_snapshots (range on retrieved_at, monthly). '
    'Backfilled and rename-swapped into place via scripts/bhn-weather-partition-cutover.sh -- see '
    'infrastructure/docs/WeatherBHN/WEATHERBHN-TABLE-INVENTORY-2026-07-06-RESOLUTION.md.';
