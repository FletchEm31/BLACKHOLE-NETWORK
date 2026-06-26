# BHN trading framework — NJ deployment runbook

End-to-end procedure for deploying the trading framework (`scripts/trading/`) on `BHN-VPS-NEWJERSEY-US2`. Assumes NJ has been bootstrapped, is reachable from LA over WG (`<BHN_WG_NJ_IP>`), and has Docker installed (or accepts the apt-installed Python stack — no Docker required for the trading framework itself).

## Prerequisites

- `BHN-VPS-NEWJERSEY-US2` bootstrapped (status='online' in `nodes` table on LA)
- WG tunnel LA↔NJ functional (`ssh nj 'ping -c 3 <BHN_WG_LA_IP>'` succeeds)
- LA PG reachable from NJ (`ssh nj 'pg_isready -h <BHN_WG_LA_IP> -p 5432'`)
- Alpaca **paper** account created — never use a live account until "Paper → Live" criteria in `bhn-trading-operations.md` are met
- Operator password manager entries exist:
  - `Alpaca-Paper-Key-ID`, `Alpaca-Paper-Secret`
  - `EH-Twilio-AuthToken`, `EH-Twilio-AccountSid`, `EH-Twilio-OperatorNumber`
  - `EH-PG-n8n_user-Password` (the PG role the trading scripts will use; reuse the existing `n8n_user` for ingest)

## Phase 0 — apply trading schemas on LA PG

```bash
# On LA, as root
cd /opt/bhn   # or wherever the repo lives — `git pull origin main` first
sudo -u postgres psql -d eventhorizon -f sql/trading-schema.sql
sudo -u postgres psql -d eventhorizon -f sql/strategy-5-weather-schema.sql  # if not already applied
sudo -u postgres psql -d eventhorizon -f sql/alerts-schema.sql               # circuit_breaker_log + alert routing

# Verify the canonical tables exist
sudo -u postgres psql -d eventhorizon -c "\dt trading_*"
sudo -u postgres psql -d eventhorizon -c "\dt paper_*"
sudo -u postgres psql -d eventhorizon -c "\dt circuit_breaker_*"
```

Expected: rows for `trading_strategies`, `paper_trades`, `paper_signals`, `circuit_breaker_log`, `strategy_performance`, `reconciliation_heartbeat`. If any are missing, fix the schema apply before proceeding.

## Phase 1 — stage code + dirs on NJ

```bash
# From operator's workstation
ssh nj   # or: ssh -J root@frankfurt root@<BHN_WG_NJ_IP> (if direct alias not configured)

# === On NJ, as root ===
mkdir -p /opt/bhn /opt/bhn/trading /etc/bhn-trading /var/log/bhn-trading /var/lib/bhn/trading

# Pull the repo (read-only clone is fine — NJ never pushes)
git clone https://github.com/FletchEm31/BLACKHOLE-NETWORK.git /opt/bhn || (cd /opt/bhn && git pull origin main)

# Copy the trading code + libs to a stable runtime path (lets you `git pull`
# /opt/bhn safely without affecting the running services until you re-copy)
cp /opt/bhn/scripts/trading/*.py /opt/bhn/trading/
cp /opt/bhn/scripts/trading/rules_schema.py /opt/bhn/trading/
cp /opt/bhn/scripts/trading/validate_rules.py /opt/bhn/trading/

# Drop the initial rules.json from the canonical template
cp /opt/bhn/config-templates/rules.example.json /opt/bhn/trading/rules.json

# Validate the rules file BEFORE the framework tries to load it
python3 /opt/bhn/trading/validate_rules.py /opt/bhn/trading/rules.json
# Expected: "rules.json validates against schema. 0 errors."
```

## Phase 2 — install Python dependencies on NJ

The framework uses `psycopg2-binary` (PG), `alpaca-py` (broker), `requests` (HTTP), `python-dateutil`, `pytz`. Install system-wide so systemd services have them:

```bash
DEBIAN_FRONTEND=noninteractive apt-get install -y python3-pip python3-psycopg2
pip3 install --break-system-packages alpaca-py requests python-dateutil pytz
```

If pip refuses on Debian 12+, use a venv at `/opt/bhn/trading/venv` and add `Environment=VIRTUAL_ENV=...` + adjust the systemd unit's `ExecStart` to the venv's python. Stick with system-wide install if it works — fewer moving parts.

## Phase 3 — env file (the secrets layer)

On NJ:

```bash
sudo touch /etc/bhn-trading/env
sudo chmod 600 /etc/bhn-trading/env
sudo nano /etc/bhn-trading/env
```

Required keys (paste values from password manager):

```bash
# Alpaca paper account
ALPACA_API_KEY=PK...                    # paper key ID
ALPACA_API_SECRET=...                   # paper secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# PostgreSQL on LA hub via WG tunnel
BHN_TRADING_PG_DSN=postgresql://n8n_user:<PW>@<BHN_WG_LA_IP>:5432/eventhorizon

# Local SQLite mirror path (for reconciliation 3-way compare)
BHN_TRADING_SQLITE=/var/lib/bhn/trading/state.sqlite

# Twilio (for killswitch + daily-summary SMS)
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_FROM_NUMBER=+1...
TWILIO_OPERATOR_NUMBER=+1...

# Optional: explicit timezone for any naive-datetime guards (defaults to America/New_York)
TZ=America/New_York

# Paper-only enforcement gate. Setting to "true" is part of the Paper→Live
# checklist in bhn-trading-operations.md — leave UNSET (or "false") for
# initial deploy.
TRADING_LIVE_MODE=false
```

Verify the env file is loadable by the script:

