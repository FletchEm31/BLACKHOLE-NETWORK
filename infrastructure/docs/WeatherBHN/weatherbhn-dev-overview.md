# WeatherBHN System Overview
## Branch: weatherbhn-dev
## Last updated: 2026-06-27

---

## What Is WeatherBHN

WeatherBHN is BHN's automated trading system for Kalshi daily temperature markets.
It trades KXHIGH (daily high temperature) contracts across 8 US cities using
NWS forecast data as the primary signal.

Edge source: NWS is the settlement authority. BHN reads the same NWS data Kalshi
uses before the market has fully priced it, then corrects for known city biases.

Target markets: KMIA, KDEN, KPHX, KLAX, KDFW, KNYC, KORD, KAUS — daily HIGH temp.

---

## Architecture — Bronze → Silver → Gold

```
NWS API ──────────┐
Open-Meteo API ───┼──► Bronze Layer ──► Silver Layer ──► Gold Layer ──► Trade Decision
Kalshi API ───────┘
VC Historical ────┘
```

### Bronze (raw ingestion)

| Table | Source | Cadence |
|-------|--------|---------|
| weather_bronze_nws_forecast_snapshots | NWS API (hourly + point) | every 30 min |
| weather_bronze_openmeteo_forecast_snapshots | Open-Meteo ensemble | every 30 min |
| weather_bronze_kalshi_market_snapshots | Kalshi orderbook | every 5 min |
| weather_bronze_nws_actuals | NWS CLI report | daily ~03:00 UTC |
| weather_bronze_nbm_snapshots | NWS NBM percentiles | every 30 min |

### Silver (conformed)

| Table | Description |
|-------|-------------|
| weather_silver_forecast_conformed | Normalized NWS + Open-Meteo forecasts |
| weather_silver_market_conformed | Normalized Kalshi prices (yes_mid, implied_prob) |
| weather_silver_actuals_conformed | Normalized NWS CLI actuals (settlement source) |
| weather_silver_forecast_error | NWS forecast error vs actuals (training data) |
| weather_silver_calibration_training_set | Model training inputs (raw_prob, outcome) |

### Gold (outputs)

| Table | Description |
|-------|-------------|
| weather_gold_daily_edge_sheet | Per-contract edge, recommended action, Kelly stake |
| weather_gold_calibrated_probabilities | Model calibrated probabilities |
| weather_gold_city_day_features | 5 hourly-derived features per city/date |
| weather_model_accuracy | Settlement reconciliation (did BHN call it right?) |
| weather_position_exits | Stop-loss monitor output |

---

## Calibration

**Sources:**
1. NWS point forecast (primary) — bias-adjusted via `weather_silver_forecast_error`
2. NWS NBM percentiles (preferred over Gaussian when available)
3. Open-Meteo GFS ensemble (secondary — 50-member counting)

**Current calibrator:** v0_passthrough — raw model probabilities, no isotonic calibration.
Isotonic calibration activates in `calibrate_probabilities.py` once 30+ error pairs exist
per (station_code, contract_side) grain. Requires VC backfill to complete first.

**Bias correction:** Per-station NWS bias from `weather_silver_forecast_error` (last 30 days).
Requires MIN_BIAS_ROWS = 7 observations. Sigma from MAE requires MIN_SIGMA_ROWS = 30.

---

## Phase Roadmap

### Phase A — Single calibrated model (ACTIVE)

CP1 (data sanity) and CP2 (arb scanner) built — `weatherbhn_cp1_sanity.py`,
`weatherbhn_cp2_arbitrage.py`. Edge calculator live every 5 min.

**CP3 unblocked by:** VC backfill landing + VISUAL_CROSSING_API_KEY in strat9.env.
**CP4 unblocked by:** CP3 producing reliable Brier-beating probabilities.

### Phase B — Ensemble (FUTURE)

RF + GradientBoosting + Gaussian + Student's-t ensemble.
Do NOT begin until CP3 passes Mincer-Zarnowitz unbiasedness test on holdout data.

### Phase C — Spatial corrections (FUTURE)

HRRR/GFS native grid interpolation, analog resampling, inter-station correlation.
Requires much more settlement history; post-Phase B.

---

## Trading Rules

**Entry logic (CP1 → CP2 → CP3 → CP4):**
1. CP1 sanity gate — parse ticker, validate prices, check NWS amendments
2. CP2 arb check — if `yes_ask + no_ask + combined_fee < 1.00`, execute BUY_BOTH (paper only)
3. CP3 model — get calibrated probability from `weather_gold_calibrated_probabilities`
4. CP4 sizer — compute edge; trade if edge ≥ 5¢ (liquid) or 8¢ (illiquid)

