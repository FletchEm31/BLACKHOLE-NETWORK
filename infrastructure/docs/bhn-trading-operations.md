# BHN trading framework — operations runbook

What to do on each cadence + how to make changes safely. Assumes deployment is complete per `bhn-trading-deployment.md` and the framework is running in paper mode on NJ.

## Daily — operator-facing checks (5 min/day)

1. **Daily summary SMS** lands at ~16:15 ET (post-market). Scan for:
   - Realized P&L per strategy — outliers from the 7-day average are flagged in-message
   - Any strategy showing `halted=true` or `last_run` older than its cadence
   - "Open positions carried" count — should match what you'd expect from yesterday's signals
2. **Killswitch state** — quick Grafana check or:
   ```bash
   ssh la 'sudo -u postgres psql -d eventhorizon -c "SELECT strategy_id, halted FROM trading_strategies WHERE halted = true;"'
   ```
   Empty result = no halts. Any row = investigate per incident-response runbook.
3. **Reconciliation freshness** — every 5 min should have a row:
   ```sql
   SELECT MAX(checked_at), NOW() - MAX(checked_at) AS age FROM reconciliation_heartbeat;
   ```
   `age` > 15 minutes triggers the existing `bhn-tor-relay-stale`-style alert (TBD: add `bhn-reconciliation-stale` alert in `bhn-alerts.yaml`).

## Weekly — operator review (15 min/week, ideally Sunday)

1. **Strategy performance review**:
   ```sql
   SELECT strategy_id,
          SUM(realized_pnl_usd)           AS week_pnl,
          SUM(trades_opened)              AS opens,
          SUM(trades_closed)              AS closes,
          AVG(win_rate_pct)               AS avg_winrate
   FROM strategy_performance
   WHERE date >= CURRENT_DATE - INTERVAL '7 days'
   GROUP BY strategy_id
   ORDER BY week_pnl DESC;
   ```
2. **rules.json drift check** — repo vs deployed:
   ```bash
   diff /opt/bhn/scripts/trading/../../config-templates/rules.example.json /opt/bhn/trading/rules.json
   # Or compare the canonical hash from git against the deployed file's hash.
   ```
   Any drift = either an unsanctioned hand-edit on NJ (bad) or a planned override that should be reflected in repo (commit it).
3. **Circuit breaker log review**:
   ```sql
   SELECT strategy_id, breaker_type, reason, tripped_at, reset_at
   FROM circuit_breaker_log
   WHERE tripped_at >= CURRENT_DATE - INTERVAL '7 days'
   ORDER BY tripped_at DESC;
   ```
   Trips that auto-reset within minutes are usually transient (Alpaca rate-limit hiccup, etc.). Trips that required manual reset = follow up; the strategy may need rule tightening.

## Monthly — system maintenance (30 min/month)

1. **PG growth check** — the trading tables can balloon if a strategy is chatty:
   ```sql
   SELECT relname AS table_name,
          pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
          n_live_tup AS row_count
   FROM pg_stat_user_tables
   WHERE relname IN ('paper_trades','paper_signals','reconciliation_heartbeat','strategy_performance','circuit_breaker_log')
   ORDER BY pg_total_relation_size(relid) DESC;
   ```
   Anything over ~1GB warrants a retention review (drop rows older than X months for non-audit tables; keep `circuit_breaker_log` forever).
2. **Tor relay accounting** — check that Hillsboro / Frankfurt aren't approaching `AccountingMax` if Tor relay traffic is squeezing trading bandwidth (NJ doesn't currently run a Tor relay; this is a no-op unless `BHNNebulaUS2` gets deployed).
3. **Alpaca paper account balance reset** — Alpaca paper accounts get auto-reset every ~6 months OR can be manually reset. After a reset, reconciliation will halt (positions in PG no longer exist at broker). Plan: pre-reset, run `master_killswitch halt`, flatten everything, manually clear `paper_trades.status='open'` rows, then unhalt.

## Updating rules.json (planned changes)

Rules live in **two places**: `/opt/bhn/trading/rules.json` on NJ (the file strategies read), and `config-templates/rules.example.json` in the repo (the canonical template). Updates flow LA→repo→NJ:

```bash
# 1. On LA, edit the canonical template
nano /opt/bhn/config-templates/rules.example.json

# 2. Validate against the schema BEFORE pushing
python3 /opt/bhn/scripts/trading/validate_rules.py /opt/bhn/config-templates/rules.example.json

# 3. Commit + push
cd /opt/bhn && git add config-templates/rules.example.json && \
  git commit -m "trading: rules.json — <what changed and why>" && \
  git push origin main

# 4. On NJ, pull + copy + validate + restart strategies that read rules at startup
ssh nj
cd /opt/bhn && git pull origin main
cp /opt/bhn/config-templates/rules.example.json /opt/bhn/trading/rules.json
python3 /opt/bhn/trading/validate_rules.py /opt/bhn/trading/rules.json

# Strategies re-read rules at the start of each cycle, so changes take effect
# on the next timer fire — no service restart needed. Confirm:
tail -F /var/log/bhn-trading/strategy-<changed_strategy>.log
# Look for: "loaded rules.json (version=<new>)" on the next cycle
```

