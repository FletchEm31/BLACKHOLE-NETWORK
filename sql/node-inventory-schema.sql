-- node-inventory-schema.sql
-- BHN Node Inventory — services, packages, and listening ports for all nodes.
-- Populated every 30 min by scripts/bhn-inventory-collector.sh running on each node.
--
-- Apply on LA hub (snapshot first):
--   sudo -u postgres pg_dump eventhorizon > /mnt/eh-nvme-hot/backups/pre-node-inventory-$(date +%Y%m%d-%H%M).sql
--   sudo -u postgres psql -d eventhorizon -f sql/node-inventory-schema.sql

-- -------------------------------------------------------------------------
-- node_services: systemd units (BHN-relevant) + Docker containers
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS node_services (
    id              SERIAL PRIMARY KEY,
    node_name       TEXT NOT NULL,
    service_name    TEXT NOT NULL,
    service_type    TEXT NOT NULL CHECK (service_type IN ('systemd', 'docker')),
    status          TEXT NOT NULL,          -- systemd: running/stopped/failed/exited; docker: raw Status string
    image           TEXT,                   -- docker only; NULL for systemd
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (node_name, service_name, service_type)
);

CREATE INDEX IF NOT EXISTS node_services_node_idx    ON node_services (node_name);
CREATE INDEX IF NOT EXISTS node_services_status_idx  ON node_services (status);
CREATE INDEX IF NOT EXISTS node_services_ts_idx      ON node_services (collected_at DESC);

COMMENT ON TABLE  node_services IS 'BHN per-node service inventory (systemd + Docker). Upserted every 30 min by bhn-inventory-collector.sh.';
COMMENT ON COLUMN node_services.service_type IS 'systemd or docker';
COMMENT ON COLUMN node_services.status IS 'systemd sub-state (running/exited/failed) or raw Docker status (e.g. "Up 2 hours")';
COMMENT ON COLUMN node_services.image IS 'Docker image name; NULL for systemd services';

-- -------------------------------------------------------------------------
-- node_packages: key package versions
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS node_packages (
    id              SERIAL PRIMARY KEY,
    node_name       TEXT NOT NULL,
    package_name    TEXT NOT NULL,
    version         TEXT NOT NULL,
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (node_name, package_name)
);

CREATE INDEX IF NOT EXISTS node_packages_node_idx ON node_packages (node_name);
CREATE INDEX IF NOT EXISTS node_packages_ts_idx   ON node_packages (collected_at DESC);

COMMENT ON TABLE node_packages IS 'Key dpkg package versions per node. Upserted every 30 min by bhn-inventory-collector.sh.';

-- -------------------------------------------------------------------------
-- node_ports: current listening TCP/UDP ports
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS node_ports (
    id              SERIAL PRIMARY KEY,
    node_name       TEXT NOT NULL,
    protocol        TEXT NOT NULL CHECK (protocol IN ('tcp', 'udp')),
    address         TEXT NOT NULL,
    port            INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
    process_name    TEXT,
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS node_ports_node_idx ON node_ports (node_name);
CREATE INDEX IF NOT EXISTS node_ports_ts_idx   ON node_ports (collected_at DESC);

COMMENT ON TABLE  node_ports IS 'Listening ports per node. Replaced wholesale (DELETE + INSERT) on every 30-min collection run.';
COMMENT ON COLUMN node_ports.address IS 'Bind address from ss -tlnp (0.0.0.0, 127.0.0.1, 10.8.0.x, ::, etc.)';

-- -------------------------------------------------------------------------
-- Grants
-- -------------------------------------------------------------------------
GRANT SELECT ON node_services, node_packages, node_ports TO grafana_reader;

GRANT SELECT, INSERT, UPDATE, DELETE ON node_services, node_packages, node_ports TO ehuser;
GRANT USAGE, SELECT ON SEQUENCE
    node_services_id_seq,
    node_packages_id_seq,
    node_ports_id_seq
  TO ehuser;

-- Verify
DO $$
BEGIN
    RAISE NOTICE 'node-inventory-schema applied. Tables: node_services, node_packages, node_ports';
    RAISE NOTICE 'Grants: grafana_reader=SELECT, ehuser=SELECT+INSERT+UPDATE+DELETE';
END
$$;
