# BHN Node Audit — 2026-05-28

Snapshot of every active BHN node taken during tonight's maintenance window
(Tor decommission + Grafana migration + LA egress lockdown + Netdata rollout).
Captured by walking each node and recording running services, listening ports,
firewall, WireGuard, RAM, disk, Docker containers, and scheduled jobs.

This file is a point-in-time source of truth. Nodes change — re-run the audit
script in `infrastructure/scripts/node-audit.sh` (TODO) any time the inventory
feels stale. Compare to repo for drift.

Hardware/OS, public IPs, and WireGuard fingerprints are stable; running
services and RAM are not — those reflect 2026-05-28 ~07:45 UTC only.

---

## Summary table

| Node | Hostname | Public IP | WG IP | vCPU | RAM | RAM used | RAM avail | Disk used | Role |
|------|----------|-----------|-------|------|-----|----------|-----------|-----------|------|
| **LA** | `vultr` | <BHN_LA_PUBLIC_IP> | 10.8.0.1 | 2 | 1.9 GB | 961 MB | 801 MB | 24/56 GB | Postgres master, n8n, HORIZON, Grafana (migrating), Redis, embeddings, mesh hub |
| **NJ** | `BHN-NEWJERSEY-US2` | <BHN_NJ_PUBLIC_IP> | 10.8.0.5 | 1 | 1.9 GB | 581 MB | 1.2 GB | 16/60 GB | Trading executor; Grafana NJ (new); spare capacity for Metabase |
| **Hillsboro** | `BHN-HILLSBORO-US3` | <BHN_HIL_PUBLIC_IP> | 10.8.0.6 | 2 | 1.9 GB | 993 MB | 764 MB | 7.6/38 GB | tinyproxy HTTP egress, Tor relay (BHNHeliosUS3), mesh-spoke |
| **Frankfurt** | `BHN-FRANKFURT-EU1` | 192.248.187.208 | 10.9.0.2 | 1 | 951 MB | 274 MB | 532 MB | 11/23 GB | SearXNG, Redis, LibreSpeed, SOCKS scrape egress (port 10808 via ssh tunnel from LA), Shadowsocks |

All four are Ubuntu 22.04.5 LTS. Kernels: LA/NJ/FRA at 5.15.0-177, Hillsboro at 5.15.0-164 (slightly behind — apply during next maintenance).

---

## Cross-cutting findings

### What this audit confirmed

- **Tor relay BHNFornaxEU1 (FRA) is fully decommissioned.** Marker file present at `/opt/bhn-tor-relay/DECOMMISSIONED-2026-05-28.txt`, no `tor` processes, no container. ~250 MB freed on FRA.
- **Grafana 13.0.1+security-01 is live on both LA and NJ.** NJ binds `10.8.0.5:3000`, UFW open to mesh only, all 10 dashboards provisioned. Datasource provisioning yaml in place but PG connection blocked at LA pg_hba.conf (see "Pending Decisions").
- **Netdata 1.x agent is running on every node.** Listening on `0.0.0.0:19999`; UFW restricts to `10.8.0.0/24` + `10.9.0.0/24`. Process RSS sits at 60–120 MB per node.
- **LA egress lockdown is wired but only partially active.** `/etc/environment`, `/etc/apt/apt.conf.d/95proxy`, and the systemd Docker drop-in are all in place; the Docker drop-in is staged but `systemctl daemon-reload && systemctl restart docker` has not run yet (would briefly stop n8n/redis/wallos).
- **Hillsboro Suricata is stopped + disabled, 2 GB swap added, swappiness=10.** Memory pressure resolved; box is healthy (993 MB used, 764 MB available).

### Surprises / drift from intent

