# BHN trading framework — incident response runbook

What to do when something goes wrong. Each scenario has a triage section (figure out what happened) and an action section (fix or contain). Default disposition: when in doubt, halt and investigate — paper trading losses from a stop are zero; live trading losses from delayed response are not.

## Decision tree (first 30 seconds)

```
Got an SMS or alert?
├── "BHN trading halted (system)" → § Killswitch fired
├── "reconciliation mismatch on TICKER" → § Reconciliation mismatch
├── Circuit breaker trip (per-strategy) → § Circuit breaker trip
├── Alpaca-related (5xx, rate limit) → § Alpaca outage
├── PG unreachable from NJ → § LA PG unreachable
└── Nothing — but you noticed a strategy stuck → § Strategy stuck / hung
```

---

## § Killswitch fired

**Symptom:** SMS "BHN trading halted — reason: <X>". `trading_strategies` rows show `halted=true`. Strategies refuse to run. Alpaca orders cancelled, positions may or may not be closed depending on the `--close-positions` flag.

**Triage — identify the trigger source:**

```sql
-- Most recent halt event and what caused it
SELECT tripped_at, breaker_type, strategy_id, reason, raw_context
FROM circuit_breaker_log
WHERE breaker_type = 'SYSTEM_HALT'
ORDER BY tripped_at DESC LIMIT 5;
```

Possible `source` values:
- `reconciliation_daemon` → 3-way state mismatch (go to § Reconciliation mismatch for root cause)
- `circuit_breaker` → per-strategy breaker cascaded to a system halt (go to § Circuit breaker trip)
- `manual` → operator-initiated (you should know why)
- `unknown` → bug; capture the full context for follow-up

**Action — investigate before re-arming:**

1. Pull the full context for the halt event:
   ```sql
   SELECT raw_context FROM circuit_breaker_log
   WHERE breaker_type='SYSTEM_HALT' ORDER BY tripped_at DESC LIMIT 1;
   ```
2. Check Alpaca for orphaned state (orders that didn't cancel, positions that didn't flatten):
   ```bash
   ssh nj 'python3 /opt/bhn/trading/trading_core.py reconcile'
   # Or directly:
   ssh nj 'python3 -c "from trading_core import alpaca_client; c = alpaca_client(); print(c.get_all_positions()); print(c.get_orders())"'
   ```
3. If positions exist that PG says shouldn't:
   - Was `--no-close-positions` passed at halt time? If so, flatten manually:
     ```bash
     ssh nj 'python3 /opt/bhn/trading/master_killswitch.py halt --reason "manual cleanup after killswitch" --close-positions'
     # (This re-runs the close-positions path; orders already cancelled are no-ops.)
     ```
   - If `--close-positions` WAS passed but didn't take, look at Alpaca console for the failure reason (usually: market closed = order rejected).

**Re-arming (only after root cause is understood):**

```bash
ssh nj 'python3 /opt/bhn/trading/master_killswitch.py reset --confirm'
```

The reset path writes a `RESET` row to `circuit_breaker_log` with the operator's identity, completing the audit trail. Strategies will resume on their next timer fire.

---

## § Reconciliation mismatch

**Symptom:** SMS "reconciliation mismatch on TICKER" OR `reconciliation_heartbeat.outcome = 'mismatch'` rows accumulating.

**What it means:** One of the three sources (Alpaca, LA PG, NJ SQLite cache) disagrees with the others. The daemon's stance is: ANY mismatch = halt the framework. There are no severity tiers — divergence is divergence.

**Triage:**

```sql
-- Most recent mismatch event
SELECT checked_at, outcome, mismatch_details
FROM reconciliation_heartbeat
WHERE outcome = 'mismatch'
ORDER BY checked_at DESC LIMIT 5;
```

`mismatch_details` (JSONB) contains: which pair-wise compare failed (alpaca↔pg, alpaca↔sqlite, pg↔sqlite), the per-ticker delta, and the specific field (qty mismatch, avg_entry_price out of tolerance, set-membership delta).

**Common root causes:**

| Pattern | Likely cause |
|---|---|
| Alpaca has positions not in PG | Order placed via Alpaca console manually; OR strategy crashed AFTER placing order but BEFORE writing PG row |
| PG has positions not in Alpaca | Position closed at broker (stop-loss hit, or operator console) but trade_close path didn't run; OR Alpaca paper-account auto-reset wiped positions |
| NJ SQLite disagrees with PG (mutually) | NJ SQLite corrupted / deleted / WG tunnel was down during a dual-write |
| All three differ in different ways | Race condition during a strategy crash mid-trade; or NJ clock skew vs LA |