**Never edit `/opt/bhn/trading/rules.json` directly on NJ.** Drift between repo and deployed = silent loss of audit trail.

## Pausing / resuming a single strategy

```bash
# Pause (timer disabled, in-flight cycle finishes)
ssh nj 'sudo systemctl disable --now bhn-strategy-momentum.timer'

# Verify no future fires
ssh nj 'systemctl list-timers bhn-strategy-momentum.timer'

# Resume
ssh nj 'sudo systemctl enable --now bhn-strategy-momentum.timer'
```

For longer pauses (e.g. "halt for 2 weeks while I'm traveling"), pair with the framework-level halt so the strategy's PG state reflects the pause:

```bash
ssh nj 'python3 /opt/bhn/trading/master_killswitch.py halt --reason "operator on PTO 2026-06-01 to 2026-06-15" --strategy strat_4_momentum'
```

## Paper → Live transition

**Default posture: stay in paper.** Live trading carries real-money downside that the framework's circuit breakers mitigate but don't eliminate. The Paper→Live checklist exists so the transition isn't impulsive.

Required before flipping a single strategy to live:

- [ ] Strategy has been running in paper mode for ≥ 30 trading days
- [ ] `strategy_performance.realized_pnl_usd` > 0 across that window
- [ ] No unexplained circuit-breaker trips in the last 14 days (transient API issues OK; logic bugs not OK)
- [ ] `win_rate_pct` ≥ 0.50 across closed trades OR `profit_factor` > 1.3
- [ ] No reconciliation mismatches in the last 14 days
- [ ] Operator has a written rationale for going live (1-paragraph rationale in `infrastructure/docs/BHN SESSION UPDATES/`)

If all checked:

```bash
# 1. Create LIVE Alpaca account + fund it (operator's bank → Alpaca, ~1-2 day settlement)

# 2. On NJ, add LIVE keys to env file ALONGSIDE paper keys (don't overwrite — keep both)
sudo nano /etc/bhn-trading/env
# Add:
#   ALPACA_LIVE_API_KEY=AK...
#   ALPACA_LIVE_API_SECRET=...
#   ALPACA_LIVE_BASE_URL=https://api.alpaca.markets

# 3. Per-strategy live-mode approval flag in PG (NOT a global flip)
ssh la 'sudo -u postgres psql -d eventhorizon -c "UPDATE trading_strategies SET live_mode_approved=true WHERE strategy_id=\"strat_4_momentum\";"'

# 4. Set the global env gate
sudo nano /etc/bhn-trading/env
# Change TRADING_LIVE_MODE=false → TRADING_LIVE_MODE=true

# 5. trading_core.py respects defense-in-depth — even with TRADING_LIVE_MODE=true,
#    a strategy only goes live if its OWN live_mode_approved is also true.
#    All other strategies stay on paper keys.

# 6. Restart ONE timer cycle in foreground to validate
sudo systemctl start bhn-strategy@momentum.service
tail /var/log/bhn-trading/strategy-momentum.log
# Look for: "LIVE MODE: strategy strat_4_momentum approved + global gate on"
```

If any output looks wrong, immediately:

```bash
# Revert: unset env gate, unflag strategy
sudo sed -i 's/^TRADING_LIVE_MODE=true/TRADING_LIVE_MODE=false/' /etc/bhn-trading/env
ssh la 'sudo -u postgres psql -d eventhorizon -c "UPDATE trading_strategies SET live_mode_approved=false WHERE strategy_id=\"strat_4_momentum\";"'
sudo systemctl restart bhn-strategy@momentum.service
```

Watch the live-graduated strategy for at least 5 trading days before promoting another.

## Halting EVERYTHING (operator-initiated, not an incident)

```bash
# On NJ
python3 /opt/bhn/trading/master_killswitch.py halt --reason "<your reason>" --no-close-positions
# (drop --no-close-positions if you want to flatten everything to cash)
```

This:
- Flips `halted=true` on every strategy + the `system` row
- Cancels open Alpaca orders
- (optionally) Flattens positions
- SMS-es you the halt confirmation

To re-arm:

```bash
python3 /opt/bhn/trading/master_killswitch.py reset --confirm
```

Sticky: even after reset, the timers WILL fire and strategies WILL run unless individually disabled. If you want a longer pause, disable timers per the "Pausing" section above.

## Audit trail

Everything writes to PG. For any post-mortem:

```sql
-- All trades for a strategy in a date range
SELECT * FROM paper_trades WHERE strategy_id='strat_4_momentum'
  AND opened_at::date BETWEEN '2026-05-01' AND '2026-05-31'
  ORDER BY opened_at;

-- All signals (even those that didn't lead to a trade)
SELECT * FROM paper_signals WHERE strategy_id='strat_4_momentum'
  AND created_at::date BETWEEN '2026-05-01' AND '2026-05-31';

-- All circuit-breaker events
SELECT * FROM circuit_breaker_log WHERE strategy_id='strat_4_momentum';
```

