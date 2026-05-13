-- node-patch-status-schema.sql
-- BHN — pending apt updates per node (security vs total).

CREATE TABLE IF NOT EXISTS node_patch_status (
    id                    BIGSERIAL PRIMARY KEY,
    measured_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    node_name             TEXT NOT NULL,
    pending_total         INTEGER NOT NULL,
    pending_security      INTEGER NOT NULL,
    reboot_required       BOOLEAN NOT NULL DEFAULT FALSE,
    pkg_list              JSONB,                -- {name, current_ver, candidate_ver, is_security}
    last_apt_update_at    TIMESTAMPTZ           -- timestamp of /var/lib/apt/periodic/update-success-stamp
);

CREATE INDEX IF NOT EXISTS nps_node_time_idx ON node_patch_status (node_name, measured_at DESC);

GRANT INSERT ON node_patch_status TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE node_patch_status_id_seq TO log_shipper;
GRANT SELECT ON node_patch_status TO agent_reader, grafana_reader, ehuser;

COMMENT ON TABLE node_patch_status IS
    'Per-node apt update queue. Snapshot daily by bhn-patch-collector.sh. reboot_required = /var/run/reboot-required present.';
