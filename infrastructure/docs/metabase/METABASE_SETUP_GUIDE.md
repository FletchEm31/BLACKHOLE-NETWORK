# WeatherBHN — Metabase Dashboard Setup Guide

**Dashboard:** WeatherBHN
**Strategy:** Strat 9 — WeatherBHN (Kalshi temperature contracts)
**Status:** DRY_RUN=true, enabled=false — calibration phase (started June 10, 2026)
**Scope:** Bronze/Silver/Gold pipeline only. ENSO, degree_days, weather_commodity_signals,
and weather_bets are Phase 2/5 and excluded from this dashboard.

---

## How to Add a Query

1. In Metabase: **New > Question > Native Query**
2. Select database: **eventhorizon** (PostgreSQL on LA node)
3. Paste the SQL from `infrastructure/docs/metabase/CLEAN_QUERIES.sql`
4. **Save** the question with the name below
5. Pin to **WeatherBHN** dashboard

All 7 queries are in a single file: `infrastructure/docs/metabase/CLEAN_QUERIES.sql`

---

## Query Reference

### 1. Daily Edge Sheet — `QUERY 1`
**Save as:** `Weather: Daily Edge Sheet`

The main trading view. Shows every open Kalshi contract with BHN's calibrated
probability, market price, edge, and recommended action (BET_YES / BET_NO / SKIP).
Scoped to today + tomorrow. Primary signal before each trading session.

---

### 2. NWS vs GFS Forecasts — `QUERY 2`
**Save as:** `Weather: NWS vs GFS Forecasts`

Latest forecast for each city from both NWS gridpoints and Open-Meteo GFS.
`is_latest_run = TRUE` ensures only the most recent run per city is shown.
Use the `model_delta` column (NWS minus GFS) to gauge confidence.

---

### 3. Live Kalshi Market Prices — `QUERY 3`
**Save as:** `Weather: Kalshi Market Prices`

Current bid/ask/mid and volume for every open Kalshi weather contract.
`is_latest_snapshot = TRUE` gives one row per market. Check `market_liquidity_flag`
before placing — illiquid markets (volume < 100) have wide spreads.

---

### 4. Forecast Accuracy — `QUERY 4`
**Save as:** `Weather: Forecast Accuracy (NWS Error)`

Rolling 30-day NWS bias per city/feature. `avg_bias_f` positive = NWS running cold
(actual higher than predicted); negative = running hot. Feed this into the edge
calculator when switching from `SIGMA_DEFAULTS` to computed MAE.

---

### 5. Calibration Progress — `QUERY 5`
**Save as:** `Weather: Calibration Progress`

Tracks paired forecast + observation days per city. Need **≥ 30 paired days**
before going live. Target: **July 11, 2026**.
`READY TO CALIBRATE` status = can flip `enabled=true` in rules.json.

---

### 6. Data Freshness — `QUERY 6`
**Save as:** `Weather: Data Freshness`

Most recent ingest timestamp per source (NWS, GFS, Kalshi, Actuals) per city.
`minutes_ago > 120` means a collector is down. Investigate with:
`journalctl -u bhn-weather-collector -n 50` on the LA server.

---

### 7. Kalshi P&L and Active Positions — `QUERY 7`
**Save as:** `Weather: Kalshi Positions & P&L`

Live positions from `kalshi_positions` table (populated every collector cycle from
`/portfolio/positions`). Shows unrealized P&L, avg entry price, and payout if right.
Will show real data once `enabled=true` in rules.json.

---

## City Coverage

| ICAO  | City               | Kalshi Series            | Status         |
|-------|--------------------|--------------------------|----------------|
| KMIA  | Miami              | KXHIGHMIA / KXLOWMIA    | Live + prices  |
| KPHX  | Phoenix            | KXHIGHPHX / KXLOWPHX    | Live + prices  |
| KDEN  | Denver             | KXHIGHDEN / KXLOWDEN    | Live + prices  |
| KLAX  | Los Angeles        | KXHIGHLAX / KXLOWLAX    | Live + prices  |
| KDFW  | Dallas/Fort Worth  | KXHIGHDFW / KXLOWDFW    | Live + prices  |
| KNYC  | New York City      | KXHIGHNY / KXLOWNY      | Forecasts only |
| KORD  | Chicago            | KXHIGHCHI / KXLOWCHI    | Forecasts only |
| KAUS  | Austin             | —                        | Forecasts only |

---

## Calibration Timeline

| Date          | Milestone                                                     |
|---------------|---------------------------------------------------------------|
| June 10, 2026 | Calibration start — Day 0                                     |
| July 11, 2026 | Target go-live — Day 30 (if paired_days ≥ 30 for all cities) |

Do not flip `enabled=true` in `rules.json` until Query 5 shows
`READY TO CALIBRATE` for all target cities.

---

## Out of Scope (Phase 2/5 — not part of WeatherBHN dashboard)

These tables exist in the DB but are excluded from this dashboard:
- `enso_index` — NOAA CPC ENSO phase data (Phase 2 commodity signals)
- `degree_days` — HDD/CDD accumulation (Phase 2 UNG/natural gas signal)
- `weather_commodity_signals` — ETF directional signals (Phase 5)
- `weather_bets` — legacy Phase 1 bet audit stub (replaced by `kalshi_positions` + `kalshi_fills`)
