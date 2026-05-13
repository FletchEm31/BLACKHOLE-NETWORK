-- node-resource-stats-schema.sql
-- BHN — CPU/RAM/swap/load/disk per node, 5-min snapshots.
-- Populated by scripts/bhn-resource-collector.sh on each node.

CREATE TABLE IF NOT EXISTS node_resource_stats (
    id              BIGSERIAL PRIMARY KEY,
    measured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    node_name       TEXT NOT NULL,
    cpu_pct         NUMERIC(5,2),       -- whole-host CPU% busy (0..100)
    cpu_count       INTEGER,
    load_1m         NUMERIC(8,2),
    load_5m         NUMERIC(8,2),
    load_15m        NUMERIC(8,2),
    mem_total_mb    BIGINT,
    mem_used_mb     BIGINT,             -- = total - available (kernel's view)
    mem_available_mb BIGINT,
    swap_total_mb   BIGINT,
    swap_used_mb    BIGINT,
    uptime_s        BIGINT,
    proc_count      INTEGER
);

CREATE TABLE IF NOT EXISTS node_disk_stats (
    id            BIGSERIAL PRIMARY KEY,
    measured_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    node_name     TEXT NOT NULL,
    filesystem    TEXT NOT NULL,        -- /dev/sda1, tmpfs, etc.
    mount_point   TEXT NOT NULL,
    total_kb      BIGINT,
    used_kb       BIGINT,
    used_pct      NUMERIC(5,2)
);

CREATE INDEX IF NOT EXISTS nrs_node_time_idx  ON node_resource_stats (node_name, measured_at DESC);
CREATE INDEX IF NOT EXISTS nds_node_time_idx  ON node_disk_stats     (node_name, measured_at DESC);
CREATE INDEX IF NOT EXISTS nds_mount_idx      ON node_disk_stats     (node_name, mount_point, measured_at DESC);

GRANT INSERT ON node_resource_stats, node_disk_stats TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE node_resource_stats_id_seq, node_disk_stats_id_seq TO log_shipper;
GRANT SELECT ON node_resource_stats, node_disk_stats TO agent_reader, grafana_reader, ehuser;

COMMENT ON TABLE node_resource_stats IS 'Per-node CPU/RAM/swap/load snapshots, 5-min cadence.';
COMMENT ON TABLE node_disk_stats     IS 'Per-node per-mount disk usage snapshots, 5-min cadence.';
