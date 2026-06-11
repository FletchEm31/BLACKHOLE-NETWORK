# BHN Weather Trading — Metabase Dashboard Setup Guide

**Dashboard:** BHN FULL SYSTEM HEALTH  
**Strategy:** Strat 9 — Weather Alpha (Kalshi temperature contracts)  
**Status:** DRY_RUN=true, enabled=false — calibration phase (Day 1 of 30, started June 10, 2026)

---

## How to Add a Query

1. In Metabase: **New > Question > Native Query**
2. Select database: **eventhorizon** (PostgreSQL on LA node)
3. Paste the SQL from the relevant file
4. **Save** the question with the name below
5. Pin to **BHN FULL SYSTEM HEALTH** dashboard

---

## Query Reference

### 1. `weather_forecast_vs_market.sql` — Edge Detection (CORE)
**Save as:** `Weather: Forecast vs Market`

Shows every open Kalshi contract with:
- BHN's NWS and GFS model forecast for that city/date
- Kalshi's implied probability (market price)
- Raw edge = market price minus what BHN's model implies

This is the primary signal view. A large positive `raw_edge` means the market is pricing the event LOWER than BHN thinks it should be — potential YES bet. Large negative = potential NO bet.

**Returns 0 rows if:** No contracts are in `prediction_contracts` yet, or no recent Kalshi prices in `weather_contract_prices`.

---

### 2. `weather_data_freshness.sql` — Collector Health
**Save as:** `Weather: Data Freshness`

Shows the most recent data timestamp per city and source. Columns:
- `source_label` — NWS gridpoints, open-meteo:gfs_seamless, ASOS observations, Kalshi prices
- `minutes_ago` — How stale the data is

**Alert threshold:** Any `minutes_ago > 120` means a collector is down (collectors run every 30 min). Investigate with `journalctl -u bhn-weather-collector -n 50` on the LA server.

---

### 3. `weather_calibration_tracker.sql` — Go-Live Tracker
**Save as:** `Weather: Calibration Progress`

Tracks how many days of paired forecast + observation data exist per city. Need **30+ paired days** before going live. Target: **July 10, 2026**.

- `paired_days` — Days with both a NWS forecast AND an ASOS observation (these are what get used for bias correction)
- `pct_complete` — % of the 30-day requirement met
- `days_remaining` — Days until strategy can go live

---

### 4. `weather_active_positions.sql` — Live Bets
**Save as:** `Weather: Active Positions`

Shows all weather bets placed via the strategy. Will be **empty** until `rules.json` has `enabled=true` and `live_execution_enabled=true`. Once live, shows:
- Contract, city, side, stake, edge at entry
- Win/loss status and P&L
- Running totals: total P&L, wins, losses

---

### 5. `weather_market_prices.sql` — Kalshi Prices
**Save as:** `Weather: Market Prices`

Live snapshot of what Kalshi is implying for each temperature contract. Columns:
- `implied_probability` — Market's probability that contract resolves YES (0-1)
- `threshold_op` / `threshold_value` — The temperature threshold (e.g. `>` `92`)
- `price_age_minutes` — How fresh the latest price snapshot is

---

### 6. `weather_quick_check.sql` — Fallback Sanity Checks
**Save as:** (paste each BLOCK as a separate question)

Simple no-join queries — useful in Days 1-7 when complex joins may return 0 rows because data is still sparse. Add these as individual cards:

- **Block 1:** Latest forecasts (last 6 hours)
- **Block 2:** Row counts + coverage per city/model
- **Block 3:** Latest ASOS observations
- **Block 4:** Latest Kalshi price snapshots
- **Block 5:** All known open prediction contracts

---

### 7. `weather_forecast_error.sql` — NWS Accuracy Tracker
**Save as:** `Weather: Forecast Error (NWS vs Actual)`

Once ASOS observations start arriving for dates that also have NWS forecasts, this query shows how accurate NWS was:

- `forecast_error_f` = `observed - predicted`
  - Negative = NWS ran **COLD** (predicted too low)
  - Positive = NWS ran **HOT** (predicted too high)

Use this to spot systematic bias before the model_calibration table is populated. If NWS consistently runs 2°F hot in Miami, your YES threshold estimates need adjusting.

**Will return 0 rows until:** At least one `target_date` has both a NWS forecast AND a matching ASOS observation.

---

## City Coverage

| ICAO  | City               | Kalshi Series       | Status       |
|-------|--------------------|---------------------|--------------|
| KMIA  | Miami              | KXHIGHMIA/KXLOWMIA  | Live + prices |
| KPHX  | Phoenix            | KXHIGHPHX/KXLOWPHX  | Live + prices |
| KDEN  | Denver             | KXHIGHDEN/KXLOWDEN  | Live + prices |
| KNYC  | New York City      | KXHIGHNY/KXLOWNY    | Forecasts only |
| KORD  | Chicago            | KXHIGHCHI/KXLOWCHI  | Forecasts only |
| KAUS  | Austin             | —                   | Forecasts only |
| KLAX  | Los Angeles        | KXHIGHLAX/KXLOWLAX  | Live + prices (added June 10) |
| KDFW  | Dallas/Fort Worth  | KXHIGHDFW/KXLOWDFW  | Live + prices (added June 10) |

---

## Calibration Timeline

| Date          | Milestone                                    |
|---------------|----------------------------------------------|
| June 10, 2026 | Calibration start — Day 0                    |
| July 10, 2026 | Target go-live — Day 30 (if 30 paired days)  |

Do not flip `enabled=true` in `rules.json` until `weather_calibration_tracker.sql` shows `paired_days >= 30` for all target cities.
