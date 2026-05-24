# CC2 Session Findings — 2026-05-23

Trading-lane + data-coverage + credential audit. **All reads, no writes.** Companion to the parked FRA-removal draft (`CC2-FRA-REMOVAL-DRAFT-PARKED-2026-05-23.md`).

---

## 🚨🚨🚨 CRITICAL PRE-MARKET ITEM — RESOLVE BEFORE MON 2026-05-25 13:30 UTC 🚨🚨🚨

**`strat_13_rsi_intraday` is armed to trade at the next market open with NO schema validation AND the killswitch effectively bypassed.**

Concrete state:
- `trading_strategies.status='active'` for both `strat_13` and `system` row → `should_run()` returns True
- `trading_strategies.halted=true` boolean is set but **NOT checked** by `should_run()` (trading_core.py:602)
- Reconciliation detects JPST divergence every 5 min, refreshes `system.halted_at`, but does NOT flip `system.status` → killswitch stays disarmed
- `rules_schema.py:STRATEGY_SCHEMAS` does not contain `strat_13_rsi_intraday` → `validate_rules.py` silently skips all 4 validation layers for the strat_13 block in `rules.json`
- Next timer firing: **Monday 2026-05-25 13:30 UTC** (~38 hours from this writing)

**Minimum acceptable pre-market actions (pick at least one):**
1. **DISABLE strat_13 in rules.json** (`enabled: false`) — fastest hard-stop, requires no schema work
2. **Pause strat_13 in PG** (`UPDATE trading_strategies SET status='paused' WHERE id='strat_13_rsi_intraday'`) — also a hard-stop, doesn't touch rules.json
3. **Flip `system.status='halted'`** — engages the actual killswitch, blocks everything (matches what reconciliation should be doing)
4. **Add `STRAT_13_RSI_INTRADAY_SCHEMA` to `rules_schema.py`** and re-run validator — restores validation but does NOT fix the killswitch-bypass code path; still want one of 1-3 alongside

Recommended: combine **(2) pause in PG** for the immediate hard-stop with **(4) schema addition** so a clean re-enable on a future session has validation in place. Both are CC2-doable when authorized.

**Do not let Monday open arrive without resolving this.**

---

## 🚨 FIN-006 — Strat 13 is running without schema validation (NEW)

**Confirmed:** `strat_13_rsi_intraday` is **completely missing from `scripts/trading/rules_schema.py`**. The `STRATEGY_SCHEMAS` dict (line 727) maps only `strat_1` through `strat_8`. `validate_rules.py` iterates `STRATEGY_SCHEMAS.keys()` across all 4 validation layers — any strategy in `rules.json` not in that dict is **silently skipped**, not rejected.

**Impact:** our only currently-active strategy (the one driving "single-strategy validation mode") has zero field-level validation on its RSI thresholds, broker key, ticker, park_ticker, position-sizing limits, or any other parameter. A fat-finger edit to `rules.json` strat_13 block would deploy without warning. The "Layer 2.5 operator safe-bound hard rejects" (validate_rules.py:293) doesn't touch it.

**Fix scope (not executed):**
1. Add `STRAT_13_RSI_INTRADAY_SCHEMA` schema block to `rules_schema.py` matching the 8 existing schemas
2. Register it in `STRATEGY_SCHEMAS` dict
3. Add safe-bound entries (RSI 0-100, trailing_stop_pct ≤ 0.10, etc.)
4. Re-run `validate_rules.py` against current `rules.json` — expect clean pass once schema exists

---

## Strat 13 — true state right now

| Field | strat_13_rsi_intraday | system |
|---|---|---|
| `status` | **active** | **active** |
| `halted` (bool column) | true | true |
| `halted_at` | 2026-05-22 13:35:24 (stale, 28h) | 2026-05-23 18:00:02 (refreshed every 5 min) |
| `last_status_change_at` | 2026-05-15 15:47:46 | 2026-05-13 09:45:51 |

`should_run()` (trading_core.py:602) gates on `system.status == 'halted'` — NOT the `halted` boolean. `system.status='active'` → **killswitch OFF**. Strat 13 IS armed and will run on next timer firing (Mon 2026-05-25 13:30 UTC).

