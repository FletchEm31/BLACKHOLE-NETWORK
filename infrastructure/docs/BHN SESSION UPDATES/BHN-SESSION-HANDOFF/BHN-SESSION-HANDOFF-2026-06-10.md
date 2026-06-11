# BHN SESSION HANDOFF — 2026-06-10
## For Next Claude Chat Session

---

## CRITICAL: READ THIS FIRST

Two back-to-back sessions today. Both are committed. Key wins:

1. **LA + Dallas added to all weather collectors** — operator was trading KXHIGHLAX and KXHIGHDFW on Kalshi with zero BHN forecast data. That gap is closed.
2. **Kalshi auth verified** — prod API key is live, exchange_active=true, trading_active=true.
3. **is_active bug fixed** — all Kalshi contracts were being written as `is_active=false` in `prediction_contracts` because the collector was checking `status == "open"` but Kalshi returns `status = "active"`. Fixed in kalshi_client.py. All 36 contracts now correctly `is_active=true`.
4. **Metabase SQL dashboard** — 8 validated query files in `infrastructure/docs/metabase/` with a full setup guide. All queries verified against the real DB schema.
5. **Calibration Day 1** — started June 10, 2026. Target go-live: July 10, 2026.

---

## COMMITS THIS SESSION

| Commit | Summary |
|--------|---------|
| `2898d42` | feat(strat9): add LA/Dallas cities + Metabase dashboard SQL |
| `7e2a6ea` | docs(strat9): Metabase weather trading dashboard SQL + guide |

---

## WEATHER TRADING — CURRENT STATE

### Strategy Controls (DO NOT CHANGE)
```
rules.json:  enabled = false
             dry_run = true
             live_execution_enabled = false
```
Do not flip any of these until `weather_calibration_tracker.sql` shows `paired_days >= 30` for all target cities.

### Kalshi Auth
- **Status: VERIFIED** (tested June 10, 2026)
- Exchange: `exchange_active=true`, `trading_active=true`
- Balance: $1.35
- Auth method: prod API key via `/etc/bhn-trading/strat9.env`

### City Coverage (8 cities)

| ICAO  | City               | Kalshi Series              | NWS Office | Added       |
|-------|--------------------|----------------------------|------------|-------------|
| KMIA  | Miami              | KXHIGHMIA / KXLOWMIA       | MFL        | Original    |
| KPHX  | Phoenix            | KXHIGHPHX / KXLOWPHX       | PSR        | Original    |
| KDEN  | Denver             | KXHIGHDEN / KXLOWDEN       | BOU        | Original    |
| KNYC  | New York City      | KXHIGHNY / KXLOWNY         | OKX        | Original    |
| KORD  | Chicago O'Hare     | KXHIGHCHI / KXLOWCHI       | LOT        | Original    |
| KAUS  | Austin             | —                          | EWX        | Original    |
| KLAX  | Los Angeles        | KXHIGHLAX / KXLOWLAX       | LOX        | 2026-06-10  |
| KDFW  | Dallas/Fort Worth  | KXHIGHDFW / KXLOWDFW       | FWD        | 2026-06-10  |

### DB Counts (as of June 10, 2026 ~03:00 UTC)
```
weather_forecasts:        ~3,700 rows  (adding KLAX + KDFW = +30 rows per run)
weather_observations:        ~91 rows
weather_contract_prices:    ~132 rows
prediction_contracts:         36 rows  (all is_active=true after bug fix)
enso_index:               ~2,336 rows
degree_days:                  24 rows
weather_snapshots:         ~1,471 rows
```

### Calibration Progress
- **Day 1 of 30** — started June 10, 2026
- Target go-live: **July 10, 2026**
- KLAX + KDFW have 0 observation_days (ASOS not yet populating for these new stations — check `weather_calibration_tracker.sql` next session)
- All original 6 cities: 1 forecast_day, 1 observation_day as of session end

### Known Gaps / Watch Items
- `threshold_op` and `threshold_value` columns in `prediction_contracts` are NULL — the kalshi_client upsert doesn't parse these from the Kalshi payload. The Metabase SQL handles this gracefully (uses `contract_id` + `title` for display). Not blocking.
- KLAX + KDFW observation_days = 0 — ASOS is collecting but `observed_at` dates haven't aligned with any forecast `target_date` yet. Will populate naturally within a day or two.
- Kalshi 429 rate-limit on `KXLOWLAX` during every kalshi_markets run — the retry backoff handles it cleanly (logged as WARNING, not ERROR). Not blocking.

---

## METABASE DASHBOARD

### Files Location
`infrastructure/docs/metabase/`

| File | Purpose |
|------|---------|
| `weather_forecast_vs_market.sql` | Core edge view: BHN model vs Kalshi implied prob |
| `weather_data_freshness.sql` | Collector health: minutes since last data per source |
| `weather_calibration_tracker.sql` | 30-day go-live tracker per city |
| `weather_active_positions.sql` | Live bets + P&L (empty until enabled=true) |
| `weather_market_prices.sql` | Latest Kalshi implied prob per contract |
| `weather_quick_check.sql` | 5 simple fallback queries — use during sparse early days |
| `weather_forecast_error.sql` | NWS accuracy: observed - predicted (neg=ran cold) |
| `METABASE_SETUP_GUIDE.md` | Full setup instructions, column explanations, city table |

### Setup
See `METABASE_SETUP_GUIDE.md` for step-by-step Metabase Native Query paste instructions.
Database name in Metabase: **eventhorizon** (PostgreSQL, LA node).

### All queries verified against real schema on June 10, 2026.

---

## IMMEDIATE PENDING TASKS (next session)

### 1. CHECK KLAX + KDFW ASOS OBSERVATIONS
In a few days, verify KLAX and KDFW are accumulating `observation_days` in the calibration tracker:
```sql
SELECT station_code, variable, forecast_days, observation_days
FROM weather_calibration_tracker_view -- or paste the SQL directly
WHERE station_code IN ('KLAX','KDFW');
```
If still 0 after 3+ days, investigate whether `CA_ASOS` / `TX_ASOS` Iowa State network codes are correct for these stations.

### 2. SET UP METABASE DASHBOARD
Paste the 8 SQL files into Metabase as Native Query questions and pin to BHN FULL SYSTEM HEALTH. Use `METABASE_SETUP_GUIDE.md`.

### 3. MONITOR CALIBRATION
Check weekly. Do not touch enabled/live_execution_enabled until paired_days >= 30 across all target cities.

---

## INFRASTRUCTURE REMINDERS

- **LA server:** `ssh root@10.8.0.1` (WireGuard tunnel — must be connected)
- **NJ SSH:** always port 2222
- **PostgreSQL:** `sudo -u postgres psql -d eventhorizon` (no -h flag)
- **Deploy method:** `scp` to `/opt/bhn/trading/` (not git clone)
- **Collector service:** runs on systemd timer on LA node (`bhn-weather-collector`)
- **Strategy flags:** never flip `enabled=true` or `live_execution_enabled=true` without 30 paired calibration days

---

*Session ended: 2026-06-10 | Branch: main | Commits: 2898d42, 7e2a6ea*
