-- connection-snapshots-schema.sql
-- BHN — kernel conntrack snapshots per node (5-min cadence).
--
-- Volume note: conntrack tables can have thousands of entries on a busy
-- node. Each cron run can insert thousands of rows. Operator-stated
-- retention: rows older than 14 days deleted by eh-purge (see
-- scripts/eh-purge-monitoring-retention.conf — to be added with item 18+
-- batch).

CREATE TABLE IF NOT EXISTS connection_snapshots (
    id            BIGSERIAL PRIMARY KEY,
    measured_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    node_name     TEXT NOT NULL,
    proto         TEXT NOT NULL,           -- 'tcp' | 'udp' | 'icmp'
    state         TEXT,                    -- 'ESTABLISHED' | 'TIME_WAIT' | etc.
    src_ip        INET,
    src_port      INTEGER,
    dst_ip        INET,
    dst_port      INTEGER,
    bytes_orig    BIGINT,                  -- bytes in original direction
    bytes_reply   BIGINT,                  -- bytes in reply direction
    packets_orig  BIGINT,
    packets_reply BIGINT
);

CREATE INDEX IF NOT EXISTS conn_node_time_idx ON connection_snapshots (node_name, measured_at DESC);
CREATE INDEX IF NOT EXISTS conn_dst_idx       ON connection_snapshots (dst_ip, dst_port);
CREATE INDEX IF NOT EXISTS conn_src_idx       ON connection_snapshots (src_ip);
CREATE INDEX IF NOT EXISTS conn_proto_state_idx ON connection_snapshots (proto, state);

GRANT INSERT ON connection_snapshots TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE connection_snapshots_id_seq TO log_shipper;
GRANT SELECT ON connection_snapshots TO agent_reader, grafana_reader, ehuser;

COMMENT ON TABLE connection_snapshots IS
    'Per-node conntrack snapshots. High volume — 14-day retention via eh-purge. Use for incident investigation, not steady-state analytics.';
