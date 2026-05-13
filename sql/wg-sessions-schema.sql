-- wg-sessions-schema.sql
-- BHN — per-peer WG session boundaries. A "session" = contiguous online window
-- bracketed by the previous-stale-now-active transition (session_start) and
-- the previous-active-now-stale transition (session_end).
-- Populated by scripts/bhn-wg-session-tracker.sh on LA every 5 min, reading
-- the last few wg_peer_stats rows per peer.

CREATE TABLE IF NOT EXISTS wg_sessions (
    id                BIGSERIAL PRIMARY KEY,
    peer_ip           INET NOT NULL,
    peer_label        TEXT NOT NULL,
    peer_pubkey       TEXT,
    session_start     TIMESTAMPTZ NOT NULL,
    session_end       TIMESTAMPTZ,                  -- NULL while session is open
    bytes_received_session BIGINT,                  -- delta over the session
    bytes_sent_session     BIGINT,
    duration_seconds  INTEGER,                       -- NULL while open; populated on close
    endpoints_seen    TEXT[]                         -- distinct endpoints during the session
);

CREATE INDEX IF NOT EXISTS wg_sessions_peer_idx   ON wg_sessions (peer_ip, session_start DESC);
CREATE INDEX IF NOT EXISTS wg_sessions_open_idx   ON wg_sessions (peer_ip) WHERE session_end IS NULL;

GRANT INSERT, UPDATE ON wg_sessions TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE wg_sessions_id_seq TO log_shipper;
GRANT SELECT ON wg_sessions TO agent_reader, grafana_reader, ehuser;

COMMENT ON TABLE wg_sessions IS
    'Per-peer WG session boundaries derived from wg_peer_stats handshake-staleness transitions. session_end = NULL = currently open.';
