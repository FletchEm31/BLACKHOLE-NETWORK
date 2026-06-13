# WeatherBHN Trading Strategy
## Version 1.0 — June 12, 2026
## Status: Manual (Phase 1) → Automated (Phase 3)

## OVERVIEW

WeatherBHN trades daily temperature contracts on Kalshi using NWS
forecast data as the primary signal. The core edge comes from:

1. NWS forecast accuracy — NWS is the settlement source. We read
   the same data Kalshi uses before the market has fully priced it.
2. Market mispricing — Kalshi markets often misprice boundary
   buckets and tail probabilities, especially early in the day.

Settlement source: NWS Daily Climate Report (CLI)
Target markets: Daily HIGH temperature contracts (KXHIGH series)
Cities: KMIA, KDEN, KPHX, KLAX, KDFW, KNYC, KORD, KAUS

## CORE STRATEGY — BOUNDARY SPLIT + TAIL NO

This strategy maps directly to the options concept of a
SHORT STRANGLE / IRON CONDOR applied to binary prediction markets.

### Step 1 — Read the NWS Forecast
- Pull the NWS hourly forecast for the target station
- Identify the predicted high temperature
- Note if it falls ON A BOUNDARY between two adjacent buckets
  Example: "high near 89F" = boundary between 88-89 and 89-90

### Step 2 — Split the Boundary Buckets (Yes Contracts)
- When NWS forecast sits on the boundary between two buckets:
  - Buy Yes contracts on BOTH adjacent buckets
  - Split evenly if both pricing similarly cheap (under 15-20c)
  - Load heavier on whichever bucket aligns more with forecast
  - These are directional bets — one of them will win

### Step 3 — Load the Improbable Tails (No Contracts)
- Identify buckets statistically impossible given the forecast
  - Typically 3+ degrees away from the NWS predicted high
  - Market will price these at 95-99c for No
- Buy No contracts heavily on these tail buckets
- This is the PRIMARY profit engine — near-guaranteed winners

### Step 4 — Hold to Settlement
- No contracts on improbable tails collected at settlement
- Yes contracts on boundary buckets — one wins, one loses
- Do not over-trade intraday unless significant forecast revision

## POSITION SIZING — KELLY CRITERION

Formula: f = (p * b - q) / b
  f = fraction of bankroll to bet
  p = probability of winning (BHN calibrated_prob)
  q = probability of losing (1 - p)
  b = net odds = (1 - price) / price

Always use HALF Kelly (multiply result by 0.5) for safety.
Always apply liquidity cap: never more than 10% of open interest
or 5% of daily volume — whichever is lower.

## POSITION SIZING RULES

Tail No Contracts (primary):
- Size: 2-7 contracts per bucket
- Entry: 80-99c only
- Maximum loss: small — these rarely hit

Boundary Yes Contracts (secondary):
- Size: 2-5 contracts per bucket, split across 2 adjacent buckets
- Entry: under 20c ideally
- Maximum loss: full cost basis on losing bucket

Overall Limits (manual phase):
- Never risk more than $20 total per city per day
- Keep at least $5 cash reserve
- Never buy Yes on more than 2 adjacent buckets simultaneously

## ENTRY CRITERIA CHECKLIST

Before any trade:
- NWS hourly forecast pulled for correct station
- Settlement station confirmed (KMIA for Miami, KDEN for Denver)
- Forecast clear — note if on boundary or in one bucket
- Tail buckets identified — 3+ degrees from forecast peak
- Market liquidity confirmed — spread under 10c, OI > 50 contracts
- Position size within daily risk limit

## EXIT CRITERIA

Hold to settlement (default):
- Tail No contracts: always hold unless catastrophic revision
- Boundary Yes: hold unless forecast shifts more than 2F

Early exit triggers:
- NWS issues significant forecast revision (2F+ shift)
- Unexpected weather event changes the outlook
- Position deeply underwater, recovery unlikely before settlement

## KNOWN CITY BIASES (preliminary — June 12, 2026)

NOTE: Based on very limited sample (2-3 days). Do not trade on
these alone until BHN calibration confirms with 30+ days.

| City | Variable | Observed Bias | Notes |
|------|----------|---------------|-------|
| KAUS | tmax_f   | Runs HOT ~13-15F | NWS overforecasts Austin |
| KDEN | tmax_f   | Runs COLD ~17-20F | NWS underforecasts Denver |
| KDFW | tmax_f   | Runs HOT ~11F | NWS overforecasts Dallas |
| KLAX | tmax_f   | Runs COLD ~10F | NWS underforecasts LA |

## RELATIONSHIP TO OPTIONS STRATEGIES

| Options Concept | WeatherBHN Equivalent |
|----------------|----------------------|
| Short Strangle | Buy No on both tail buckets |
| Long Straddle | Buy Yes on both boundary buckets |
| Iron Condor | Combine — No on tails, Yes on middle |
| Premium collection | Buying No at 90-99c |
| Strike price | Bucket floor/cap temperature |
| Underlying | NWS CLI reported maximum temperature |

## LIQUIDITY CONSTRAINTS BY BANKROLL

| Bankroll | Safe per bucket | Constraint starts |
|----------|----------------|-------------------|
| $14      | $5-10          | Never             |
| $50      | $15-25         | Never             |
| $200     | $50            | Thin buckets      |
| $500     | $100           | Some MIA/DEN      |
| $2,000   | $200           | MIA/DEN regularly |
| $5,000   | $500           | Most markets      |
| $10,000+ | $1,000         | NYC/LA only       |

## PHASE 2 — BHN AUTOMATION TARGET

