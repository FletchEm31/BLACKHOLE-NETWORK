-- speedtest-schema.sql
-- BHN (Blackhole Network) — speedtest result log.
-- Populated by per-node speedtest cron (TBD: bhn-speedtest-probe.sh) which runs
-- librespeed-cli against the local + peer LibreSpeed endpoints and inserts a row
-- per measurement. HORIZON reads via query_db for latency-trend monitoring.
--
-- Apply on the hub:
--   sudo -u postgres psql -d eventhorizon -f sql/speedtest-schema.sql

CREATE TABLE IF NOT EXISTS speedtest_results (
    id              BIGSERIAL PRIMARY KEY,
    measured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_node     TEXT NOT NULL,             -- which node ran the test (e.g. 'la', 'frankfurt', 'nj')
    target_node     TEXT NOT NULL,             -- which node's endpoint was tested
    ping_ms         NUMERIC(7,2),
    jitter_ms       NUMERIC(7,2),
    download_mbps   NUMERIC(8,2),
    upload_mbps     NUMERIC(8,2),
    packet_loss_pct NUMERIC(5,2),
    test_mode       TEXT,                       -- 'ping' (light, hourly) | 'bandwidth' (heavy, daily)
    raw_payload     JSONB
);

CREATE INDEX IF NOT EXISTS speedtest_results_time_idx
    ON speedtest_results (measured_at DESC);
CREATE INDEX IF NOT EXISTS speedtest_results_pair_idx
    ON speedtest_results (source_node, target_node, measured_at DESC);

-- INSERT for the probe (n8n_user is the rw role used by other ingest workflows)
GRANT SELECT, INSERT ON speedtest_results TO n8n_user;
GRANT USAGE, SELECT ON SEQUENCE speedtest_results_id_seq TO n8n_user;

-- HORIZON reads via its agent_reader role
GRANT SELECT ON speedtest_results TO agent_reader;

-- Grafana future panel
GRANT SELECT ON speedtest_results TO grafana_reader;

COMMENT ON TABLE speedtest_results IS
    'Per-pair latency + bandwidth measurements from LibreSpeed probes. Populated by bhn-speedtest-probe cron on each node.';