**Action:**

1. Halt is already in place (the daemon called it). Don't reset yet.
2. Take a snapshot of all three sources for audit:
   ```bash
   ssh nj 'python3 /opt/bhn/trading/trading_core.py status --json' > /tmp/reconcile-snapshot-$(date +%s).json
   ```
3. Resolve to ONE canonical truth:
   - **Alpaca is broker-of-record** for live trading. For paper, Alpaca is still authoritative.
   - Reconcile PG to match Alpaca:
     ```sql
     -- Positions Alpaca claims but PG doesn't have → INSERT
     -- Positions PG claims but Alpaca doesn't have → UPDATE status='closed' with closed_at=NOW(), exit_price=<your-judgment-call>
     ```
   - Delete NJ SQLite and let it regenerate from PG:
     ```bash
     ssh nj 'rm /var/lib/bhn/trading/state.sqlite'
     ```
   - On next strategy cycle, dual-write re-populates SQLite from new trades.
4. Re-run reconciliation manually:
   ```bash
   ssh nj 'python3 /opt/bhn/trading/reconciliation_daemon.py --once --dry-run'
   ```
   `--dry-run` reports without halting. If clean, drop the flag and run one more cycle live — if THAT exits 0, the mismatch is resolved.
5. Re-arm killswitch per § Killswitch fired.

**Prevention:**

- Never place orders via Alpaca console — always via the framework, so PG sees it
- WG tunnel monitoring (existing `bhn-node-down` alert covers NJ being offline)
- Future: a `bhn-reconciliation-stale` alert (TBD — same shape as `bhn-tor-relay-stale`) catches the daemon silently dying

---

## § Circuit breaker trip

**Symptom:** SMS "circuit breaker tripped — strategy X — type Y — reason Z". `trading_strategies.halted=true` on one strategy (not necessarily a system-wide halt).

**Breaker types:**

| Type | What trips it | Default threshold |
|---|---|---|
| `DAILY_LOSS` | Day's realized P&L ≤ -X% of strategy capital | -2% (per strategy in rules.json) |
| `WEEKLY_LOSS` | Week's realized P&L ≤ -Y% | -5% |
| `MAX_DRAWDOWN` | Peak-to-trough drawdown ≥ Z% | -10% |
| `CONSEC_LOSSES` | N consecutive losing trades | 5 |
| `RATE_LIMIT` | Alpaca returned 429 N times in M minutes | 10 in 5 |

**Triage:**

```sql
-- Was this a transient or a real failure?
SELECT cb.tripped_at, cb.breaker_type, cb.reason, cb.raw_context,
       ts.halted, ts.last_run_at
FROM circuit_breaker_log cb
JOIN trading_strategies ts USING (strategy_id)
WHERE cb.strategy_id = '<strategy>'
ORDER BY cb.tripped_at DESC LIMIT 5;
```

- `RATE_LIMIT` trips that already auto-reset = transient. No action needed unless they're recurring (then check Alpaca's status page).
- `DAILY_LOSS` / `WEEKLY_LOSS` / `MAX_DRAWDOWN` = strategy is underperforming. Don't auto-reset — review the recent trade tape:
  ```sql
  SELECT * FROM paper_trades
  WHERE strategy_id='<strategy>' AND closed_at >= NOW() - INTERVAL '7 days'
  ORDER BY closed_at DESC;
  ```
  Then decide: tune `rules.json` (tighter stops, narrower universe, smaller positions), or accept the loss and re-arm.

**Action:**

For transient trips (RATE_LIMIT etc.), the strategy auto-resets after the cooldown. No operator action.

For loss-based trips, manual reset only after a deliberate decision:

```bash
ssh nj 'python3 /opt/bhn/trading/master_killswitch.py reset --confirm --strategy strat_4_momentum'
```

If the strategy keeps tripping the same breaker after reset, **disable the timer until the rules are tuned**:

```bash
ssh nj 'sudo systemctl disable --now bhn-strategy-momentum.timer'
```

Then update `rules.json` per the operations runbook's "Updating rules.json" section.

---

## § Alpaca outage

**Symptom:** Strategies logging `AlpacaAPIError: 5xx` or `requests.exceptions.ConnectionError`. Reconciliation may also fail.

**Triage:**

