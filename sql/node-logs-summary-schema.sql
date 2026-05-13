-- node-logs-summary-schema.sql
-- BHN — periodic aggregation of node_logs (Suricata alerts + CrowdSec alerts)
-- for fast dashboard rendering instead of scanning the full node_logs table.
-- Populated by scripts/bhn-node-logs-summarizer.sh on LA every 15 min.

CREATE TABLE IF NOT EXISTS node_logs_summary (
    id                BIGSERIAL PRIMARY KEY,
    measured_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    window_start      TIMESTAMPTZ NOT NULL,        -- start of the aggregated window
    window_end        TIMESTAMPTZ NOT NULL,
    node_name         TEXT NOT NULL,
    source            TEXT NOT NULL,                -- 'suricata' | 'crowdsec'
    alert_count       INTEGER NOT NULL,
    severity_critical INTEGER NOT NULL DEFAULT 0,
    severity_high     INTEGER NOT NULL DEFAULT 0,
    severity_medium   INTEGER NOT NULL DEFAULT 0,
    severity_low      INTEGER NOT NULL DEFAULT 0,
    top_signatures    JSONB,                        -- [{signature, count}] top-10
    unique_src_ips    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (node_name, source, window_start)
);

CREATE INDEX IF NOT EXISTS nls_node_time_idx ON node_logs_summary (node_name, window_start DESC);
CREATE INDEX IF NOT EXISTS nls_source_idx    ON node_logs_summary (source);

GRANT INSERT ON node_logs_summary TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE node_logs_summary_id_seq TO log_shipper;
GRANT SELECT ON node_logs_summary TO agent_reader, grafana_reader, ehuser;

COMMENT ON TABLE node_logs_summary IS
    'Per-(node,source) aggregation of node_logs over rolling 15-min windows. Populated by bhn-node-logs-summarizer.sh.';
