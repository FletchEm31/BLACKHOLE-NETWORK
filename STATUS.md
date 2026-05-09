# EventHorizon — Network Status

Last updated: **2026-05-08**

## Phase progress

```
Phase 1: NETWORK              ████████░░  ~85%
Phase 2: DASHBOARD            ███████░░░  ~75%
Phase 3: AI INTEGRATION       ███░░░░░░░  ~30%
```

## Nodes

### LA Hub — `EH|VPS-LOSANGELES-US1` ✅ Operational

| Component | Status |
|-----------|--------|
| IP | `149.28.91.100` |
| Specs | 2 vCPU, 2 GB RAM, 60 GB system NVMe |
| OS | Ubuntu 22.04.5 LTS |
| WireGuard server (port 51820) | ✅ Active |
| Shadowsocks (port 8388) | ✅ Active (password rotated 2026-05-08) |
| dnscrypt-proxy | ✅ Active |
| Fail2ban (lean: sshd + grafana + postgresql + n8n jails, VPN-whitelisted) | ✅ Active |
| CrowdSec (linux + sshd + nginx + http-cve collections, cs-firewall-bouncer) | ✅ Active |
| Suricata IDS | ✅ Active (49,955 rules) |
| Honeypots | ⛔ Removed 2026-05-08 (low signal, ehuser PG creds in source) |
| UFW firewall | ✅ Configured. **Outbound default-deny** since 2026-05-08, whitelist: 53/udp+tcp, 123/udp (NTP), 443/tcp (HTTPS — apt mirrors converted to HTTPS, certbot, dnscrypt-proxy DoH, n8n→Anthropic API, CrowdSec central), 587/tcp (SMTP submission), 51821/udp→192.248.187.208 (FRA wg underlay), 10.9.0.0/24 (LA→FRA via tunnel). ICMP egress and outbound SSH intentionally blocked — use `apt`/HTTPS git instead of git+ssh. |
| **NVMe block storage** (`SSD-LOSANGELES-US1`, 101 GB) | ✅ LUKS2 encrypted, XFS, mounted |
| **HDD block storage** (`HDD-LOSANGELES-US1`, 399 GB) | ✅ LUKS2 encrypted, XFS, mounted |
| PostgreSQL 14 | ✅ Running on encrypted NVMe |
| Grafana | ✅ VPN-only access at `http://10.8.0.1:3000` |
| SSH hardening | ✅ Key-only root, passwords disabled |
| iptables ACCEPT for VPN→SSH | ✅ Persisted |
| Reboot survival | ✅ Verified |

### Frankfurt — `EH|VPS-FRANKFURT-EU1` ✅ Operational