```bash
sudo -u root bash -c 'set -a; . /etc/bhn-trading/env; python3 /opt/bhn/trading/trading_core.py health'
# Expected: "Alpaca OK (paper)" + "PG OK" + "SQLite OK"
```

If any of the three fail, fix before installing units. The most common issues:
- `Alpaca FAIL`: env value typo, key revoked in Alpaca dashboard, or `ALPACA_BASE_URL` is live-api by accident
- `PG FAIL`: WG tunnel down, n8n_user password rotated, LA PG firewall blocking <BHN_WG_NJ_IP>
- `SQLite FAIL`: `/var/lib/bhn/trading/` not writable

## Phase 4 — install systemd units

```bash
# On NJ
sudo cp /opt/bhn/scripts/trading/systemd-units/bhn-*.service /etc/systemd/system/
sudo cp /opt/bhn/scripts/trading/systemd-units/bhn-*.timer   /etc/systemd/system/
sudo systemctl daemon-reload
systemctl list-unit-files 'bhn-*' --no-pager
```

Expected: 10 units listed (5 strategy timers + 1 strategy template + 2 reconciliation + 2 daily-summary).

## Phase 5 — phased enable

**Strict order — do not skip phases. Each phase validates that lower-risk components work before adding higher-risk ones.**

### Phase 5a — reconciliation only (read-only, no orders)

```bash
sudo systemctl enable --now bhn-reconciliation.timer
sudo journalctl -fu bhn-reconciliation.service --since '5 minutes ago'
tail -F /var/log/bhn-trading/reconciliation.log
```

Watch for 4-6 cycles (20-30 min). Expected log line per cycle:

> `reconciliation ok — alpaca:0 nj_cache:0 la_pg:0 (no positions to compare)`

Or if positions exist (they shouldn't at first deploy):

> `reconciliation ok — alpaca:N nj_cache:N la_pg:N — set-equal, qty-equal, price-tolerance-ok`

If any cycle exits non-zero, **do not proceed**. Investigate per `bhn-trading-incident-response.md` § "Reconciliation mismatch".

### Phase 5b — daily summary (read-only, fires once daily)

```bash
sudo systemctl enable --now bhn-trading-daily-summary.timer
systemctl list-timers bhn-trading-daily-summary.timer
```

Verify the next-fire is at 16:15 ET. If you're deploying after 16:15 ET, the unit will not fire until next trading day — manually trigger to verify it works:

```bash
sudo systemctl start bhn-trading-daily-summary.service
tail /var/log/bhn-trading/daily-summary.log
# Expected: SMS sent + JSON to log + strategy_performance row inserted for today
```

### Phase 5c — ONE strategy (mean_reversion, fires every 5 min so feedback is fast)

```bash
sudo systemctl enable --now bhn-strategy-mean-reversion.timer

# Watch ONE full trading day. During market hours you should see:
sudo journalctl -fu bhn-strategy@mean-reversion.service --since '1 hour ago'
tail -F /var/log/bhn-trading/strategy-mean-reversion.log
```

Expected per cycle (during market hours):
- `strategy_mean_reversion.py: starting cycle`
- `loaded rules.json (version=...)`
- `checking universe of N tickers`
- `0 BUY signals / 0 SELL signals` OR `placed paper-order BUY 10 AAPL @ 234.56`
- `cycle complete in 4.2s`

Outside market hours:
- `strategy_mean_reversion.py: market closed, no-op`

**Do not enable other strategies until Phase 5c has 1 full trading day clean.**

### Phase 5d — remaining strategies

After Phase 5c looks clean (no exceptions in logs, reconciliation stays green):

```bash
sudo systemctl enable --now bhn-strategy-momentum.timer
sudo systemctl enable --now bhn-strategy-value.timer
sudo systemctl enable --now bhn-strategy-congress.timer
sudo systemctl enable --now bhn-strategy-prediction-market.timer
```

## Phase 6 — Grafana visibility

If the existing trading dashboards aren't yet provisioned (check `infrastructure/grafana/dashboards/` for `bhn-trading-*.json`), they'll need to be added in a follow-up. Until then, the operator can query the trading tables directly:

```sql
-- Active vs halted strategies right now
SELECT strategy_id, status, halted, capital_allocated_usd, last_run_at
FROM trading_strategies
WHERE strategy_id != 'system' ORDER BY strategy_id;

-- Reconciliation heartbeat (most recent)
SELECT * FROM reconciliation_heartbeat ORDER BY checked_at DESC LIMIT 5;

-- Trades today
SELECT strategy_id, ticker, side, qty, entry_price, exit_price, status, opened_at, closed_at
FROM paper_trades WHERE opened_at::date = CURRENT_DATE ORDER BY opened_at DESC;
```

## Phase 7 — STATUS.md update

Update the NJ section in `STATUS.md` from `🔨 Framework committed to repo 2026-05-12...` to `✅ Deployed YYYY-MM-DD — N strategies enabled in paper mode, reconciliation green for X hours`. Commit + push.

## Rollback

If anything fundamentally broken (PG writes failing en masse, Alpaca returning unexpected errors, killswitch in halt-loop):

```bash
# Stop all timers — no more cycles fire
for t in $(systemctl list-unit-files 'bhn-*.timer' --no-pager | awk '/enabled/ {print $1}'); do
  sudo systemctl disable --now $t
done

# Halt at the framework layer (idempotent — sets PG state, cancels orders, closes positions)
python3 /opt/bhn/trading/master_killswitch.py halt --reason "deployment rollback"

# Verify no open positions remain on Alpaca
python3 /opt/bhn/trading/trading_core.py status
```

Then investigate offline. See `bhn-trading-incident-response.md`.
