-- alpaca-extras-schema.sql
-- BHN — Alpaca REST extras (corporate actions, earnings calendar, news, options).

CREATE TABLE IF NOT EXISTS corporate_actions (
    id              BIGSERIAL PRIMARY KEY,
    measured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol          TEXT NOT NULL,
    action_type     TEXT NOT NULL,            -- 'cash_dividend', 'stock_split', 'reverse_split', 'merger', 'spinoff', 'name_change'
    ex_date         DATE,
    payable_date    DATE,
    record_date     DATE,
    declared_date   DATE,
    cash_amount     NUMERIC(14,6),
    split_ratio     TEXT,                      -- '2:1', '1:4', etc.
    raw_payload     JSONB,
    UNIQUE (symbol, action_type, ex_date)
);

CREATE INDEX IF NOT EXISTS ca_symbol_date_idx ON corporate_actions (symbol, ex_date DESC);
CREATE INDEX IF NOT EXISTS ca_action_idx      ON corporate_actions (action_type);

-- Alpaca News (extends market_signals with source='alpaca_news', but provides
-- a denormalized table for dashboard-friendly access).
CREATE TABLE IF NOT EXISTS alpaca_news (
    id              BIGSERIAL PRIMARY KEY,
    measured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    article_id      BIGINT NOT NULL,
    headline        TEXT,
    summary         TEXT,
    author          TEXT,
    source          TEXT,                      -- e.g. 'benzinga'
    created_at      TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ,
    url             TEXT,
    symbols         TEXT[],                    -- tickers tagged on the article
    raw_payload     JSONB,
    UNIQUE (article_id)
);

CREATE INDEX IF NOT EXISTS an_created_idx     ON alpaca_news (created_at DESC);
CREATE INDEX IF NOT EXISTS an_symbols_gin_idx ON alpaca_news USING GIN (symbols);

-- Options chain snapshot (per-symbol-per-expiry chain at observation time)
CREATE TABLE IF NOT EXISTS options_chain_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    measured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    underlying      TEXT NOT NULL,
    expiry          DATE NOT NULL,
    strike          NUMERIC(14,4) NOT NULL,
    right_type      TEXT NOT NULL,             -- 'C' or 'P'
    bid             NUMERIC(14,6),
    ask             NUMERIC(14,6),
    last            NUMERIC(14,6),
    iv              NUMERIC(10,6),
    delta           NUMERIC(10,6),
    gamma           NUMERIC(10,6),
    theta           NUMERIC(10,6),
    vega            NUMERIC(10,6),
    volume          BIGINT,
    open_interest   BIGINT,
    raw_payload     JSONB
);

CREATE INDEX IF NOT EXISTS oc_under_expiry_idx ON options_chain_snapshots (underlying, expiry, strike, right_type, measured_at DESC);

GRANT INSERT ON corporate_actions, alpaca_news, options_chain_snapshots TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE corporate_actions_id_seq, alpaca_news_id_seq, options_chain_snapshots_id_seq TO log_shipper;
GRANT SELECT ON corporate_actions, alpaca_news, options_chain_snapshots TO agent_reader, grafana_reader, ehuser;

COMMENT ON TABLE corporate_actions        IS 'Alpaca corporate-actions feed (dividends, splits, mergers). Keyed by (symbol, action, ex_date).';
COMMENT ON TABLE alpaca_news              IS 'Alpaca News API articles tagged with symbols. Free with paper key.';
COMMENT ON TABLE options_chain_snapshots  IS 'Alpaca options chain snapshots — captures full chain per (underlying, expiry) at observation time. Volume-heavy: query latest-only with DISTINCT ON (underlying, expiry, strike, right).';
