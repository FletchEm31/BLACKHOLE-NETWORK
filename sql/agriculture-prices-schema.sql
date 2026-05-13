-- agriculture-prices-schema.sql
-- BHN — USDA NASS commodity prices.

CREATE TABLE IF NOT EXISTS agriculture_prices (
    id              BIGSERIAL PRIMARY KEY,
    measured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    commodity       TEXT NOT NULL,           -- e.g. 'CORN', 'SOYBEANS', 'WHEAT', 'CATTLE'
    statisticcat    TEXT,                     -- 'PRICE RECEIVED', 'PRODUCTION', etc.
    short_desc      TEXT,                     -- 'CORN - PRICE RECEIVED, MEASURED IN $ / BU'
    value           NUMERIC(20,6),
    units           TEXT,                     -- '$ / BU', 'BU', 'HEAD'
    period_start    DATE NOT NULL,            -- year_month or year
    period_label    TEXT,                     -- 'MARKETING YEAR', 'QUARTER', 'MONTH'
    state_alpha     TEXT,                     -- 'US' (national) or state code
    raw_payload     JSONB,
    UNIQUE (commodity, short_desc, period_start, state_alpha)
);

CREATE INDEX IF NOT EXISTS ap_commodity_idx ON agriculture_prices (commodity, period_start DESC);

GRANT INSERT ON agriculture_prices TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE agriculture_prices_id_seq TO log_shipper;
GRANT SELECT ON agriculture_prices TO agent_reader, grafana_reader, ehuser;

COMMENT ON TABLE agriculture_prices IS 'USDA NASS commodity prices. Polled weekly via bhn-usda-poller.py.';