| Component | Status |
|-----------|--------|
| IP | `192.248.187.208` |
| OS | Ubuntu 22.04 |
| v3 bootstrap applied | ✅ 2026-05-07 (via Vultr web console; SSH key auth confirmed working) |
| SSH | ✅ Key-only root, passwords disabled |
| WireGuard installed + configured | ✅ Local config + LA hub registration in place (peer key `zkfJNbdL9Ptdxv...KA8=`, allowed-ips 10.9.0.2/32) |
| WireGuard tunnel handshake | ✅ 2026-05-07 — Vultr UDP path restored, tunnel up, 140 ms LA↔Frankfurt RTT, 0% loss bidirectional |
| Shadowsocks (8388) | ✅ Password rotated 2026-05-08 |
| dnscrypt-proxy | ✅ |
| Fail2ban | ⛔ Removed 2026-05-08 (was already inactive on a clean v3 bootstrap — silent gap) |
| CrowdSec + cs-firewall-bouncer-iptables (linux + sshd collections) | ✅ Installed 2026-05-08 |
| Suricata IDS (49,968 rules, listening on enp1s0) | ✅ Installed 2026-05-08 |
| Root password rotated away from `EventHorizon2026` | ✅ 2026-05-08 |
| UFW + iptables | ✅ Pruned 2026-05-08 — removed `51820/udp Anywhere` (wrong port, FRA uses 51821), `51821/udp Anywhere` (redundant with LA-scoped rule), `8443/tcp Anywhere` (nothing listening), `Anywhere from 149.28.91.100` (over-grant). Final rule set: 22/tcp anywhere, 51821/udp from LA, 8388 from LA, wg1→enp1s0 FWD. Note: sshd still listens on 80+443 with no UFW allow → those alt-ports unreachable. SSH-config cleanup deferred (operator confirmation required). |
| **Vultr support ticket** | ✅ Resolved 2026-05-07. UDP path restored. Required two follow-up fixes after handshake landed: (a) Frankfurt's `wg1.conf` had stale `AllowedIPs = 10.9.0.0/30` from a prior install — the latest bootstrap wrote correct config but didn't down/up wg1, so kernel kept the old mapping. Bootstrap script patched to `wg-quick down && up` after writing config. (b) UFW route-allow added on `wg1 → enp1s0` so peer traffic can egress to internet (same FORWARD-chain default-deny gap LA had earlier today) |

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
| n8n install | ✅ VPN-only access at `http://10.8.0.1:5678` (mirrors Grafana). Public hostname `n8n.eventhorizonvpn.com` deprecated 2026-05-08, LE cert deleted, nginx site removed. **DNS A record at provider should be removed by operator.** |
| n8n: `EH Network Pulse - 2h` workflow | ⏸️ Deactivated 2026-05-07 to reduce Anthropic API spend. Workflow logic intact (Sonnet 4.6, structured outputs, semantic memory retrieval + ingestion), webhook still registered for ad-hoc trigger. Re-enable by flipping `active=1` and restarting n8n |
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

- **Vultr LA UDP filtering** — ✅ Resolved 2026-05-07. Path restored. Frankfurt tunnel is up and bidirectional ping is clean.

## Pending build items

In rough order:

1. Tokyo or other 3rd region node
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

- **2026-05-08 rotation pass** — Frankfurt root password, Shadowsocks password (both nodes, shared), and n8n admin password all rotated. New values stored in operator's password manager only; no plaintext on disk or in repo. Old defaults (`EventHorizon2026` family) are dead.
- **2026-05-08 Frankfurt security delta** — fail2ban removed (was inactive on a clean v3 bootstrap — silent gap), CrowdSec + cs-firewall-bouncer-iptables installed, Suricata 6.0.4 with 49,968 rules listening on enp1s0
- **2026-05-08 LA cleanup** — `eh-honeypot.service` (custom python listener on 2222/3306/6379/8081) removed along with its UFW rules. fail2ban stripped to a lean 4-jail config: sshd, grafana, postgresql, n8n. VPN whitelist (`10.8.0.0/24`, `10.9.0.0/24`) added back; was missing on the prior config
- **2026-05-08 bootstrap v3 revision** — fail2ban removed from script, CrowdSec install added. Suricata intentionally NOT in bootstrap (per-node based on capacity). `SS_PASSWORD` no longer hardcoded; per-node random by default with env-var override
- **2026-05-08 PostgreSQL `ehuser` rotation** — done. ALTER ROLE'd to fresh 32-char random; updated dependents:
  - `/usr/local/bin/eh-security-collector.py` (cron `*/5 * * * *` in `/etc/crontab`) — was hardcoded `EventHorizon2026`
  - `/usr/local/bin/eh-dns-collector.py` (cron `*/5 * * * *` in `/etc/crontab`) — was hardcoded `EventHorizon2026`
  - `/root/.eh-metadata.env` `EH_METADATA_PG_PASSWORD` (read by `eh-metadata-collector.py`, cron `*/30 * * * *`)
  - n8n + Grafana + `eh-purge.sh` confirmed NOT to use `ehuser` — n8n uses `n8n_user` + `agent_reader`, Grafana uses `grafana_reader`, eh-purge uses peer auth as `postgres`. So no n8n/Grafana cred edits needed for this rotation. Verified pulse webhook + Grafana dashboard still load post-rotation
  - PG log auth-failures stopped at 13:01 UTC — 13:00:01 cron tick was the last gasp before sed completed; 13:05+ ticks all clean
