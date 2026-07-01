# BHN Security Audit — 2026-07-01

**Audited by:** Claude Code (automated, read-only)  
**Scope:** LA hub (149.28.91.100) — post-deployment review after WeatherBHN Phase 2 changes  
**Audit time:** 2026-07-01 ~10:45 UTC  
**Status:** ⚠️ NEEDS REVIEW — 5 items flagged for morning review (none are critical/active exploits)

---

## Summary

Ten security domains were checked on LA: WireGuard peers, exposed ports, SSH auth, failed login attempts, backup storage, disk usage, service health, file permissions, PostgreSQL access controls, and today's deployment footprint. All authorized WireGuard peers are confirmed active; all successful SSH logins used ED25519 pubkey auth from the operator's IP. Five items need Fletch's attention: multiple monitoring/admin services are publicly exposed on 0.0.0.0 (including Grafana, Netdata, AdGuardHome, cAdvisor — confirm which are intentional); root partition is at 77% due to ~83G of unmanaged manual pg_dumps in `/root/backups/`; four strat env files are world-readable; n8n has a recurring hourly SQLite error; and Helsinki (10.8.0.8) is configured without a WireGuard preshared key.

---

## 1. WireGuard Peers

### wg0 (LA hub — 51820/UDP)

| VPN IP | Endpoint | Last Handshake | Transfer (rx/tx) | PSK | Active | Authorized |
|---|---|---|---|---|---|---|
| 10.8.0.2 | 68.96.70.83:57173 | 44s ago | 42 MiB / 770 MiB | ✓ | ✓ | ✓ Operator desktop |
| 10.8.0.4 | 68.96.70.83:59627 | 1m 57s ago | 2.77 GiB / 7.35 GiB | ✓ | ✓ | ✓ Operator desktop |
| 10.8.0.5 | 140.82.4.35:51820 | 1m 57s ago | 11.43 GiB / 735 MiB | ✓ | ✓ | ✓ NJ Grafana node |
| 10.8.0.6 | 5.78.94.237:51821 | 1m 1s ago | 18.64 GiB / 1.59 GiB | ✓ | ✓ | ✓ Hillsboro egress (persistent KA) |
| 10.8.0.8 | 46.62.162.87:51821 | 1m 47s ago | 635 MiB / 14 MiB | ✗ MISSING | ✓ | ✓ Helsinki EU1 (persistent KA) |
| 10.8.0.7 | 68.96.70.83:49665 | No handshake shown | — | ✓ | ✗ | ? Operator IP, no recent traffic |
| 10.8.0.9 | 68.96.70.83:60937 | No handshake shown | — | ✓ | ✗ | ? Operator IP, no recent traffic |
| 10.8.0.10 | (no endpoint) | Never | — | ✓ | ✗ | Provisioned, not yet connected |
| 10.8.0.20 | (no endpoint) | Never | — | ✓ | ✗ | Provisioned, not yet connected |
| 10.8.0.21 | (no endpoint) | Never | — | ✓ | ✗ | Provisioned, not yet connected |
| (none) | (no endpoint) | Never | — | ✓ | ✗ | Ghost config — allowed ips: (none) |

### wg1 (Hillsboro egress tunnel — 51822, fwmark 0xca6c)

| Endpoint | Allowed IPs | Last Handshake | Transfer | Status |
|---|---|---|---|---|
| 5.78.94.237:51821 | 0.0.0.0/0 | 2m 9s ago | 4.07 GiB / 2.65 GiB | ✓ Active egress tunnel |

**Verdict:**
- All peers with recent handshakes (10.8.0.2, .4, .5, .6, .8) are authorized. ✓
- ⚠️ **Helsinki (10.8.0.8) has no preshared key configured.** All other active peers use PSK for an extra authentication layer. Helsinki's absence is a gap — recommend adding PSK for defence-in-depth.
- ⚠️ **Ghost peer with `allowed ips: (none)`** — a peer configured with no allowed IPs cannot route any traffic but represents an unexplained config entry. Identify and remove if unneeded.
- 10.8.0.7 and 10.8.0.9 share the operator's external IP (68.96.70.83) with no recent handshakes — likely old device configs, low concern, worth pruning.

---

## 2. Exposed Ports