- **Suricata is still running on LA, NJ, and FRA.** Only Hillsboro had it stopped. LA's Suricata at 466 MB virtual / ~21 MB resident is small; NJ's at 18 MB; FRA's at 21 MB. **Decision needed:** stop on all three? It is unclear what role Suricata plays in the BHN security stack (CrowdSec + fail2ban + WireGuard already provide IDS/IPS layers).
- **fail2ban is missing on Hillsboro.** Present on LA, NJ, FRA. Hillsboro relies on CrowdSec alone.
- **The `vultr` hostname on LA is non-canonical.** Other nodes follow `BHN-<CITY>-<COUNTRY><N>`. Rename to `BHN-LOSANGELES-US1`? Low-priority cosmetic.
- **`fwupd.service` runs on all 4 nodes.** Firmware update daemon on VPSes is essentially noise (no firmware to update). Candidate for disable: `systemctl disable --now fwupd`. Frees a few MB and removes a non-functional dependency.
- **`ModemManager.service` runs on LA, NJ.** Same reasoning — no modems on a VPS. Disable.
- **`packagekit.service` runs on all 4.** Used by GNOME software center; useless on a server. Disable.
- **`snapd.service` on LA, NJ.** Snap not used. Candidate for purge.
- **`multipathd` on every node.** No multipath storage on Vultr VPS; safe to disable (`systemctl disable --now multipathd`). Frees ~27 MB resident per node.
- **FRA's wg1 peer to 10.10.0.0/30** has an undocumented purpose. Captures 2.58 MB/714 KB — low traffic but unknown counterparty. Worth identifying.
- **Hillsboro Tor relay (BHNHeliosUS3) is the only Tor process left in the BHN fleet.** Its MyFamily list previously referenced BHNFornaxEU1 (now down). Tor's directory consensus will mark the dead fingerprint within ~24h; no immediate action required, but the torrc inside the Hillsboro Tor container could be scrubbed.
- **Hillsboro's `/etc/tor/torrc` is not present at the OS level** because Tor runs inside a Docker container (`docker-proxy` listening on 9001 + 9050). Config lives inside the container — to inspect: `docker exec <tor-container> cat /etc/tor/torrc`.

---

## NODE 1: LA (10.8.0.1, `vultr`)

**Role:** Primary mesh hub. PostgreSQL master. n8n orchestrator. HORIZON. Grafana (migrating out tonight). Redis cache. Local embedding service. Shadowsocks server. dnscrypt-proxy. Outbound SOCKS to FRA on 10808 for eBay scrape egress.

**Hardware:** AMD EPYC-Genoa, 2 vCPU, 1.9 GB RAM, 2.3 GB swap (863 MB used — high but not pathological), 56 GB OS disk + 100 GB NVMe hot tier + 399 GB HDD cold tier. Load 0.65 / 1.28 / 1.19.

**OS:** Ubuntu 22.04.5 LTS, kernel 5.15.0-177-generic. Uptime 17 days.

**Listening ports** (mesh-only unless noted):
- `0.0.0.0:22` SSH (UFW-open public)
- `0.0.0.0:8388` Shadowsocks server (UFW-open public)
- `0.0.0.0:19999` Netdata (UFW restricted to mesh)
- `10.8.0.1:53` dnscrypt-proxy (DNS-from-tunnel)
- `10.8.0.1:3000` Grafana
- `10.8.0.1:5432` PostgreSQL 14 (eventhorizon DB; 8 connections active)
- `10.8.0.1:6379` Redis (Docker)
- `10.8.0.1:8090` (docker-proxy — unknown service)
- `127.0.0.1:5679` HORIZON (Python MainThread)
- `127.0.0.1:6060,8080` CrowdSec
- `127.0.0.1:8001` Python (HORIZON-related?)
- `127.0.0.1:8125` Netdata StatsD
- `127.0.0.1:10808` SOCKS tunnel to FRA (`ssh -D`)
- `*:5678` n8n (LAN-wide bound — IPv6 catch-all)

**Active services** (28 total). Notable:
- `postgresql@14-main` — PG master, 8 connections, hosts `eventhorizon` DB
- `grafana-server` — 13.0.1+security-01 (migrating)
- `docker` + `containerd` — bhn-horizon-redis (Redis 7-alpine, 24h), bhn-wallos (3 days, healthy), n8n (2h)
- `eh-embed.service` — BGE-small-en-v1.5 fastembed (Python, port 8001)
- `dnscrypt-proxy` — local DNS resolver, 10.8.0.1:53 + 127.0.0.1:53
- `crowdsec` + `crowdsec-firewall-bouncer` — IPS layer
- `fail2ban`, `suricata` (still active), `netdata`, `shadowsocks-libev`, `vnstat`
- `unattended-upgrades`, `cron`, `watchdog`

**Docker containers** (3):
- `bhn-horizon-redis` (redis:7-alpine, 24h uptime)
- `bhn-wallos` (bellamy/wallos:latest, 3 days, healthy)
- `n8n` (n8nio/n8n:latest, 2h uptime — `/healthz` returns `ok`)

**Top RAM consumers (resident):**
1. n8n — 190 MB
2. grafana-server — 187 MB (will drop to 0 after NJ cutover)
3. netdata go.d.plugin — 119 MB
4. netdata — 116 MB
5. crowdsec — 97 MB

