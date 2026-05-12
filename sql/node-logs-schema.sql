-- node-logs-schema.sql
-- Per-node security events shipped from non-hub nodes to the LA hub PostgreSQL.
-- Populated by scripts/bhn-log-shipper.py (deployed to /usr/local/bin/eh-log-shipper.py
-- on each non-hub node, cron */5 — LA-side binary name kept until migration).
--
-- Apply once on the hub:
--   sudo -u postgres psql -d eventhorizon -f sql/node-logs-schema.sql

CREATE TABLE IF NOT EXISTS node_logs (
    id           BIGSERIAL PRIMARY KEY,
    node_name    TEXT NOT NULL,
    event_time   TIMESTAMPTZ NOT NULL,
    source       TEXT NOT NULL,           -- 'suricata' | 'crowdsec' | future: 'sshd' | 'ufw'
    severity     TEXT,                    -- 'critical' | 'high' | 'medium' | 'low' | 'info'
    signature    TEXT,                    -- Suricata rule name | CrowdSec scenario | etc.
    src_ip       INET,
    dst_ip       INET,
    proto        TEXT,
    meta         JSONB,                   -- everything else from the source event
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS node_logs_node_time_idx ON node_logs (node_name, event_time DESC);
CREATE INDEX IF NOT EXISTS node_logs_source_idx   ON node_logs (source);
CREATE INDEX IF NOT EXISTS node_logs_severity_idx ON node_logs (severity);
CREATE INDEX IF NOT EXISTS node_logs_src_ip_idx   ON node_logs (src_ip);
CREATE INDEX IF NOT EXISTS node_logs_meta_idx     ON node_logs USING GIN (meta);

-- INSERT-only role used by remote nodes' shippers
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'log_shipper') THEN
    CREATE ROLE log_shipper WITH LOGIN PASSWORD 'CHANGE_ME_AT_FIRST_USE';
  END IF;
END $$;
GRANT INSERT ON node_logs TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE node_logs_id_seq TO log_shipper;

-- Read access for Grafana
GRANT SELECT ON node_logs TO grafana_reader;

COMMENT ON TABLE node_logs IS 'Per-node security events shipped from exit/scan/proxy nodes via eh-log-shipper.';
COMMENT ON COLUMN node_logs.source IS 'suricata | crowdsec | sshd | ufw — extensible';
COMMENT ON COLUMN node_logs.severity IS 'critical | high | medium | low | info';
COMMENT ON COLUMN node_logs.meta IS 'Arbitrary JSONB blob preserving source-specific fields not promoted to columns';

-- pg_hba.conf needs (per non-hub subnet):
--   host eventhorizon log_shipper 10.X.0.0/24 scram-sha-256
--
-- ALTER ROLE log_shipper WITH PASSWORD '<random>'; after applying.
