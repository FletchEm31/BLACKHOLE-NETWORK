-- crypto-market-data-schema.sql
-- BHN — CoinGecko top-N crypto prices.

CREATE TABLE IF NOT EXISTS crypto_market_data (
    id                 BIGSERIAL PRIMARY KEY,
    measured_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol             TEXT NOT NULL,        -- 'BTC', 'ETH', 'SOL', ...
    name               TEXT,                  -- 'Bitcoin'
    price_usd          NUMERIC(20,8),
    market_cap_usd     NUMERIC(20,2),
    volume_24h_usd     NUMERIC(20,2),
    change_24h_pct     NUMERIC(10,4),
    rank               INTEGER,               -- by market cap
    raw_payload        JSONB
);

CREATE INDEX IF NOT EXISTS cmd_symbol_time_idx ON crypto_market_data (symbol, measured_at DESC);
CREATE INDEX IF NOT EXISTS cmd_time_idx        ON crypto_market_data (measured_at DESC);

GRANT INSERT ON crypto_market_data TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE crypto_market_data_id_seq TO log_shipper;
GRANT SELECT ON crypto_market_data TO agent_reader, grafana_reader, ehuser;

COMMENT ON TABLE crypto_market_data IS
    'CoinGecko top-10 by market cap. 15-min cadence. Price/volume/24h-change/rank per snapshot.';
