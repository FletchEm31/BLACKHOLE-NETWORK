-- node-bandwidth-stats-schema.sql
-- BHN — per-node bandwidth totals from vnstat (hourly / daily / monthly per interface).
-- Populated by scripts/bhn-vnstat-collector.sh (cron, every 15 min on each node).
-- Each node ships its own bandwidth snapshots to LA PG over the WG tunnel.
--
-- Apply on the hub:
--   sudo -u postgres psql -d eventhorizon -f sql/node-bandwidth-stats-schema.sql

CREATE TABLE IF NOT EXISTS node_bandwidth_stats (
    id            BIGSERIAL PRIMARY KEY,
    measured_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    node_name     TEXT NOT NULL,
    interface     TEXT NOT NULL,         -- 'enp1s0', 'wg0', etc.
    period_type   TEXT NOT NULL CHECK (period_type IN ('hour','day','month','top')),
    period_start  TIMESTAMPTZ NOT NULL,  -- start of the bucket (UTC)
    rx_bytes      BIGINT NOT NULL,
    tx_bytes      BIGINT NOT NULL,
    raw_payload   JSONB,                 -- the source vnstat JSON object (for offline analysis)
    UNIQUE (node_name, interface, period_type, period_start)
);

CREATE INDEX IF NOT EXISTS node_bw_node_time_idx     ON node_bandwidth_stats (node_name, measured_at DESC);
CREATE INDEX IF NOT EXISTS node_bw_node_period_idx   ON node_bandwidth_stats (node_name, period_type, period_start DESC);
CREATE INDEX IF NOT EXISTS node_bw_interface_idx     ON node_bandwidth_stats (interface);

GRANT INSERT ON node_bandwidth_stats TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE node_bandwidth_stats_id_seq TO log_shipper;
GRANT SELECT ON node_bandwidth_stats TO agent_reader;
GRANT SELECT ON node_bandwidth_stats TO grafana_reader;
GRANT SELECT ON node_bandwidth_stats TO ehuser;

COMMENT ON TABLE node_bandwidth_stats IS
    'Per-node bandwidth snapshots from vnstat. Hour/day/month rows by interface. Populated by bhn-vnstat-collector.sh on each node.';
