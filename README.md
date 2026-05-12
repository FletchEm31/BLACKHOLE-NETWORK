# Blackhole Network (BHN)

A privacy-focused personal VPN built on WireGuard with deep defense-in-depth security and AI-powered operations. **Single-operator network — no customers, no public service offering. Personal infrastructure only.**

> **Note:** Repo renamed 2026-05-11 from EventHorizon VPN → Blackhole Network. LA-deployed script paths and many legacy hostnames (`EH|VPS-LOSANGELES-US1`, `EH|VPS-FRANKFURT-EU1`, `eh-*` script names, `/opt/eh-diagnostics/*`) still use `eh-*` until a coordinated LA migration session. The "EventHorizon VPN" name is reserved for the future separate commercial product. See `project_blackhole_network_rename` memory for full scope.

## Overview

Blackhole Network is the operator's self-hosted personal VPN for a single user (Hayden). No outside users on this infrastructure, ever. The architecture combines battle-tested open-source tools (WireGuard, Shadowsocks, dnscrypt-proxy, PostgreSQL, Grafana) with custom automation and AI-driven monitoring.

Any future public VPN product is a separate concern (different servers, different protocol, different holding entity) and is not part of this repository.

## Architecture

### Three-phase build plan

```
Phase 1: NETWORK
├─ LA hub (operational)
├─ Frankfurt exit (operational; reachable only via WG tunnel — Vultr blocks cross-region public TCP)
├─ Bootstrap script v3 (codifies all hardening)
└─ Future nodes via snapshot deployment

Phase 2: DASHBOARD
├─ PostgreSQL on encrypted NVMe
├─ Grafana (VPN-only access)
├─ n8n for action automation
└─ Single pane of glass for all nodes

Phase 3: AI INTEGRATION
├─ pgvector memory layer
├─ Claude API for analysis
├─ Voice ops interface (Vapi/Retell)
└─ Proactive alerting + auto-response
```

### Storage tiering

```
NVMe (101 GB encrypted, hot tier)
  ├─ /mnt/eh-nvme-hot/postgres   PostgreSQL data (live writes)
  ├─ /mnt/eh-nvme-hot/pcap       Active packet captures
  ├─ /mnt/eh-nvme-hot/logs       Active logs
  └─ /mnt/eh-nvme-hot/grafana    Grafana state

HDD (399 GB encrypted, cold tier)
  ├─ /mnt/eh-hdd-cold/archives/  Compressed daily archives
  ├─ /mnt/eh-hdd-cold/snapshots  Hourly stats snapshots (kept forever)
  └─ /mnt/eh-hdd-cold/reports    Weekly analysis reports
```

Both volumes use LUKS2 with auto-unlock keyfiles, XFS filesystem, and persistent mounts via `/etc/crypttab` and `/etc/fstab`.

## Security stack

Each node runs:

- **WireGuard** — modern encrypted tunnel
- **Shadowsocks** — DPI-resistant traffic obfuscation
- **dnscrypt-proxy** — encrypted DNS (Cloudflare, Quad9, Mullvad, NextDNS)
- **Fail2ban** — automated intrusion blocking with VPN-tunnel whitelist
- **CrowdSec** — collaborative threat intelligence
- **Suricata** — IDS with 49,955+ rules
- **Honeypots** — fake services trapping scanners (SSH, admin panel, MySQL, Redis)
- **UFW** — host firewall
- **LUKS2** — full-disk encryption for storage volumes
- **SSH hardening** — key-only root login, passwords disabled

## Repository layout