- **2026-05-08 operator-pc WG tunnel** — fixed. The local `eventhorizon` tunnel's `[Peer]` block had all four fields wrong simultaneously (`PublicKey` was `BlWh02K4o…` — not LA's hub key; `AllowedIPs` was `149.28.91.100/32` instead of `10.8.0.0/24`; `Endpoint` was `10.8.0.1:22` instead of `149.28.91.100:51820`; `PresharedKey` missing). The combination explains why `ssh root@10.8.0.1` had been timing out and why LA's last successful handshake from this peer was timestamped 14h ago: handshake initiations were going to a tunnel-internal address that requires the tunnel to already be up. Replaced the entire `[Peer]` block with correct values and the handshake came back in <5 seconds. Since `iperf3 ~55 Mbps up` measurements from earlier STATUS.md notes couldn't have come from this config, the corruption likely happened recently — possibly during the 2026-05-07 PSK rollout when the operator was editing the file manually
- **2026-05-09 operator-pc full key rotation** — fresh keypair + PSK generated for operator-pc after the previous PSK was inadvertently surfaced in chat output. Hub `wg0` peer entry (registered pubkey: `y+ekkxKZsCn9LERiQ3unZxn2zDjsS1yqbz12limv1kA=`) carries `allowed-ips 10.8.0.2/32, 10.8.0.4/32` so the workstation can address either tunnel slot. Client now runs **two profiles, same keypair, different `AllowedIPs`**: split (`10.8.0.0/24, 10.9.0.0/24`) for admin work and full (`0.0.0.0/0` + `DNS = 10.8.0.1`) for untrusted networks. IPv6 left out of full-tunnel AllowedIPs deliberately — WireGuard for Windows kill-switch errors v6 instead of leaking. Workstation tunnel IP is now `10.8.0.4`; previous key (`1Slpqh…`) and PSK are dead. Rotation runbook (with the race-condition lesson — allowed-ips uniqueness causes implicit moves, so destructive WG ops MUST run via public-IP SSH not via the tunnel itself) at `infrastructure/bootstrap/docs/wg-key-rotation.md`
- **Outstanding follow-ups (flagged 2026-05-08, not yet acted on)**:
  1. Frankfurt UFW has manually-added rules contradicting bootstrap intent: `51820/udp Anywhere`, `51821/udp Anywhere`, `8443/tcp Anywhere`, `Anywhere ALLOW IN from LA`. Source of "server is exposed" feeling. Awaiting decision on which to prune
- LA root password rotated on 2026-05-07 (stored in operator's password manager)
- Frankfurt: v3 bootstrap applied 2026-05-07; SSH is now key-only root, passwords disabled. Bootstrap was delivered via Vultr web console (operator ran `bash /root/eh-node-bootstrap.sh EH-VPS-FRANKFURT-EU1 192.248.187.208 wg1` after pulling the script from LA's temp HTTP server)
- LUKS passphrases backed up in operator's password manager as `EH-NVMe-LUKS` and `EH-HDD-LUKS`
- LUKS keyfiles on LA at `/root/.luks-eh-nvme` and `/root/.luks-eh-hdd` (auto-unlock)
- **WireGuard PreSharedKeys deployed network-wide 2026-05-07** — every peer-pair now negotiates with an additional symmetric secret in addition to the X25519 ECDH handshake. Mitigates "harvest now, decrypt later" quantum attacks on the key exchange. PSK files at `/etc/wireguard/psk/{frankfurt,operator-pc,device-2,device-3}.psk` on LA hub (chmod 600). Frankfurt has the LA PSK at `/etc/wireguard/psk-la.psk`. Operator's three personal-device PSKs are also in his password manager (`EH-WG-PSK-PC`, `EH-WG-PSK-Device2`, `EH-WG-PSK-Device3`)
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