**Scheduled jobs (`/etc/cron.d/`):** 18 BHN data collectors (CoinGecko, EIA, FRED, USDA, vnstat, conntrack, db-size, DNS log, docker stats, fail2ban, freshness, iptables, n8n stats, PG stats, resource, security events, Tor metrics, WG stats). All under the `bhn-*-collector` / `bhn-*-poller` naming scheme. Plus `certbot`.

**UFW (26 rules):** mesh-only on 3000 / 5432 (from FRA + NJ), 5678 (n8n), 53 DNS; public 22 / 51820 / 8388. Egress restricted to specific endpoints (FRA 51821, NJ 51820, Hillsboro 51821, DNS 53, NTP 123). `wg1` (port 51822) interface also listening — purpose?

**WireGuard (`wg0` only listed in audit):**
- Self pubkey: `TOYnFt18v4NynEN91o6zkmV5hsvHBLJTb8qL7GG/KAo=`
- Peer 10.8.0.5 (NJ, `ylnSJOq...`) — 51 MB rx / 44 MB tx
- Peer 10.8.0.4 (operator workstation, `y+ekkxK...`) — 5.5 GB rx / 38 GB tx
- Peer FRA via wg1 (`zkfJNbd...`, allowed 0.0.0.0/0) — 2.3 GB rx / 252 MB tx
- Peer 10.8.0.2 (`N9Tg0dO...`, `68.96.70.83:51352`) — 1.67 GB rx / 19.3 GB tx (second operator endpoint?)
- Peer Hillsboro (`EwBHwk...`, allowed `10.8.0.6/32, 10.8.0.0/24`) — 313 MB rx / 8 MB tx

**Public-facing UFW exposure:** ports 22 (SSH), 51820 (WG), 8388 (Shadowsocks). All other public ingress is denied. wg1 listener on 51822 is also exposed (peer-to-peer).

**Packages:** 953 installed (dpkg-l count).

---

## NODE 2: NJ (10.8.0.5, `BHN-NEWJERSEY-US2`)

**Role:** Trading executor (Alpaca strategies). New home of Grafana as of 2026-05-28. Spare capacity for Metabase install (Track 3, deferred).

**Hardware:** Intel Skylake, 1 vCPU, 1.9 GB RAM, 6.2 GB swap (184 MB used — very healthy), 60 GB OS disk. Load 0.91 / 0.38 / 0.16 (recent install activity).

**OS:** Ubuntu 22.04.5 LTS, kernel 5.15.0-177-generic. Uptime 16 days.

**Listening ports:**
- `0.0.0.0:2222` SSH (non-standard port)
- `0.0.0.0:19999` Netdata (UFW mesh-only)
- `10.8.0.5:3000` Grafana (UFW mesh-only)
- `127.0.0.1:4317` `otel-plugin` (OpenTelemetry gRPC collector — unknown purpose / not documented?)
- `127.0.0.1:6060,8080` CrowdSec
- `127.0.0.1:8125` Netdata StatsD
- `127.0.2.1:53` dnscrypt-proxy + `127.0.0.53` systemd-resolved (split DNS stack)

**Active services** (24 total). Notable:
- `grafana-server` (13.0.1+security-01) — newly installed tonight
- `netdata` — newly installed tonight
- `crowdsec`, `fail2ban`, `suricata` (still active), `vnstat`, `dnscrypt-proxy`
- `unattended-upgrades`, `cron`, `watchdog`

**Docker:** not installed (notable — NJ is currently container-free).

**Top RAM:**
1. grafana-server — 292 MB
2. crowdsec — 124 MB
3. netdata go.d.plugin — 108 MB
4. netdata — 64 MB
5. suricata — 18 MB

**UFW (13 rules):** mesh-only on 3000 (Grafana); public 22 (legacy), 2222 (SSH), 51820 (WG). 2222 also restricted to 10.8.0.0/24 in a redundant rule. IPv6 rules mirror.

**WireGuard (`wg0`):**
- Self pubkey: `ylnSJOqwkqrNZwt/saJdqoMG7j3l35hoUk+zejru1Sk=`
- Single peer: LA (`TOYnFt...` at `<BHN_LA_PUBLIC_IP>:51820`, allowed `10.8.0.0/24`) — 44 MB rx / 56 MB tx

**Public-facing UFW exposure:** ports 22, 2222, 51820. Everything else mesh-only.

**Env file (`/etc/bhn-trading/env`, mode 0600):** Alpaca credentials (3 keys + per-strategy), Postgres connection vars (back to LA), TRADING_LIVE_MODE flag, FMP API key. Pending: `GF_DATABASE_GRAFANA_READER_PASSWORD` (from Proton Pass).