```
.
├── README.md                    Project overview (this file)
├── STATUS.md                    Live state of every node + component
├── BACKUP.md                    Backup/restore procedures + Hetzner swap
├── infrastructure/
│   └── bootstrap/               v4 node bootstrap — three-phase orchestrator
│       ├── bhn-node-bootstrap.sh master script (open → install → lockdown)
│       ├── node-types/          hub.sh, exit.sh, scan.sh, proxy.sh
│       ├── modules/             reusable libraries: wireguard, crowdsec, suricata,
│       │                        shadowsocks, dnscrypt, firewall, ssh-hardening,
│       │                        storage, network-policy, backup
│       ├── policies/            declarative network policies per node type
│       └── docs/                bootstrap-guide.md + network-access-policy.md
├── scripts/                     Admin & ops scripts (deployed to /usr/local/sbin as `eh-*` until LA migration)
│   ├── bhn-node-bootstrap.sh    v3 — single-script deployment (production-tested)
│   ├── bhn-purge.sh             Hot→cold tiering, pg_dump + VACUUM, 48h cron + 80% safety net
│   ├── bhn-backup.sh            Daily encrypted offsite backup (PG + n8n via restic)
│   └── bhn-metadata-collector.py Sessions/security-events ingestion into PostgreSQL
├── services/
│   └── embedding/               pgvector embedding service (systemd unit + installer)
├── n8n-workflows/               Exported n8n workflow JSONs
└── sql/                         PostgreSQL schemas (memories, agent token log, nodes, etc.)
```

Both bootstrap paths exist intentionally during the v3→v4 transition. v3 (`scripts/`) remains the field-proven path; v4 (`infrastructure/bootstrap/`) introduces explicit node types, declarative network policies, three-phase install, and auto-registration. See `infrastructure/bootstrap/docs/bootstrap-guide.md` for v4 usage.

## Quick start (new node)

```bash
# On a fresh Ubuntu 22.04 VPS in any region
curl -O https://raw.githubusercontent.com/FletchEm31/BLACKHOLE-NETWORK/main/scripts/bhn-node-bootstrap.sh
chmod +x bhn-node-bootstrap.sh

# Standard exit node (new node — uses BHN| convention)
bash bhn-node-bootstrap.sh BHN-VPS-NYC-US2 123.456.789.0 wg2

# Hub node (with encrypted storage + PostgreSQL) — legacy hostname kept
ATTACH_NVME=/dev/vdb \
ATTACH_HDD=/dev/vdc \
INSTALL_POSTGRES=1 \
bash bhn-node-bootstrap.sh EH-VPS-LOSANGELES-US1 149.28.91.100 wg0
```

After deployment, the script outputs the node's WireGuard public key and the command to register it on the LA hub.

## Naming conventions

```
Standalone resources (VPS) — NEW nodes:
  BHN|VPS-LOCATION-COUNTRY+SEQINDEX
  Example: BHN|VPS-NEWJERSEY-US2

Legacy nodes (operator renames manually):
  EH|VPS-LOSANGELES-US1   (LA hub, pre-rename)
  EH|VPS-FRANKFURT-EU1    (Frankfurt exit, pre-rename)

Attachments (block storage):
  DEVICE-LOCATION-COUNTRY+SEQINDEX
  Example: SSD-LOSANGELES-US1, HDD-FRANKFURT-DE1
```

`SEQINDEX` is the sequential number for multiple resources of the same type in the same location.

## Console terminology

| Term | Definition |
|------|------------|
| **REMOTE BROWSER WINDOW** | noVNC web console (Vultr, in-browser terminal) |
| **PC LA CONSOLE** | SSH session from operator's PC to LA hub |
| **PC GE CONSOLE** | SSH session from operator's PC to Frankfurt node |

## Access methods

### LA hub
```
Direct SSH (from anywhere):    ssh root@149.28.91.100
SSH via VPN tunnel:            ssh root@10.8.0.1
Grafana dashboard (VPN only):  http://10.8.0.1:3000
PostgreSQL (VPN only):         psql -h 10.8.0.1 -U <user> -d eventhorizon
Vultr web console:             via Vultr panel (root + password)
```

### Frankfurt node
```
SSH (from LA only):    ssh frankfurt          # alias in /root/.ssh/config → root@10.9.0.2:2222
                                              # via wg1 tunnel (Vultr blocks cross-region public TCP)
Vultr console:         via Vultr panel        # emergency-only fallback when tunnel is down
```

## License

Private — all rights reserved.

## Support

For issues, open a GitHub issue. For operational concerns, contact the maintainer directly.
