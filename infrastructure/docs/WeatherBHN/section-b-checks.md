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

## Investigation — Kalshi Orderbook NULL yes_ask / no_ask (Task 3 — DEFERRED)

**Symptom:** 14.4% of rows in `weather_bronze_kalshi_market_snapshots` have NULL `yes_ask`
and `no_ask`. Observed across 7-day window.

**Root cause confirmed: thin overnight orderbook, NOT a poller bug.**

The poller has two price sources:
1. Market snapshot API (`yes_ask_dollars` / `no_ask_dollars` in the Kalshi response)
2. Orderbook fallback — calls `get_orderbook()` per ticker when snapshot lacks bid/ask;
   derives `yes_ask = 1 - best_no_bid` and `no_ask = 1 - best_yes_bid` (correct binary
   market convention: best ask on one side = 1 - best bid on the other).

When both sources return empty, `yes_ask` and `no_ask` stay NULL. This happens when
Kalshi's KXHIGH contracts have zero resting orders on either side.

**Hour-by-hour fill rate (last 7 days, UTC):**

| UTC hours | Fill rate (yes_ask populated) | ET equivalent |
|-----------|-------------------------------|---------------|
| 0–4       | 42–45% (mostly NULL)          | 7pm–midnight ET |
| 5–7       | 44–68%                        | midnight–3am ET |
| 8–13      | 73–80%                        | 4am–9am ET |
| 14–20     | 68–86% (peak)                 | 10am–4pm ET |
| 21–23     | 49–65%                        | 5pm–7pm ET |

Pattern is US market hours. KXHIGH contracts trade most actively during US daytime;
overnight books are thin or empty.

**CP1 handles this correctly:** `NULL_PRICES` gate is the first filter — contracts
without valid asks never reach the edge calculator.

**Edge calculator fallback:** When `_get_orderbook_asks()` returns None (no row with
both asks non-null), the edge calculator sets `adj_edge_no = -1.0` and falls back to
`mip` (yes mid-price) for `yes_ask`. BET_NO is suppressed. This is safe but means we
miss BET_NO opportunities during off-hours. Acceptable during paper-trading period.

**Recommended fix (deferred, not today):** None needed. The NULL rate will self-correct
as more contracts get posted and markets approach expiry (higher liquidity near settlement).
If overnight miss rate becomes a concern, add a min-liquidity requirement to CP1
(e.g., only trade contracts with OI > 100 and age < 6 h before settlement).

**What is NOT the cause:**
- API endpoint change: auth/URL is fine (200s with ask data during active hours)
- Poller polling rate: 5-minute cadence is correct
- Field name migration: `yes_ask_dollars` fallback to `yes_ask` in poller handles both formats

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
