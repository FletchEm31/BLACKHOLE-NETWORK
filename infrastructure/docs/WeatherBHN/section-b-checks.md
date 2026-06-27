# WeatherBHN Section B — Data Foundation Checks
## Date: 2026-06-27

Run all queries on the LA server via psql:
```
psql -U ehuser -d bhn
```

---

## B1 — VC Backfill Status

Did Visual Crossing historical actuals land?

```sql
SELECT station_code, count(*), min(target_date), max(target_date)
FROM weather_bronze_nws_actuals
GROUP BY station_code;
```

**Expected:** Each active station (KMIA, KDEN, KPHX, KLAX, KDFW, KNYC, KORD, KAUS)
should show rows dating back to ~2024-01-01 if VC backfill ran.
If count < 90 for any active station, calibration is still blocked.

---

## B2 — Orderbook Null Check

Are `no_bid`/`no_ask` populating from the Kalshi snapshot poller?

```sql
SELECT count(*) total,
  count(yes_bid) with_yes_bid, count(yes_ask) with_yes_ask,
  count(no_bid)  with_no_bid,  count(no_ask)  with_no_ask
FROM weather_bronze_kalshi_market_snapshots
WHERE created_at > NOW() - INTERVAL '24 hours';
```

**Expected:** `with_no_bid` and `with_no_ask` should equal `total`.
If `with_no_ask < total`, the A1 fix (which requires real `no_ask`) will
fall back to `mip` for BET_NO decisions — investigate the snapshot poller.

---

## B3 — Phase 2 Feature Population

Are the 5 hourly-derived features writing to `weather_gold_city_day_features`?

```sql
SELECT station_code, count(*),
  count(sea_breeze_flag)      AS with_sea_breeze,
  count(afternoon_storm_flag) AS with_storm_flag,
  count(peak_hour)            AS with_peak_hour
FROM weather_gold_city_day_features
GROUP BY station_code;
```

**Expected:** Coastal cities (KMIA, KLAX, KNYC) should have `with_sea_breeze > 0`
if `wind_direction_deg` is being collected. Inland cities will have `with_sea_breeze = 0`
(correct — sea_breeze_flag is NULL for inland). All cities should have `with_storm_flag`
and `with_peak_hour` populated wherever NWS hourly data exists.

---

## Quick Win — `/etc/hosts` sudo warning fix

Run once on the LA server to suppress the sudo hostname warning:

```bash
echo "127.0.1.1 BHN-LOSANGELES-US1" >> /etc/hosts
```

---

## Quick Win — Apply migration_003_raw_payload.sql

```bash
psql -U ehuser -d bhn -f /opt/bhn/trading/sql/migration_003_raw_payload.sql
```

Or paste the two ALTER TABLE statements directly:

```sql
ALTER TABLE weather_bronze_nws_hourly
    ADD COLUMN IF NOT EXISTS raw_payload JSONB;

ALTER TABLE weather_bronze_nws_forecasts
    ADD COLUMN IF NOT EXISTS raw_payload JSONB;
```

---

## Investigation — `fetch_nbm()` returning 0 rows

**Location:** `weather_data_collector.py` → `fetch_nbm()` function on LA.

**Symptom:** NBM (National Blend of Models) fetch returns 0 rows — NBM percentile
data is not populating `weather_bronze_nbm_snapshots`. The edge calculator falls
back to Gaussian approximation instead of the preferred NBM piecewise-CDF path.

**Most likely causes (check in this order):**

1. **URL path change.** NOAA restructured the NBM endpoint in 2024-2025.
   Check the current URL against: `https://api.weather.gov/gridpoints/{wfo}/{x},{y}/forecast`
   The NBM-specific endpoint may have moved. Verify the exact URL the collector is hitting.

2. **Grid-point lookup returning empty.** NBM requires a WFO grid-point lookup
   (`/points/{lat},{lon}`) before the forecast fetch. If the intermediate response
   changed format or the lat/lon resolution changed, `fetch_nbm()` may be getting
   an empty grid response silently.

3. **Response schema change.** NBM switched from XML to a mixed JSON/binary format
   in some endpoints. If the parser expects one format and the API now returns another,
   the parse produces 0 rows without raising an error.

4. **Time-window mismatch.** NBM cycles run at 00, 06, 12, 18 UTC. If the collector
   request window is too tight (e.g., requests at 03 UTC for a cycle that hasn't run yet),
   `fetch_nbm()` may return an empty response that the caller treats as 0 rows.

**To diagnose on LA:**
```bash
# Run fetch_nbm() in isolation with verbose output
python3 -c "
import logging; logging.basicConfig(level=logging.DEBUG)
from weather_data_collector import fetch_nbm
rows = fetch_nbm('KMIA')
print(f'KMIA rows: {len(rows)}')
"
```
Then inspect the raw HTTP response and parse the URL being hit.

**Note:** Do NOT fix today — document and defer until the root cause is confirmed.
The Gaussian fallback continues to work; NBM just improves tail-bucket accuracy.
