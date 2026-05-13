-- fail2ban-events-schema.sql
-- BHN — fail2ban ban events per node. Snapshot-based: every cron run records
-- currently-banned IPs per jail. New bans surface as rows that didn't exist
-- in the previous snapshot (compare with LAG window in queries).

CREATE TABLE IF NOT EXISTS fail2ban_events (
    id            BIGSERIAL PRIMARY KEY,
    measured_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    node_name     TEXT NOT NULL,
    jail          TEXT NOT NULL,
    banned_ip     INET NOT NULL,
    banned_count_in_jail INTEGER,   -- total currently banned in that jail at snapshot time
    raw_payload   JSONB,
    UNIQUE (node_name, jail, banned_ip, measured_at)
);

CREATE INDEX IF NOT EXISTS f2b_node_time_idx ON fail2ban_events (node_name, measured_at DESC);
CREATE INDEX IF NOT EXISTS f2b_jail_idx      ON fail2ban_events (jail);
CREATE INDEX IF NOT EXISTS f2b_ip_idx        ON fail2ban_events (banned_ip);

GRANT INSERT ON fail2ban_events TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE fail2ban_events_id_seq TO log_shipper;
GRANT SELECT ON fail2ban_events TO agent_reader, grafana_reader, ehuser;

COMMENT ON TABLE fail2ban_events IS
    'fail2ban currently-banned IPs per (node, jail) per cron snapshot. Snapshot-based — diff against earlier rows to detect new bans.';
