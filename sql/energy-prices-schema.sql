-- energy-prices-schema.sql
-- BHN — EIA (US Energy Information Administration) commodity prices.

CREATE TABLE IF NOT EXISTS energy_prices (
    id            BIGSERIAL PRIMARY KEY,
    measured_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    series_id     TEXT NOT NULL,             -- e.g. 'PET.RWTC.D' (WTI), 'NG.RNGWHHD.D' (Henry Hub)
    series_title  TEXT,
    value         NUMERIC(20,6),
    units         TEXT,
    period_start  DATE NOT NULL,
    raw_payload   JSONB,
    UNIQUE (series_id, period_start)
);

CREATE INDEX IF NOT EXISTS ep_series_time_idx ON energy_prices (series_id, period_start DESC);

GRANT INSERT ON energy_prices TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE energy_prices_id_seq TO log_shipper;
GRANT SELECT ON energy_prices TO agent_reader, grafana_reader, ehuser;

COMMENT ON TABLE energy_prices IS 'EIA daily/weekly energy commodity prices.';
