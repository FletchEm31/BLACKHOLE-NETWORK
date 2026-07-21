-- Migration: Kalshi WebSocket bronze tables (real-time market data relay)
-- 2026-07-21
--
-- Purely additive capture layer for Kalshi's WebSocket API (ticker,
-- orderbook_delta/snapshot, trade, market_lifecycle_v2 channels) -- does
-- NOT touch weather_bronze_kalshi_market_snapshots or
-- weather_silver_market_conformed, which keep working exactly as they do
-- today via the existing REST poller (poll_weather_prices_aggressive).
-- Wiring this into CP4/downstream trading logic is a deliberately separate
-- future step -- today's scope is capture only (operator: "no decision
-- logic, no keys on NJ yet... clean, well-scoped first step").
--
-- Raw deltas are stored as-is, not reconstructed into live book state --
-- book reconstruction (cumulative sum per price level over time) can be
-- done later in a silver-layer view/query if ever needed, keeping this
-- collector simple and stateless per the project's bronze/silver
-- separation convention.
--
-- Run on LA:
--   sudo -u postgres psql eventhorizon -f sql/migrations/2026-07-21-kalshi-ws-bronze-tables.sql

CREATE TABLE IF NOT EXISTS weather_bronze_kalshi_ws_ticker (
    id                  BIGSERIAL       PRIMARY KEY,
    market_ticker       TEXT            NOT NULL,
    price_dollars       NUMERIC,
    yes_bid_dollars     NUMERIC,
    yes_ask_dollars     NUMERIC,
    volume_fp           NUMERIC,
    open_interest_fp    NUMERIC,
    dollar_volume       BIGINT,
    dollar_open_interest BIGINT,
    yes_bid_size_fp     NUMERIC,
    yes_ask_size_fp     NUMERIC,
    last_trade_size_fp  NUMERIC,
    event_ts_ms         BIGINT          NOT NULL,   -- Kalshi's own ts_ms
    received_at         TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS bkwt_ticker_time_idx
    ON weather_bronze_kalshi_ws_ticker (market_ticker, event_ts_ms DESC);

CREATE TABLE IF NOT EXISTS weather_bronze_kalshi_ws_orderbook (
    id                  BIGSERIAL       PRIMARY KEY,
    market_ticker       TEXT            NOT NULL,
    is_snapshot         BOOLEAN         NOT NULL,   -- true = orderbook_snapshot level, false = orderbook_delta
    side                TEXT            NOT NULL CHECK (side IN ('yes', 'no')),
    price_dollars       NUMERIC         NOT NULL,
    count_or_delta_fp   NUMERIC         NOT NULL,   -- snapshot: absolute contract count at this level; delta: signed change
    event_ts_ms         BIGINT,                     -- NULL for snapshot rows (Kalshi doesn't timestamp snapshot levels individually)
    received_at         TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS bkwo_ticker_time_idx
    ON weather_bronze_kalshi_ws_orderbook (market_ticker, received_at DESC);

CREATE TABLE IF NOT EXISTS weather_bronze_kalshi_ws_trade (
    id                  BIGSERIAL       PRIMARY KEY,
    trade_id            TEXT            NOT NULL UNIQUE,
    market_ticker       TEXT            NOT NULL,
    yes_price_dollars   NUMERIC,
    no_price_dollars    NUMERIC,
    count_fp            NUMERIC,
    taker_side          TEXT CHECK (taker_side IN ('yes', 'no')),
    event_ts_ms         BIGINT          NOT NULL,
    received_at         TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS bkwtr_ticker_time_idx
    ON weather_bronze_kalshi_ws_trade (market_ticker, event_ts_ms DESC);

CREATE TABLE IF NOT EXISTS weather_bronze_kalshi_ws_lifecycle (
    id                  BIGSERIAL       PRIMARY KEY,
    market_ticker       TEXT            NOT NULL,
    event_type          TEXT            NOT NULL,
    open_ts             BIGINT,
    close_ts            BIGINT,
    raw_msg_json        JSONB,
    received_at         TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS bkwl_ticker_time_idx
    ON weather_bronze_kalshi_ws_lifecycle (market_ticker, received_at DESC);

-- NJ's relay connects as bhn_trader (already configured/in-use there for
-- the existing trading scripts) rather than a new dedicated role, to keep
-- today's scope tight -- revisit as a dedicated least-privilege role later
-- if this becomes a permanent production pipeline rather than a first cut.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_trader') THEN
        GRANT SELECT, INSERT ON weather_bronze_kalshi_ws_ticker TO bhn_trader;
        GRANT SELECT, INSERT ON weather_bronze_kalshi_ws_orderbook TO bhn_trader;
        GRANT SELECT, INSERT ON weather_bronze_kalshi_ws_trade TO bhn_trader;
        GRANT SELECT, INSERT ON weather_bronze_kalshi_ws_lifecycle TO bhn_trader;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_kalshi_ws_ticker_id_seq TO bhn_trader;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_kalshi_ws_orderbook_id_seq TO bhn_trader;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_kalshi_ws_trade_id_seq TO bhn_trader;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_kalshi_ws_lifecycle_id_seq TO bhn_trader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_reader') THEN
        GRANT SELECT ON weather_bronze_kalshi_ws_ticker TO agent_reader;
        GRANT SELECT ON weather_bronze_kalshi_ws_orderbook TO agent_reader;
        GRANT SELECT ON weather_bronze_kalshi_ws_trade TO agent_reader;
        GRANT SELECT ON weather_bronze_kalshi_ws_lifecycle TO agent_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader') THEN
        GRANT SELECT ON weather_bronze_kalshi_ws_ticker TO grafana_reader;
        GRANT SELECT ON weather_bronze_kalshi_ws_orderbook TO grafana_reader;
        GRANT SELECT ON weather_bronze_kalshi_ws_trade TO grafana_reader;
        GRANT SELECT ON weather_bronze_kalshi_ws_lifecycle TO grafana_reader;
    END IF;
END $$;
