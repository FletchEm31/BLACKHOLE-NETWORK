-- macro-indicators-schema.sql
-- BHN — FRED (Federal Reserve Economic Data) series observations.

CREATE TABLE IF NOT EXISTS macro_indicators (
    id            BIGSERIAL PRIMARY KEY,
    measured_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    series_id     TEXT NOT NULL,            -- e.g. 'DGS10' (10Y treasury), 'CPIAUCSL' (CPI), 'UNRATE'
    series_title  TEXT,
    value         NUMERIC(20,6),
    units         TEXT,                      -- 'Percent', 'Index 1982=100', 'Thousands of Persons'
    period_start  TIMESTAMPTZ NOT NULL,      -- observation date
    frequency     TEXT,                      -- 'Daily', 'Weekly', 'Monthly', 'Quarterly', 'Annual'
    raw_payload   JSONB,
    UNIQUE (series_id, period_start)
);

CREATE INDEX IF NOT EXISTS mi_series_time_idx ON macro_indicators (series_id, period_start DESC);
CREATE INDEX IF NOT EXISTS mi_period_idx      ON macro_indicators (period_start DESC);

GRANT INSERT ON macro_indicators TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE macro_indicators_id_seq TO log_shipper;
GRANT SELECT ON macro_indicators TO agent_reader, grafana_reader, ehuser;

COMMENT ON TABLE macro_indicators IS 'FRED series observations. Polled daily by bhn-fred-poller.py. UNIQUE(series_id, period_start) for ON CONFLICT DO NOTHING.';