Once calibration is trained:
1. Edge calculator identifies boundary situations automatically
2. Tail No contracts flagged when calibrated_prob < 3%
3. Position sizing via Kelly criterion
4. Entry orders via Kalshi API
5. Stop-loss monitor every 5 minutes
6. Exit orders triggered automatically

See WEATHERBHN_STOP_LOSS_SPEC.md for stop-loss details.

## PHASE 2 — MIAMI MODEL SPEC (gradient boosting calibration)

### Why NWS Overforecasts Miami Highs

Miami (KMIA) consistently runs 13-15°F cooler than NWS predicts (observed Jun 12).
The physical mechanisms are predictable from available data and will be learned
automatically by a gradient boosting model once Visual Crossing backfill provides
sufficient history. Do not hardcode corrections — let the model learn interaction
effects between these features.

### Feature Set

**1. afternoon_storm_flag**
```
afternoon_storm_flag = 1 if MAX(pop_pct) > 20% over hours 12-17 else 0
```
Source: weather_bronze_nws_forecast_snapshots (hourly, hours 12-17)
Physical mechanism: Miami sea breeze convergence drives afternoon convection that
caps surface heating. NWS operational models systematically underweight this
cooling effect in the daily high forecast. Days with >20% afternoon PoP are
reliably cooler than the NWS gridded high.

**2. sea_breeze_index**
```
sea_breeze_index = wind_speed_mph * direction_sign
direction_sign = +1 if wind_direction_deg in onshore range else -1
KMIA onshore range: 045–225° (east through south — Atlantic + Bay inflow)
```
Source: weather_bronze_nws_forecast_snapshots (wind_speed_mph, wind_direction_deg)
Physical mechanism: onshore flow brings cooler, moisture-laden Atlantic air across
the peninsula before peak heating. Strong onshore index → stronger marine layer
suppression → lower observed tmax. Offshore flow removes the cooling buffer.
Note: requires wind_direction_deg column — verify it exists in bronze before
implementing. If missing, add to fetch_nws_hourly() in weather_data_collector.py.

**3. nws_gfs_uncertainty**
```
nws_gfs_uncertainty = ABS(nws_forecast_tmax - gfs_forecast_tmax)
```
Source: weather_silver_forecast_conformed (both sources already present)
Physical mechanism: large NWS/GFS disagreement signals a synoptic pattern neither
model handles confidently. For Miami, model spread > 3°F historically correlates
with NWS overforecasting the high — likely because GFS resolves the sea breeze
boundary layer dynamics better at coarser resolution. High uncertainty = larger
expected NWS bias.

**4. humidity_suppression**
```
humidity_suppression = dewpoint_f / tmax_forecast_f
```
Source: weather_bronze_nws_forecast_snapshots (dewpoint_f, tmax_f)
Physical mechanism: high dewpoint relative to forecast high indicates a moisture-
rich boundary layer that suppresses sensible heating. In Miami, the ratio captures
the degree to which latent heat flux dominates over dry convective heating. Ratio
approaching 1.0 → near-saturated air → afternoon clouds and storms cap the high.

**5. cloud_timing**
```
cloud_timing = peak_cloud_hour - peak_heating_hour
peak_cloud_hour  = hour with MAX(cloud_cover_pct) between hours 10-18
peak_heating_hour = 15  (3 PM local — typical solar maximum lag for Miami latitude)
```
Source: weather_bronze_nws_forecast_snapshots (cloud_cover_pct, hourly)
Physical mechanism: cloud cover arriving BEFORE peak heating (negative cloud_timing)
blocks the solar radiation window and hard-caps the high. Cloud cover arriving AFTER
peak heating has minimal effect on tmax. NWS point forecasts do not account for
this timing effect when issuing the daily high. Negative cloud_timing is the
strongest individual predictor of NWS overforecast in humid subtropical climates.

### Model Architecture

Algorithm: gradient boosting (LightGBM or XGBoost)
Target: nws_forecast_error_f = actual_tmax_f - nws_forecast_tmax_f
Features: all 5 above + raw_nws_forecast_f + month + day_of_week
Training data: weather_silver_forecast_error JOIN feature table (≥90 days minimum)
Output: calibrated_correction_f → applied as bias adjustment in edge calculator

Data requirement: Visual Crossing backfill provides ~140 days of history.
Minimum viable training set: 90 days. Target: 140+ days.
Implementation trigger: VC backfill complete + VISUAL_CROSSING_API_KEY set in strat9.env.

Feature storage: weather_silver_features (station_code, target_date, feature_name, value)
or wide table (station_code, target_date, afternoon_storm_flag, sea_breeze_index, …)
Decide schema before implementation — wide table preferred for query simplicity.

## PERFORMANCE TRACKING

| Date | City | Bucket | Side | Contracts | Entry | P&L |
|------|------|--------|------|-----------|-------|-----|
| Jun 12 | KMIA | 92-93 | No | 6 | 79.8c | TBD |
| Jun 12 | KDEN | 92+ | No | 4 | 85.25c | TBD |
| Jun 12 | KDEN | 76-77 | No | 2 | 83c | TBD |
| Jun 12 | KDEN | 82-83 | No | 2 | 90.5c | TBD |

## DATA STANDARDS

All WeatherBHN timestamps follow the naming convention in:
`infrastructure/docs/WeatherBHN/WEATHERBHN_TIMESTAMP_STANDARD.md`

Summary: every timestamp appears in THREE columns in all Metabase queries:
- `[vocab]_time_utc` — raw UTC (server time)
- `[vocab]_time_pt` — Pacific time (operator local, auto-handles DST)
- `mins_ago` — integer minutes between now and that timestamp

For the master index of all WeatherBHN standards and documentation, see:
`infrastructure/docs/WeatherBHN/WEATHERBHN_DATA_STANDARD.md`
