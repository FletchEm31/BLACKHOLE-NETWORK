# WeatherBHN — Kalshi Temperature Prediction Market Trading

**Status:** Live (paper mode) | **Markets:** Kalshi daily high/low temperature contracts | **Phase:** 30-day calibration

---

## Overview

WeatherBHN is a systematic, model-driven trading strategy operating on [Kalshi](https://kalshi.com), the U.S.-regulated prediction market exchange. The system targets **daily high and low temperature contracts** — binary markets that settle to $1 if a city's maximum (or minimum) temperature on a given day falls within a specified bucket.

The core thesis: NWS (National Weather Service) probabilistic forecast data, when processed through an ensemble modeling layer and calibrated against historical actuals, produces probability estimates that diverge measurably from Kalshi's market-implied probabilities. That divergence — the edge — is the tradeable signal.

The system is end-to-end: it ingests raw forecast data from four independent sources, computes ensemble-weighted probabilities, identifies contracts where model probability diverges from market-implied probability beyond a threshold, and sizes positions using a fractional Kelly criterion. Settlement reconciliation runs nightly against NWS Climate Data reports.

**Phase 1 cities:** Denver (KDEN) and Miami (KMIA). Chosen for contrasting forecast regimes — Denver has high temperature volatility and low humidity (cleaner thermodynamic signal); Miami has strong diurnal patterns with afternoon convective suppression and a dominant sea breeze signal.

---

## Kalshi Contract Structure

Kalshi temperature contracts are discrete buckets covering the full probability distribution of a day's high or low temperature. For a given city and date there are typically 10–15 open contracts, one per temperature range (e.g., "Will Miami's high on July 4th be 89–91°F?"). Each contract:

- Settles binary: $1 (YES wins) or $0 (NO wins)
- Settlement source: NWS Climate Data (CLI reports) — the official daily extremes issued by the local NWS forecast office
- Market-implied probability: midpoint of the YES bid/ask spread, expressed as a fraction (0–1)
- Trades like any binary option with a bid/ask spread; taker buys the YES side at the ask

The sum of all bucket probabilities for a given city/date should equal 1 (normalization). In practice, market-implied bucket probs sum slightly above 1 due to the spread, representing the exchange's take.

---

## Data Pipeline

### Bronze layer — raw ingestion

Four independent forecast sources collected every 30 minutes:

| Source | Resolution | Notes |
|--------|-----------|-------|
| **NWS Gridpoint API** (`/gridpoints/{office}/{x,y}`) | Hourly, 7-day | Primary source — NWS is also the settlement authority |
| **Open-Meteo GFS ensemble** | Hourly, 28–30 members | GFS ensemble members; percentiles computed from member distribution |
| **Visual Crossing** | Daily, 15-day | Commercial API; calibrated against historical actuals |
| **NOAA GHCND actuals** | Daily, historical | 127k rows across 5 stations back to 1928; ground truth for calibration |

All raw snapshots stored in Postgres bronze tables with full provenance (`source_name`, `retrieved_at`, `lead_time_hours`, `target_date`).

Additionally:
- **Kalshi market snapshots** — YES bid/ask, volume, open interest per contract every 5 minutes
- **ENSO index** — weekly ONI values (El Niño/La Niña phase); used as a stratification variable in accuracy analysis

### Silver layer — conformation and error tracking

Bronze data is conformed to a standard schema for cross-source comparison:

- `weather_silver_forecast_conformed` — normalized forecasts per source, city, target date, lead time
- `weather_silver_market_conformed` — normalized Kalshi market state per contract
- `weather_silver_actuals_conformed` — NWS CLI actuals, flagged `is_final=TRUE` once the official daily climate report is issued
- `weather_silver_forecast_error` — rolling MAE and Brier score per source, per city, per lead-time bucket

The error tracking table is the core input to the ensemble weighting function.

### Gold layer — trading signals

`weather_gold_daily_edge_sheet` is the primary trading view, refreshed every 5 minutes. One row per open contract per city per date:

| Field | Description |
|-------|-------------|
| `nws_forecast_f` | NWS gridpoint forecast high for target date |
| `gfs_forecast_f` | Open-Meteo GFS ensemble median high |
| `model_delta_f` | NWS − GFS forecast spread |
| `model_delta_flag` | `AGREE` / `DIVERGE` / `NO_GFS` |
| `calibrated_prob` | Ensemble-weighted probability that the contract resolves YES |
| `raw_model_prob` | Pre-calibration model probability |
| `market_implied_prob` | Kalshi mid-market probability |
| `edge` | `calibrated_prob − market_implied_prob` |
| `edge_pct` | Edge as a fraction of market-implied probability |
| `recommended_action` | `BET_YES` / `BET_NO` / `SKIP` |
| `stake_fraction` | Kelly fraction (see below) |
| `stake_usd` | Dollar size given current bankroll |
| `model_confidence` | `HIGH` / `MEDIUM` / `LOW` (from ensemble spread) |
| `afternoon_storm_flag` | Miami convective suppression indicator |
| `sea_breeze_flag` | Miami onshore flow indicator |
| `ensemble_spread` | Inter-source forecast disagreement in °F |

`weather_gold_contract_ledger` is a companion table (one row per contract lifetime) that joins the edge sheet snapshot at signal time with the eventual settlement outcome, producing realized edge, paper P&L, and model accuracy by feature segment.

---

## Forecast Modeling

### Ensemble weighting

Rather than using a single forecast source, the system maintains **rolling Brier scores** per source, per city, per lead-time bucket:

```
Brier score: BS = (forecast_probability − outcome)²
```

Lower Brier score = better calibrated source. Each source's weight in the ensemble is proportional to `1 / rolling_BS`, computed over a trailing window. This means a source that's been consistently wrong on Denver temperatures gets down-weighted automatically without manual intervention.

### Ensemble probability computation

For each contract (bucket `[T_floor, T_cap)`), the model probability is:

```
P_model = Σ_s  w_s · P_s(T_high ∈ bucket)
```

where `w_s` is the normalized Brier-score weight for source `s`, and `P_s` is derived from source `s`'s forecast distribution over the temperature range.

For sources delivering point forecasts (NWS, Visual Crossing), a local Gaussian approximation is used with spread informed by ensemble disagreement. For Open-Meteo GFS ensemble, the member distribution is used directly — P(high ∈ bucket) = fraction of members whose daily max falls in the bucket.

### Calibration

Raw model probabilities are passed through a **Platt scaling** calibration layer trained on historical settlements. The calibrator maps model output to empirical frequencies — if the model says 0.7, the calibrated output reflects what fraction of contracts at that model probability actually resolved YES historically.

The calibration model is retrained on a rolling basis. During the Phase 1 accumulation period (first 30 days of live data), hardcoded fallbacks are used; calibrated Platt parameters replace them once sufficient settlement history exists.

### Lead time and forecast decay

Forecast accuracy degrades with lead time. The system tracks a **forecast accuracy decay curve** per source: MAE as a function of hours-to-settlement. For NWS, accuracy is approximately stable from T-48h to T-6h; beyond T-72h the signal degrades meaningfully. This decay is not yet explicitly modeled in weights but is tracked for Phase 2 integration.

### Miami-specific feature flags

Miami's temperature dynamics are dominated by two suppressors:

- **Afternoon storm flag** (`afternoon_storm_flag`): when NWS probabilistic precipitation forecasts show >40% convective precipitation probability between 14:00–17:00 local, the flag is set. Afternoon convective events suppress the daily high by 2–4°F on average.
- **Sea breeze flag** (`sea_breeze_flag`): set when NWS surface wind forecast shows SE/S at >5 mph during peak heating hours. Onshore flow limits afternoon heating. Historical records show ~1.5°F suppression on flagged days.

These flags are stored on the edge sheet and the contract ledger for outcome analysis — the eventual goal is to use them as stratified calibration inputs.

---

## Position Sizing — Fractional Kelly

The position size for a given contract uses the **fractional Kelly criterion**:

```
f* = (b·p − q) / b
```

where:
- `p` = model probability the contract resolves YES
- `q` = 1 − p  
- `b` = contract payout odds = `(1 − market_price) / market_price`

Full Kelly maximizes log-expected wealth but is notoriously sensitive to model error — a miscalibrated `p` can result in oversize positions and ruin. The system uses **quarter-Kelly** (`f = f* × 0.25`) to account for model uncertainty and parameter estimation error during the calibration period. This will be revisited once Platt calibration is fully live.

**Current parameter values (Phase 1):**

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Kelly multiplier | 0.25× (quarter-Kelly) | Reduces sensitivity to model miscalibration during accumulation period; revisit after Platt scaling is live |
| Edge threshold (weather strategy) | 5% minimum `\|calibrated_prob − market_implied_prob\|` | Below this the signal is too close to noise; 5% chosen empirically from distribution of historical edge magnitudes |
| Edge threshold (prediction signal) | 8% minimum edge | Tighter filter for the primary prediction alpha strategy where Kelly fractions are larger |
| Spread filter | >20¢ YES/NO spread → skip | Ensures adequate liquidity; wider spread implies the market is thin or uncertain |
| Max OI fraction | 10% of open interest per contract | Prevents moving a thin market against itself |
| Max daily volume fraction | 5% of 24h volume | Secondary liquidity cap |

**Hard position limits apply regardless of Kelly output:**
- Maximum exposure per contract: capped at max OI fraction above
- Maximum exposure per city per day: limits geographic concentration
- Spread filter enforced at signal generation time, not order time

**Kelly stop condition:** if the model's rolling Brier score on settled contracts exceeds a threshold over a trailing window, the strategy auto-pauses pending recalibration. This is a model quality circuit breaker, not a P&L stop.

---

## Risk Controls

**DRY_RUN mode (default ON):** All signal generation, edge calculation, and sizing runs in dry-run mode until an operator explicitly flips `DRY_RUN=false` in the strategy environment. In dry-run, orders are logged and tracked for paper P&L but never submitted to Kalshi.

**Strategy halt flag:** Each strategy has a `halted` boolean checked before any order submission. The master killswitch can halt all strategies simultaneously via a single API call. The killswitch is also triggered by the daily loss limit circuit breaker.

**Daily loss limit:** If realized paper P&L on a given calendar day falls below a threshold (as a fraction of bankroll), the strategy disables for the remainder of that day and logs a halt event. Requires manual operator re-enable.

**NWS settlement verification:** The settlement reconciliation script cross-references Kalshi contract outcomes against NWS CLI actual reports before marking contracts closed. This catches any divergence between expected and actual settlement (e.g., NWS data revision).

**KALSHI_PAPER_ONLY flag:** The Kalshi API client enforces this flag at the request layer — orders are rate-checked, sized, and constructed but not submitted if the flag is set.

---

## Current Status and Roadmap

### Phase 1 (active) — Denver + Miami HIGH temperature

- Live signal generation on KDEN and KMIA HIGH contracts
- Visual Crossing historical backfill: 500 days per night, quota-limited; NOAA GHCND actuals supplement (127k rows, free, unlimited)
- 30-day calibration accumulation in progress; Platt calibration parameters pending
- Paper P&L tracked in `weather_gold_contract_ledger` (192 contracts bootstrapped, 156 settled)

### Phase 2 — Expansion (planned)

- Additional cities: Chicago (KORD), Los Angeles (KLAX), New York (KJFK)
- LOW temperature contracts alongside HIGH
- HRRR 3km short-range model integration (herbie-data)
- ECMWF 51-member ensemble (ecmwf-opendata)
- Live sigma-based edge threshold replacing hardcoded fallback
- NWS gridpoint two-step API (`/points → /gridpoints`) for probabilistic forecast data

### Phase 3 — Signal publication (planned)

- Expose daily edge sheet and model accuracy metrics via public API endpoint
- Allow independent verification of settlement outcomes against model predictions

---

## Infrastructure

The full trading stack runs on a Linux VPS hub (LA node) behind a WireGuard mesh:

- **Postgres** (`eventhorizon` database): all bronze/silver/gold tables
- **Python collector** (`weather_data_collector.py`): 30-minute systemd timer for all forecast sources + Kalshi snapshots
- **Edge calculator** (`weather_edge_calculator.py`): 5-minute systemd timer; reads latest snapshots, writes gold edge sheet
- **Settlement reconciler** (`weather_settlement_reconciliation.py`): 10 AM ET daily; reads NWS CLI actuals, updates contract ledger
- **Grafana** (NJ node): live dashboards for edge sheet, model accuracy, P&L attribution, forecast error by source

Source: [`scripts/trading/`](../scripts/trading/) | Schema: [`sql/`](../sql/)
