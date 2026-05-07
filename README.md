# EventHorizon VPN

A privacy-focused VPN service built on WireGuard with deep defense-in-depth security and AI-powered operations.

## Overview

EventHorizon is a self-hosted VPN network designed for solo operation at scale. The architecture combines battle-tested open-source tools (WireGuard, Shadowsocks, dnscrypt-proxy, PostgreSQL, Grafana) with custom automation and AI-driven monitoring.

## Architecture

### Three-phase build plan

```
Phase 1: NETWORK
├─ LA hub (operational)
├─ Frankfurt exit (UDP issue, Vultr ticket open)
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

## Repository contents

| File | Purpose |
|------|---------|
| `eh-node-bootstrap.sh` | v3 deployment script — provisions a new node end-to-end |
| `STATUS.md` | Current state of all nodes and components |
| `docs/dashboard.md` | Grafana access and panel SQL reference |
| `docs/credentials-recovery.md` | Recovery procedures for lost keys/passwords |

## Quick start (new node)

```bash
# On a fresh Ubuntu 22.04 VPS in any region
curl -O https://raw.githubusercontent.com/[your-org]/EVENT-HORIZON-VPN-DASH/main/eh-node-bootstrap.sh
chmod +x eh-node-bootstrap.sh

# Standard exit node
bash eh-node-bootstrap.sh EH-NYC-US2 123.456.789.0 wg2

# Hub node (with encrypted storage + PostgreSQL)
ATTACH_NVME=/dev/vdb \
ATTACH_HDD=/dev/vdc \
INSTALL_POSTGRES=1 \
bash eh-node-bootstrap.sh EH-VPS-LOSANGELES-US1 149.28.91.100 wg0
```

After deployment, the script outputs the node's WireGuard public key and the command to register it on the LA hub.

## Naming conventions

```
Standalone resources (VPS):
  EH|VPS-LOCATION-COUNTRY+SEQINDEX
  Example: EH|VPS-LOSANGELES-US1

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
SSH:                ssh root@192.248.187.208
Vultr console:      via Vultr panel
```

## License

Private — all rights reserved.

## Support

For issues, open a GitHub issue. For operational concerns, contact the maintainer directly.