**Fee model (Kalshi July 2025):**
- Maker: `ceil(0.0175 * p * (1-p) * n * 100) / 100`
- Taker: `ceil(0.07 * p * (1-p) * n * 100) / 100`
- Use maker_fee() for limit orders (standard); taker_fee() for market orders only.

**Edge computation (post A1/A3 fix):**
- YES edge = `calibrated_prob - yes_ask - maker_fee(yes_ask)`
- NO edge  = `(1 - calibrated_prob) - no_ask - maker_fee(no_ask)`
- `no_ask` is ALWAYS read from the real orderbook — never derived as `(1 - yes_ask)`

**Position sizing:** Quarter-Kelly (per CESifo paper recommendation); cap at 10% OI and 5% volume.

**Spread gates:** SKIP if spread > 20¢; REDUCE 50% if > 10¢; REDUCE 25% if > 5¢.

---

## Open Bugs and Known Issues (2026-06-27)

| # | Severity | Description | Status |
|---|----------|-------------|--------|
| A1 | HIGH | NO-side edge was inverted (used model_p not 1-model_p) + no_ask derived as 1-yes_ask | FIXED 2026-06-27 |
| A2 | HIGH | `datetime.now()` in calibrate_probabilities → future-dated rows, negative mins_ago | FIXED 2026-06-27 |
| A3 | MEDIUM | Flat FEE_BUFFER replaced with real Kalshi July-2025 maker/taker formula | FIXED 2026-06-27 |
| B1 | MEDIUM | fetch_nbm() returns 0 rows — NBM not populating; Gaussian fallback active | OPEN — investigate endpoint |
| B2 | LOW | wind_direction_deg missing from older NWS hourly rows → sea_breeze_flag NULL | OPEN — populates on new rows |
| B3 | LOW | weather_observations: observed_at shows -1,086 mins (TZ bug predates A2 fix) | DEFERRED |
| B4 | LOW | Visual Crossing backfill pending — calibration blocked until VC key set | PENDING operator action |

---

## Systemd Services (LA server)

| Unit | Cadence | Script |
|------|---------|--------|
| bhn-weather-collector.timer | every 30 min | weather_data_collector.py |
| bhn-weather-edge-calculator.timer | every 5 min | weather_edge_calculator.py |
| bhn-kalshi-portfolio.service | continuous | kalshi_portfolio.py |
| bhn-weather-settlement-recon.timer | daily 15:00 UTC | weather_settlement_reconciliation.py |
| bhn-weather-position-monitor.timer | every 60s | weather_position_monitor.py (STOP_LOSS_DRY_RUN=true) |

---

## Operator Pending Actions

1. Set `VISUAL_CROSSING_API_KEY=` in `/etc/bhn-trading/strat9.env`, then run:
   `python3 /opt/bhn/trading/weather_vc_backfill.py --dry-run`
2. Run settlement recon live:
   `DRY_RUN=false python3 /opt/bhn/trading/weather_settlement_reconciliation.py`
3. Apply migration_003: `psql -U ehuser -d bhn -f /opt/bhn/trading/sql/migration_003_raw_payload.sql`
4. Run Section B checks (see `section-b-checks.md`)
5. Investigate `fetch_nbm()` returning 0 rows — see `section-b-checks.md`

---

## Session Handoff Protocol

Before handing off to a new CC session:
1. Run `git log --oneline -10` on `weatherbhn-dev` to show recent work
2. Run Section B checks — paste results in the new session
3. Note any systemd service failures: `systemctl status bhn-weather-*.service`
4. Note the current calibration status: `SELECT station_code, count(*) FROM weather_silver_forecast_error GROUP BY station_code;`
5. Reference this document + `section-b-checks.md` + `WEATHERBHN_DATA_STANDARD.md`

When extracting `weatherbhn-dev` to a public repo: include `infrastructure/docs/WeatherBHN/`,
`scripts/trading/`, `sql/`. Exclude raw NOAA CSV files (data, not code).

---

## Key File Locations on LA

```
/opt/bhn/trading/
├── weather_data_collector.py     # Bronze ingest (NWS, Open-Meteo, Kalshi, ASOS)
├── weather_edge_calculator.py    # Gold edge sheet (A1/A3 fixed 2026-06-27)
├── calibrate_probabilities.py    # Gold calibrator (A2 fixed 2026-06-27)
├── fee_calculator.py             # Kalshi July-2025 fee formula
├── weatherbhn_cp1_sanity.py      # CP1: ticker parse + sanity gate
├── weatherbhn_cp2_arbitrage.py   # CP2: post-fee arb scanner (paper-only)
├── weather_settlement_reconciliation.py
├── weather_position_monitor.py
└── trading_core.py               # Shared DB connection, utilities
/etc/bhn-trading/
├── env                           # Shared env (BHN_PG_DSN etc.)
└── strat9.env                    # Strategy 9 secrets (KALSHI_API_KEY, VC key)
```