**Reconciliation paradox:** `bhn-reconciliation.timer` fires every 5 min on NJ, detects JPST divergence, refreshes `system.halted_at` — but does NOT write to `circuit_breaker_log` (last log entry 2026-05-19) and does NOT flip `system.status` to `'halted'`. Three concerns:
- Killswitch effectively bypassed
- No audit trail of detections since May 19
- `halted` boolean is informational-only despite the schema implying it's a gate

---

## NJ + Alpaca

- `bhn-strategy-rsi-intraday.timer`: active (waiting); last fired 2026-05-22 20:00:28 exit 0; next Mon 2026-05-25 13:30 UTC
- `bhn-reconciliation.timer`: active (waiting); fires every 5 min including weekends; last beat 2026-05-23 18:00:02
- Alpaca endpoint reachability from NJ: HTTP 401 in 61 ms (network path good)
- Yesterday's market session: 13 successful timer firings (15:30→20:00 UTC), all exit 0

**Verdict: NJ is alive, Alpaca path is alive, strat 13 traded successfully 13 times on 2026-05-22.** Only "problem" is the cosmetic `halted` tripwire.

---

## Strat 13 Alpaca account sharing

Operator's prior assumption was strat_13 shares with strat_4. **Actual layout** (rules.json → alpaca_key_id, masked at last 4):

| Strategy | Key (last 4) | Sharing |
|---|---|---|
| strat_3_mean_reversion | …UE2U | distinct ✅ |
| strat_4_momentum | …B4BT | distinct ✅ |
| strat_6_nasdaq_long | …VTG5 | shares default |
| strat_7_nasdaq_short | …VTG5 | shares default |
| strat_8_sector_rotation | …VTG5 | shares default |
| **strat_13_rsi_intraday** | **…VTG5** | **shares default with strat_6/7/8** |

JPST -7808 short on the …VTG5 account is **legacy strat_7_nasdaq_short residue** (paused 2026-05-14, open position never closed). Strat 13's reconciliation keeps catching this as a "divergence" because it's strat_13's expected state vs. the shared account's actual state.

**Isolation proposal (not executed):**
1. Operator creates new Alpaca paper account dedicated to strat_13 (alpaca.markets dashboard, possibly 2FA — **operator-only**)
2. Generate new key/secret
3. CC2: add `STRAT13_ALPACA_KEY_ID` + `STRAT13_ALPACA_SECRET` to `/etc/bhn-trading/env` on NJ
4. CC2: update `rules.json` strat_13 `broker.alpaca_key_id`
5. CC2: restart `bhn-strategy@rsi-intraday.service` (or wait for next timer)
6. CC2: reset `system.halted_at` + strat_13 `halted` to false; observe reconciliation passes clean

Legacy strat_7 -7808 JPST short stays in the …VTG5 account — separate cleanup (manual close or accept loss).

---

## Postgres credential rotation history

| Role | Last rotated | Source | Status |
|---|---|---|---|
| `ehuser` | 2026-05-08 | Commit `098608e`, Proton Pass entry `EH-Postgres-ehuser-2026-05-08`. Pre-rotation password was hardcoded in `eh-security-collector.py`, `eh-dns-collector.py`, `/root/.eh-metadata.env` | Current, likely fine |
| `log_shipper` | **Never rotated since initial setup** | Commit `f5207ab` (May 12) explicitly flagged as *"weak manually-set value as rotation-recommended"* | **Needs rotation** |
| `agent_reader` | No rotation event in git log | — | Unknown |
| `bootstrap_writer` | No rotation event in git log | — | Unknown |
| `n8n_user` | n8n encrypted credential, not in git | — | Unknown |

PG doesn't track password-change timestamps natively. `pg_authid.rolvaliduntil` is NULL for all roles (no expiry-based rotation configured).

**Open question for operator:** when you said "previously-exposed ehuser and log_shipper credentials" — is there a specific incident I don't have records for? The recent 5-23 handoff mentions `Gixen credentials exposed in PSA workflow JSON:153` but no equivalent entry for ehuser/log_shipper. Log_shipper rotation is recommended regardless on weak-password grounds.

---

## Data coverage — May-13 outage root cause confirmed

