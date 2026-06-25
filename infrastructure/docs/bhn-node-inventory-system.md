# BHN Node Inventory & Management System

**Deployed:** 2026-06-25  
**Status:** Built (repo-side); deploy via `scripts/deploy-inventory-system.sh`

Provides auto-discovery of Docker containers, BHN-relevant systemd services, listening ports, and key package versions across all three active nodes. Data is written to PostgreSQL on LA every 30 minutes and surfaced in Grafana.

---

## Components

| Component | Location in repo | Purpose |
|-----------|-----------------|---------|
| SQL schema | `sql/node-inventory-schema.sql` | Tables: `node_services`, `node_packages`, `node_ports` |
| Collector script | `scripts/bhn-inventory-collector.sh` | Runs on each node every 30 min |
| Deploy script | `scripts/deploy-inventory-system.sh` | Pushes script + cron to all 3 nodes |
| Grafana dashboard | `infrastructure/grafana/dashboards/bhn-node-inventory.json` | "BHN Node Inventory" |
| Dozzle (hub) | `infrastructure/services/dozzle/docker-compose-hub.yml` | Log viewer on LA:9999 |
| Dozzle (agents) | `infrastructure/services/dozzle/docker-compose-agent.yml` | Agents on NJ + Hillsboro |
| Ansible inventory | `infrastructure/ansible/inventory.yml` | All 3 nodes with WG IPs + SSH ports |
| Ansible playbooks | `infrastructure/ansible/playbooks/` | health-check, restart-dns-stack, ufw-audit |

---

## PostgreSQL tables (eventhorizon DB)

### node_services
Systemd services (BHN-relevant) and Docker containers per node. Upserted on each run.

| Column | Type | Notes |
|--------|------|-------|
| node_name | TEXT | e.g. BHN-LOSANGELES-US1 |
| service_name | TEXT | systemd unit name or Docker container name |
| service_type | TEXT | `systemd` or `docker` |
| status | TEXT | systemd: `running`/`exited`/`failed`; Docker: raw status string |
| image | TEXT | Docker image; NULL for systemd |
| collected_at | TIMESTAMPTZ | Last update time |

### node_packages
Key dpkg package versions. Upserted on each run.

| Column | Type | Notes |
|--------|------|-------|
| node_name | TEXT | |
| package_name | TEXT | |
| version | TEXT | dpkg-query output |
| collected_at | TIMESTAMPTZ | |

### node_ports
Listening TCP/UDP ports. Replaced wholesale (DELETE + INSERT) on each run.

| Column | Type | Notes |
|--------|------|-------|
| node_name | TEXT | |
| protocol | TEXT | `tcp` or `udp` |
| address | TEXT | Bind address from ss (0.0.0.0, 10.8.0.x, 127.0.0.1, ::) |
| port | INTEGER | |
| process_name | TEXT | Process name from ss; NULL if unavailable |
| collected_at | TIMESTAMPTZ | |

**Grants:** `grafana_reader` → SELECT; `ehuser` → SELECT + INSERT + UPDATE + DELETE

---

## Collector script

**File:** `/usr/local/bin/bhn-inventory-collector.sh` on each node  
**Cron:** `/etc/cron.d/bhn-inventory` — runs at `*/30 * * * *`  
**Log:** `/var/log/bhn-inventory-collector.log`  
**Config:** `/root/.bhn-inventory.env` (mode 0600) — contains `BHN_INVENTORY_PG_DSN`  
**Identity:** reads `NODE_NAME` from `/etc/bhn-node-info.conf`

The collector detects Docker availability dynamically — NJ (no Docker by default) will skip the Docker collection step without error.

Systemd services tracked: grafana, postgresql, docker, crowdsec, fail2ban, suricata, netdata, dnscrypt-proxy, tinyproxy, shadowsocks, wg-quick, bhn-*, eh-embed, n8n, wallos, unbound, tor, redis.

---

## Dozzle unified log viewer

**Hub UI:** `http://10.8.0.1:9999` (WireGuard tunnel only)  
**Hub container:** `bhn-dozzle` on LA  
**Agent containers:** `bhn-dozzle-agent` on NJ and Hillsboro (port 7007)

