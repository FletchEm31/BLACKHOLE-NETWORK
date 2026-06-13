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

## PERFORMANCE TRACKING

| Date | City | Bucket | Side | Contracts | Entry | P&L |
|------|------|--------|------|-----------|-------|-----|
| Jun 12 | KMIA | 92-93 | No | 6 | 79.8c | TBD |
| Jun 12 | KDEN | 92+ | No | 4 | 85.25c | TBD |
| Jun 12 | KDEN | 76-77 | No | 2 | 83c | TBD |
| Jun 12 | KDEN | 82-83 | No | 2 | 90.5c | TBD |
