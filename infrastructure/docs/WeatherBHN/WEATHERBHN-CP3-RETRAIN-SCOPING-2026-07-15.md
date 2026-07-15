# WeatherBHN CP3 retrain scoping — KAUS/KNYC/KORD — 2026-07-15

**This is a scoping document, not a build. Nothing here has been implemented.**

## Scope clarification first

The request was "retrain CP3 with all 8 stations." Only **6** stations have HIGH Kalshi markets and belong in this (tmax) model: KDEN/KLAX/KMIA (already live) + KAUS/KNYC/KORD (blocked, this doc). **KPHX/KDFW have no HIGH market at all** — confirmed earlier — so including them in a tmax retrain wouldn't serve any tradeable purpose; they're LOW-only candidates for a future tmin model (see `WEATHERBHN-LOW-SIDE-SCOPING-2026-07-15.md`). This doc scopes the 3-station HIGH expansion (KAUS/KNYC/KORD). Say the word if you actually want KPHX/KDFW's data bootstrapped into the gold layer now anyway (harmless, just unused until LOW exists) — not included below by default.

## Step 0 — process gap: the training pipeline isn't in git

Neither `cp3_train_model.py` nor `weather_historical_backfill.py` (the script that built the 2020–2026 historical training set) exist in this repo — both are LA-only files at `/opt/bhn/trading/`, found by direct filesystem search, never committed. This means the only copy of "how the current production model was actually trained" has no version history and no backup. **Recommend committing both to `scripts/weather/` before touching either** — cheap, and removes a single point of failure on the LA disk.

## Step 1 — code changes (mechanical, low risk)

Four station-keyed dicts need KAUS/KNYC/KORD added, kept in sync by hand (no shared config module currently — worth a follow-up refactor, not required to unblock this):

