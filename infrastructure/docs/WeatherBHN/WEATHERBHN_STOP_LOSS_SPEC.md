# WeatherBHN Stop-Loss Automation Specification
## Version 1.0 — June 12, 2026
## Status: PLANNED — Phase 3
## Priority: HIGH

## OVERVIEW

BHN needs automated risk management on open Kalshi positions.
The price poller captures market data every 5 seconds into
weather_bronze_kalshi_market_snapshots. Build a stop-loss monitor
on top of that existing infrastructure.

## NEW COMPONENT: weather_position_monitor.py

Location: /opt/bhn/trading/weather_position_monitor.py
Runs: every 60 seconds via systemd timer
Role: reads open positions + current prices, triggers exits

## STOP-LOSS TRIGGERS

### Trigger 1 — Implied Probability Shift (primary)
For YES contracts:
  IF entry_implied_prob - current_implied_prob >= PROB_SHIFT_THRESHOLD
  THEN trigger exit

For NO contracts:
  IF current_implied_prob - entry_implied_prob >= PROB_SHIFT_THRESHOLD
  THEN trigger exit

Default PROB_SHIFT_THRESHOLD = 0.20 (20 percentage points)
Set in strat9.env as STOP_LOSS_PROB_SHIFT=0.20

### Trigger 2 — Dollar Loss Threshold
  IF (entry_price - current_price) * contracts >= DOLLAR_LOSS_THRESHOLD
  THEN trigger exit

Default DOLLAR_LOSS_THRESHOLD = $2.00 per position
Set in strat9.env as STOP_LOSS_DOLLAR=2.00

### Trigger 3 — Forecast Revision Alert
  IF ABS(current_nws_forecast - entry_nws_forecast) >= FORECAST_SHIFT_F
  THEN flag for review

Default review threshold = 2.0F, hard exit = 4.0F

### Trigger 4 — Time-Based Tightening
> 6 hours to settlement: normal thresholds
2-6 hours: tighten PROB_SHIFT to 0.15
< 2 hours: tighten to 0.10
< 1 hour: NO auto-exits (let ride to settlement)

## TAIL NO EXCEPTION

Tail No contracts (entry prob_yes < 5%) use RELAXED thresholds:
  PROB_SHIFT_THRESHOLD = 0.40 (not 0.20)
  DOLLAR_LOSS_THRESHOLD = $5.00 (not $2.00)
  No time tightening until < 30 min to settlement

## EXIT EXECUTION

1. Log trigger to weather_position_exits table
2. Send HORIZON SMS: "STOP LOSS TRIGGERED: {city} {bucket} {side}"
3. Place limit sell at current_bid - 1c
4. If not filled in 60s: place market order
5. Log fill to kalshi_fills
6. Update kalshi_positions status

## NEW DB TABLE: weather_position_exits

```sql
CREATE TABLE weather_position_exits (
    id                  BIGSERIAL PRIMARY KEY,
    triggered_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    market_ticker       TEXT NOT NULL,
    city                TEXT NOT NULL,
    contract_side       TEXT NOT NULL,
    bucket_floor        NUMERIC,
    bucket_cap          NUMERIC,
    trigger_type        TEXT NOT NULL,
    entry_price         NUMERIC NOT NULL,
    exit_price          NUMERIC,
    contracts           NUMERIC NOT NULL,
    entry_implied_prob  NUMERIC NOT NULL,
    exit_implied_prob   NUMERIC,
    prob_shift          NUMERIC,
    dollar_loss         NUMERIC,
    forecast_at_entry   NUMERIC,
    forecast_at_exit    NUMERIC,
    forecast_shift_f    NUMERIC,
    exit_order_id       TEXT,
    fill_price          NUMERIC,
    realized_pnl        NUMERIC,
    notes               TEXT
);
```

## NEW ENV VARIABLES (add to strat9.env)

```
STOP_LOSS_ENABLED=true
STOP_LOSS_PROB_SHIFT=0.20
STOP_LOSS_DOLLAR=2.00
STOP_LOSS_FORECAST_SHIFT_F=2.0
STOP_LOSS_FORECAST_HARD_LIMIT_F=4.0
STOP_LOSS_TAIL_NO_THRESHOLD=0.05
STOP_LOSS_DRY_RUN=true
```

## DATA STANDARDS

All WeatherBHN timestamps follow the naming convention in:
`infrastructure/docs/WeatherBHN/WEATHERBHN_TIMESTAMP_STANDARD.md`

Summary: every timestamp appears in THREE columns in all Metabase queries:
- `[vocab]_time_utc` — raw UTC (server time)
- `[vocab]_time_pt` — Pacific time (operator local, auto-handles DST)
- `mins_ago` — integer minutes between now and that timestamp

For `weather_position_exits`, the relevant vocab is:
- `triggered_at` → `triggered_time_utc` / `triggered_time_pt` / `mins_ago`

For the master index of all WeatherBHN standards and documentation, see:
`infrastructure/docs/WeatherBHN/WEATHERBHN_DATA_STANDARD.md`

## IMPLEMENTATION ORDER

1. Create weather_position_exits table
2. Write weather_position_monitor.py with all 4 triggers
3. Add tail_no exception logic
4. Write systemd unit files
5. Add env variables to strat9.env
6. Test with STOP_LOSS_DRY_RUN=true for 7 days
7. Review dry-run logs
8. Set STOP_LOSS_DRY_RUN=false only with operator approval
9. Add HORIZON SMS alerts
