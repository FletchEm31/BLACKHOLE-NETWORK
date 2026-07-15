# WeatherBHN LOW-side (tmin_f) scoping — 2026-07-15

**This is a scoping document, not a build. Nothing here has been implemented.**

## The one thing this doc exists to say

LOW-side calibration data being "READY TO CALIBRATE" (34/34 error pairs, all 8 cities, verified gap-free — see the 2026-07-15 calibration verification) **does not unlock LOW-side trading on its own.** The blocker was never data. CP3 (model inference) and CP4 (position sizing) have no code path for `tmin_f` at all — `STATION_STATUS[...]['low']` is `'not_built'` for every city, and that's accurate: it's an engineering project, not a waiting-on-data state. Don't let this get reclassified as "pending calibration" in status tracking — it's pending engineering work that hasn't been scoped for effort yet (this doc scopes *what*, not *how long*).

## What already exists (reusable as-is)

Verified directly against the live LA database, not assumed:

- **Kalshi LOW markets are real and active.** Confirmed live: `KXLOWTAUS-26JUL15-T67`, `-B71.5`, `-B69.5`, etc. — real, active market tickers with correct series (`KXLOWTAUS`), same structure as the HIGH side. This contradicts the 2026-06-30 status doc's "Scope Lock: no tmin markets exist on Kalshi" — that was true at the time it was written but is stale now. Don't cite that doc for LOW-side market availability going forward.
- **`weather_gold_city_day_features` already has a full parallel `tmin_f` column set** — `nws_tmin_f`, `om_tmin_f`, `nws_tmin_mean_bias`, `nws_tmin_rmse`, `om_tmin_mean_bias`, `om_tmin_rmse`, `nws_tmin_calibrated_f`, `om_tmin_calibrated_f`, `actual_tmin_f` — populated already by `weather_gold_builder.py`.
- **`model_calibration` already has tmin_f rows** — confirmed 96 tmin_f rows vs 96 tmax_f rows live. Calibration bias/RMSE by station/season/lead-time already exists for both sides.
- **`weather_gold_contract_ledger` already has a `contract_side` column** and is already receiving `'low'` rows (450 of them, all 8 cities) — but these come from a separate storage-only populator process, not from CP4. Confirmed by direct inspection: those rows have `nws_forecast_f` populated but `calibrated_prob`, `raw_model_prob`, `recommended_action`, `stake_usd` all NULL. It's raw forecast logging, not a trading signal — CP4 has never written a `'low'` row.
- **CP4's `SETTLEMENT_UTC_HOUR`, `CITY_MAP`, sigma/Kelly engine are side-agnostic** — no code changes needed there for LOW itself (separate from the KAUS/KNYC/KORD timezone-default bug tracked elsewhere).

## What's HIGH-only and would need to be built

**CP3 (`scripts/weather/cp3_inference.py`):**
- `MODEL_PATH = Path(".../weather_xgb_tmax.json")` — single hardcoded model file. A LOW model needs its own trained artifact (`weather_xgb_tmin.json` or similar) and its own `MODEL_PATH`/`_load_model()` — not just a parameter swap, an actual second trained model.
- `FEATURE_COLS` — hardcoded to the 10 `tmax_*`-named columns. Needs a parallel `tmin_*` list (the source columns already exist, per above).
- Three SQL blocks (`run_cp3_inference`'s gold-row query, bronze-fallback query, and the two `model_calibration WHERE variable = 'tmax_f'` calibration lookups) all hardcode the `tmax_f`/`tmax_*` literals — each needs a `feature_name`/side parameter threaded through.
- Return dict keys (`predicted_tmax_f`, `nws_forecast_f`, `om_tmax_f`, `calibrated_forecast_f`) are tmax-named and consumed by name downstream in the orchestrator and CP4 — a LOW path would need parallel-named keys or a side-generic contract.

**CP4 (`scripts/weather/cp4_kelly_sizer.py`):**
- The bronze-snapshot query explicitly filters `AND contract_side = 'high'` in two places (line ~281, ~285) — deliberately added, with a comment explaining it's needed *because* LOW-series tickers are already being collected and would otherwise contaminate HIGH sizing. This is the mirror-image change: a LOW path needs the same filter flipped to `'low'`, not removed.
- `write_to_ledger()` hardcodes the literal `'contract_side': 'high'` in the row dict (line ~618) — single-line change point per side, low risk.
- The function signature takes `predicted_tmax_f` as a named parameter, not a generic `predicted_f` — same naming-convention work as CP3.

## Sequencing note

Given CP3's model retrain requirement already exists as an open item for the HIGH-side rollout (KAUS/KNYC/KORD need `STATION_ENC` extended and the model retrained to recognize them — see the CP3/CP4 station-dict bug found 2026-07-15), it's worth deciding whether to retrain once with all 8 stations × both sides, or twice (HIGH-fix now, LOW-build later). Retraining twice is simpler to reason about and doesn't block the HIGH rollout on LOW-side design decisions; retraining once is less total model-churn. Not resolved here — flagging as a sequencing choice for whoever picks this up.

## KPHX/KDFW confirmed as genuine LOW-only candidates

Checked directly: KPHX has 96 active LOW-side Kalshi contracts, KDFW has 90, both zero HIGH (consistent with the earlier `unavailable_high` finding). This confirms the orchestrator's own comment — "Phoenix and Dallas are LOW-only candidates once Low-side modeling eventually exists" — is accurate, not aspirational. Once LOW-side CP3/CP4 support is built, these two are real, market-backed candidates, not a hypothetical.

## Not investigated here

Kelly/edge-threshold tuning for LOW markets specifically — climatology and error structure differ between high and low temps per city (colder-side tail behavior, etc.), so even after the code changes above, LOW would likely need its own threshold/backtest pass before going live, not just a copy of HIGH's thresholds.
