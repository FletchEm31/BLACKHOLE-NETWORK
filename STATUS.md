# Blackhole Network (BHN) — Network Status

Last updated: **2026-05-13**

> **Note:** Project renamed 2026-05-11 from EventHorizon → Blackhole Network. Vultr-side server display names updated to `BHN|VPS-LOSANGELES-US1`, `BHN|VPS-FRANKFURT-EU1`, `BHN|VPS-NEWJERSEY-US2`. Intentionally preserved as live-system identifiers (NOT renamed): n8n credential names (`Postgres EventHorizon`, `EventHorizonVPN-Claude`), Proton Pass entries (`EH-*`), LA-deployed script paths (`/usr/local/sbin/eh-purge`, `/opt/eh-diagnostics/eh-*`), the PostgreSQL database name `eventhorizon`, and the email domain `eventhorizonvpn.com`. LA-side script renames are deferred to a coordinated migration session. The "EventHorizon VPN" name is reserved for the future separate commercial product.

## Phase progress

```
Phase 1: NETWORK              ████████░░  ~85%   (LA + FRA + NJ all live; Frankfurt exit routing applied but BROKEN — FRA MASQUERADE missing, deferred to next session)
Phase 2: DASHBOARD            ████████░░  ~80%
Phase 3: AI INTEGRATION       ████░░░░░░  ~40%   (pgvector memory live, JARVIS→HORIZON rename done, voice/SMS/calling pending A2P 10DLC)
Phase 4: PER-NODE SERVICES    ███████░░░  ~70%   (Wallos + SearXNG + LibreSpeed FRA deployed; LibreSpeed LA + Tor relays pending)
Phase 5: RESILIENCE           ░░░░░░░░░░   designed (Sweden cold standby — Bahnhof hosting, deferred)

Trading framework (NJ workstream, separate from 5-phase plan)  █████████░  ~85%
  → All 5 strategies + trading_core + killswitch + daily_summary + reconciliation_daemon
    committed. Rules schema/validator/example expanded with per-strategy execution_limits +
    per-strategy broker isolation + free-tier FMP path for strategy_value (2026-05-13).
    Pending: systemd units, NJ deployment, runbooks, 5 Alpaca paper accounts + per-strategy env files.
```

## Nodes

### LA Hub — `BHN|VPS-LOSANGELES-US1` ✅ Operational

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
| UFW firewall | ✅ Configured. **Outbound default-deny** since 2026-05-08, whitelist: 53/udp+tcp, 123/udp (NTP), 443/tcp (HTTPS — apt mirrors converted to HTTPS, certbot, dnscrypt-proxy DoH, n8n→Anthropic API, CrowdSec central), 587/tcp (SMTP submission), per-peer pairs for each WG server peer: 51821/udp→`192.248.187.208` + `10.9.0.0/24` (FRA), 51820/udp→`140.82.4.35` + `10.8.0.5` (NJ, added 2026-05-12), 51821/udp→`5.78.94.237` + `10.8.0.6` (Hillsboro, added 2026-05-13). ICMP egress and outbound SSH intentionally blocked — use `apt`/HTTPS git instead of git+ssh. |
| **NVMe block storage** (`SSD-LOSANGELES-US1`, 101 GB) | ✅ LUKS2 encrypted, XFS, mounted |
| **HDD block storage** (`HDD-LOSANGELES-US1`, 399 GB) | ✅ LUKS2 encrypted, XFS, mounted |
| PostgreSQL 14 | ✅ Running on encrypted NVMe |
| Grafana | ✅ VPN-only access at `http://10.8.0.1:3000` |
| SSH hardening | ✅ Key-only root, passwords disabled |
| iptables ACCEPT for VPN→SSH | ✅ Persisted |
| Reboot survival | ✅ Verified |
| Kernel | `5.15.0-177-generic` (last apt-upgrade pass 2026-05-12 — crowdsec 1.7.7→1.7.8, no kernel update available) |
| CVE-2026-31431 (algif_aead "Copy Fail") | ✅ Mitigated — module blacklisted in `/etc/modprobe.d/disable-algif_aead.conf` + `/etc/modprobe.d/blacklist.conf`; persistent across reboot; revisit when Canonical ships kernel backport |
| Wallos (subscription tracker, Docker, VPN-only, PG-connected) | ✅ Deployed 2026-05-12 at `http://10.8.0.1:8090` |
| LibreSpeed (US-West speed test endpoint) | 🔨 Not yet deployed — port `:8088` planned at `http://10.8.0.1:8088` |

### Frankfurt — `BHN|VPS-FRANKFURT-EU1` ✅ Operational *(exit + privacy node)*

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
| Kernel | `5.15.0-177-generic` (last apt-upgrade pass 2026-05-12 — crowdsec 1.7.7→1.7.8, no kernel update available) |
| CVE-2026-31431 (algif_aead "Copy Fail") | 🆗 Mitigated — module blacklisted in 3 `/etc/modprobe.d/` files; persistent across reboot |
| Exit-routing for operator "full" profile | 🅱️ **Deprioritized to backlog 2026-05-13** — see `infrastructure/docs/frankfurt-exit-backlog.md`. Full-tunnel profile broken (no internet); 2026-05-13 debugging attempts removed LA's unconditional MASQUERADE PostUp + tried fwmark-conditional routing, none worked. Cleanup tool `scripts/bhn-la-restore.sh` (commit `d1732b9`) restores LA's general egress. FRA-side MASQUERADE (`scripts/bhn-frankfurt-masquerade-fix.sh apply`) is the load-bearing piece per earlier diagnosis and should be applied FIRST on the next fresh attempt. Until then: `EH-admin` (split-tunnel) works; clients on `EH-full` should switch back. |
| SearXNG (private meta-search, Docker, VPN-only) | ✅ Deployed 2026-05-12 at `http://10.9.0.2:8089` |
| Tor bridge/relay (non-exit middle relay, `BHNFornaxEU1`, 1 MB/s rate, 1500 GB/month cap) | ✅ Deployed 2026-05-11 at `/opt/bhn-tor-relay/`. Renamed from `BHNFrankfurt` → `BHNFornaxEU1` 2026-05-13 (fingerprint preserved, ~2-min outage during rebuild; original astro-with-hyphen format `BHNFornax-EU1` rejected by Tor — see commit `d47a5d6`). SocksPort `10.9.0.2:9050` available for local SearXNG. MyFamily empty until peer fingerprints exist. |
| LibreSpeed (EU speed test endpoint) | ✅ Deployed 2026-05-12 at `http://10.9.0.2:8088` |

### New Jersey — `BHN|VPS-NEWJERSEY-US2` ✅ Operational (trading node)

