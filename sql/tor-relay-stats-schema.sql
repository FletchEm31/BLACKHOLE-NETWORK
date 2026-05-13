-- tor-relay-stats-schema.sql
-- BHN — Tor relay accounting + uptime history.
-- Populated by scripts/bhn-tor-stats.sh (cron, every 5 min on each relay node).
-- Each collector pushes to LA PG over the WG tunnel. One row per relay per
-- measurement. HORIZON reads for "how is the relay performing?" + Grafana
-- renders the 80%-of-AccountingMax alert.
--
-- v1 limitations:
--   - circuits_built stays NULL — requires Tor ControlSocket enabled in
--     torrc (currently disabled — ControlSocket 0 per all three torrcs).
--     Deferred to a follow-up that enables ControlSocket + mounts the
--     socket out of the container.
--   - bytes_read / bytes_written come from AccountingBytes*InInterval in
--     /var/lib/tor/state — cumulative since the current accounting cycle
--     start (resets on `AccountingStart month 1 00:00`). Grafana renders
--     these as monthly usage against the AccountingMax cap.
--   - relay_bandwidth_rate / _burst are parsed from /etc/tor/torrc inside
--     the container — constants until torrc is changed + rebuilt.
--   - uptime_seconds = NOW - container's State.StartedAt.
--
-- Apply on the hub:
--   sudo -u postgres psql -d eventhorizon -f sql/tor-relay-stats-schema.sql

CREATE TABLE IF NOT EXISTS tor_relay_stats (
    id                      BIGSERIAL PRIMARY KEY,
    measured_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    node                    TEXT NOT NULL,         -- nickname: BHNFornaxEU1, BHNNebulaUS2, BHNHeliosUS3, ...
    bytes_read              BIGINT,                -- cumulative since accounting interval start
    bytes_written           BIGINT,                -- cumulative since accounting interval start
    circuits_built          INTEGER,               -- NULL until ControlSocket added
    relay_bandwidth_rate    BIGINT,                -- bytes/sec (from torrc RelayBandwidthRate)
    relay_bandwidth_burst   BIGINT,                -- bytes/sec (from torrc RelayBandwidthBurst)
    uptime_seconds          BIGINT,                -- since container started
    fingerprint             TEXT,                  -- Tor identity key fingerprint (RSA hex, 40 chars)
    raw_payload             JSONB                  -- everything else from state + container inspect
);

CREATE INDEX IF NOT EXISTS tor_relay_stats_time_idx
    ON tor_relay_stats (measured_at DESC);
CREATE INDEX IF NOT EXISTS tor_relay_stats_node_idx
    ON tor_relay_stats (node, measured_at DESC);

GRANT SELECT, INSERT ON tor_relay_stats TO n8n_user;
GRANT USAGE, SELECT ON SEQUENCE tor_relay_stats_id_seq TO n8n_user;

GRANT SELECT ON tor_relay_stats TO agent_reader;
GRANT SELECT ON tor_relay_stats TO grafana_reader;

COMMENT ON TABLE tor_relay_stats IS
    'Tor relay accounting + uptime history. Populated by bhn-tor-stats.sh cron on each relay node every 5 min, pushed over WG to LA PG.';