NJ requires Docker installation before the agent can run — see `infrastructure/services/dozzle/README.md`.

New ports added:
- `10.8.0.1:9999` — Dozzle UI (mesh-only; UFW restrict to wg0)
- `10.8.0.5:7007` — Dozzle agent listener (WG-internal)
- `10.8.0.6:7007` — Dozzle agent listener (WG-internal)

---

## Ansible

**Run from:** LA hub or operator PC while connected to WireGuard.  
**Inventory:** `infrastructure/ansible/inventory.yml`

```bash
# Prerequisite
pip install ansible

# Test connectivity
cd infrastructure/ansible
ansible all -m ping

# Run a playbook
ansible-playbook playbooks/health-check.yml
ansible-playbook playbooks/ufw-audit.yml
ansible-playbook playbooks/restart-dns-stack.yml --limit la
```

| Playbook | What it does |
|----------|-------------|
| `health-check.yml` | Uptime, memory, disk, failed units, Docker ps, WG peers |
| `restart-dns-stack.yml` | Restarts dnscrypt-proxy + systemd-resolved; verifies DNS |
| `ufw-audit.yml` | Collects ufw status + public listeners; saves local reports |

---

## Grafana dashboard

**Title:** BHN Node Inventory  
**UID:** `bhn-node-inventory`  
**Folder:** Blackhole Network  
**Refresh:** 5m  
**Datasource:** eventhorizon (PostgreSQL on LA)

Panels: Running service counts, failed service alerts, systemd services table, Docker containers table, listening ports table, package version matrix (LA/NJ/Hillsboro columns), last-collection time with staleness alerting.

Deploy to Grafana (run on NJ where Grafana lives):
```bash
cp infrastructure/grafana/dashboards/bhn-node-inventory.json /var/lib/grafana/dashboards/
# Grafana picks it up within 30 s (updateIntervalSeconds: 30 in provisioner)
```

---

## Deploy sequence (first-time)

Run all of this from LA as root:

```bash
# 1. Snapshot DB
sudo -u postgres pg_dump eventhorizon > /mnt/eh-nvme-hot/backups/pre-node-inventory-$(date +%Y%m%d-%H%M).sql

# 2. Apply schema
sudo -u postgres psql -d eventhorizon -f sql/node-inventory-schema.sql

# 3. Deploy collector to all nodes
export BHN_EHUSER_PG_PASS='<from Proton Pass: EH-Postgres-ehuser-2026-05-08>'
bash scripts/deploy-inventory-system.sh

# 4. Verify data landed
sudo -u postgres psql -d eventhorizon -c \
  'SELECT node_name, service_type, COUNT(*) FROM node_services GROUP BY 1,2 ORDER BY 1,2;'

# 5. Deploy Dozzle hub on LA
mkdir -p /opt/bhn-dozzle
cp infrastructure/services/dozzle/docker-compose-hub.yml /opt/bhn-dozzle/docker-compose.yml
cd /opt/bhn-dozzle && docker compose up -d

# 6. Deploy Dozzle agents (Hillsboro — Docker already present)
scp infrastructure/services/dozzle/docker-compose-agent.yml root@10.8.0.6:/opt/bhn-dozzle/docker-compose.yml
ssh root@10.8.0.6 'cd /opt/bhn-dozzle && docker compose up -d'

# 7. NJ agent (install Docker first)
ssh -p 2222 root@10.8.0.5 'apt-get update && apt-get install -y docker.io && systemctl enable --now docker'
scp -P 2222 infrastructure/services/dozzle/docker-compose-agent.yml root@10.8.0.5:/opt/bhn-dozzle/docker-compose.yml
ssh -p 2222 root@10.8.0.5 'cd /opt/bhn-dozzle && docker compose up -d'

# 8. Copy Grafana dashboard to NJ
scp -P 2222 infrastructure/grafana/dashboards/bhn-node-inventory.json \
    root@10.8.0.5:/var/lib/grafana/dashboards/
```
