# EventHorizon — Network Status

Last updated: **2026-05-07**

## Phase progress

```
Phase 1: NETWORK              ███████░░░  ~70%
Phase 2: DASHBOARD            ███████░░░  ~65%
Phase 3: AI INTEGRATION       █░░░░░░░░░  ~10%
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

### Frankfurt — `EH|VPS-FRANKFURT-EU1` ⚠️ Hardened, tunnel still blocked

| Component | Status |
|-----------|--------|
| IP | `192.248.187.208` |
| OS | Ubuntu 22.04 |
| v3 bootstrap applied | ✅ 2026-05-07 (via Vultr web console; SSH key auth confirmed working) |
| SSH | ✅ Key-only root, passwords disabled |
| WireGuard installed + configured | ✅ Local config + LA hub registration in place (peer key `zkfJNbdL9Ptdxv...KA8=`, allowed-ips 10.9.0.2/32) |
| WireGuard tunnel handshake | ❌ Still no handshake — UDP filtering at Vultr's LA edge is upstream of the bootstrap |
| Shadowsocks (8388) | ✅ |
| dnscrypt-proxy | ✅ |
| Fail2ban + UFW + iptables | ✅ Hardened by v3 |
| **Vultr support ticket** | 🟡 Open — once UDP path is restored, tunnel will establish automatically with no further config |

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
| DNS query persistence | ⛔ Intentionally disabled — `dns_queries` table dropped per external-observer principle (domains are content) |
| Packet payload capture | ⛔ Intentionally not enabled — payloads are content |
| Hourly stats snapshots | ❌ Not yet implemented |
| Weekly analysis | ❌ Not yet implemented |
| n8n install | ✅ Running at `https://n8n.eventhorizonvpn.com` (nginx + LE on 443) |
| n8n: `EH Network Pulse - 2h` workflow | ✅ Live — schedule trigger + manual UI execute + on-demand webhook all firing correctly. Pulls 3 metadata tables every 2h, sends aggregate to Claude (Sonnet 4.6) with structured outputs, writes to `pulse_reports`, ntfy push to `eh-alerts-hayden-x7k2` when `important=true`. Alert thresholds calibrated to operator's actual baseline (~470 high events/cycle is honeypot+fail2ban noise, not anomaly) |
| n8n: `EventHorizon Proxy Health Monitor v1.0` | ⛔ Deactivated 2026-05-07 — the 4 monitored proxies were decommissioned (operator concluded they were insecure). Workflow JSON archived in `n8n-workflows/eh-proxy-health-monitor.json` for future reactivation/repurposing |
| n8n: `EventHorizon AI Agent v1.0` | ✅ Live (chat-trigger workflow, pre-existing) |

## Database row counts (snapshot)

| Table | Rows |
|-------|------|
| `sessions` | 280 |
| `security_events` | 12,280 (predominantly bot SSH brute-force attempts on the public IP) |
| `anomalies` | 0 |
| `purge_log` | 1 (manual smoke run on 2026-05-07) |
| `pulse_reports` | 8 (workflow live; schedule + webhook + manual all firing) |

`dns_queries` was dropped on 2026-05-07 — domains are content, not metadata.

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

1. Frankfurt UDP fix (blocked on Vultr ticket)
2. Tokyo or other 3rd region node
4. tmpfs migration for ephemeral content per external-observer principle (Suricata payload audit, /var/log/suricata logrotate tuning)
5. n8n: import + activate the `eh-pulse-2h` workflow on the live instance, subscribe to ntfy topic from phone
6. Additional n8n workflows (deeper analysis, weekly summaries, action automation)
7. Claude API integration for deeper / on-demand analysis
8. pgvector memory layer
9. Voice ops interface (Vapi or Retell evaluation)

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
- Frankfurt: v3 bootstrap applied 2026-05-07; SSH is now key-only root, passwords disabled. Bootstrap was delivered via Vultr web console (operator ran `bash /root/eh-node-bootstrap.sh EH-VPS-FRANKFURT-EU1 192.248.187.208 wg1` after pulling the script from LA's temp HTTP server)
- LUKS passphrases backed up in operator's password manager as `EH-NVMe-LUKS` and `EH-HDD-LUKS`
- LUKS keyfiles on LA at `/root/.luks-eh-nvme` and `/root/.luks-eh-hdd` (auto-unlock)
- SSH key authorization for `fletch-desktop` workstation deployed to LA
- Fail2ban whitelist includes VPN tunnel range (`10.8.0.0/24`) and operator's PC home IP
- Grafana admin credential stored as `EH-Grafana-Admin` in password manager
- `grafana_reader` PostgreSQL role exists for read-only dashboard queries
- Leak-test pass on 2026-05-07 caught a DNS bypass: client config had `DNS = 1.1.1.1, 1.0.0.1` (queries went straight to Cloudflare, skipping the on-hub dnscrypt-proxy resolver rotation). Fixed by setting client `DNS = 10.8.0.1` and adding UFW rule allowing port 53 from the tunnel network — bootstrap script updated so future nodes don't ship with this gap
- Hub-side DNS was also broken on 2026-05-07: `/etc/resolv.conf` had `nameserver 0.0.0.0` (resolvconf populated it from dnscrypt-proxy's bind address). `dig` fell back to 127.0.0.1 silently, but Node.js / nodemailer queried 0.0.0.0:53 directly and timed out — which is why the Proxy Health Monitor n8n workflow had been failing every 5 min for hours. Fixed by writing `nameserver 127.0.0.1` to resolv.conf and `chattr +i` to lock it; bootstrap script updated.
- `iperf3` over WG measured ~55 Mbps up / ~360–420 Mbps down sustained between operator PC and LA hub; bandwidth ceiling is the home upload, not the VPN. Suricata-CPU is the realistic scale ceiling (~100 Mbps inspected per vCPU with the full 50k ruleset)
- Pulse workflow is live with three trigger paths: schedule (every 2h at :00 UTC), manual (n8n UI Execute Workflow), and webhook (POST to a path stored in operator's password manager — anyone with the URL can trigger a Claude API call, so treat as a secret)
- `EventHorizonVPN-Claude` n8n credential is the shared Anthropic API key used by both the Pulse and Proxy Health Monitor workflows
- Pulse alert calibration: 30-60 sessions / 50-200 GB inbound / ~2-5k events_total / ~400-700 events_high per 2h cycle is BASELINE for this hub. Honeypot + fail2ban events are routine. Alerts now fire only on critical events, anomalies, novel event_types, internal-IP source IPs, or genuinely anomalous volume
