# EventHorizon — Bootstrap v4 Guide

Three-phase node provisioning: **open window → install by type → auto lockdown.**

## Layout

```
infrastructure/bootstrap/
├── eh-node-bootstrap.sh         master orchestrator
├── node-types/
│   ├── hub.sh                   storage + WG hub + PG + Grafana + n8n + backup
│   ├── exit.sh                  WG peer + Shadowsocks
│   ├── scan.sh                  WG peer + Suricata + node_exporter
│   └── proxy.sh                 WG peer (mgmt only) + Shadowsocks
├── modules/                     reusable libraries (sourced, not run directly)
├── policies/<type>-network-policy.conf
└── docs/                        this directory
```

## Quick start (any node type)

```bash
# On a fresh Ubuntu 22.04 VPS in any region
git clone https://github.com/[your-org]/event-horizon-vpn-dashboard.git
cd event-horizon-vpn-dashboard

bash infrastructure/bootstrap/eh-node-bootstrap.sh \
    <NAME> <PUBLIC_IP> <WG_INTERFACE> <TYPE> <REGION>
```

### Examples

```bash
# Hub node — full stack with encrypted block storage
ATTACH_NVME=/dev/vdb \
ATTACH_HDD=/dev/vdc \
bash infrastructure/bootstrap/eh-node-bootstrap.sh \
    EH-VPS-LOSANGELES-US1 149.28.91.100 wg0 hub US-WEST

# Exit node
bash infrastructure/bootstrap/eh-node-bootstrap.sh \
    EH-VPS-FRANKFURT-EU1 192.248.187.208 wg1 exit EU-CENTRAL

# Scan node — passive sensor with Suricata + node_exporter
bash infrastructure/bootstrap/eh-node-bootstrap.sh \
    EH-VPS-NYC-US2 1.2.3.4 wg2 scan US-EAST

# Proxy node — Shadowsocks-only DPI-resistant entry
bash infrastructure/bootstrap/eh-node-bootstrap.sh \
    EH-VPS-SGP-SG1 5.6.7.8 wg3 proxy APAC-SE
```

## Three phases

### Phase 1 — open window
- System update + base utilities
- SSH hardening (regen host keys, key-only root, load admin pubkeys)
- Open enough firewall to install: 22/tcp + 80/tcp + 443/tcp inbound, egress allow-all (temporary)
- iptables ACCEPT for tunnel→SSH at INPUT pos 1 (defense in depth)

### Phase 2 — install by type
The node-type composer (`node-types/<TYPE>.sh`) is sourced and `node_type_install` is called. It:
- Calls module functions in the right order for the type's role
- Generates `/etc/eh-node-info.conf` capturing identity (consumed by ops + Grafana labels)
- Sets the hostname

### Phase 3 — auto lockdown
- Resets UFW and applies `policies/<TYPE>-network-policy.conf` declaratively
- Inserts/upserts the node in the hub's `nodes` PG table (over the freshly-up tunnel) if `EH_BOOTSTRAP_PG_DSN` is set; otherwise stages SQL at `/root/eh-node-register.sql`
- Sends ntfy push notification if `EH_NTFY_URL` is set
- Prints summary with WG pubkey + Shadowsocks password + hub registration command

If phase 3 fails, the node is still in phase-1's open-window state — recover by re-running the bootstrap with the same args (idempotent). Master returns exit code 4 in that case.

## Environment variables

| Var                       | Purpose                                                                              | Default                       |
|---------------------------|--------------------------------------------------------------------------------------|-------------------------------|
| `ATTACH_NVME`             | Encrypt + mount as `/mnt/eh-nvme-hot` (hub only)                                     | unset                         |
| `ATTACH_HDD`              | Encrypt + mount as `/mnt/eh-hdd-cold` (hub only)                                     | unset                         |
| `ADMIN_PUBKEYS_FILE`      | File of SSH pubkeys (one per line) to load into `/root/.ssh/authorized_keys`         | hardcoded fallback            |
| `INSTALL_SURICATA`        | `1` to force Suricata install                                                        | auto-on for `hub` and `scan`  |
| `EH_BOOTSTRAP_PG_DSN`     | DSN for hub PG to register this node, e.g. `postgresql://bootstrap_writer:PASS@10.8.0.1/eventhorizon` | unset (SQL staged for manual apply) |
| `EH_NTFY_URL`             | ntfy endpoint, e.g. `https://ntfy.sh/eh-bootstrap-<topic>`                           | unset (skipped)               |
| `HUB_IP`                  | Override hub IP — useful when bootstrapping a new hub or migrating                   | `149.28.91.100`               |
| `HUB_PUBKEY`              | Override hub WG pubkey — paired with `HUB_IP` for new hubs                           | LA hub pubkey                 |