---

## NODE 3: HILLSBORO (10.8.0.6, `BHN-HILLSBORO-US3`)

**Role:** HTTP egress proxy (tinyproxy on `10.8.0.6:8888`), Tor relay (BHNHeliosUS3, in Docker), mesh spoke. Acts as LA's outbound HTTP funnel for the egress-lockdown initiative.

**Hardware:** AMD EPYC-Rome, 2 vCPU, 1.9 GB RAM, 2 GB swap (added 2026-05-28; 2 MB used), 38 GB disk. swappiness=10. Load 0.27 / 0.11 / 0.04 — quiet box. Uptime 15 days.

**OS:** Ubuntu 22.04.5 LTS, kernel 5.15.0-164-generic *(behind LA/NJ/FRA — apply 5.15.0-177 during next maintenance)*. 

**Listening ports:**
- `0.0.0.0:22` SSH (public)
- `0.0.0.0:53` dnscrypt-proxy
- `0.0.0.0:9001` Tor ORPort (docker-proxy)
- `0.0.0.0:19999` Netdata (mesh-only)
- `10.8.0.6:8888` tinyproxy (mesh-only — `Allow 10.8.0.0/24`)
- `10.8.0.6:9050` Tor SOCKS (docker-proxy, mesh-only)
- `127.0.0.1:6060,8080` CrowdSec

**Active services** (22 total). Notable:
- `tinyproxy` — HTTP proxy (port 8888, listens on 10.8.0.6)
- `docker` + `containerd` — Tor container running
- `crowdsec` + `crowdsec-firewall-bouncer`, `dnscrypt-proxy`, `netdata`
- `suricata` — **stopped + disabled** (intentional, 2026-05-28)
- `atd`, `cron`, `irqbalance`, `qemu-guest-agent`
- **no `fail2ban`** (anomalous — every other node has it)

**Top RAM:**
1. `tor` (inside container) — 578 MB (29.4% — biggest single consumer)
2. crowdsec — 178 MB
3. netdata, dockerd, containerd, dnscrypt-proxy — modest

**Tor relay:** `BHNHeliosUS3`. Runs in a Docker container (no `/etc/tor/torrc` on host). MyFamily previously listed `BHNFornaxEU1` (decommissioned today). Tor's directory consensus will mark Fornax down within ~24h; no urgent action.

**UFW (21 rules):** in 22 (public), 51821/udp + 8388 from LA only, 22 from `10.9.0.0/24` (FRA mesh), 9001 (Tor public). Outbound restricted to DNS, NTP, 443, plus specific peer endpoints. Tinyproxy rule explicit: `10.8.0.6:8888 ALLOW IN 10.8.0.0/24`.

**WireGuard (`wg0`):**
- Self pubkey: `EwBHwkT4iJXzhJZMvtlo70NOLx+wPv8IXmAGSa89zBg=`
- Peer LA (`TOYnFt...`, allowed `10.8.0.0/24`) — 8.2 MB rx / 313 MB tx
- Peer `V3RenHJ...` (allowed `10.10.0.0/30`, endpoint same `<BHN_LA_PUBLIC_IP>:51822`) — **purpose unknown, low traffic 2.58 MB / 714 KB**

**Public-facing UFW exposure:** ports 22 (SSH), 9001 (Tor ORPort). 51821 + 8388 are restricted to LA only. Everything else mesh- or peer-restricted.

---

## NODE 4: FRANKFURT (10.9.0.2, `BHN-FRANKFURT-EU1`)

**Role:** EU-side spoke. SearXNG search aggregator, Redis cache, LibreSpeed bandwidth test, Shadowsocks server, SOCKS scrape-egress endpoint for the eBay scraper (operator runs `ssh -D 10808` from LA). Formerly hosted Tor relay BHNFornaxEU1 (decommissioned 2026-05-28).

**Hardware:** Intel Haswell, 1 vCPU, **951 MB RAM** (smallest node), 2.3 GB swap (414 MB used — moderate, baseline from pre-Tor-decom pressure), 23 GB disk. Load 0.14 / 0.10 / 0.09 — idle. Uptime 21 days.

**OS:** Ubuntu 22.04.5 LTS, kernel 5.15.0-177-generic.

**Listening ports:**
- `0.0.0.0:22, :2222` SSH (both ports open)
- `0.0.0.0:8388` Shadowsocks
- `10.9.0.2:8088, :8089` docker-proxy (SearXNG + LibreSpeed)
- `0.0.0.0:19999` Netdata (mesh-only)
- `127.0.0.1:6060,8080` CrowdSec
- `127.0.0.53` systemd-resolved

