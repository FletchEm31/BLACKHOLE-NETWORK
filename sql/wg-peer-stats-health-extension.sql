-- wg-peer-stats-health-extension.sql
-- BHN — extend wg_peer_stats with computed handshake-age + staleness flag.
-- Populated by bhn-wg-stats.sh (post-update); read by Grafana for the
-- stale-peer alert.

ALTER TABLE wg_peer_stats ADD COLUMN IF NOT EXISTS handshake_age_seconds INTEGER;
ALTER TABLE wg_peer_stats ADD COLUMN IF NOT EXISTS is_stale BOOLEAN;

CREATE INDEX IF NOT EXISTS wg_peer_stale_idx ON wg_peer_stats (is_stale, measured_at DESC) WHERE is_stale = TRUE;

COMMENT ON COLUMN wg_peer_stats.handshake_age_seconds IS
    'Seconds since latest_handshake at measurement time; NULL if peer never handshook.';
COMMENT ON COLUMN wg_peer_stats.is_stale IS
    'TRUE when handshake_age_seconds > 180 (3 min). Drives the bhn-wg-peer-stale Grafana alert.';
