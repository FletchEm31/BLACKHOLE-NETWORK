-- finnhub-data-schema.sql
-- BHN — Finnhub analyst + earnings data for trading watchlist symbols.

CREATE TABLE IF NOT EXISTS analyst_data (
    id              BIGSERIAL PRIMARY KEY,
    measured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol          TEXT NOT NULL,
    period          DATE,                       -- recommendation month (e.g. '2026-05-01')
    buy             INTEGER,
    strong_buy      INTEGER,
    hold            INTEGER,
    sell            INTEGER,
    strong_sell     INTEGER,
    target_high     NUMERIC(14,4),
    target_low      NUMERIC(14,4),
    target_mean     NUMERIC(14,4),
    target_median   NUMERIC(14,4),
    raw_payload     JSONB,
    UNIQUE (symbol, period)
);

CREATE INDEX IF NOT EXISTS ad_symbol_period_idx ON analyst_data (symbol, period DESC);

CREATE TABLE IF NOT EXISTS earnings_data (
    id              BIGSERIAL PRIMARY KEY,
    measured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol          TEXT NOT NULL,
    period          DATE NOT NULL,              -- reporting period end
    eps_actual      NUMERIC(14,4),
    eps_estimate    NUMERIC(14,4),
    revenue_actual  NUMERIC(20,2),
    revenue_estimate NUMERIC(20,2),
    surprise_pct    NUMERIC(10,4),
    raw_payload     JSONB,
    UNIQUE (symbol, period)
);

CREATE INDEX IF NOT EXISTS ed_symbol_period_idx ON earnings_data (symbol, period DESC);

GRANT INSERT ON analyst_data, earnings_data TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE analyst_data_id_seq, earnings_data_id_seq TO log_shipper;
GRANT SELECT ON analyst_data, earnings_data TO agent_reader, grafana_reader, ehuser;

COMMENT ON TABLE analyst_data IS 'Finnhub analyst recommendation trends per symbol per month. UNIQUE for ON CONFLICT DO NOTHING.';
COMMENT ON TABLE earnings_data IS 'Finnhub earnings calendar (actual vs estimate) per symbol per period.';
