-- prediction-market-data-schema.sql
-- BHN — Kalshi + Polymarket top-market prices.

CREATE TABLE IF NOT EXISTS prediction_market_data (
    id              BIGSERIAL PRIMARY KEY,
    measured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    venue           TEXT NOT NULL,           -- 'kalshi' | 'polymarket'
    market_id       TEXT NOT NULL,           -- venue-specific id
    market_title    TEXT,                    -- human-readable
    outcome         TEXT,                    -- 'YES'/'NO' or specific outcome label
    price           NUMERIC(10,6),           -- 0..1 implied probability
    yes_bid         NUMERIC(10,6),
    yes_ask         NUMERIC(10,6),
    volume_24h      NUMERIC(18,2),
    liquidity       NUMERIC(18,2),
    open_interest   NUMERIC(18,2),
    raw_payload     JSONB
);

CREATE INDEX IF NOT EXISTS pmd_venue_time_idx    ON prediction_market_data (venue, measured_at DESC);
CREATE INDEX IF NOT EXISTS pmd_market_time_idx   ON prediction_market_data (venue, market_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS pmd_title_idx         ON prediction_market_data (market_title);

GRANT INSERT ON prediction_market_data TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE prediction_market_data_id_seq TO log_shipper;
GRANT SELECT ON prediction_market_data TO agent_reader, grafana_reader, ehuser;

COMMENT ON TABLE prediction_market_data IS
    'Kalshi + Polymarket top-market price snapshots (10-min cadence). Used by strategy_prediction_market + HORIZON.';
