-- container-stats-schema.sql
-- BHN — per-container Docker resource usage snapshots.
-- LA hub primarily, but the same collector works on any node running Docker
-- (Frankfurt + Hillsboro both run bhn-tor-relay; Frankfurt also has SearXNG + LibreSpeed).

CREATE TABLE IF NOT EXISTS container_stats (
    id              BIGSERIAL PRIMARY KEY,
    measured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    node_name       TEXT NOT NULL,
    container_name  TEXT NOT NULL,
    container_id    TEXT,
    image           TEXT,
    cpu_pct         NUMERIC(7,2),       -- as docker stats reports (can exceed 100 on multi-core)
    mem_used_mb     NUMERIC(10,2),
    mem_limit_mb    NUMERIC(10,2),
    mem_pct         NUMERIC(5,2),
    net_rx_bytes    BIGINT,             -- cumulative since container start
    net_tx_bytes    BIGINT,
    block_read_bytes  BIGINT,
    block_write_bytes BIGINT,
    pids            INTEGER
);

CREATE INDEX IF NOT EXISTS cs_node_time_idx ON container_stats (node_name, measured_at DESC);
CREATE INDEX IF NOT EXISTS cs_container_idx ON container_stats (node_name, container_name, measured_at DESC);

GRANT INSERT ON container_stats TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE container_stats_id_seq TO log_shipper;
GRANT SELECT ON container_stats TO agent_reader, grafana_reader, ehuser;

COMMENT ON TABLE container_stats IS
    'Per-Docker-container resource snapshots, 5-min cadence. Populated by bhn-docker-stats-collector.sh on any node running Docker.';
