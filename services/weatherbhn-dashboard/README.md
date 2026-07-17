# WeatherBHN Trading Dashboard

Read-only planning/simulation tool for KDEN, KLAX, KMIA weather markets.
**Does not place orders, does not touch DRY_RUN/enabled flags, does not
read or write any CP1-4 decision logic.** Structured so adding one of the
other 5 BHN weather cities later is a config change (`CITIES` in
`app/main.py`), not a rebuild.

## What it shows

- Sigma reference strip: -4σ..+4σ markers computed from that day's live
  μ (`predicted_tmax_f`) / σ (`sigma_used`), preferring the entry-frozen
  `weather_position_exits_clean` columns when available, falling back to
  the live-decaying columns with a visible source badge otherwise.
- Probability-overlay chart (model Gaussian vs. market-implied chance) +
  a volume/open-interest/liquidity table, sharing the bucket x-axis.
- Full ladder with a NO and a YES mini price/investment/payout calculator
  on every row (maker-fee by default, matching the live trading
  architecture), a single-select "winning bucket" checkbox, and per-bucket
  σ-marker chips.
- Simulation summary: resolves mixed Yes/No positions across every row
  against the selected winning bucket, with gross/net totals.
- A manual trade journal (its own standalone table, never touched by any
  trading pipeline) for tracking informal 0σ-bucket Yes-bet simulations
  over time.

## Data sources

- Live ladder (price/volume/open interest/market_ticker): read directly
  from `weather_bronze_kalshi_market_snapshots` — this table already has
  `volume`/`open_interest` columns despite CP4's code comments claiming
  otherwise (collector was upgraded, CP4 was never updated to use it).
- μ/σ: `weather_position_exits_clean` (`final_entry_predicted_tmax_f`,
  `final_entry_sigma_used`).

## Deploy (LA, as root)

```bash
# One-time:
psql -U postgres eventhorizon -f sql/weatherbhn-dashboard-journal-schema.sql
sudo -u postgres psql eventhorizon -c "ALTER ROLE weatherbhn_dashboard WITH PASSWORD '<generate one>';"
cat > /etc/bhn-trading/weatherbhn-dashboard.env <<'EOF'
DATABASE_URL=postgresql://weatherbhn_dashboard:<password>@/eventhorizon?host=/var/run/postgresql
EOF
chmod 600 /etc/bhn-trading/weatherbhn-dashboard.env

# Every deploy:
./install.sh
```

Binds to `10.8.0.1:8098` (mesh-only, matches the Homarr/Grafana/Metabase
port pattern — not exposed to the public internet). Add a Homarr tile
pointing at `http://10.8.0.1:8098`.

## Refresh cadence

Frontend polls `/api/ladder` every 20s for live price/volume/chance data.
μ/σ only actually changes on the ~5-minute orchestrator cadence but is
served from the same endpoint — no separate poll loop needed. In-progress
user input (investment amounts, selected winning bucket) is never
overwritten by a refresh; only the live-data cells re-render.
