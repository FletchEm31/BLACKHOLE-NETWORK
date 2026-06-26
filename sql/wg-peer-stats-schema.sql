-- wg-peer-stats-schema.sql
-- BHN — WireGuard hub peer bandwidth + handshake history.
-- Populated by scripts/bhn-wg-stats.sh (cron, every 5 min on LA hub).
-- One row per peer per measurement. HORIZON reads via query_db for
-- traffic-anomaly detection. Grafana renders per-peer bandwidth panels +
-- the >1GB/hr spike alert on server peers.
--
-- Apply on the hub:
--   sudo -u postgres psql -d eventhorizon -f sql/wg-peer-stats-schema.sql

CREATE TABLE IF NOT EXISTS wg_peer_stats (
    id               BIGSERIAL PRIMARY KEY,
    measured_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    peer_ip          TEXT NOT NULL,             -- tunnel IP, e.g. '10.8.0.6'
    peer_label       TEXT NOT NULL,             -- 'Phone', 'PC', 'NJ', 'Hillsboro', 'Frankfurt'
    peer_pubkey      TEXT,                       -- WG public key (cross-ref to nodes.wg_pubkey)
    bytes_received   BIGINT NOT NULL,            -- cumulative since wg interface up (hub's view: rx from peer)
    bytes_sent       BIGINT NOT NULL,            -- cumulative since wg interface up (hub's view: tx to peer)
    latest_handshake TIMESTAMPTZ,                -- NULL if no handshake yet
    endpoint         TEXT                        -- peer's public endpoint, e.g. '<BHN_HIL_PUBLIC_IP>:51821'
);

CREATE INDEX IF NOT EXISTS wg_peer_stats_time_idx
    ON wg_peer_stats (measured_at DESC);
CREATE INDEX IF NOT EXISTS wg_peer_stats_peer_idx
    ON wg_peer_stats (peer_ip, measured_at DESC);

-- INSERT for the collector
GRANT SELECT, INSERT ON wg_peer_stats TO n8n_user;
GRANT USAGE, SELECT ON SEQUENCE wg_peer_stats_id_seq TO n8n_user;

-- HORIZON reads via agent_reader
GRANT SELECT ON wg_peer_stats TO agent_reader;

-- Grafana panels + alerts
GRANT SELECT ON wg_peer_stats TO grafana_reader;

COMMENT ON TABLE wg_peer_stats IS
    'WireGuard hub peer bandwidth + handshake history. Populated by bhn-wg-stats.sh cron on LA every 5 min.';
