# Blackhole Network (BHN)

A privacy-focused personal VPN built on WireGuard with deep defense-in-depth security and AI-powered operations. **Single-operator network — no customers, no public service offering. Personal infrastructure only.**

> **Note:** Repo renamed 2026-05-11 from EventHorizon VPN → Blackhole Network. Vultr-side server display names updated to `BHN|VPS-LOSANGELES-US1`, `BHN|VPS-FRANKFURT-EU1`, `BHN|VPS-NEWJERSEY-US2`. LA-deployed script paths (`/usr/local/sbin/eh-*`, `/opt/eh-diagnostics/*`), PostgreSQL database name `eventhorizon`, email domain `eventhorizonvpn.com`, and n8n credential names (`Postgres EventHorizon`, `EventHorizonVPN-Claude`) are intentionally preserved as live-system identifiers until a coordinated migration session. The "EventHorizon VPN" name is reserved for the future separate commercial product. See `project_blackhole_network_rename` memory for full scope.

## Overview

Blackhole Network is the operator's self-hosted personal VPN for a single user (Hayden). No outside users on this infrastructure, ever. The architecture combines battle-tested open-source tools (WireGuard, Shadowsocks, dnscrypt-proxy, PostgreSQL, Grafana) with custom automation and AI-driven monitoring.

Any future public VPN product is a separate concern (different servers, different protocol, different holding entity) and is not part of this repository.

## Architecture

### Five-phase build plan

```
Phase 1: NETWORK                        ✅ operational
├─ LA hub (BHN|VPS-LOSANGELES-US1)
├─ Frankfurt exit + privacy node (BHN|VPS-FRANKFURT-EU1, WG tunnel only — Vultr blocks cross-region public TCP)
├─ NJ trading node (BHN|VPS-NEWJERSEY-US2, joined LA's wg0 at 10.8.0.5, operational 2026-05-12)
├─ Frankfurt exit routing (LA-side applied 2026-05-12, BROKEN — FRA MASQUERADE missing for 10.8.0.0/24, deferred to next session)
├─ Bootstrap script v4 (declarative node types + modular install)
└─ Future nodes via snapshot deployment

Phase 2: DASHBOARD                      ✅ operational
├─ PostgreSQL on encrypted NVMe
├─ Grafana (VPN-only access) + BHN Data Ingest Monitor dashboard
├─ n8n for action automation
└─ Single pane of glass for all nodes

Phase 3: AI INTEGRATION                 🔨 in progress
├─ pgvector memory layer (operational)
├─ HORIZON workflow (operational; chat-trigger live)
├─ Voice ops interface (Twilio + ElevenLabs — modules M1-M9 staged; A2P 10DLC in review)
└─ Proactive alerting + auto-response

Phase 4: PER-NODE SERVICES              🔨 mostly operational
├─ Wallos (LA — subscription / cost tracking) ✅ deployed at http://10.8.0.1:8090
├─ SearXNG (Frankfurt — private meta-search) ✅ deployed at http://10.9.0.2:8089
├─ LibreSpeed Frankfurt (EU speedtest endpoint) ✅ deployed at http://10.9.0.2:8088
├─ LibreSpeed LA (US-West speedtest endpoint) 🔨 not yet deployed
└─ Tor non-exit middle relays (Frankfurt + NJ, MyFamily-linked) 🔨 planned

Phase 5: RESILIENCE                     📋 designed
├─ Sweden cold standby + dark replication node (Bahnhof hosting, outside Vultr)
├─ Tor hidden-service replication LA → Sweden (no Vultr cross-region correlation)
├─ Single-command failover (bhn-failover-activate.sh)
├─ Sweden Tor middle relay (joins MyFamily with FRA + NJ)
└─ See infrastructure/docs/sweden-failover-architecture.md

Trading framework (separate workstream, runs on NJ)  🔨 in progress
├─ scripts/trading/ — Python framework, $100k Alpaca paper account
├─ 5 strategies committed: Congress / Buffett / Bollinger scalp / SMA momentum / Pred-market arb
├─ Support scripts committed: trading_core, master_killswitch, daily_summary, reconciliation_daemon
└─ Pending: rules_schema/validator, systemd units, deployment to NJ + runbooks
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

# Standard exit node
bash bhn-node-bootstrap.sh BHN-VPS-NYC-US2 123.456.789.0 wg2

# Hub node (with encrypted storage + PostgreSQL)
ATTACH_NVME=/dev/vdb \
ATTACH_HDD=/dev/vdc \
INSTALL_POSTGRES=1 \
bash bhn-node-bootstrap.sh BHN-VPS-LOSANGELES-US1 149.28.91.100 wg0
```

After deployment, the script outputs the node's WireGuard public key and the command to register it on the LA hub.

## Naming conventions

```
Standalone resources (VPS):
  BHN|VPS-LOCATION-COUNTRY+SEQINDEX
  Examples: BHN|VPS-LOSANGELES-US1, BHN|VPS-FRANKFURT-EU1, BHN|VPS-NEWJERSEY-US2

Attachments (block storage):
  DEVICE-LOCATION-COUNTRY+SEQINDEX
  Example: SSD-LOSANGELES-US1, HDD-FRANKFURT-DE1
```

`SEQINDEX` is the sequential number for multiple resources of the same type in the same location.

## Console terminology

| Term | Definition |
|------|------------|
| **REMOTE BROWSER WINDOW** | noVNC web console (Vultr, in-browser terminal) — emergency-only fallback when tunnel/SSH is down |
| **PC LA CONSOLE** | SSH session from operator's PC to LA hub (`ssh root@149.28.91.100`) |
| **PC GE CONSOLE** | SSH session from operator's PC to Frankfurt node (`ssh root@192.248.187.208`) |
| **PC NJ CONSOLE** | SSH session from operator's PC to NJ trading node (`ssh -p 2222 root@140.82.4.35`) |

## Access methods

For the canonical comprehensive access-methods sheet (every node, every service, every port), see [`BHN-INFRASTRUCTURE.txt`](BHN-INFRASTRUCTURE.txt) at repo root.

### LA hub
```
Direct SSH (from anywhere):    ssh root@149.28.91.100
SSH via VPN tunnel:            ssh root@10.8.0.1
Grafana dashboard (VPN only):  http://10.8.0.1:3000
n8n (VPN only):                http://10.8.0.1:5678
HORIZON chat (VPN only):       http://10.8.0.1:5678/webhook/ec1592c6-8715-4b0f-8ee8-5bc02f551a27/chat
Wallos (VPN only):             http://10.8.0.1:8090
PostgreSQL (VPN only):         psql -h 10.8.0.1 -U <user> -d eventhorizon
Vultr web console:             via Vultr panel (root + password)
```

### Frankfurt node
```
SSH (from LA only):       ssh frankfurt       # alias → root@10.9.0.2:2222 via wg0 tunnel
                                              # (Vultr blocks cross-region public TCP)
SearXNG (private search): http://10.9.0.2:8089
LibreSpeed (EU):          http://10.9.0.2:8088
Vultr console:            via Vultr panel     # emergency-only fallback when tunnel is down
```

### NJ trading node
```
SSH (from LA):          ssh nj                # alias → root@10.8.0.5:2222 via wg0 tunnel
SSH (from operator PC): ssh -p 2222 root@140.82.4.35   # direct to public IP also works
Vultr console:          via Vultr panel       # emergency-only fallback
```

## License

Private — all rights reserved.

## Support

For issues, open a GitHub issue. For operational concerns, contact the maintainer directly.