| Component | Status |
|-----------|--------|
| IP (public) | `140.82.4.35` |
| Tunnel IP | `10.8.0.5` (on LA's `wg0` hub as peer — NOT separate wg2) |
| SSH from LA | ✅ `ssh nj` alias → `root@10.8.0.5:2222` (orchestrator key `/root/.ssh/eh_frankfurt`) |
| WireGuard tunnel | ✅ Operational 2026-05-12 — 58 ms LA↔NJ RTT, 0% loss |
| WG resolution | Required two LA-side UFW egress rules: `allow out to 140.82.4.35 port 51820 proto udp` (underlay) + `allow out to 10.8.0.5` (inner tunnel). Symptom before fix: handshake worked, NJ→LA traffic worked, LA→NJ dropped at LA's `ufw-after-output` default-DROP. |
| Hardening | ✅ SSH key-only, UFW + iptables, fail2ban, CrowdSec, Suricata, dnscrypt-proxy |
| Nightly diagnostic enrollment | ✅ Added to `bhn-nightly-diagnostic.sh` REMOTE_NODES |
| Trading workloads (FMP, Congress.gov, Polymarket, Kalshi, Alpaca paper) | 🔨 Framework committed to repo 2026-05-12 (`scripts/trading/`): 5 strategy files + trading_core + master_killswitch + daily_summary + reconciliation_daemon. NJ deployment pending — rules schema/validator + systemd units + runbooks still to ship before strategies can run. |
| LibreSpeed (US-East speed test endpoint) | 🔨 Planned (Phase 4 backlog) |
| Tor bridge/relay (non-exit middle relay, BHNNebulaUS2, 512 KB/s + 750 GB/month cap; pairs with FornaxEU1 + HeliosUS3 via MyFamily — see `infrastructure/docs/tor-relay-naming.md`) | 🔨 Planned (Phase 4 backlog) |
| LUKS2 storage | ⚠️ Not yet — NJ has no persistent sensitive data yet; revisit when trading rules / fill history lands |

### Hillsboro — `BHN-HILLSBORO-US3` ✅ Operational (LA egress proxy + planned Tor relay)

| Component | Status |
|-----------|--------|
| IP (public) | `5.78.94.237` (Hetzner Cloud, US-WEST — provider diversity from Vultr) |
| Tunnel IP | `10.8.0.6` (on LA's `wg0` hub as peer — flat /24, NJ-style, set via `TUNNEL_IP_OVERRIDE=10.8.0.6`) |
| SSH from LA | ✅ `ssh hillsboro` alias → `root@10.8.0.6:22` (key `/root/.ssh/id_ed25519`, added 2026-05-13) |
| WireGuard tunnel | ✅ Operational 2026-05-13 — ~27 ms LA↔Hillsboro RTT |
| WG resolution | Same NJ-style gap (line 83): LA's `deny (outgoing)` default + no rule for Hillsboro = packets dropped at OUTPUT before reaching wg0. Two LA-side UFW egress rules added: `allow out to 5.78.94.237 port 51821 proto udp` + `allow out to 10.8.0.6`. Bootstrap script now emits these as paste-ready in its final summary (commit `27ac0db`) so future nodes don't need this manual fix. |
| Hardening | ✅ SSH key-only on 22 (Hetzner default kept), UFW + iptables, fail2ban, CrowdSec, Suricata, dnscrypt-proxy (same 6 resolvers as LA: cloudflare, quad9-dnscrypt-ip4-filter-pri, mullvad, adguard, nextdns, digitale-gesellschaft) |
| Bootstrap quirks | (a) `chattr +i /etc/resolv.conf` fails on Hetzner Cloud — fs doesn't support immutable flag. Made non-fatal in `dnscrypt.sh` (commit `f3cce93`); systemd-resolved + resolvconf disabled so drift shouldn't occur. (b) `TUNNEL_IP_OVERRIDE` env var added (commit `8ac85f4`) to join LA's flat /24 instead of getting a derived /24 — same pattern operator established with NJ. |
| Nightly diagnostic enrollment | ✅ Added to `/opt/eh-diagnostics/eh-nightly-diagnostic.sh` REMOTE_NODES 2026-05-13 (correction: prior STATUS.md cited `/root/bhn-nightly-diagnostic.sh` but actual path is `eh-` prefix in `/opt/eh-diagnostics/`) |
| LA pubkey authorized | ✅ LA's `/root/.ssh/id_ed25519.pub` (`root@vultr`) added to Hillsboro's `/root/.ssh/authorized_keys` 2026-05-13 — required for both the `ssh hillsboro` alias AND the nightly diagnostic to authenticate |
| **tinyproxy (LA egress proxy)** | ✅ Deployed 2026-05-13 at `10.8.0.6:8888`. Binds to WG tunnel only, ACL'd to `10.8.0.0/24`, `DisableViaHeader`, `ConnectPort 443` only. UFW restricts to 10.8.0.0/24. Verified: `curl -x http://10.8.0.6:8888 https://api.ipify.org` from LA returns `5.78.94.237`. Service files at `infrastructure/services/tinyproxy/` (`install.sh` is idempotent). |
| **Tor relay (BHNHeliosUS3, 512 KB/s, 750 GB cap)** | ✅ Deployed 2026-05-13 at `/opt/bhn-tor-relay/`. Hetzner ToS for non-exit relays confirmed. UFW 9001/tcp open. Fingerprint `CEBFF0886A263D4EA1D6D08A7ED86138F98D10AA` (RSA) / `OC9D4QOTuevgLz+KsiyaF8OyzrXGXkHqOuAi8EfGYGE` (ed25519). Consensus listing expected 12–48h after first start. **Inline nickname fix needed at deploy time** — the `/opt/bhn` clone on Hillsboro pre-dates commit `d47a5d6`, so its torrc had the hyphenated `BHNHelios-US3`; sed'd to `BHNHeliosUS3` before `docker compose up`. Future Hillsboro re-deploys should `git pull` first (after the local commits are pushed). |
| WireGuard PSK | ✅ Generated + bilaterally applied 2026-05-13, saved as `EH-WG-Hillsboro-PSK` in operator PM. Live copy in `/etc/wireguard/wg0.conf` on both LA and Hillsboro (mode 600). Fresh handshake at 1s after dual-apply, tunnel healthy. |
| LibreSpeed (US-West speed test endpoint) | 🔨 Planned (Phase 4 backlog) |

## Data pipeline

| Component | Status |
|-----------|--------|
| Storage layer | ✅ Done |
| Directory structure | ✅ Created on both tiers |
| PostgreSQL on NVMe | ✅ Migrated, online |
| Grafana installed | ✅ Live, connected to PostgreSQL |
| Initial dashboards | ✅ 8-panel "BHN Network Overview" live (Grafana dashboard JSON still named under old `eh-` prefix until coordinated migration) |
| Purge cycle (`eh-purge` script) | ✅ Deployed to `/usr/local/sbin/eh-purge` on LA hub |
| Cron schedule (48hr default) | ✅ `/etc/cron.d/eh-purge` — `--auto` at 03:00 UTC every 48h |
| 80% capacity safety net | ✅ Same cron — `--check-capacity` every 15 min |
| DNS query persistence | ⛔ Intentionally disabled — `dns_queries` table dropped per external-observer principle (domains are content) |
| Packet payload capture | ⛔ Intentionally not enabled — payloads are content |
| Hourly stats snapshots | ❌ Not yet implemented |
| Weekly analysis | ❌ Not yet implemented |
| n8n install | ✅ VPN-only access at `http://10.8.0.1:5678` (mirrors Grafana). Public hostname `n8n.eventhorizonvpn.com` deprecated 2026-05-08, LE cert deleted, nginx site removed. **DNS A record at provider should be removed by operator.** |
| n8n: `BHN Network Pulse - 2h` workflow (renamed from `EH Network Pulse - 2h` 2026-05-11) | ✅ Re-enabled 2026-05-09 with tightened `If Important` threshold — dropped the `important==true` (Claude verdict) clause; ntfy now fires only on `events_critical > 0` OR `anomalies_open > 0`. `pulse_reports` table accumulates every 2h regardless; phone pings only land for real signals |
| n8n: `BHN Proxy Health Monitor v1.0` (renamed from `EventHorizon Proxy Health Monitor v1.0` 2026-05-11) | ⛔ **Permanently deleted 2026-05-09** — workflow row + execution history removed from n8n DB (the 5-min schedule trigger had been bleeding queued alerts into operator's ProtonMail inbox even after deactivation). JSON archived in `n8n-workflows/bhn-proxy-health-monitor.json` (renamed from eh- 2026-05-11) for reference; will NOT auto-import on n8n restart |
| n8n: `HORIZON` (workflow renamed from `EventHorizon AI Agent v1.0` 2026-05-09) | ✅ Live (chat-trigger workflow) |

## WireGuard peer registry

Hub `wg0` on LA listens on UDP `51820`. Peers identified below by pubkey (public, safe to commit). Private keys + PSKs live in operator's password manager (see "Secrets inventory" below).

| Label | Device | Pubkey | Tunnel IP | Endpoint (last seen) | Client profile(s) |
|-------|--------|--------|-----------|---------------------|--------------------|
| **FRA exit** | `BHN-VPS-FRANKFURT-EU1` | `zkfJNbdL9Ptdxv+fxwV2e1q0mbCR5Z/9T80QanSxKA8=` | `10.9.0.2/32` | `192.248.187.208:51821` | server peer, persistent keepalive 25s |
| **NJ trading** | `BHN-VPS-NEWJERSEY-US2` | `ylnSJOqwkqrNZwt/saJdqoMG7j3l35hoUk+zejru1Sk=` | `10.8.0.5/32` | `140.82.4.35:51820` | server peer; **no PSK yet** |
| **Hillsboro egress/proxy** | `BHN-HILLSBORO-US3` | `EwBHwkT4iJXzhJZMvtlo70NOLx+wPv8IXmAGSa89zBg=` | `10.8.0.6/32` | `5.78.94.237:51821` | server peer (tinyproxy egress + Tor relay `BHNHeliosUS3`), persistent keepalive 25s, PSK `EH-WG-Hillsboro-PSK` |
| **FLETCH-DESKTOP** | operator workstation (Windows) | `y+ekkxKZsCn9LERiQ3unZxn2zDjsS1yqbz12limv1kA=` | `10.8.0.4/32` | `68.96.70.83:<dynamic>` | `EH-admin` (split: `10.8.0.0/24, 10.9.0.0/24`) + `EH-full` (full: `0.0.0.0/0, ::/0` + `DNS=10.8.0.1`) |
| **FLETCH-PHONE** | operator phone (iOS) | `N9Tg0dOEE7GQgE7lG1FgfI+pGSQoIo9+EmSUucnEAVA=` | `10.8.0.2/32` | `68.96.70.83:<dynamic>` | `FLETCH-PHONE-SPLIT` + `FLETCH-PHONE-FULL` (same PSK + privkey across both, only `AllowedIPs` differs) |

Hub-side persistent config at `/etc/wireguard/wg0.conf` on LA. `wg-quick save wg0` rewrites the file and **drops any inline comments above [Peer] blocks** — peer labels are kept here in STATUS.md as the durable record, not in the conf file.

WG-rotation runbook (with the 2026-05-09 race-condition lesson): `infrastructure/bootstrap/docs/wg-key-rotation.md`.

## Secrets inventory

What should be in the operator's password manager. **No values stored here** — values live in the PM (and on each server's root-only configs as the runtime copy). When a new secret is generated or rotated, update this table in the same commit.

### 🔴 Disk encryption (lose = data unrecoverable)

| PM entry | Type | Live copy on server | Notes |
|----------|------|--------------------|-------|
| `EH-NVMe-LUKS` | LUKS2 passphrase | `/root/.luks-eh-nvme` (LA) | Hot tier (PG data) |
| `EH-HDD-LUKS` | LUKS2 passphrase | `/root/.luks-eh-hdd` (LA) | Cold tier (backups, archives) |
| `EH-Restic-Repo` | restic repo password | `/root/.eh-backup.env` (LA, mode 0600) | Decrypts daily encrypted snapshots |

### 🔴 Server root access

| PM entry | Type | Live copy on server | Notes |
|----------|------|--------------------|-------|
| `EH-LA-Root` | Linux root password | shadow on LA | Rotated 2026-05-07 |
| `EH-FRA-Root` | Linux root password | shadow on FRA | Rotated 2026-05-08 |
| `EH-SSH-Privkey-fletch-desktop` | SSH ed25519 private key | `~/.ssh/id_ed25519` (operator-pc) | Backup off-machine; if workstation dies, you can't SSH in either node |

### 🔴 WireGuard private keys (lose = device can't reconnect without rotation)

| PM entry | Type | Live copy on server | Notes |
|----------|------|--------------------|-------|
| `EH-WG-LA-Hub-Privkey` | WG ed25519 privkey | `/etc/wireguard/wg0.conf` `[Interface] PrivateKey` line on LA | v3 bootstrap stored inline; v4 splits to `/etc/wireguard/private.key` |
| `EH-WG-FRA-Exit-Privkey` | WG ed25519 privkey | `/etc/wireguard/private.key` on FRA | v3 bootstrap stored separately |
| `EH-WG-FLETCH-DESKTOP-Privkey` | WG ed25519 privkey | DPAPI-encrypted in WireGuard for Windows | Both `EH-admin` and `EH-full` profiles use same key |
| `EH-WG-FLETCH-PHONE-Privkey` | WG ed25519 privkey | iOS keychain (WireGuard app) | Both phone profiles use same key |

### 🟠 WireGuard pre-shared keys (per peer pair — quantum-resistance hedge)

| PM entry | Type | Live copy on server | Notes |
|----------|------|--------------------|-------|
| `EH-WG-FRA-PSK` | 32-byte symmetric secret | `/etc/wireguard/wg0.conf` (LA) + `/etc/wireguard/wg1.conf` (FRA) | Same value on both ends |
| `EH-WG-FLETCH-DESKTOP-PSK` | 32-byte symmetric secret | `/etc/wireguard/wg0.conf` (LA, peer `y+ekkxKZ…`) | Rotated 2026-05-09 |
| `EH-WG-FLETCH-PHONE-PSK` | 32-byte symmetric secret | `/etc/wireguard/wg0.conf` (LA, peer `N9Tg0d…`) | Generated 2026-05-09 |
| `EH-WG-Hillsboro-PSK` | 32-byte symmetric secret | `/etc/wireguard/wg0.conf` (LA, peer `EwBHwkT…`) + `/etc/wireguard/wg0.conf` (Hillsboro) | Generated + saved 2026-05-13. Bilateral apply within ~2s, fresh handshake confirmed at 26ms RTT. |

### 🟠 PostgreSQL roles (LA — `eventhorizon` DB)

| PM entry | Type | Live copy on server | Notes |
|----------|------|--------------------|-------|
| `EH-PG-postgres` | PG superuser password | currently **unset** — peer auth via local socket | Set with `ALTER ROLE postgres WITH PASSWORD '…';` if remote superuser needed |
| `EH-PG-ehuser` | PG role password | scripts that read it: `eh-security-collector.py`, `eh-dns-collector.py`, `/root/.eh-metadata.env` | Rotated 2026-05-08 |
| `EH-PG-bootstrap_writer` | PG role password | `/root/.eh-heartbeat.env` (mode 0600, both nodes) | Used by `eh-heartbeat` script + future v4 bootstraps via `EH_BOOTSTRAP_PG_DSN` |
| `EH-PG-log_shipper` | PG role password | All 4 BHN nodes, mode-0600 env files: `/root/.eh-log-shipper.env` (FRA, original), plus 20+ collector-specific files added 2026-05-13: `/root/.bhn-vnstat.env`, `/root/.bhn-crowdsec.env`, `/root/.bhn-resource.env`, `/root/.bhn-fail2ban.env`, `/root/.bhn-patch.env`, `/root/.bhn-conntrack.env`, `/root/.bhn-iptables.env`, `/root/.bhn-docker-stats.env`, `/root/.bhn-pg-stats.env`, `/root/.bhn-n8n-stats.env`, `/root/.bhn-proxy-log.env`, `/root/.bhn-dns-log.env`, `/root/.bhn-ssh-sessions.env`, `/root/.bhn-fred.env`, `/root/.bhn-eia.env`, `/root/.bhn-usda.env`, `/root/.bhn-finnhub.env`, `/root/.bhn-coingecko.env`, `/root/.bhn-kalshi.env`, `/root/.bhn-polymarket.env`, `/root/.bhn-quiver.env`, `/root/.bhn-fmp.env`, `/root/.bhn-tor-metrics.env`, `/root/.bhn-node-logs-summary.env`, `/root/.bhn-wg-sessions.env`, `/root/.bhn-tor-control-stats.env` | INSERT-only on monitoring + market-data tables (35+ tables granted via 2026-05-13 schemas). Manually set 2026-05-13 to a weak value (`BHN-LogShipper-<year>`-style). ⚠️ **Rotation recommended** — value is guessable from project context; see `scripts/bhn-la-restore.sh`-style approach for the rotation flow. |

### 🟠 HORIZON service credentials (operator-provisioned — see `infrastructure/docs/horizon-roadmap.md`)

These are **TBD until operator creates the accounts**. Listed here so they have canonical PM entry names from day one. Once provisioned, values land in n8n's encrypted credential store on LA (workflow-side) and in operator's Proton Pass (recovery-side). No values ever in this repo.

| PM entry | Type | Live copy on server | Notes |
|----------|------|--------------------|-------|
| `EH-Twilio-AccountSID` | Twilio account SID | n8n credential (encrypted via n8n master key) | TBD — operator provisions Twilio account |
| `EH-Twilio-AuthToken` | Twilio auth token | n8n credential | TBD |
| `EH-Twilio-PhoneNumber` | E.164 phone number | n8n credential + STATUS.md / config | TBD |
| `EH-ElevenLabs-API-Key` | ElevenLabs API key | n8n credential | TBD — Creator tier ($22/mo) for Professional Voice Clone |
| `EH-ElevenLabs-VoiceID-HORIZON` | Voice ID (string, library voice) | n8n credential / config | TBD — operator picks library female voice at setup |
| `EH-ElevenLabs-VoiceID-Operator` | Voice ID (cloned operator voice) | n8n credential / config | TBD — generated after PVC upload of 30s sample |
| `EH-Google-Horizon-Account` | Login + 2FA recovery | Google account + n8n OAuth credential | TBD — `horizon@gmail.com` for Calendar API |
| `EH-OpenWeatherMap-API-Key` | API key (free tier, 60 req/min) | n8n credential | TBD |
| `EH-NewsAPI-API-Key` | API key (free tier, 100 req/day) | n8n credential | TBD |
| `EH-Alpaca-Paper-KeyID` + `EH-Alpaca-Paper-SecretKey` | Alpaca paper API credentials | n8n credential | TBD |
| `EH-Alpaca-Live-KeyID` + `EH-Alpaca-Live-SecretKey` | Alpaca live API credentials | n8n credential | **Provision LATER** — only after operator flips a STATUS.md "PROMOTE TO LIVE" gate per individual ruleset |
| `EH-eBay-AppID` + `EH-eBay-CertID` + `EH-eBay-Token` | eBay API credentials | n8n credential | Already approved; operator needs to feed values into n8n |
| `EH-FMP-API-Key` | Financial Modeling Prep API key | n8n credential / MCP config | Already connected via MCP — verify the key is also captured in PM |
| `EH-PG-n8n_user` | PG role password | n8n encrypted credential `Postgres EventHorizon` (`/root/.n8n/database.sqlite`) | Used by n8n workflows that write to PG |
| `EH-PG-grafana_reader` | PG role password | Grafana datasource `secureJsonData` in `/var/lib/grafana/grafana.db` (encrypted with Grafana `secret_key`) | Read-only, used by dashboard queries |
| `EH-PG-agent_reader` | PG role password | n8n encrypted credential `Postgres EventHorizon (agent read-only)` | Used by AI Agent workflow |

### 🟠 Service logins

| PM entry | Type | Live copy on server | Notes |
|----------|------|--------------------|-------|
| `EH-Grafana-Admin` | Grafana admin password | grafana.db (bcrypt) | Used to log into `http://10.8.0.1:3000` |
| `EH-n8n-Admin` | n8n admin password | `/root/.n8n/database.sqlite` `user` table (bcrypt cost 10) | `admin@eventhorizonvpn.com`; rotated 2026-05-09 |
| `EH-Shadowsocks-Password` | SS password (shared) | `/etc/shadowsocks-libev/config.json` on both nodes | Rotated 2026-05-08 |

### 🟠 n8n workflow credentials (encrypted in `/root/.n8n` with n8n's `config` encryption key)

| PM entry | Type | Live copy on server | Notes |
|----------|------|--------------------|-------|
| `EH-n8n-SMTP-ProtonMail` | SMTP submission token | n8n credential `SMTP account` | `admin@eventhorizonvpn.com` @ `smtp.protonmail.ch:587` |
| `EH-Anthropic-API-Key` | API key | n8n credential `EventHorizonVPN-Claude` | Rotate via console.anthropic.com if exposed |
| `EH-n8n-Pulse-Webhook-URL` | Webhook URL (treat as secret) | n8n workflow definition | Anyone with URL can trigger Claude API calls |
| `EH-n8n-Encryption-Key` | n8n master key (encrypts all above) | `/root/.n8n/config` (mode 0600) | If lost, all stored credentials become unrecoverable; backup pipeline tars this file |

### 🟡 External / provider

| PM entry | Type | Notes |
|----------|------|-------|
| `Vultr-Account` | Cloud provider login | Controls VPS billing + web console (out-of-band recovery path) |
| `Domain-Registrar` | DNS registrar account | Holds `eventhorizonvpn.com` records |
| `EH-CrowdSec-Console-Enroll` | Enrollment key | Only relevant if shipping to CrowdSec central |

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

The "BHN Network Overview" Grafana dashboard currently includes:

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

1. **Frankfurt exit routing — finish MASQUERADE fix** (next session) — FRA-side `iptables -t nat -A POSTROUTING -s 10.8.0.0/24 -o enp1s0 -j MASQUERADE` + persist via `iptables-save > /etc/iptables/rules.v4`, then re-run `bhn-frankfurt-exit.sh apply` on LA, then verify `curl https://api.ipify.org` from full-tunnel profile returns 192.248.187.208.
2. **BHN trading framework — remaining support work** — rules JSON schema + validator (`rules_schema.py`, `validate_rules.py`), `config-templates/rules.example.json`, systemd units (per-strategy timers + reconciliation service + daily_summary timer), three runbook docs (`bhn-trading-strategies.md`, `bhn-rules-propagation.md`, `bhn-reconciliation.md`), then NJ deployment.
3. Tokyo or other 3rd region node
2. tmpfs migration for ephemeral content per external-observer principle (Suricata payload audit, /var/log/suricata logrotate tuning)
3. **HORIZON Phase 3 modules** — see `infrastructure/docs/horizon-roadmap.md` for the full 10-module spec, build phasing, voice stack architecture, jurisdictional posture, and decisions log. Operator pre-session-1 actions: provision Twilio + ElevenLabs Creator + Google `horizon@gmail.com` + OpenWeatherMap + NewsAPI + Alpaca paper, plus record a 30s voice sample for operator-voice cloning. Session 1 build target: M1 Voice Pipeline + start on M2 Morning Briefing.
4. Hourly stats snapshots + Weekly analysis (long-pending from Phase 2 data pipeline; can be a HORIZON M3/M4 by-product)
5. Hetzner Storage Box swap for backups (DR completion — see `BACKUP.md`)

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

- **2026-05-12 documentation + Vultr rename pass** — Vultr-side server display names renamed `EH|VPS-*` → `BHN|VPS-*`. Repo docs (README.md, STATUS.md, peer registry) updated to match. `BHN-INFRASTRUCTURE.txt` created at repo root as the canonical access-methods quick reference (every node + service + port + tunnel path). Intentional preservations (PG database name `eventhorizon`, email `eventhorizonvpn.com`, n8n credential names `Postgres EventHorizon` / `EventHorizonVPN-Claude`, LA `eh-*` script paths, Proton Pass `EH-*` entries) explicitly NOT renamed — these are live-system identifiers, not branding.
- **2026-05-12 Frankfurt exit routing applied but BROKEN — deferred to next session** — Phase 1 policy-routing for "full" tunnel profile applied to LA. Routes wg0 client traffic via fwmark 0x100 → table 100 → `default via 10.9.0.2 dev wg0 onlink` (Frankfurt is a wg0 peer at AllowedIPs=10.9.0.2/32, NOT a separate wg1 interface — script `scripts/bhn-frankfurt-exit.sh` corrected from stale wg1 references). The wg0→wg0 hairpin FORWARD rule replaces the prior wg0↔wg1 dual rules. **Observed**: internet dies on the operator's full-tunnel profile after apply. Root cause: **Frankfurt is missing the MASQUERADE rule on `10.8.0.0/24` source** — hub-client packets reach FRA over the tunnel but FRA cannot rewrite the source IP to its public IP for the return path, so external traffic is black-holed. **Next session work**: (1) on FRA: `iptables -t nat -A POSTROUTING -s 10.8.0.0/24 -o enp1s0 -j MASQUERADE` then `iptables-save > /etc/iptables/rules.v4` for persistence; (2) re-run `bhn-frankfurt-exit.sh apply` on LA; (3) verify `curl https://api.ipify.org` from full-tunnel profile returns 192.248.187.208. Until fixed, LA exit routing is reverted via `bhn-frankfurt-exit.sh rollback`.
- **2026-05-12 Phase 4 services deployed** — Wallos (LA, http://10.8.0.1:8090), SearXNG (Frankfurt, http://10.9.0.2:8089), LibreSpeed Frankfurt (http://10.9.0.2:8088). LibreSpeed LA + Tor relays remain in Phase 4 backlog.
- **2026-05-13 evening — trading rules architecture pivot + free-tier FMP rewrite + collector deploy script v2**:
  - **rules.json: each strategy is now fully self-contained.** `system.broker` (portfolio-wide caps, master paper_mode, killswitch flags) added then removed within the same session — operator preference is no shared cross-strategy config. Each strategy block now carries its OWN `broker: {alpaca_key_id, alpaca_secret, alpaca_base_url, account_alias}` (with the env-var-NAME pattern — actual keys live in `/etc/bhn-trading/strat<N>.env` on NJ, mode 0600, one file per strategy). Five dedicated Alpaca paper accounts required, mapped to env-var names `STRAT{1..5}_ALPACA_{KEY_ID,SECRET}`. Account aliases `BHN-Paper-Strat{1..5}` are operator-facing labels in logs / daily summaries / Grafana — pure presentation, not routing.
  - **Per-strategy `execution_limits` subblock** layered on top of existing signal-gen config. Fields: max_position_size, daily_loss_limit, max_trades_per_day, overnight_hold, weekend_hold, hold_through_earnings, stop_loss_pct, profit_target_pct, plus strategy-specific optionals (max_hold_days / force_close_time / close_before_earnings_days). Values per operator spec for Strats 2/3/4; conservative placeholders for Strats 1/5 (flagged in operator_note for review before flipping `enabled=true`).
  - **Strategy 3 renamed** `strat_3_scalp` → `strat_3_mean_reversion` across schema, JSON example, validator, trading_core (StrategyId enum + STRAT_NUMBER + SMS_NAME_MAP). Operator-facing SMS short-form `STRAT3` unchanged. If `paper_trades` / `signals_log` / `strategy_performance` / `circuit_breaker_log` / `trading_strategies` already have rows with the old id (unlikely — framework not yet deployed to NJ), they'll need a one-shot UPDATE before next deploy.
  - **`strategy_value.py` rewritten** off the paid FMP `/stock-screener` onto three free-tier endpoints: `/profile/{symbol}`, `/ratios-ttm/{symbol}`, `/balance-sheet-statement/{symbol}` (D/E computed in-script from `totalDebt` + `totalStockholdersEquity`). Plus one bulk `/earning_calendar` call for the blackout window. Iterates a fixed 20-symbol large-cap value universe in `rules.strat_2_value.universe` (JPM/BAC/JNJ/KO/etc.). Cycle cost: 61 FMP calls vs the 250/day free-tier ceiling. Also fixed a pre-existing key-name mismatch (`filters`/`debt_equity_max` vs schema `screener_filters`/`de_max`) that was making rules.json overrides silently no-op.
  - **`trading_core.py` per-strategy infrastructure landed**: `is_strategy_enabled(sid)` reads the rules.json flag (gates `should_run`), `set_strategy_enabled(sid, enabled, reason)` does an atomic .tmp + os.replace rewrite + busts the mtime cache, `parse_sms_toggle_command("ENABLE STRAT2")` resolves HORIZON SMS into `(enabled, sid)`, `get_strategy_alpaca(sid)` returns a per-strategy Alpaca client cached by id (each loads its own `/etc/bhn-trading/strat<N>.env`). New CLI subcommands `enable`, `disable`, `sms`. Also fixed a pre-existing structural bug: `get_strategy_rules` was reading from `rules["strategies"][sid]` but the schema puts strategy blocks at the top level (silent empty-dict bug — never triggered because no strategy had been deployed yet).
  - **`validate_rules.py` Layer 2.5 tightened**: dropped all system.broker bound checks + cross-field invariants tied to it. Kept the per-strategy execution_limits bounds. Strengthened the "enabled strategies must have populated broker block" check to also require `account_alias`. New cross-field: `live_mode_approved=true` paired with the paper URL is rejected as contradictory (replaces the dropped system-wide paper_mode check).
  - **`scripts/bhn-deploy-all-collectors.sh`** — built tonight as the master deploy/test/status runner from LA. Modes: `deploy` (scp + chmod + env + cron + test + ≥1-row verify per collector per node), `test`, `status`. Filters: `--nodes A,B,C` / `--skip-nodes A,B` (added after Hillsboro SSH hung). NJ port-2222 + tunnel-IP routing baked in via `NODE_SSH[NJ]="root@10.8.0.5"` + `NODE_SSH_PORT[NJ]="2222"` (no longer depends on operator's `~/.ssh/config`). Every ssh + scp call carries `-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3` — dead sessions die in ~30s. Preflight `verify_collector_env_vars()` cross-checks COLLECTOR_META against each collector source's actual env-var name (catches drift). Deploy step prints `[node/short] env=... var=BHN_X_PG_DSN=<dsn>` so the operator can see exactly which per-collector var is being written.
  - **Tonight's actual collector deploy state** (operator did this manually mid-session, NOT via the script): LA = iptables + docker-stats + dns-log + wg-stats + resource-stats passing; FRA = iptables + resource-stats passing; NJ = resource-stats passing + env files staged for iptables/fail2ban/dns-log/conntrack (not yet test-run); Hillsboro = nothing (SSH hangs persist after both ConnectTimeout and ServerAliveInterval fixes — likely WG tunnel or remote sshd wedge, not TCP-level). Next session: route around Hillsboro via `--skip-nodes Hillsboro`, finish NJ test-runs, then diagnose Hillsboro from a fresh PC LA CONSOLE session (check `wg show wg0 | grep -A2 10.8.0.6` for handshake age first).
  - **`infrastructure/docs/horizon-roadmap.md`** — Phase 5.5 Redis hot-memory layer sketched (deferred until M9 calendar polish ships; gated on fixing the existing HORIZON workflow disconnects: Format Memory Block + token logging). Design: Redis = working memory (last N turns + running summary keyed by session_id, 12h sliding TTL); pgvector = long-term semantic; new PG table `conversation_sessions` = durable session metadata. Two new HORIZON nodes + a session-close sweep sub-workflow. No infra change tonight.
  - **Operator action items for next session**: (a) create 5 Alpaca paper accounts, save keys in Proton Pass as `BHN-Alpaca-Paper-Strat{1..5}`, drop them on NJ as `/etc/bhn-trading/strat<N>.env` (0600); (b) diagnose Hillsboro SSH/WG; (c) wire API keys (FRED/Finnhub/EIA/USDA already obtained per session start) into LA env files; (d) finish collector deploy for NJ + Hillsboro; (e) HORIZON workflow gets the SMS toggle node (`python3 /opt/bhn/trading/trading_core.py sms "$BODY"`); (f) re-validate + rsync rules.json → NJ.
- **2026-05-12 BHN trading framework committed** — 5 paper-trading strategies + 3 support scripts (master_killswitch, daily_summary, reconciliation_daemon) + trading_core + foundation SQL schema + Strategy 5 weather-arbitrage tables committed to `scripts/trading/` and `sql/`. Alpaca paper account `PA39LSUT2NW8` provisioned with $100k virtual capital. Runs on NJ (`BHN|VPS-NEWJERSEY-US2`). Pending before strategies can fire: rules schema + validator, systemd units, NJ deployment + runbook docs.
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
- **2026-05-09 n8n admin password rotation** — `admin@eventhorizonvpn.com` row in `/root/.n8n/database.sqlite` updated with a fresh 44-char random, hashed by n8n's bundled `bcryptjs` (cost 10, `$2a$10$…` format). Other workflow encryption material (`/root/.n8n/config`) and per-credential stored secrets are unchanged — only the login password was rotated. New value lives only in operator's password manager.
- **2026-05-09 LA hardening sweep** — four loose ends from earlier provisioning eras cleaned up:
  1. `danted.service` (SOCKS daemon, leftover from decommissioned-proxy era; was OOM-killed 2026-05-07 but config was wide-open `0.0.0.0:1080` with `from: 0.0.0.0/0`) stopped + disabled. UFW rule `1080 ALLOW IN Anywhere` (v4+v6) removed. `dante-server` package retained but inert — uninstall later if not reusing.
  2. `proxy-rotate` cron (`*/30 * * * *` rotating between 4 decommissioned proxy IPs via `redsocks`) removed from root crontab. Script moved to `/usr/local/bin/proxy-rotate.disabled.bak`. `redsocks` service stopped + disabled (still installed; uninstall later).
  3. `netfilter-persistent.service` was failing at every boot (status: failed since 2026-05-07) because saved `rules.v4` referenced CrowdSec ipset `crowdsec-blacklists-0` which doesn't exist at boot — chicken-and-egg with `crowdsec-firewall-bouncer` startup ordering. Re-saved `rules.v4`/`v6` with CrowdSec lines stripped (`iptables-save | grep -v crowdsec`); the bouncer rebuilds its own chain on its own start. Service now `active (exited)` — manual iptables rules (VPN→SSH ACCEPT pos 1, NAT MASQUERADE) now actually survive reboot.
  4. Grafana bind tightened from `*:3000` (all interfaces, UFW-protected) to `10.8.0.1:3000` (tunnel-only at the socket layer). `http_addr = 10.8.0.1` set in `/etc/grafana/grafana.ini`. Public-IP `:3000` now refuses connections at TCP level instead of getting dropped at UFW — defense-in-depth alignment with v4 `hub.sh` intent. dnscrypt-proxy `0.0.0.0:53` deliberately left wide (UFW-protected from non-tunnel) since it serves both LA-host lookups (127.0.0.1) and tunnel clients (10.8.0.1).
- Backups of pre-change configs left at `/etc/iptables/rules.v4.bak.20260509-050053` (+ v6) and `/etc/grafana/grafana.ini.bak.20260509-050053` on LA.
- **2026-05-09 follow-up cleanup queue (items 4a, 5, 6b, 7, 8, 9a)**:
  - **FRA sshd alt-port listeners removed** — `Port 80` and `Port 443` lines deleted from `/etc/ssh/sshd_config`; sshd now listens only on `22/tcp` (v4 + v6). Pre-edit backup at `/etc/ssh/sshd_config.bak.20260509-*`. Was inconsistent (listeners with no UFW allows); now clean.
  - **`linuxuser` (uid 1000) hardened on both nodes** — was already password-locked with empty `authorized_keys` from cloud-init; further reduced surface: shell changed to `/usr/sbin/nologin`, removed from `sudo` group. Even if a future config error allowed key auth, no shell + no sudo.
  - **`eventhorizon` (uid 1001) on LA locked** — operator's secondary account used to compile n8n's native `sqlite3` module on 2026-05-06; work complete. Shell `/usr/sbin/nologin`, password locked (`L`), removed from `sudo`. SSH key in `/home/eventhorizon/.ssh/authorized_keys` retained but unusable without shell. Files owned by this user under `/usr/lib/node_modules/n8n/...` (build artifacts) intentionally left in place.
  - **eh-backup duplicate-log fix deployed** — `scripts/eh-backup.sh` no longer pipes `restic backup` / `restic forget` through `tee -a $LOG_FILE` (cron's `>>` redirect already covers it; tee'ing duplicated every line). Manual trial verified clean. Commit `2c3e18c`.
  - **Unknown WG peer `YJBUy0o9Ge6QxkX4RXTmd8S0v4Z9BTStAiobRCJk1lw=` (10.8.0.3) removed** — never connected, no endpoint ever recorded, no documented purpose. `wg-quick save wg0` persisted. PSK `EH-WG-PSK-Device3` in PM is now stale.
  - **PC + phone tunnel identities separated** — phone generated its own keypair (`N9Tg0dOE…`) on the iOS WG app, registered on hub at `10.8.0.2/32` with fresh PSK. `FLETCH-DESKTOP` peer (`y+ekkxKZ…`) trimmed to allowed-ips `10.8.0.4/32` only. Phone now has two profiles (`FLETCH-PHONE-SPLIT` + `FLETCH-PHONE-FULL`) mirroring the desktop pattern. Verified end-to-end: split-tunnel test loads `http://10.8.0.1:5678` (n8n login), full-tunnel test returns LA's public IP from `ifconfig.me`, DNS leak test shows dnscrypt-proxy upstreams (Cloudflare + Anexia/Digitale Gesellschaft), IPv6 kill-switch'd by iOS WG. Old `EH-WG-PSK-Device2` PM entry (the previous `PPjYFx…` phone key) is now stale — both old peers (`PPjYFx`, `1Slpqh`) are gone from the hub.
- **2026-05-09 notification quieting + public web takedown**:
  - **EH Network Pulse - 2h re-activated with tightened threshold** — was paused since 2026-05-07 to reduce Anthropic API spend. Re-enabled with the `If Important` IF node trimmed: dropped condition `c1` (Claude's `important==true` verdict — too generous, fires on most reports). Remaining: `c3` (events_critical > 0) and `c4` (anomalies_open > 0) joined by OR. ntfy push now lands only on actually-critical signals. `pulse_reports` table still accumulates every 2h for retroactive analysis. Workflow `active=1`, n8n service restarted, schedule trigger registered.
  - **EventHorizon Proxy Health Monitor v1.0 deleted permanently** — workflow row, execution history, tag/share links all removed from `/root/.n8n/database.sqlite`. The 5-min schedule trigger had been firing through to 2026-05-07 19:15 UTC before deactivation, leaving ~120 alert emails queued in ProtonMail that were still trickling out to operator's phone 2 days later. Permanent delete eliminates the chance of manual/webhook re-trigger. JSON archived in repo at `n8n-workflows/eh-proxy-health-monitor.json`. n8n DB backup at `/root/.n8n/database.sqlite.bak.20260509-070933`.
  - **Public web on LA taken offline** — `systemctl stop nginx && systemctl disable nginx`. UFW `80/tcp` + `443/tcp` (v4 + v6) rules dropped. LA's public ingress is now SSH/22, WG/51820, Shadowsocks/8388. Largest public attack surface (HTTP server + static login HTML at `/var/www/html`) eliminated; ~4,600 daily HTTP-probe attempts no longer arrive at the application layer (still hit UFW deny but reduced workload for CrowdSec). **Side effect**: LE cert for `eventhorizonvpn.com` (valid until 2026-08-04) cannot auto-renew via HTTP-01 with port 80 closed. Either bring nginx back temporarily a few days before expiry, or switch to DNS-01 challenge with the DNS provider's API. `/var/www/html` retained for restoration. nginx site-config in `/etc/nginx/sites-enabled/default` retained.
  - **Cosmetic loose end** flagged for next cleanup: `51821/udp ALLOW IN Anywhere` UFW rule on LA is dead (LA hub listens on `51820`, not `51821` — that's the peer-side WG listen port for non-hub nodes, leaked into LA's UFW from v3 bootstrap). Safe to remove in a future pruning pass.
- **2026-05-09 evening cleanup + JARVIS memory wiring**:
  - **Stale `51821/udp ALLOW IN Anywhere` UFW rule removed on LA** (the cosmetic loose end flagged earlier today). v4 + v6 deleted. Outbound rule `51821/udp ALLOW OUT to 192.248.187.208` retained — that's the FRA tunnel underlay and is intentional.
  - **dnscrypt-proxy bind tightened on LA** from `0.0.0.0:53` → `127.0.0.1:53` + `10.8.0.1:53` only (UDP + TCP). Mirrors the Grafana defense-in-depth move from this morning. Edited `/lib/systemd/system/dnscrypt-proxy.socket` (backup at `dnscrypt-proxy.socket.bak.20260509-*`), `daemon-reload` + restarted `dnscrypt-proxy.socket` and `dnscrypt-proxy.service`. LA's own DNS lookups still resolve via 127.0.0.1 (verified). VPN clients still resolve via 10.8.0.1. Public ingress on UDP/TCP 53 now refused at the kernel even before UFW (matches v4 hub posture). Note: dnscrypt-proxy upstreams are reached via the configured DoH/DNSCrypt URLs over outbound 443 — this bind change does NOT affect that path.
  - **`linuxuser` verified locked on both nodes** — already done in the earlier hardening sweep (shell `/usr/sbin/nologin`, password `L`, `sudo` group removed); no further action needed.
  - **JARVIS pgvector retrieval wiring** — extended the `EventHorizon AI Agent v1.0` workflow with three new nodes between `When chat message received` and `AI Agent`:
    1. `Embed Chat Query` (Code) — embeds the operator's message via `http://127.0.0.1:8001/embed` (BAAI/bge-small-en-v1.5, 384-dim) and builds a parameterized similarity SQL.
    2. `Retrieve Chat Memories` (Postgres `executeQuery`) — runs the SQL with the `agent_reader` credential against the `memories` table; HNSW cosine index returns top-5.
    3. `Format Memory Block` (Code) — formats results into a markdown block.
    The `AI Agent` `systemMessage` now starts with `=` (n8n expression mode) and appends `{{ $('Format Memory Block').first().json.memoryBlock }}` so retrieved context is auto-injected into Claude's prompt every chat turn — same shape as Pulse's `Embed Pulse Query → Retrieve Memories → Build Claude Request` pipeline. Updated workflow JSON exported to `n8n-workflows/eh-ai-agent-v1.json`. n8n DB backup at `/root/.n8n/database.sqlite.bak.20260509-*` (latest from this rewire).
    JARVIS's existing `embed_text` and `query_db` agent tools are retained — auto-retrieval happens FIRST (no agent decision required); the tools remain available for ad-hoc deeper queries the agent decides it needs.
    Test memory seeded (id 7, memory_type=`deployment`, title="JARVIS pgvector retrieval wiring") so the next chat that semantically relates to JARVIS / pgvector / memory wiring will demonstrate the recall loop.
  - **`node_logs` schema applied + log_shipper PG role created on LA** (committed earlier as `6523cd4`) — INSERT-only role, password rotated to PM-stored value. `pg_hba.conf` extended with two `host eventhorizon log_shipper` rules for 10.8.0.0/24 and 10.9.0.0/24. FRA's `eh-log-shipper.py` deployed and cron'd; ready to ship Suricata + CrowdSec events to LA when any actually fire.
- **2026-05-09 HORIZON — JARVIS rename + roadmap landing**:
  - **Persona renamed JARVIS → HORIZON** across the live n8n workflow (workflow row name + entire system prompt swept case-insensitively, 0 residual references), seeded memory id 7 in `memories` table updated, repo file `n8n-workflows/eh-ai-agent-v1.json` renamed to `n8n-workflows/eh-horizon.json` and re-exported from the live install. `EventHorizon AI Agent v1.0` is now `HORIZON` in n8n.
  - **Roadmap doc landed** at `infrastructure/docs/horizon-roadmap.md` — consolidates the operator's `HORIZON PLAN.txt` (repo root) plus all interactive decisions captured during this session. Covers: identity, build phasing, 10 module specs (M1 Voice Pipeline → M10 Job Search), voice stack architecture (LA-only, Whisper tiny, ElevenLabs Creator, Twilio), recording posture (delete immediately all phases for now; 48h business-test hold deferred until operator activates business calls), **jurisdictional posture** (FRA never touches voice data — § 201 StGB risk; LA-only with universal disclosure prefix is the defensible pattern), three pgvector memory lanes, retention policy, RAG-first cost cascade with Haiku query router, ~$37-47/mo HORIZON monthly add at full build.
  - **Operator pre-Session-1 actions**: provision 6 service accounts (Twilio, ElevenLabs Creator, Google `horizon@gmail.com`, OpenWeatherMap, NewsAPI, Alpaca paper), record a 30s voice sample for PVC, populate the new HORIZON entries in the secrets inventory above.
  - **Phase 3 progress** bumped 30% → 40% reflecting pgvector memory live + JARVIS→HORIZON rename complete + roadmap finalized; voice/SMS/calling/integrations are the remaining ~60%.
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