Audit data is retained indefinitely. The 80%-of-PG-capacity safety net (`eh-purge --check-capacity`) does NOT purge trading tables — they're flagged as `keep-forever` in the purge config.

---

## WeatherBHN — Kalshi prediction market trading operations

WeatherBHN runs on LA (not NJ). All timers live on LA. The framework is always `DRY_RUN=true` + `KALSHI_PAPER_ONLY=true` until operator explicitly flips.

### Key tables

| Table | Updated by | Cadence |
|-------|-----------|---------|
| `weather_gold_daily_edge_sheet` | `weather_edge_calculator.py` | Every 5 min |
| `weather_model_accuracy` | `weather_settlement_reconciliation.py` | 10 AM ET daily |
| `weather_gold_contract_ledger` | `refresh_contract_ledger()` (called from recon) | 10 AM ET daily |

### Contract ledger — the primary analysis table

`weather_gold_contract_ledger` is the master performance table. One row per Kalshi contract ticker. Pre-settlement rows have NULL in `actual_tmax_f`, `contract_resolved_yes`, `paper_pnl`. After settlement those columns populate.

**Manual bootstrap** (after applying the migration for the first time):
```sql
-- Snapshot first:
sudo -u postgres pg_dump eventhorizon > /mnt/eh-nvme-hot/backups/pre-contract-ledger-$(date +%Y%m%d-%H%M).sql

-- Apply migration:
sudo -u postgres psql -d eventhorizon -f /opt/bhn/sql/migrations/2026-06-25-contract-ledger.sql

-- Bootstrap all historical contracts:
sudo -u postgres psql -d eventhorizon -c "SELECT refresh_contract_ledger(NULL);"
```

**Verify bootstrap:**
```sql
SELECT
    COUNT(*) AS total_rows,
    COUNT(*) FILTER (WHERE contract_resolved_yes IS NOT NULL) AS settled,
    COUNT(*) FILTER (WHERE recommended_action IN ('BET_YES', 'BET_NO')) AS bet_recommendations,
    ROUND(100.0 * COUNT(*) FILTER (WHERE bhn_correct)
          / NULLIF(COUNT(*) FILTER (WHERE bhn_correct IS NOT NULL), 0), 1) AS accuracy_pct,
    ROUND(SUM(paper_pnl), 2) AS total_paper_pnl
FROM weather_gold_contract_ledger;
```

**Refresh manually** (after any ad-hoc settlement recon run):
```sql
-- All dates:
SELECT refresh_contract_ledger(NULL);

-- One date:
SELECT refresh_contract_ledger('2026-06-20');
```

The function is called automatically from `weather_settlement_reconciliation.py` at the end of every reconciliation run (lines 277-286 of that script).

### Settlement reconciliation timing

NWS CLI reports publish ~6-8 AM ET for the prior day's observations. The settlement recon timer fires at 10 AM ET to give NWS time to publish:

```bash
# Check timer status on LA:
sudo systemctl status bhn-weather-settlement-recon.timer

# Check last reconciliation run:
sudo journalctl -u bhn-weather-settlement-recon.service --since today | tail -30

# Run manually for a specific date (dry-run first):
python3 /opt/bhn/scripts/trading/weather_settlement_reconciliation.py --dry-run --date 2026-06-20
python3 /opt/bhn/scripts/trading/weather_settlement_reconciliation.py --date 2026-06-20
```

If the recon runs but finds zero actuals, check that `weather_bronze_nws_actuals` has rows for that date:
```sql
SELECT station_code, target_date, final_tmax_f, report_issued_at
FROM weather_bronze_nws_actuals
WHERE target_date = '2026-06-20'
ORDER BY station_code;
```

### WeatherBHN paper → live checklist

**Default posture: stay in DRY_RUN.** Do not flip without all boxes checked.

- [ ] `weather_gold_contract_ledger` has ≥ 60 settled contracts with `recommended_action IN ('BET_YES','BET_NO')`
- [ ] `bhn_correct` rate ≥ 55% across those contracts
- [ ] Platt/isotonic calibration implemented and `calibrator_version` != `v0_passthrough`
- [ ] Edge distribution audit: BET_YES at edge ≥ 10% has higher accuracy than BET_YES at edge 5-10%
- [ ] VC backfill complete (3 years of actuals in `weather_bronze_visual_crossing_actuals`)
- [ ] Grafana contract-ledger dashboard reviewed for no obvious data-quality anomalies
- [ ] Daily loss limit reviewed and set in `strat9.env`
- [ ] Kalshi API key confirmed working (live, not sandbox)

If all checked, flip in `strat9.env` on LA:
```bash
sudo nano /etc/bhn-trading/strat9.env
# Change: DRY_RUN=true → DRY_RUN=false
# KALSHI_PAPER_ONLY stays true until operator explicitly removes it
sudo systemctl restart bhn-weather-edge-calculator.timer
```

Watch the next edge calculator run (5-min cycle) and confirm orders appear in `bhn-weather-contract-ledger` Grafana dashboard before removing `KALSHI_PAPER_ONLY`.
