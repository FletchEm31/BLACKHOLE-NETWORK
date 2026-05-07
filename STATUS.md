# EventHorizon — Network Status

Last updated: **2026-05-07**

## Phase progress

```
Phase 1: NETWORK              ███████░░░  ~70%
Phase 2: DASHBOARD            ████░░░░░░  ~40%
Phase 3: AI INTEGRATION       ░░░░░░░░░░   0%
```

## Nodes

### LA Hub — `EH|VPS-LOSANGELES-US1` ✅ Operational

| Component | Status |
|-----------|--------|
| IP | `149.28.91.100` |
| Specs | 2 vCPU, 2 GB RAM, 60 GB system NVMe |
| OS | Ubuntu 22.04.5 LTS |
| WireGuard server (port 51820) | ✅ Active |
| Shadowsocks (port 8388) | ✅ Active |
| dnscrypt-proxy | ✅ Active |
| Fail2ban + CrowdSec | ✅ Active |
| Suricata IDS | ✅ Active (49,955 rules) |
| Honeypots (4 services) | ✅ Active |
| UFW firewall | ✅ Configured |
| **NVMe block storage** (`SSD-LOSANGELES-US1`, 101 GB) | ✅ LUKS2 encrypted, XFS, mounted |
| **HDD block storage** (`HDD-LOSANGELES-US1`, 399 GB) | ✅ LUKS2 encrypted, XFS, mounted |
| PostgreSQL 14 | ✅ Running on encrypted NVMe |
| Grafana | ✅ VPN-only access at `http://10.8.0.1:3000` |
| SSH hardening | ✅ Key-only root, passwords disabled |
| iptables ACCEPT for VPN→SSH | ✅ Persisted |
| Reboot survival | ✅ Verified |

### Frankfurt — `EH|VPS-FRANKFURT-EU1` ⚠️ Partial

| Component | Status |
|-----------|--------|
| IP | `192.248.187.208` |
| OS | Ubuntu 22.04 |
| WireGuard installed | ✅ |
| WireGuard tunnel to LA | ❌ UDP packets dropped at Vultr's LA edge |
| Shadowsocks | ✅ |
| dnscrypt-proxy | ✅ |
| **Vultr support ticket** | 🟡 Open — MTR data submitted, awaiting response |
| v3 hardening applied | ❌ Pending (will replicate from LA after v3 stabilizes) |

## Data pipeline

| Component | Status |
|-----------|--------|
| Storage layer | ✅ Done |
| Directory structure | ✅ Created on both tiers |
| PostgreSQL on NVMe | ✅ Migrated, online |
| Grafana installed | ✅ Live, connected to PostgreSQL |
| Initial dashboards | ✅ 8-panel "EH Network Overview" live |
| Purge cycle (`eh-purge` script) | ✅ Deployed to `/usr/local/sbin/eh-purge` on LA hub |
| Cron schedule (48hr default) | ✅ `/etc/cron.d/eh-purge` — `--auto` at 03:00 UTC every 48h |
| 80% capacity safety net | ✅ Same cron — `--check-capacity` every 15 min |
| Full DNS query capture | ⚠️ Logger not capturing (only 1 row) |
| Packet payload capture (testing-only) | ❌ Not yet enabled |
| Hourly stats snapshots | ❌ Not yet implemented |
| Weekly analysis | ❌ Not yet implemented |
| n8n action automation | ❌ Not yet installed |

## Database row counts (snapshot)

| Table | Rows |
|-------|------|
| `sessions` | 198 |
| `dns_queries` | 1 (logger needs investigation) |
| `security_events` | 7,969 |
| `anomalies` | 0 |

## Live dashboard panels

The "EH Network Overview" Grafana dashboard currently includes:

1. Active Sessions (Stat) — `204` at last check
2. Bandwidth Today (Stat with bytes unit)
3. Security Events 24h (Stat/Gauge)
4. Open Anomalies (Stat) — `0`
5. Sessions Over Time (Time series)
6. Events by Severity (Pie chart)
7. Top Source IPs 7d (Table)
8. Recent Events (Table)

## Outstanding tickets

- **Vultr LA UDP filtering** — MTR shows 58% loss starting at hop 12 (`ce-1-3-3.a03.lsanca07.us.bb.gin.ntt.net`) and 100% at destination. Anthony confirmed WG is configured; awaiting infrastructure investigation.

## Pending build items

In rough order:

1. DNS logger fix (currently only capturing 1 row)
2. Frankfurt UDP fix (blocked on Vultr)
3. v3 bootstrap apply to Frankfurt
4. Tokyo or other 3rd region node
5. n8n install + initial workflows
6. Claude API integration for analysis
7. pgvector memory layer
8. Voice ops interface (Vapi or Retell evaluation)

## Cost snapshot

| Item | Monthly |
|------|---------|
| LA VPS | ~$12 |
| Frankfurt VPS | ~$12 |
| LA NVMe (101 GB) | $10.10 |
| LA HDD (399 GB) | $9.97 |
| Auto Backups (LA) | ~$2.40 |
| AI (subscription + API estimate) | ~$60–130 |
| Domain (annualized) | ~$1 |
| **Total** | **~$107–177** |

## Operational notes

- LA root password rotated on 2026-05-07 (stored in operator's password manager)
- Frankfurt still uses original `EventHorizon2026` password
- LUKS passphrases backed up in operator's password manager as `EH-NVMe-LUKS` and `EH-HDD-LUKS`
- LUKS keyfiles on LA at `/root/.luks-eh-nvme` and `/root/.luks-eh-hdd` (auto-unlock)
- SSH key authorization for `fletch-desktop` workstation deployed to LA
- Fail2ban whitelist includes VPN tunnel range (`10.8.0.0/24`) and operator's PC home IP
- Grafana admin credential stored as `EH-Grafana-Admin` in password manager
- `grafana_reader` PostgreSQL role exists for read-only dashboard queries
