-- tor-relay-stats-consensus-extension.sql
-- BHN — extend tor_relay_stats with Tor Metrics consensus fields.
-- Polled daily from onionoo.torproject.org by bhn-tor-metrics-poller.py
-- on LA. New rows get the consensus columns populated; per-node rows
-- from bhn-tor-stats.sh leave them NULL.
--
-- Apply on the hub:
--   sudo -u postgres psql -d eventhorizon -f sql/tor-relay-stats-consensus-extension.sql

ALTER TABLE tor_relay_stats ADD COLUMN IF NOT EXISTS consensus_weight    INTEGER;
ALTER TABLE tor_relay_stats ADD COLUMN IF NOT EXISTS flags               TEXT[];        -- ['Guard','Fast','Stable','Running','Valid', ...]
ALTER TABLE tor_relay_stats ADD COLUMN IF NOT EXISTS observed_bandwidth  BIGINT;        -- bytes/sec
ALTER TABLE tor_relay_stats ADD COLUMN IF NOT EXISTS advertised_bandwidth BIGINT;
ALTER TABLE tor_relay_stats ADD COLUMN IF NOT EXISTS country             TEXT;
ALTER TABLE tor_relay_stats ADD COLUMN IF NOT EXISTS as_name             TEXT;
ALTER TABLE tor_relay_stats ADD COLUMN IF NOT EXISTS first_seen          TIMESTAMPTZ;
ALTER TABLE tor_relay_stats ADD COLUMN IF NOT EXISTS last_restarted      TIMESTAMPTZ;
ALTER TABLE tor_relay_stats ADD COLUMN IF NOT EXISTS source              TEXT;          -- 'node' | 'onionoo'

COMMENT ON COLUMN tor_relay_stats.consensus_weight   IS 'Tor consensus weight (bandwidth-weighted directory authority vote).';
COMMENT ON COLUMN tor_relay_stats.flags              IS 'Consensus flags array: Guard, Fast, Stable, Running, Valid, HSDir, etc.';
COMMENT ON COLUMN tor_relay_stats.observed_bandwidth IS 'Bytes/sec the relay reports it can sustain (from onionoo).';
COMMENT ON COLUMN tor_relay_stats.source             IS 'node = bhn-tor-stats.sh on relay host; onionoo = bhn-tor-metrics-poller.py on LA.';