### Loopback-only (safe)
| Port | Process | Notes |
|---|---|---|
| 127.0.0.1:5432 | postgres | PostgreSQL loopback ✓ |
| 127.0.0.1:5679 | node (n8n) | n8n internal port ✓ |
| 127.0.0.1:6060 | crowdsec | CrowdSec API ✓ |
| 127.0.0.1:8888 | haproxy | HAProxy internal ✓ |
| 127.0.0.1:9100 | node_exporter | Prometheus exporter ✓ |
| 127.0.0.1:9121 | redis_exporter | Redis exporter ✓ |
| 127.0.0.1:9187 | postgres_exporter | PG exporter ✓ |
| 127.0.0.1:8080 | crowdsec | CrowdSec dashboard ✓ |

### VPN-only (10.8.0.1 — safe, WireGuard bridge)
| Port | Process | Notes |
|---|---|---|
| 10.8.0.1:5432 | postgres | PG via VPN ✓ |
| 10.8.0.1:5678 | node (n8n) | n8n via VPN ✓ |
| 10.8.0.1:8008 | python | Internal service ✓ |
| 10.8.0.1:8448 | python | Internal service ✓ |
| 10.8.0.1:8088–8095 | docker-proxy | Docker services via VPN ✓ |
| 10.8.0.1:8890 | haproxy | HAProxy VPN listener ✓ |
| 10.8.0.1:6379 | docker-proxy | Redis via VPN ✓ |
| 10.8.0.1:7575 | docker-proxy | Internal ✓ |
| 10.8.0.1:9090 | prometheus | Prometheus via VPN ✓ |

### Public (0.0.0.0 or \* — review required)
| Port | Process | Expected? | Notes |
|---|---|---|---|
| 0.0.0.0:22 | sshd | ✓ Expected | Key-auth only; CrowdSec monitors |
| 0.0.0.0:8388 | ss-server | ? | **Shadowsocks proxy — public proxy service** |
| 0.0.0.0:9586 | prometheus_wireguard | ⚠️ | WG metrics publicly exposed |
| \*:19999 | netdata | ⚠️ | Netdata monitoring dashboard — public |
| \*:3000 | grafana | ⚠️ | **Grafana publicly accessible on LA** — migration note said LA Grafana was purged 2026-05-28; this conflicts |
| \*:3001 | AdGuardHome | ⚠️ | AdGuard admin UI publicly accessible |
| \*:8081 | cAdvisor | ⚠️ | Docker container metrics — public |
| \*:53 | AdGuardHome | ⚠️ | Public DNS resolver — open resolver risk |
| \*:9617 | adguard_exporter | ⚠️ | Prometheus exporter — public |

**Verdict:** ⚠️ NEEDS REVIEW

Seven ports are publicly accessible beyond SSH that require confirmation:

1. **Port 3000 (Grafana)** — the 2026-05-28 Grafana migration was recorded as complete with LA package purged, but Grafana is still listening on LA at 0.0.0.0:3000 (pid 3095). Confirm whether this is a Docker container or an unremoved install.
2. **Port 8388 (Shadowsocks)** — public proxy server. Confirm this is intentional operator infrastructure, not a residual service.
3. **Port 53 (AdGuardHome DNS)** — public-facing DNS resolver could be abused for DNS amplification. Confirm ACLs are in place inside AdGuard.
4. **Port 19999 (Netdata), 3001 (AdGuard UI), 8081 (cAdvisor), 9586/9617 (exporters)** — monitoring/admin UIs publicly accessible. Confirm these are intentionally public or add WireGuard restriction.

---

## 3. Authentication Log

All successful SSH logins today originated from a single source:

- **IP:** 68.96.70.83 (operator's external IP, WireGuard peer 10.8.0.2/10.8.0.4)
- **Auth method:** ED25519 pubkey — `SHA256:fd/ip/3P2sqZ7k5zEE2/CILL12zbI/vlbAemDS1g5ZE`
- **User:** root
- **Count:** ~20+ sessions (active deployment work 10:30–10:45 UTC)

One anomalous pre-auth disconnect:
- **10:30:33 UTC** — `185.89.249.3 port 34656` disconnected at pre-auth. Not a successful login; likely a scanner that connected but didn't proceed with auth.

`last -20` confirms all interactive logins have been from `10.8.0.4` (VPN) with one exception: **direct login from 68.96.70.83 on Sun Jun 28 05:37 UTC** (not via VPN). This was likely the operator connecting directly during maintenance. Low concern given key-auth only.

**Verdict:** ✓ PASS — No unauthorized successful logins. All sessions use pubkey auth.

---

## 4. Failed Login Attempts

**Total today (2026-07-01 00:00–10:45 UTC): 113 Failed/Invalid attempts**

Top offending IPs:
| Source IP | Attempts | Notes |
|---|---|---|
| 160.202.47.241 | 25 | Persistent — tried `eventhorizonvpn` username (matches internal hostname) |
| 139.155.104.136 | 21 | Tried `eventhorizonvpn` username |
| 195.178.110.217 | 16 | Password brute-force on `root` |
| 66.116.196.243 | 12 | Generic scanner |
| 154.92.22.131 | 10 | Generic scanner |
| 178.105.9.204 | 8 | Generic scanner |
| 134.209.255.30 | 6 | Generic scanner |
| 111.231.105.41 | 5 | Tried `eventhorizonvpn` username |

Notable: three IPs tried the username `eventhorizonvpn` which matches the server's hostname (`BHN-LOSANGELES-US1` is in the banner, but `eventhorizonvpn` suggests they may have discovered the hostname via DNS or prior reconnaissance). No successful auth in any case.

All `Failed password for root` attempts are against password auth which should be disabled (pubkey-only). CrowdSec (port 6060) is running and should be banning persistent offenders.

**Verdict:** ✓ PASS — Volume is normal brute-force background noise for an internet-facing server. No successful unauthorized auth. CrowdSec active.

---

## 5. Backup Storage

### `/root/backups/` contents
| File | Size | Date | Type |
|---|---|---|---|
| `eventhorizon_pre_era5_20260628_002059.sql` | 26G | Jun 28 | Manual pg_dump (pre-ERA5 load) |
| `eventhorizon_pre_gold_20260629_141436.sql` | 29G | Jun 29 | Manual pg_dump (pre-gold build) |
| `eventhorizon_pre_datasource_20260629_184044.sql` | 30G | Jun 30 | Manual pg_dump (pre-datasource) |
| `weather_position_exits_pre_003_20260701_032112.sql` | 6.3K | Jul 1 | Table-only backup (today, migration 003) |
| `weather_position_exits_pre_004_20260701_033216.sql` | 7.2K | Jul 1 | Table-only backup (today, migration 004) |

**Total: 83G in /root/backups/**

### Cron jobs
| Schedule | Command | Status |
|---|---|---|
| Daily 02:30 UTC | `eh-backup backup` | ✓ Configured |
| Sunday 03:30 UTC | `eh-backup check` | ✓ Configured |
| Every 48h at 03:00 UTC | `eh-purge --auto` | ✓ Ran today at 03:00 UTC |
| Every 15 min | `eh-purge --check-capacity` | ✓ Active capacity guard |

eh-purge ran cleanly at 03:00 UTC today. It dumps to `/mnt/eh-hdd-cold/archives/postgres/` (the managed path) — NVMe was at 31% before and after. The `eh-purge` system is healthy.

**Verdict:** ⚠️ MONITOR

The three large manual pg_dumps (total ~85G) in `/root/backups/` are **not managed by eh-purge** — they live on the root partition (`/dev/vda2`, 77% full). They predate the current backup automation and will not be auto-rotated. With only 39G free on the root partition, leaving three 25–30G dumps there creates risk. Recommend moving to `/mnt/eh-hdd-cold/` or deleting after confirming they're no longer needed for rollback.

---

## 6. Disk Usage

| Filesystem | Size | Used | Avail | Use% | Status |
|---|---|---|---|---|---|
| `/dev/vda2` (root) | 169G | 123G | 39G | **77%** | ⚠️ Monitor — see backup note |
| `/mnt/eh-nvme-hot` | 101G | 32G | 70G | 32% | ✓ Healthy |
| `/mnt/eh-hdd-cold` | 399G | 29G | 371G | 8% | ✓ Healthy |
| `/boot/efi` | 511M | 6.1M | 505M | 2% | ✓ Healthy |

Block devices: `vda` (180G OS), `vdb` (101G NVMe, LUKS encrypted → `/mnt/eh-nvme-hot`), `vdc` (399G HDD, LUKS encrypted → `/mnt/eh-hdd-cold`). Both data volumes are encrypted at rest.

**Verdict:** ⚠️ MONITOR — Root partition at 77%, driven primarily by the unmanaged large backups in `/root/backups/` (83G). Not critical today but worth addressing before next major data load.

---

## 7. BHN Service Health

| Service | Status | Notes |
|---|---|---|
| `bhn-weather-orchestrator` | inactive (dead) | ✓ Normal — timer-triggered oneshot; last ran 10:42:57 UTC, exit=0 |
| `bhn-weather-collector` | inactive (dead) | ✓ Normal — timer-triggered oneshot; last ran 10:32:23 UTC, exit=0 |
| `bhn-weather-settlement-recon` | inactive | ✓ Normal — runs at 15:00 UTC daily |
| `n8n` | **active (running)** | ✓ Running since Jun 27 05:07 UTC (4 days); 365.9MB RAM |
| `bhn-wg1-hillsboro` | inactive | ⚠️ See note below |

**n8n recurring error:** `SQLITE_CONSTRAINT: FOREIGN KEY constraint failed` appears in logs every hour on the hour (06:07, 07:07, 08:07, 09:07, 10:07 UTC). The process is stable and running correctly — this appears to be a specific n8n workflow hitting an FK constraint in its SQLite metadata DB. Hourly cadence suggests a scheduled workflow with a bug.

**bhn-wg1-hillsboro:** Reports inactive via `systemctl is-active` but wg1 tunnel IS up and passing traffic (wg show confirms 2m 9s handshake, 4.07 GiB received). The service unit is likely a oneshot that set up the interface and exited — wg1 persists as a kernel interface. This is probably normal given the lifecycle described in `bhn-wg1-hillsboro.sh` via wg0 PostUp. Flag for confirmation.

**Verdict:** ✓ PASS for all trading services. ⚠️ MONITOR n8n SQLite FK error (hourly, stable process).

---

## 8. Sensitive File Permissions

### `/etc/bhn-trading/`

| File | Mode | Notes |
|---|---|---|
| `env` | 0640 (rw-r-----) | ✓ Root-only read; not world-readable |
| `strat9.env` | 0600 (rw-------) | ✓ Root-only |
| `kalshi_private.pem` | 0600 (rw-------) | ✓ Root-only — private key correct |
| `strat6.env` | **0644 (rw-r--r--)** | ⚠️ World-readable |
| `strat7.env` | **0644 (rw-r--r--)** | ⚠️ World-readable |
| `strat8.env` | **0644 (rw-r--r--)** | ⚠️ World-readable |
| `strat13.env` | **0644 (rw-r--r--)** | ⚠️ World-readable |

### `/opt/bhn/trading/`
Files are mostly 0755 (rwxr-xr-x) or 0644 (rw-r--r--). These are Python scripts, not credential files — world-readable is not a concern for source code. No credentials are hardcoded in the trading scripts (env-var based config confirmed).

**Verdict:** ⚠️ FLAG — `strat6.env`, `strat7.env`, `strat8.env`, `strat13.env` are world-readable (mode 0644). If these files contain API keys, passwords, or other secrets, they should be `chmod 640` or `chmod 600`. Even without other users on the system, world-readable secrets are a bad practice. Review contents and tighten permissions if needed.

Recommended fix (do not run until Fletch reviews):
```bash
chmod 640 /etc/bhn-trading/strat6.env
chmod 640 /etc/bhn-trading/strat7.env
chmod 640 /etc/bhn-trading/strat8.env
chmod 640 /etc/bhn-trading/strat13.env
```

---

## 9. PostgreSQL Access Controls

### Users

| Username | Superuser | Create DB | Notes |
|---|---|---|---|
| postgres | ✓ | ✓ | System superuser — expected |
| bhn_trader | ✗ | ✗ | Trading scripts user ✓ |
| bhn_weather_collector | ✗ | ✗ | Collector user ✓ |
| bootstrap_writer | ✗ | ✗ | Bootstrap ingestion ✓ |
| ehuser | ✗ | ✗ | n8n / DBeaver user ✓ |
| grafana_reader | ✗ | ✗ | Read-only Grafana ✓ |
| horizon_memory_writer | ✗ | ✗ | HORIZON writes ✓ |
| log_shipper | ✗ | ✗ | Log shipping ✓ |
| n8n_user | ✗ | ✗ | n8n dedicated user ✓ |
| agent_reader | ✗ | ✗ | Read-only agent ✓ |

Only `postgres` is superuser. All application users are unprivileged. ✓

### Active Remote Connections

| Client | User | Application | State |
|---|---|---|---|
| ::1 (loopback) | bhn_trader | — | idle |
| ::1 (loopback) | bhn_trader | — | idle |
| 10.8.0.4 (operator VPN) | ehuser | DBeaver 26.1.0 (multiple tabs) | idle |

All remote connections are from loopback or the operator's VPN IP (10.8.0.4). No unexpected remote connections. ✓

### pg_hba.conf Summary
- All local connections: peer or scram-sha-256 ✓
- Remote access: restricted to specific users + VPN subnets (10.8.0.0/24, 10.9.0.0/24) ✓
- Two entries use `md5` instead of `scram-sha-256`:
  - `bhn_trader` from 10.8.0.5 (NJ node)
  - `ehuser` from 10.8.0.4 (operator desktop)
- md5 is weaker than scram-sha-256 but still password-hashed; risk is low over VPN. Not a blocker.
- `bhn_weather_collector` allowed from 10.8.0.8 (Helsinki) and 10.8.0.6 (Hillsboro) ✓ expected.

**Verdict:** ✓ PASS — No unauthorized users, no superuser proliferation, no unexpected remote connections, remote access VPN-gated.

---

## 10. Today's Deployment Footprint

### Files modified in `/opt/bhn/trading/` (newer than cp1_data_sanity.py as baseline)

| File | Modified | Expected | Notes |
|---|---|---|---|
| `cp4_kelly_sizer.py` | Jul 1 10:38 UTC | ✓ | Pre-open filter + real market_ticker lookup |
| `exit_audit_logger.py` | Jul 1 10:35 UTC | ✓ | entry_no_ask_cents locking + real ticker |
| `core_trading_orchestrator.py` | Jul 1 10:35 UTC | ✓ | Real ticker logging |
| `weather_core.py` | Jul 1 01:43 UTC | ⚠️ | Modified at 01:43 UTC before today's session — confirm this was intended overnight work |
| `cp2_arb_check.py` | Jun 30 19:02 UTC | ✓ | Prior session (Jun 30) |
| `cp3_inference.py` | Jun 30 19:02 UTC | ✓ | Prior session (Jun 30) |

### Migrations applied today
| File | Applied | Result |
|---|---|---|
| `003_fix_entry_price_integrity.sql` | ~10:20 UTC | entry_no_ask_cents + entry_captured_at added, 9 rows backfilled |
| `004_add_real_market_ticker.sql` | ~10:31 UTC | real_market_ticker added, 10 rows backfilled, contract_ticker updated to real Kalshi format |

### Backups taken before schema changes
| File | Size | Coverage |
|---|---|---|
| `weather_position_exits_pre_003_20260701_032112.sql` | 6.3K | Pre-migration 003 ✓ |
| `weather_position_exits_pre_004_20260701_033216.sql` | 7.2K | Pre-migration 004 ✓ |

**Verdict:** ✓ PASS for today's deployment — three expected Python files updated, two migrations applied with backups taken. One minor flag: `weather_core.py` was modified at 01:43 UTC before the session started; confirm this was intentional overnight work.

---

## Flags for Morning Review

| # | Severity | Item | Action |
|---|---|---|---|
| 1 | ⚠️ Confirm | **Publicly exposed services**: Grafana (:3000), Netdata (:19999), AdGuardHome UI (:3001), cAdvisor (:8081), prometheus_wireguard (:9586/9617), Shadowsocks (:8388), DNS (:53) all listening on 0.0.0.0/`*`. Confirm which are intentional. Grafana conflicts with the recorded 2026-05-28 LA purge. | Review each; bind to 10.8.0.1 if not meant to be public |
| 2 | ⚠️ Monitor | **Root partition at 77%** — 83G of unmanaged manual pg_dumps in `/root/backups/` (not rotated by eh-purge). Jun 28–30 dumps are likely safe to delete or move to `/mnt/eh-hdd-cold/`. | Move or delete old dumps after confirming no rollback needed |
| 3 | ⚠️ Fix | **World-readable strat env files**: `strat6.env`, `strat7.env`, `strat8.env`, `strat13.env` all mode 0644. Should be 0640. | `chmod 640 /etc/bhn-trading/strat{6,7,8,13}.env` |
| 4 | ⚠️ Investigate | **n8n hourly SQLITE_CONSTRAINT FK error** — stable process but a workflow is hitting an FK constraint every hour. | Check n8n workflow logs to identify which workflow and what's causing it |
| 5 | ⚠️ Tighten | **Helsinki (10.8.0.8) missing WireGuard preshared key** — all other active peers have PSK configured. PSK provides quantum-resistance and extra auth layer. | Add PSK to the Helsinki peer on both ends |

---

---

## 11. Orchestrator Health & Paper Trade Baseline

**Timer:** `bhn-weather-orchestrator.timer` active (waiting), firing every 5 minutes. Three consecutive clean cycles at 10:32, 10:37, 10:42 UTC. Next trigger confirmed at 10:47 UTC.

**Deployment-window failures (note — not a security issue):**
4 cycles failed with exit code 1 between 10:12–10:27 UTC. Root cause: mid-deployment inconsistency while cp4_kelly_sizer.py and exit_audit_logger.py were being updated in sequence during Task 4. Intermediate file state caused import/schema errors. Recovered cleanly at 10:32 with no data corruption.

**Paper trade baseline as of 10:42 UTC 2026-07-01 (10 open, 0 settled):**

| Station | Target | Bucket | Contract Ticker | Current ¢ | Entry ¢ | Contracts | Last Updated |
|---|---|---|---|---|---|---|---|
| KDEN | 2026-07-01 | 91-92 | KXHIGHDEN-26JUN30-B91.5 | 4¢ | 6¢ | 2499 | 01:53 UTC |
| KDEN | 2026-07-02 | 86-87 | KXHIGHDEN-26JUL01-B86.5 | 86¢ | 75¢ | 116 | 02:13 UTC |
| KDEN | 2026-07-02 | 88-89 | KXHIGHDEN-26JUL01-B88.5 | 69¢ | 75¢ | 144 | 05:45 UTC |
| KDEN | 2026-07-02 | 90-91 | KXHIGHDEN-26JUL01-B90.5 | 59¢ | 80¢ | 169 | 05:45 UTC |
| KDEN | 2026-07-02 | 92-93 | KXHIGHDEN-26JUL01-B92.5 | 81¢ | 82¢ | 123 | 05:45 UTC |
| KLAX | 2026-07-02 | 69-70 | KXHIGHLAX-26JUL01-B69.5 | 61¢ | 69¢ | 163 | 05:45 UTC |
| KLAX | 2026-07-02 | 71-72 | KXHIGHLAX-26JUL01-B71.5 | 50¢ | 67¢ | 200 | 05:45 UTC |
| KMIA | 2026-07-02 | 90-91 | KXHIGHMIA-26JUL01-B90.5 | 77¢ | 90¢ | 129 | 03:03 UTC |
| KMIA | 2026-07-02 | 92-93 | KXHIGHMIA-26JUL01-B92.5 | 38¢ | 63¢ | 263 | 10:42 UTC |
| KMIA | 2026-07-02 | 94-95 | KXHIGHMIA-26JUL01-B94.5 | 84¢ | 84¢ | 119 | 10:42 UTC |

**Notes:**
- All `contract_ticker` values are real Kalshi format — TICKET-W1 fix applied this session ✓
- KDEN Jul 1 91-92 settles at **22:00 UTC tonight** — first real settlement event
- KDEN positions show stale `last_updated` (01:53–05:45 UTC) because KDEN is outside market hours (04:xx MDT); correct behaviour
- KDEN 91-92 shows 2499 contracts — sized at current 4¢ market price, not 6¢ true entry. Known artefact of the dedup keeping the newest row; `entry_no_ask_cents = 6¢` now recorded correctly
- KMIA 94-95 is a new position first captured at 10:42 UTC this session

---

*Audit completed: 2026-07-01 ~10:45 UTC. All checks were read-only. No changes made to LA.*  
*Next audit: after next major deployment or on first sign of capacity approaching 85%.*
