# WeatherBHN Data Standard Index
## Version 1.0 — June 13, 2026
## Owner: operator

This is the master index of all WeatherBHN documentation and standards.
Every WeatherBHN contributor should read this before touching any WeatherBHN
code, queries, or schema.

---

## CORE STANDARDS

### Timestamp Standard
`infrastructure/docs/WeatherBHN/WEATHERBHN_TIMESTAMP_STANDARD.md`

Mandatory for all Metabase queries and new code. Defines:
- Vocabulary per pipeline layer (retrieved / processed / calculated / snapshot /
  settled / observed / forecast / reconciled)
- Three-column display format: `[vocab]_time_utc`, `[vocab]_time_pt`, `mins_ago`
- Freshness thresholds per vocabulary
- Known bug: -1,086 min timestamps on weather_observations (tracked, unfixed as of Jun 12)

---

## STRATEGY DOCUMENTATION

### WeatherBHN Trading Strategy (Manual Phase 1)
`infrastructure/docs/WeatherBHN/WEATHERBHN_TRADING_STRATEGY.md`

Complete manual trading methodology:
- Boundary split + tail No strategy
- Position sizing (Kelly criterion, half-Kelly, liquidity caps)
- Entry/exit criteria checklist
- Known city biases (preliminary — June 12 data only)
- Phase 2 automation targets

### Stop-Loss Automation Specification (Phase 3)
`infrastructure/docs/WeatherBHN/WEATHERBHN_STOP_LOSS_SPEC.md`

Spec for `weather_position_monitor.py`:
- 4 stop-loss triggers (prob shift, dollar loss, forecast revision, time tightening)
- Tail No exception (relaxed thresholds for entry P(YES) < 5%)
- `weather_position_exits` table DDL
- 9-step implementation order (currently at step 5-6, STOP_LOSS_DRY_RUN=true)

---

## METABASE QUERIES

### Master Query File
`infrastructure/docs/metabase/CLEAN_QUERIES.sql`

All production Metabase queries (14 total):
- Q1: Daily Edge Sheet (main trading view)
- Q2: Latest NWS vs GFS Forecasts
- Q3: Live Kalshi Market Prices
- Q4: Forecast Accuracy — NWS Error by City
- Q5: Calibration Progress (30-day tracker)
- Q6: Data Freshness by Source + Station
- Q7: Kalshi P&L and Active Positions
- Q16: Kelly Sizing (Market Only + Liquidity)
- Q17: Kelly Sizing (BHN Edge + Liquidity) — requires calibrated_prob
- Q18: Pre-Trade Liquidity Scanner
- Q19: BHN Overall Scorecard — headline win rate vs market (FORMULA/MODELS, pin top)
- Q20: Signal Performance by Edge Tier (FORMULA/MODELS)
- Q21: Recent Recommendations + Results — raw trade log (FORMULA/MODELS)
- Q22: Performance by City (FORMULA/MODELS)

### Individual Kelly + Liquidity Queries
`infrastructure/docs/WeatherBHN/queries/KELLY_MARKET_ONLY.sql`
`infrastructure/docs/WeatherBHN/queries/KELLY_BHN_EDGE.sql`
`infrastructure/docs/WeatherBHN/queries/LIQUIDITY_SCANNER.sql`

### Performance Tracking Queries (FORMULA/MODELS tab)
`infrastructure/docs/WeatherBHN/queries/BHN_OVERALL_SCORECARD.sql`
`infrastructure/docs/WeatherBHN/queries/BHN_EDGE_TIER_PERFORMANCE.sql`
`infrastructure/docs/WeatherBHN/queries/BHN_RECENT_RESULTS.sql`
`infrastructure/docs/WeatherBHN/queries/BHN_CITY_PERFORMANCE.sql`

### Metabase Setup Guide
`infrastructure/docs/metabase/METABASE_SETUP_GUIDE.md`

How to add queries to Metabase, configure tabs, and pin dashboards.

---

## SCHEMA MIGRATIONS

`infrastructure/docs/WeatherBHN/migrations/`

| File | Description | Applied |
|------|-------------|---------|
| 001_create_weather_position_exits.sql | Stop-loss exits table | 2026-06-13 ✓ |

---

## PIPELINE LAYERS

| Layer | Tables | Source | Status |
|-------|--------|--------|--------|
| Bronze | weather_bronze_nws_forecast_snapshots | NWS API | Running |
| Bronze | weather_bronze_openmeteo_forecast_snapshots | Open-Meteo | Running |
| Bronze | weather_bronze_kalshi_market_snapshots | Kalshi API | Running |
| Bronze | weather_bronze_nws_actuals | NWS CLI | Running |
| Silver | weather_silver_forecast_conformed | Bronze → Silver | Running |
| Silver | weather_silver_market_conformed | Bronze → Silver | Running |
| Silver | weather_silver_actuals_conformed | Bronze → Silver | Running |
| Silver | weather_silver_forecast_error | Silver join | Running |
| Gold | weather_gold_daily_edge_sheet | Edge calculator | Running |
| Gold | weather_gold_calibrated_probabilities | Calibrator | Awaiting VC backfill |
| Accuracy | weather_model_accuracy | Settlement recon | Running (timer: 15:00 UTC daily) |
| Risk | weather_position_exits | Stop-loss monitor | Running (timer: every 60s, DRY_RUN=true) |

---

## SYSTEMD SERVICES (on LA — 10.8.0.1)

| Unit | Cadence | Description |
|------|---------|-------------|
| bhn-weather-collector.timer | Every 30 min | All weather data sources (NWS, Open-Meteo, Kalshi, ASOS) |
| bhn-weather-edge-calculator.timer | Every 5 min | Gold layer edge sheet refresh |
| bhn-kalshi-portfolio.service | Continuous | Kalshi position + fill poller |
| bhn-weather-settlement-recon.timer | Daily 15:00 UTC | Settlement reconciliation → weather_model_accuracy |
| bhn-weather-position-monitor.timer | Every 60s | Stop-loss monitor (STOP_LOSS_DRY_RUN=true) |

---

## PENDING (operator action required)

1. **Visual Crossing API key** → `/etc/bhn-trading/strat9.env` as `VISUAL_CROSSING_API_KEY=`
   Then run: `python3 /opt/bhn/trading/weather_vc_backfill.py --dry-run` first
2. **Settlement recon live run** → `DRY_RUN=false python3 /opt/bhn/trading/weather_settlement_reconciliation.py`
   (classifier blocked automated run; safe to run manually — writes historical data only)
3. **HORIZON SMS alerts** for dead collectors (Section 6 item 4)
4. **kalshi_positions + kalshi_fills Metabase queries** (Section 6 item 5)
5. **Fix -1,086 min timestamps** on weather_observations + weather_gold_calibrated_probabilities

---

*WeatherBHN is a standalone trading system within BHN, similar to PokemonBHN.*
*Version: 1.0 — June 13, 2026*
