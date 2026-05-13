-- tor-relay-stats-control-port-extension.sql
-- BHN — extend tor_relay_stats with control-port-only fields.
-- Populated by bhn-tor-control-stats.py on each relay node.

ALTER TABLE tor_relay_stats ADD COLUMN IF NOT EXISTS circuit_count            INTEGER;
ALTER TABLE tor_relay_stats ADD COLUMN IF NOT EXISTS accounting_bytes_remaining BIGINT;
ALTER TABLE tor_relay_stats ADD COLUMN IF NOT EXISTS accounting_max_bytes     BIGINT;
ALTER TABLE tor_relay_stats ADD COLUMN IF NOT EXISTS traffic_read_bytes       BIGINT;   -- since-start counter
ALTER TABLE tor_relay_stats ADD COLUMN IF NOT EXISTS traffic_written_bytes    BIGINT;

COMMENT ON COLUMN tor_relay_stats.circuit_count             IS 'Active circuits at snapshot time (GETINFO circuit-status | count).';
COMMENT ON COLUMN tor_relay_stats.accounting_bytes_remaining IS 'GETINFO accounting/bytes-left — bytes until hibernation this period.';
COMMENT ON COLUMN tor_relay_stats.traffic_read_bytes        IS 'GETINFO traffic/read — total bytes read since Tor started.';
COMMENT ON COLUMN tor_relay_stats.traffic_written_bytes     IS 'GETINFO traffic/written — total bytes written since Tor started.';