- `cp3_inference.py` — `STATION_ENC = {'KDEN': 0, 'KLAX': 1, 'KMIA': 2}` → add `'KAUS': 3, 'KNYC': 4, 'KORD': 5`.
- `cp3_train_model.py` — same `STATION_ENC` dict (must match cp3_inference.py's order exactly, per its own comment) + `TRADEABLE_STATIONS` list.
- `weather_gold_builder.py` — `TRADEABLE_STATIONS = ('KDEN', 'KLAX', 'KMIA')`. **This one gates whether gold rows get built at all** — currently KAUS/KNYC/KORD have zero rows in `weather_gold_city_day_features` because this daily builder has never run for them (confirmed live: 0 rows for all three vs. 2,384 each for KDEN/KLAX/KMIA). This edit needs to land and the daily builder needs to run (or be backfilled) before training data exists going forward.
- `weather_historical_backfill.py` — `STATION_COORDS`/`STATION_TIMEZONE` dicts need lat/lon + IANA timezone for KAUS/KNYC/KORD (public airport/station coordinates, trivial to source — just needs doing carefully since a wrong coordinate silently pulls the wrong city's weather).

None of this requires design decisions — it's copy-the-pattern work. The risk is purely "did all four dicts get the same encoding," which is exactly the kind of drift that caused the KAUS bug found earlier today.

## Step 2 — historical backfill: two real data gaps found, one station is ready as-is

Checked `weather_bronze_noaa_daily_actuals` (the actuals source `weather_historical_backfill.py` pairs against Open-Meteo's reconstructed historical forecasts):

| Station | NOAA actuals status |
|---|---|
| **KORD** | ✅ Ready — 29,108 rows, 1946–2026, already loaded (matches a CSV already sitting in this docs folder) |
| **KNYC** | ⚠️ **Real gap, not just missing data** — the bronze table has `KJFK` (JFK Airport), not `KNYC` (Central Park). Kalshi's NYC weather markets settle against the NWS Central Park climate station, not JFK — these are physically different sites with a known, measurable climate offset (Central Park runs warmer, notably at the extremes, due to urban heat island effects vs. coastal JFK). Silently backfilling "KNYC" training rows from JFK data would bake a systematic bias into the model, not just add noise. **Needs genuine Central Park historical actuals sourced and loaded under the `KNYC` label** before backfilling — don't take the shortcut of pointing the backfill at KJFK. |
| **KAUS** | ❌ Zero rows — needs a fresh NOAA CDO/GHCND ingest for Austin's actual settlement station before the historical backfill script can run for it at all. |

Once actuals exist, `weather_historical_backfill.py` itself is in good shape to reuse as-is: it's idempotent (`ON CONFLICT DO NOTHING`), resumable, rate-limited (0.25s/request), and pulls forecasts from Open-Meteo's Historical Forecast API by lat/lon — that part has no per-station data-availability blocker, it works for any coordinates globally. The mechanical Step 1 edit (adding 3 rows to `STATION_COORDS`) is all that's needed there once the actuals gap above is closed.

**Depth question worth deciding, not just defaulting to 6 years:** KDEN/KLAX/KMIA were backfilled 2020–2026. The training script weights live rows 3x vs. historical 1x and validates only on live rows, so historical depth mostly helps the model learn broad seasonal/station patterns rather than driving validation. Full 6-year parity is the safe default but not obviously required — a shorter backfill (say 1–2 years) would be faster to source and might be enough to get KAUS/KNYC/KORD functional, with deeper backfill as a later refinement. Flagging as a scope choice, not deciding it here.

## Step 3 — retrain and validate

`cp3_train_model.py` is already multi-station-generic once the dicts above are extended — no logic changes needed. One thing worth knowing going in, not a new problem introduced by this change: **the live-row validation set is already small for the existing stations** (32 live rows total for KDEN/KLAX/KMIA combined as of today, so a ~7-row test set) — KAUS/KNYC/KORD will start in the same position (34 days of live silver data ≈ similar order of magnitude), not meaningfully worse than what's already in production.

**Real risk worth flagging:** there is one shared model file (`weather_xgb_tmax.json`) for all stations — retraining to add 3 stations retrains the *whole* model, so KDEN/KLAX/KMIA's predictions could shift too, not just gain 3 new stations additively. Recommend comparing the new model's predictions against the current one on KDEN/KLAX/KMIA's held-out rows specifically before deploying, to catch any regression on the stations already trading live (even in DRY_RUN, a silent regression there would corrupt data operators are currently trusting).

## Step 4 — deploy, then unblock the flip sequence

Once retrained and validated, redeploy `weather_xgb_tmax.json` + updated `cp3_inference.py` (same deploy pattern as everything else — repo → scp → LA). This closes task #5 (CP3 half of it — the CP4 `SETTLEMENT_UTC_HOUR`/`CITY_MAP` fix is independent, no retrain needed, can happen anytime in parallel). Only after both are done should the KAUS → KNYC → KORD flip-one-watch-confirm sequence resume.

## Summary — ordered, with the real blockers called out

1. Commit `cp3_train_model.py` + `weather_historical_backfill.py` to git. *(no blockers)*
2. Extend the 4 station dicts (Step 1). *(no blockers, just care)*
3. Source genuine Central Park (KNYC) historical actuals — **the one step here that isn't just "run a script,"** needs actual sourcing/verification of the right station data.
4. Ingest fresh NOAA historical actuals for KAUS. *(mechanical, standard NOAA CDO pull)*
5. Run `weather_historical_backfill.py` for KAUS/KNYC/KORD once 3–4 are done.
6. Run/confirm `weather_gold_builder.py`'s daily job now covers all 6 stations going forward.
7. Retrain via `cp3_train_model.py`; compare new-vs-old predictions on KDEN/KLAX/KMIA before trusting it.
8. Deploy alongside the CP4 dict fix; resume the flip sequence.

Steps 1, 2, 4–8 are mechanical. Step 3 (KNYC/Central Park data) is the one genuine unknown — it's the only place this could take meaningfully longer than "an afternoon of scripted work," because it depends on finding and validating the right external dataset rather than just running existing code.
