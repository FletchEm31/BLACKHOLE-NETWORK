-- nodes-schema.sql
-- EventHorizon — registry of all bootstrapped nodes in the network.
-- Written by infrastructure/bootstrap/eh-node-bootstrap.sh phase 3 via psql
-- when EH_BOOTSTRAP_PG_DSN is set, otherwise staged at /root/eh-node-register.sql.
--
-- Apply once on the hub:
--   sudo -u postgres psql -d eventhorizon -f sql/nodes-schema.sql

CREATE TABLE IF NOT EXISTS nodes (
    id                      SERIAL PRIMARY KEY,
    name                    TEXT UNIQUE NOT NULL,
    type                    TEXT NOT NULL CHECK (type IN ('hub', 'exit', 'scan', 'proxy')),
    region                  TEXT NOT NULL,
    public_ip               INET,
    tunnel_ip               INET,
    wg_interface            TEXT,
    wg_pubkey               TEXT,
    bootstrap_version       TEXT,
    bootstrap_completed_at  TIMESTAMPTZ,
    status                  TEXT NOT NULL DEFAULT 'bootstrapping'
                            CHECK (status IN ('bootstrapping', 'online', 'degraded',
                                              'offline', 'decommissioned')),
    last_seen               TIMESTAMPTZ,
    notes                   TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS nodes_type_idx   ON nodes (type);
CREATE INDEX IF NOT EXISTS nodes_region_idx ON nodes (region);
CREATE INDEX IF NOT EXISTS nodes_status_idx ON nodes (status);

-- Auto-update `updated_at` on row mutation
CREATE OR REPLACE FUNCTION nodes_touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS nodes_touch_updated_at ON nodes;
CREATE TRIGGER nodes_touch_updated_at
    BEFORE UPDATE ON nodes
    FOR EACH ROW EXECUTE FUNCTION nodes_touch_updated_at();

-- Read-only role for Grafana dashboards (already exists if hub bootstrap ran)
GRANT SELECT ON nodes TO grafana_reader;

-- Bootstrap writer role used by new nodes during phase 3 registration.
-- Operator should set the password and store it in their password manager:
--   ALTER ROLE bootstrap_writer WITH PASSWORD '<random-44-char>';
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bootstrap_writer') THEN
        CREATE ROLE bootstrap_writer WITH LOGIN PASSWORD 'CHANGE_ME_AT_FIRST_USE';
    END IF;
END
$$;
GRANT INSERT, UPDATE, SELECT ON nodes TO bootstrap_writer;
GRANT USAGE, SELECT ON SEQUENCE nodes_id_seq TO bootstrap_writer;

-- Comment metadata
COMMENT ON TABLE  nodes IS 'EventHorizon node registry — populated by eh-node-bootstrap.sh v4+';
COMMENT ON COLUMN nodes.status IS 'bootstrapping → online → (degraded|offline|decommissioned)';
