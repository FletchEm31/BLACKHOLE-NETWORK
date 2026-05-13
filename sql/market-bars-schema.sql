-- market-bars-schema.sql
-- BHN — multi-timeframe market bars partitioned by timeframe.
-- Populated by:
--   1. scripts/trading/market_bars_backfill.py (30-day initial backfill)
--   2. scripts/trading/market_stream.py (live aggregation from Alpaca WebSocket — item 18)
--
-- Storage tiers per operator addendum (eh-purge-monitoring-retention.conf):
--   1Min:  archive cold after 30 days
--   5Min, 15Min: archive cold after 90 days
--   1Hour, 1Day: keep forever (small + analytically useful long-tail)

CREATE TABLE IF NOT EXISTS market_bars (
    id              BIGSERIAL,
    measured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol          TEXT NOT NULL,
    timeframe       TEXT NOT NULL,        -- '1Min' | '5Min' | '15Min' | '1Hour' | '1Day'  (Alpaca's nomenclature)
    bar_start       TIMESTAMPTZ NOT NULL,
    open_price      NUMERIC(14,4),
    high_price      NUMERIC(14,4),
    low_price       NUMERIC(14,4),
    close_price     NUMERIC(14,4),
    volume          BIGINT,
    vwap            NUMERIC(14,4),
    trade_count     INTEGER,
    raw_payload     JSONB,
    PRIMARY KEY (id, timeframe),
    UNIQUE (symbol, timeframe, bar_start)
) PARTITION BY LIST (timeframe);

CREATE TABLE IF NOT EXISTS market_bars_1min   PARTITION OF market_bars FOR VALUES IN ('1Min');
CREATE TABLE IF NOT EXISTS market_bars_5min   PARTITION OF market_bars FOR VALUES IN ('5Min');
CREATE TABLE IF NOT EXISTS market_bars_15min  PARTITION OF market_bars FOR VALUES IN ('15Min');
CREATE TABLE IF NOT EXISTS market_bars_1hour  PARTITION OF market_bars FOR VALUES IN ('1Hour');
CREATE TABLE IF NOT EXISTS market_bars_1day   PARTITION OF market_bars FOR VALUES IN ('1Day');

CREATE INDEX IF NOT EXISTS mb_1min_symbol_idx  ON market_bars_1min  (symbol, bar_start DESC);
CREATE INDEX IF NOT EXISTS mb_5min_symbol_idx  ON market_bars_5min  (symbol, bar_start DESC);
CREATE INDEX IF NOT EXISTS mb_15min_symbol_idx ON market_bars_15min (symbol, bar_start DESC);
CREATE INDEX IF NOT EXISTS mb_1hour_symbol_idx ON market_bars_1hour (symbol, bar_start DESC);
CREATE INDEX IF NOT EXISTS mb_1day_symbol_idx  ON market_bars_1day  (symbol, bar_start DESC);

-- Ticks table (operator addendum: NVMe HOT, 48hr purge, scoped to open positions only).
CREATE TABLE IF NOT EXISTS market_ticks (
    id              BIGSERIAL PRIMARY KEY,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol          TEXT NOT NULL,
    tick_type       TEXT NOT NULL,        -- 'trade' | 'quote'
    price           NUMERIC(14,4),
    size            BIGINT,
    bid_price       NUMERIC(14,4),
    bid_size        BIGINT,
    ask_price       NUMERIC(14,4),
    ask_size        BIGINT,
    exchange        TEXT,
    conditions      TEXT[],
    timestamp_ns    BIGINT,               -- Alpaca's nanosecond timestamp
    raw_payload     JSONB
);

CREATE INDEX IF NOT EXISTS mt_symbol_time_idx ON market_ticks (symbol, received_at DESC);
CREATE INDEX IF NOT EXISTS mt_type_idx        ON market_ticks (tick_type, received_at DESC);

-- Order events (operator addendum: small volume, keep forever).
CREATE TABLE IF NOT EXISTS order_events (
    id              BIGSERIAL PRIMARY KEY,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    order_id        TEXT NOT NULL,
    event_type      TEXT NOT NULL,        -- 'new' | 'fill' | 'partial_fill' | 'canceled' | 'rejected' | 'expired'
    symbol          TEXT,
    side            TEXT,
    qty             NUMERIC(14,4),
    filled_qty      NUMERIC(14,4),
    filled_avg_price NUMERIC(14,4),
    status          TEXT,
    strategy_id     TEXT,
    raw_payload     JSONB
);

CREATE INDEX IF NOT EXISTS oe_order_idx     ON order_events (order_id, received_at);
CREATE INDEX IF NOT EXISTS oe_symbol_idx    ON order_events (symbol, received_at DESC);
CREATE INDEX IF NOT EXISTS oe_strategy_idx  ON order_events (strategy_id, received_at DESC);

GRANT INSERT ON market_bars, market_bars_1min, market_bars_5min, market_bars_15min, market_bars_1hour, market_bars_1day,
                market_ticks, order_events TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE market_bars_id_seq, market_ticks_id_seq, order_events_id_seq TO log_shipper;
GRANT SELECT ON market_bars, market_bars_1min, market_bars_5min, market_bars_15min, market_bars_1hour, market_bars_1day,
                market_ticks, order_events TO agent_reader, grafana_reader, ehuser;

COMMENT ON TABLE market_bars   IS 'Multi-timeframe market bars partitioned by timeframe (LIST). Five partitions: 1Min/5Min/15Min/1Hour/1Day. Strategies read from this; eh-purge tiers older bars to HDD COLD per the retention config.';
COMMENT ON TABLE market_ticks  IS 'Tick-level data ONLY for symbols with open positions. NVMe HOT, 48hr purge.';
COMMENT ON TABLE order_events  IS 'Every order lifecycle event from the Alpaca stream. Small volume, keep forever (audit + reconciliation).';