The 7-table dead cluster (`iptables_stats`, `dns_query_log`, `fail2ban_events`, `container_stats`, `node_resource_stats`, `node_disk_stats`, `tor_relay_stats`) stopped 2026-05-13 08:13-08:46 because **10 `/etc/cron.d/*` files end with `.sh` instead of `.sh\n`**. Cron error: `(*system*bhn-iptables-collector) ERROR (Missing newline before EOF, this crontab file will be ignored)`.

**Broken (10):** bhn-iptables-collector, bhn-dns-log-collector, bhn-fail2ban-collector, bhn-docker-stats-collector, bhn-resource-collector, bhn-wg-stats, bhn-conntrack-collector, bhn-n8n-stats-collector, bhn-pg-stats-collector, bhn-vnstat-collector

**Working (7):** bhn-coingecko-poller, bhn-eia-poller, bhn-fred-poller, bhn-freshness-check, bhn-security-events, bhn-tor-metrics-poller, bhn-usda-poller

**Fix (not executed):**
```bash
for f in /etc/cron.d/bhn-{iptables-collector,dns-log-collector,fail2ban-collector,docker-stats-collector,resource-collector,wg-stats,conntrack-collector,n8n-stats-collector,pg-stats-collector,vnstat-collector}; do
  printf "\n" >> "$f" && echo "fixed $f"
done
```

**Separate bug, not cron-newline:** `agriculture_prices` / `energy_prices` / `earnings_data` / `analyst_data` cron files end with `\n` correctly — the pollers fire but silently fail to write. Needs manual poller run with stderr capture in a dedicated session.

---

## Data freshness summary (full table available in chat history)

**🟢 Healthy + current:** security_events, reconciliation_heartbeat, n8n_chat_histories, weather_snapshots, news_articles, pulse_reports, macro_indicators, market_daily, macro_daily, ebay_listings, sold_listings, memories, pop_reports

**🟡 Stale (built-in monitor flagged):** agriculture_prices (10d), energy_prices (10d), earnings_data (10d), analyst_data (10d), crypto_market_data (0 rows), node_resource_stats (10d), wg_peer_stats (10d)

**🔴 Silent outage cluster (cron-newline bug):** 10 cron files broken since 2026-05-13 → 7 tables already known stale + ~5 more (conntrack, n8n_execution_stats, pg_*_stats × 3, node_bandwidth_stats) silently dead and not in `table_freshness_targets`

**🟡 0-row by design (unbuilt features):** conversation_sessions, call_transcripts (HORIZON voice — not built), market_bars*, options_chain_snapshots, prediction_market_data, ssh_*, etc.

---

## Recommended action queue

1. **Cron-newline fix** (10 files, trivial, restores ~10 collectors)
2. **Add the 10 newly-explained tables to `table_freshness_targets`** so next silent outage gets flagged
3. **Investigate USDA/EIA/Finnhub poller silent-fail** (separate cause from cron-newline)
4. **FIN-006 fix:** add `STRAT_13_RSI_INTRADAY_SCHEMA` to `rules_schema.py` so our only active strategy is schema-validated
5. **Log_shipper rotation** (weak password, never rotated)
6. **Strat 13 Alpaca isolation** — operator creates new paper account, CC2 wires steps 3-6
7. **Trading-core code audit:** reconcile the `system.status` vs `halted` boolean discrepancy — currently halted+active = false killswitch

---

## Parked / not executed this session

- **FRA peer removal on LA wg0** — draft approved 2026-05-23 Option A. Held until operator destroys Vultr `EH|VPS-FRANKFURT-EU1` (Monday earliest; operator locked out of Vultr until then). Draft: `CC2-FRA-REMOVAL-DRAFT-PARKED-2026-05-23.md`.
- All items in the action queue above — pending operator approval.

---

## Strategies paused this session (the one write that did happen)

Per operator decision 2026-05-23, single-strategy validation mode. UPDATE applied:
```
strat_3_mean_reversion  active → paused
strat_4_momentum        active → paused
strat_6_nasdaq_long     active → paused
strat_8_sector_rotation active → paused
```
`system` row intentionally NOT touched (virtual killswitch row). `strat_13_rsi_intraday` remains active per intent. 1/7/2/5 were already paused going in.