## Identity file: `/etc/eh-node-info.conf`

Generated in phase 2. Consumed by ops scripts, Grafana labels, and `eh-purge`/`eh-backup` for hostname tagging.

```
NODE_NAME=EH-VPS-NYC-US2
NODE_TYPE=exit
NODE_REGION=US-EAST
NODE_PUBLIC_IP=1.2.3.4
NODE_TUNNEL_IP=10.10.0.2
WG_INTERFACE=wg2
WG_PUBKEY=...
HUB_IP=149.28.91.100
HUB_PUBKEY=TOYnFt18v4NynEN91o6zkmV5hsvHBLJTb8qL7GG/KAo=
BOOTSTRAP_VERSION=4
BOOTSTRAP_TIMESTAMP=2026-05-08T23:50:12Z
```

This file is **not** a secret — no passwords, no private keys. Safe to read from any user.

## Hub PG registration

If `EH_BOOTSTRAP_PG_DSN` is set, phase 3 runs an `INSERT … ON CONFLICT DO UPDATE` against the `nodes` table. The `bootstrap_writer` role needs `INSERT, UPDATE, SELECT` on that table (set up by `sql/nodes-schema.sql`).

If the DSN is unset (or the insert fails — typically because the WG tunnel hasn't fully come up yet), the SQL is staged at `/root/eh-node-register.sql` and the operator applies it manually:

```bash
# On the hub:
sudo -u postgres psql -d eventhorizon -f /root/eh-node-register.sql
```

## ntfy notifications

Set `EH_NTFY_URL=https://ntfy.sh/<your-topic>` (or self-hosted). Phase 3 POSTs a brief summary including node name, type, region, public IP, tunnel IP, and the first 24 chars of the WG pubkey.

Subscribe via the ntfy mobile app or `curl -s https://ntfy.sh/<topic>/json` from a watcher process.

## Re-running the bootstrap

The script is idempotent — re-running with the same args:
- Won't re-format LUKS volumes (checks `cryptsetup isLuks`)
- Won't re-generate WG keys (checks for existing files)
- Won't re-init restic repo (checks for existing snapshots)
- Will re-apply the policy (UFW reset + reload)
- Will re-write `/etc/eh-node-info.conf`
- Will re-attempt PG registration (`ON CONFLICT DO UPDATE`)

Use this when the policy file changes, or when a phase-3 failure left the node in open-window state.

## Decommissioning a node

Currently manual:
1. On the hub: `wg set wg0 peer <NODE_PUBKEY> remove && wg-quick save wg0`
2. On the hub: `UPDATE nodes SET status='decommissioned', notes='<reason>' WHERE name='<NODE_NAME>';`
3. Destroy the VPS at the cloud provider.

Planned: `eh-node-decommission <NODE_NAME>` script that does all three over the tunnel.

## Differences from v3

| Aspect                        | v3                                              | v4                                                 |
|-------------------------------|-------------------------------------------------|----------------------------------------------------|
| Args                          | `<NAME> <IP> <WG_INTERFACE>`                    | `<NAME> <IP> <WG_INTERFACE> <TYPE> <REGION>`        |
| Node types                    | implicit (one big script + `INSTALL_POSTGRES=1`) | explicit (`hub`/`exit`/`scan`/`proxy`)              |
| Firewall                      | inline `ufw allow` calls                        | declarative policy files                            |
| Egress lockdown               | not applied at bootstrap time                   | applied automatically in phase 3                    |
| PG registration               | none                                            | auto-upsert into `nodes` table                      |
| Notifications                 | none                                            | ntfy push on completion                             |
| Phase boundaries              | none — one big script                           | open → install → lockdown, with explicit traps      |
| Module reuse                  | copy-paste between scripts                      | sourced library functions                           |

## Troubleshooting

- **"Policy file missing"** — confirm `policies/<TYPE>-network-policy.conf` exists alongside the master script. The master expects a sibling `policies/` directory.
- **PG registration warns and stages SQL** — usually means the tunnel didn't come up in time, or `EH_BOOTSTRAP_PG_DSN` isn't set. Apply `/root/eh-node-register.sql` manually on the hub.
- **Suricata install fails on a small node** — set `INSTALL_SURICATA=0` to skip. Suricata wants ≥4 GB RAM under the full ruleset.
- **ntfy POST fails** — non-fatal. Bootstrap completes anyway; check `EH_NTFY_URL` reachability post-hoc.
- **Re-running locks me out** — shouldn't, but if it does: ssh into the open-window phase via the iptables INPUT pos 1 ACCEPT for the tunnel network, or use the cloud provider's web console.
