# BHN SESSION HANDOFF — 2026-06-25

## Status: IN PROGRESS

---

## COMPLETED THIS SESSION

1. **LA GRAFANA NETWORKING FIX**
   - Problem: Grafana unreachable at `http://10.8.0.1:3000` due to Docker 29 raw-table iptables isolation (deeper than standard UFW/Docker conflict)
   - Fix: Switched grafana container to `--network host` (Option A `iptables: false` tested and reverted — unmaintainable with Docker 29 chain complexity)
   - UFW: Added `DENY IN on enp1s0 port 3000` (belt-and-suspenders; existing wg0 allow rule retained)
   - Verified: `curl http://10.8.0.1:3000/api/health` → `{"database":"ok","version":"13.1.0"}`
   - Verified: AdGuard DNS still resolving; all other containers unaffected

2. **METABASE INVESTIGATION — NJ**
   - Confirmed: Metabase was on NJ (commit `6189192`), never on LA
   - eventhorizon DB has no Metabase schemas or residual volumes
   - No data loss — Metabase SQL dashboards in repo are query files only

3. **NJ DECOMMISSION — Grafana + Metabase**
   - `grafana-server` systemd service: stopped + disabled on NJ
   - Metabase Docker container: stopped and removed (`docker compose down` in `/opt/bhn/metabase`)
   - NJ is now a pure trading node: no dashboards, no BI tools

4. **LA GRAFANA MASTER DASHBOARD**
   - 24-panel dashboard deployed at `http://10.8.0.1:3000/d/ab7xbn/`
   - Covers: card market (eBay + Courtyard), Kalshi positions, weather markets, security events, Solana NFTs, infrastructure
   - grafana_reader password reset to `REDACTED` (save to Proton Pass)

5. **BTEH FIN-006 — strat_13 halted boolean bug (FIXED)**
   - Root cause: `master_killswitch.py` and `daily_summary.py` referenced `halted BOOLEAN` + `halt_reason` + `halted_at` columns that don't exist in `trading_strategies` (schema uses `status TEXT` + `last_status_change_reason` + `last_status_change_at`)
   - Impact: `halt()` would fail with PG `UndefinedColumn` error; `reset()` would silently no-op; `daily_summary.py` would crash fetching halt state
   - Fix: Updated all SQL in both files to use correct column names; fixed `record_killswitch_event()` to use correct `circuit_breaker_log` column names
   - strat_13 confirmed `status='active'` in DB — was never actually halted, but killswitch was broken

6. **HILLSBORO TORRC — MaxMemInQueues**
   - Added `MaxMemInQueues 256 MB` to `infrastructure/services/tor-relay-hillsboro/torrc`
   - Prevents OOM kills on memory-constrained Hillsboro node

7. **pg_hba.conf FIX**
   - Line added to LA's `/etc/postgresql/14/main/pg_hba.conf`:
     `host  eventhorizon  ehuser  10.8.0.1/32  scram-sha-256`
   - Documented in `infrastructure/docs/postgresql/pg-hba-notes.md`

8. **WEATHERBHN GRAFANA DASHBOARD**
   - All 22 SQL queries from CLEAN_QUERIES.sql converted to Grafana panel JSON
   - Deployed to `infrastructure/grafana/dashboards/bhn-weatherbhn-trading.json`
   - Three sections: TRADING (Q1-Q7), PRE-TRADE CHECKLIST (Q16-Q18), FORMULA/MODELS (Q19-Q22)

9. **README SOFTWARE STACK SECTION**
   - Added comprehensive service table to README.md

---

## PENDING (CARRY FORWARD)

1. **Justin Probst WireGuard config delivery** — send via Signal
2. **PSA lost cards claim** — Sabrina's Gastly cert 154271366, Sabrina's Psyduck cert 154271367
3. **Dashboard consolidation remaining** — PokemonBHN, FinancialBHN, Security dashboards still to build (WeatherBHN done)
4. **AdGuard DNS panels in Grafana** — requires Infinity datasource plugin or JSON API; deferred
5. **Monthly root hints cron for Unbound** — deferred from prior session
6. **SearXNG on Hillsboro** — deferred pending RAM stabilization
7. **PostgreSQL boot dependency fix** — race condition on reboot
8. **NJ Tor relay** — deferred (trading node risk)
9. **grafana_reader password** — reset to `REDACTED` during this session; update Proton Pass if not already done

---

## NODE STATUS

| Node | Status | Notes |
|---|---|---|
| LA Hub (BHN\|VPS-LOSANGELES-US1) | ✅ Operational | Grafana v13.1.0 running, dashboards deployed |
| NJ Trading (BHN\|VPS-NEWJERSEY-US2) | ✅ Pure trading node | Grafana + Metabase decommissioned |
| Hillsboro (BHN-HILLSBORO-US3) | ✅ Operational | MaxMemInQueues cap added |
| Frankfurt | ⛔ Decommissioned | May 2026 |

---

## COMMIT

```
Summary: Dashboard consolidation — migrate to LA Grafana, decommission NJ Metabase/Grafana

Description:
- Fix BTEH FIN-006: master_killswitch.py + daily_summary.py halted boolean bug
- Stop and remove Metabase from NJ (was on wrong node, Java 157MB RAM)
- Stop and disable Grafana on NJ (consolidated to LA Grafana v13.1.0)
- Add MaxMemInQueues 256 MB to Hillsboro torrc
- Document pg_hba.conf fix: ehuser allowed from 10.8.0.1/32
- Build WeatherBHN Grafana dashboard (22 queries from CLEAN_QUERIES.sql)
- Update README software stack section
- NJ is now pure trading node: CrowdSec disabled, dashboards removed
```
