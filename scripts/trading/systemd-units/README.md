# BHN trading — systemd units

10 unit files that schedule the trading framework on NJ. All units use absolute paths under `/opt/bhn/trading/` (where the operator copies `scripts/trading/` at deploy time) and read shared env from `/etc/bhn-trading/env`.

## Inventory

| Unit | Type | Schedule | What it runs |
|---|---|---|---|
| `bhn-strategy@.service` | templated oneshot | (none — fired by timers) | `strategy_<name>.py` per `%i` parameter |
| `bhn-strategy-momentum.timer` | timer | Mon-Fri 17:00 ET | `strategy_momentum.py` |
| `bhn-strategy-value.timer` | timer | Mon-Fri 17:15 ET | `strategy_value.py` (staggered +15 from momentum) |
| `bhn-strategy-mean-reversion.timer` | timer | Mon-Fri 09:00-16:00 ET every 5 min | `strategy_mean_reversion.py` |
| `bhn-strategy-congress.timer` | timer | every 15 min 24/7 | `strategy_congress.py` |
| `bhn-strategy-prediction-market.timer` | timer | every 10 min 24/7 | `strategy_prediction_market.py` |
| `bhn-reconciliation.service` | oneshot | (none — fired by timer) | `reconciliation_daemon.py --once` |
| `bhn-reconciliation.timer` | timer | every 5 min 24/7 | reconciliation cycle |
| `bhn-trading-daily-summary.service` | oneshot | (none — fired by timer) | `daily_summary.py` |
| `bhn-trading-daily-summary.timer` | timer | Mon-Fri 16:15 ET | end-of-day digest + SMS |

Master killswitch (`master_killswitch.py`) has NO systemd unit — it's event-driven and called from `reconciliation_daemon.py` on mismatch, or manually by operator. See `infrastructure/docs/bhn-trading-incident-response.md`.

## Pre-deploy: env file + log dir + code path

Before enabling any timer, create on NJ:

```bash
sudo mkdir -p /opt/bhn/trading /etc/bhn-trading /var/log/bhn-trading

# Copy the strategy code + libs
sudo cp /opt/bhn/scripts/trading/*.py /opt/bhn/trading/
sudo cp /opt/bhn/scripts/trading/rules_schema.py /opt/bhn/trading/
sudo cp /opt/bhn/scripts/trading/validate_rules.py /opt/bhn/trading/

# Drop initial rules.json (from config-templates/)
sudo cp /opt/bhn/config-templates/rules.example.json /opt/bhn/trading/rules.json

# Env file — see infrastructure/docs/bhn-trading-deployment.md for the
# canonical list of required env vars. Mode 0600 because it contains
# secrets (Alpaca paper key, PG DSN, Twilio token, etc.).
sudo touch /etc/bhn-trading/env
sudo chmod 600 /etc/bhn-trading/env
# (edit /etc/bhn-trading/env with operator's saved secrets)
```

## Install the units

```bash
sudo cp /opt/bhn/scripts/trading/systemd-units/bhn-*.service /etc/systemd/system/
sudo cp /opt/bhn/scripts/trading/systemd-units/bhn-*.timer   /etc/systemd/system/
sudo systemctl daemon-reload
```

## Enable timers (cold start)

**Do NOT enable the strategy timers until paper trading is verified end-to-end.** Start with reconciliation only — it's read-only and catches deployment issues early.

```bash
# Phase 1: reconciliation only — read-only, validates Alpaca + PG + SQLite reachable
sudo systemctl enable --now bhn-reconciliation.timer

# Watch logs for clean cycles for 1-2 hours
sudo journalctl -fu bhn-reconciliation.service
tail -F /var/log/bhn-trading/reconciliation.log
# Look for: "reconciliation ok" or "no positions to compare" — no halts, no exceptions.

# Phase 2: daily summary — also read-only, fires once at 16:15 ET
sudo systemctl enable --now bhn-trading-daily-summary.timer

# Phase 3: ONE strategy in paper mode (recommended: mean_reversion since
# it fires most frequently and surfaces issues fastest)
sudo systemctl enable --now bhn-strategy-mean-reversion.timer

# Watch for ~1 full trading day before enabling the rest.

# Phase 4: remaining strategies (after Phase 3 looks clean)
sudo systemctl enable --now bhn-strategy-momentum.timer
sudo systemctl enable --now bhn-strategy-value.timer
sudo systemctl enable --now bhn-strategy-congress.timer
sudo systemctl enable --now bhn-strategy-prediction-market.timer
```

## Operational commands

```bash
# Show all BHN trading timers + next-fire times
systemctl list-timers 'bhn-*'

# Show enabled state per strategy
for s in momentum value mean-reversion congress prediction-market; do
  printf '%-25s' "$s"; systemctl is-enabled bhn-strategy-$s.timer
done

# Pause a single strategy (timer disabled but unit still installed)
sudo systemctl disable --now bhn-strategy-momentum.timer

# Pause EVERYTHING (use the killswitch instead — it flips the PG state too)
python3 /opt/bhn/trading/master_killswitch.py halt --reason "pre-deploy ops pause"

# Manual fire (debug — runs the strategy once outside its schedule)
sudo systemctl start bhn-strategy@momentum.service
```

## Logs

All units append to `/var/log/bhn-trading/*.log`:
- `reconciliation.log` — every 5 min
- `daily-summary.log` — once daily
- `strategy-<name>.log` — per strategy

Also in `journalctl -u <unit>` (systemd-tracked).

Recommend a logrotate config (not provided yet) — strategy logs can grow to ~50MB/month at full cadence.