1. Check Alpaca's status page: https://status.alpaca.markets/
2. Check NJ→Alpaca reachability:
   ```bash
   ssh nj 'curl -fsS -o /dev/null -w "%{http_code}\n" https://paper-api.alpaca.markets/v2/account -H "APCA-API-KEY-ID: $ALPACA_API_KEY" -H "APCA-API-SECRET-KEY: $ALPACA_API_SECRET"'
   ```
   401 = key issue (rotate). 5xx = Alpaca-side. Timeout = NJ networking issue.

**Action:**

- If Alpaca is the issue: pause timers until status page goes green. The RATE_LIMIT/transient-error breakers should auto-halt strategies that hit it, so manual intervention may not be needed. Verify killswitch state per § Killswitch fired.
- If NJ networking is the issue: check WG tunnel + NJ public connectivity:
  ```bash
  ssh nj 'wg show'
  ssh nj 'ping -c 3 1.1.1.1'
  ```
  If NJ can't reach the public internet, check `bhn-node-down` alert in Grafana — the standard node-recovery flow applies.

---

## § LA PG unreachable

**Symptom:** Strategies logging `psycopg2.OperationalError: could not connect to server`. `reconciliation_daemon.service` exits non-zero. NJ SQLite still works (offline-tolerant) but the PG canonical store falls behind.

**Triage:**

```bash
ssh nj 'pg_isready -h 10.8.0.1 -p 5432'
ssh nj 'ping -c 3 10.8.0.1'
ssh nj 'wg show wg0 | grep -A 3 endpoint'
```

- `pg_isready` returns "accepting connections" = PG is up; problem is probably the strategy's credential / DSN
- `ping 10.8.0.1` fails = WG tunnel down; check the LA UFW gap pattern that bit NJ + Hillsboro (`STATUS.md:83` for NJ, `STATUS.md` Hillsboro section)
- `wg show` shows no handshake within the last 3 min = WG is dead; check both ends' UFW underlay rules

**Action:**

- If WG is down: restore the tunnel first (see the WG resolution notes in the relevant `STATUS.md` per-node section).
- If PG is down: standard PG recovery on LA. Trading framework will catch up via SQLite cache replay on next reconciliation cycle.
- If credential rotation is the cause (n8n_user password changed in PM but not in NJ's env file): update `/etc/bhn-trading/env` on NJ, restart any in-flight strategy services.

**Important:** while LA PG is unreachable, dual-writes from `trading_core.open_trade` will FAIL the PG write. The strategy code should treat PG-write failure as a hard error and halt the strategy (NOT continue writing only to SQLite — that creates exactly the kind of drift reconciliation is designed to detect). If you see strategies continuing to trade with `pg_write_failed=true` logged, that's a bug — file an issue and halt manually.

---

## § Strategy stuck / hung

**Symptom:** Timer fires every N minutes but the strategy log shows the last cycle running for hours without completing. Or `systemctl status bhn-strategy@<name>.service` shows the unit in "activating" forever.

**Triage:**

```bash
ssh nj 'systemctl status bhn-strategy@momentum.service'
ssh nj 'ps auxf | grep strategy_'
```

The systemd unit has `TimeoutStartSec=600` (10 min). After that, systemd kills the process and the timer fires again next cycle. If you're seeing the same strategy stuck multiple cycles in a row, it's hanging on the same op every time.

Common culprits:
- Alpaca API hanging (no response, no error — TCP keepalive expires eventually)
- PG advisory-lock contention (another strategy / reconciliation holding the lock)
- An external data source (FMP, Polymarket, Kalshi) blocking and not honoring the request timeout

**Action:**

1. Kill the stuck process:
   ```bash
   ssh nj 'sudo systemctl stop bhn-strategy@momentum.service'
   ```
2. Look at where it was last:
   ```bash
   ssh nj 'tail -100 /var/log/bhn-trading/strategy-momentum.log'
   ```
3. If it's clear which external API it hung on, disable the timer temporarily and add a request timeout to that call site if not already there. File a follow-up to make all external HTTP calls have explicit timeouts.

---

## When to call a human (this runbook is exhausted)

- Reconciliation mismatch that can't be resolved by reconciling to Alpaca (broker shows positions you don't recognize at all)
- Killswitch was triggered by `source='unknown'` (framework bug — capture state, halt, investigate)
- Multiple incidents stacking faster than you can triage (cascade — halt everything, walk away from keyboard, come back fresh in 30 min)
- Live-mode strategy losing real money outside of expected risk bounds (halt, flatten everything to cash via console if needed, audit later)