**Active services** (24 total). Notable:
- `docker` + `containerd` — 3 containers running
- `crowdsec`, `crowdsec-firewall-bouncer`, `fail2ban`, `dnscrypt-proxy`, `suricata` (still active), `netdata`, `shadowsocks-libev`
- `unattended-upgrades`, `cron`, `watchdog`
- **No `cron.d/bhn-*` collectors** (FRA isn't a collector host)

**Docker containers** (3 unique; the audit script over-counted via repeated runs):
- `bhn-searxng` (searxng/searxng:latest, 2 weeks uptime)
- `bhn-searxng-redis` (redis:alpine)
- `bhn-librespeed` (ghcr.io/linuxserver/librespeed:latest)

**Decommissioned today:**
- `bhn-tor-relay` (the BHNFornaxEU1 stack) — `docker compose down`, volumes (tor-data, tor-logs) preserved. Marker at `/opt/bhn-tor-relay/DECOMMISSIONED-2026-05-28.txt`.

**Top RAM:**
1. crowdsec — 60 MB
2. dockerd — 35 MB
3. multipathd — 27 MB (unnecessary on VPS — disable candidate)
4. suricata — 21 MB
5. containerd — 21 MB
6. netdata (~80 MB, ranked lower because of CPU/mem weighting)

**UFW (10 rules):** in 22, 2222, 9001 (Tor — still allowed though container is gone — safe to remove), 51821 + 8388 from LA only. Forwarding via `wg1` to `enp1s0`.

**WireGuard (`wg1`):**
- Self pubkey: `zkfJNbdL9Ptdxv+fxwV2e1q0mbCR5Z/9T80QanSxKA8=`
- Single peer: LA (`TOYnFt...` at `<BHN_LA_PUBLIC_IP>:51820`, allowed `10.8.0.0/24`) — 251 MB rx / 2.35 GB tx (the 2.35 GB is mostly SOCKS scrape egress traffic)

**Public-facing UFW exposure:** 22, 2222, 9001 (now orphan rule — remove), 8088/8089 are mesh-only via WG. Everything else WG-restricted.

---

## Pending Decisions (operator action required)

1. **LA pg_hba.conf entry for NJ.** Grafana on NJ cannot query `eventhorizon` until LA's PG allows `10.8.0.5/32` as `grafana_reader`. Adding the line touches PG — operator's "do not touch" constraint requires explicit approval.
2. **`EH-PG-grafana_reader` password for NJ env file.** Append to `/etc/bhn-trading/env` as `GF_DATABASE_GRAFANA_READER_PASSWORD=<value>`.
3. **Suricata on LA / NJ / FRA — stop and disable?** Only Hillsboro had it removed. Decision pending whether the CrowdSec + fail2ban + WireGuard layers are sufficient.
4. **Fail2ban missing on Hillsboro.** Add or accept?
5. **wg1 peer `V3RenHJ...` on Hillsboro (allowed 10.10.0.0/30) — who is it?** Document or remove.
6. **LA wg1 listener on port 51822** — what is this interface for? Not documented anywhere visible.
7. **LA rename from `vultr` to `BHN-LOSANGELES-US1`?** Hostname mismatch with other nodes. Cosmetic.

## Recommended cleanups (low-priority)

- Disable `fwupd`, `ModemManager`, `multipathd`, `packagekit`, `snapd` on every VPS — frees ~30–50 MB and reduces attack surface.
- Hillsboro kernel: catch up to 5.15.0-177 on the next reboot maintenance.
- FRA UFW rule 6 (`9001/tcp ALLOW IN`) — Tor relay is gone, drop the rule.
- LA: bhn-tor-metrics-poller cron may now be polling a dead Tor. Disable or repoint.
- Cleanup `/opt/bhn-tor-relay/` on FRA (volumes preserved) once 48h rollback window passes.

---

## Verification commands (to re-run the audit)

```bash
# Per-node baseline
ssh root@<host> '
  echo "=== $(hostname) ==="
  uname -a; uptime; free -h
  df -h | grep -vE "(tmpfs|udev|loop)"
  ss -tlnp 2>&1 | awk "NR==1 || /LISTEN/" | head -30
  systemctl list-units --type=service --state=running --no-legend
  docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}" 2>&1
  ufw status numbered
  wg show
'
```

To produce a fresh audit doc: re-run the four ssh blocks captured in the conversation that produced this file, then synthesize.
