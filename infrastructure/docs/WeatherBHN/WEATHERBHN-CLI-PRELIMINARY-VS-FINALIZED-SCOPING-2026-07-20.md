# CLI preliminary-vs-finalized report scoping — 2026-07-20

**This is a scoping document, not a fix. Nothing here has been implemented.**

## The bug

`fetch_nws_actuals()` (in `weather_data_collector.py`) fetches "the most recent CLI product" for a station and stores it as final settlement truth (`weather_silver_actuals_conformed.is_final = TRUE`, unconditionally, no check). NWS actually issues (at least) two different CLI-family products per day for the same station:

1. **A preliminary same-day product**, issued in the evening, whose text explicitly says `VALID TODAY AS OF 0500 PM LOCAL TIME` — i.e. it only covers that calendar day up through 5pm, not the full 24 hours.
2. **The true finalized report**, issued around 1:00-2:00am the *following* morning, covering the full prior calendar day with no "VALID TODAY AS OF" caveat.

Confirmed directly against raw product text (not inferred from timestamps alone): of KLAX's 40 genuine CLI rows, **38 (95%) are the preliminary 5pm-cutoff product**, only 2 are the true finalized report. `is_final = TRUE` is set the same way for both.

## Why this matters

`weather_settlement_reconciliation.py` filters on `is_final = TRUE` to decide what's safe to settle real trades against. `weather_model_accuracy` has 348 resolved rows in the window 2026-06-12 to 2026-07-16 — almost exactly the window affected by this. Unlike the earlier VC-mislabeling bug (which never touched a settled trade), **this one plausibly has**, though the practical impact is likely small for HIGH (LAX's daily high almost always lands before 3pm, well before the 5pm cutoff — see `WEATHERBHN-ASOS-MULTIVARIATE-PROBABILITY-MODEL...` item 2 findings) and more plausible for LOW (~16% of LAX days have their low land after 8pm, which a 5pm-cutoff report would miss entirely).

## What needs to be built (not started)

1. **Detection**: parse `source_payload_json->>'product_text'` for the `VALID TODAY AS OF` phrase (or equivalent — confirm the exact phrasing NWS uses doesn't vary before hardcoding a match) to distinguish preliminary from finalized reports at ingest time.
2. **Collector behavior change**: `fetch_nws_actuals()` needs to either (a) re-poll later (after ~2am local) specifically to capture the true finalized report and overwrite/upgrade the preliminary one, or (b) hold `is_final = FALSE` until the finalized report is actually captured, so downstream consumers (`weather_settlement_reconciliation.py`, `weather_gold_builder.py`) don't treat a 5pm-cutoff read as certified truth.
3. **Backfill decision**: the 38 already-stored preliminary rows for KLAX (and presumably a similar fraction for the other 5 stations with genuine CLI data) need a decision — re-fetch the true finalized report retroactively if NWS's product archive still has it, or leave them flagged as `is_final = FALSE` / `provisional` going forward without a full historical correction.
4. **Re-check settlement impact**: once finalized values are available (or at least once the magnitude of preliminary-vs-finalized divergence is known per station), re-run `weather_settlement_reconciliation.py`'s logic against the corrected values for the 348 already-resolved rows and confirm none of them would have settled differently. This is the concrete way to close out "did this actually cause a wrong trade" rather than reasoning about it abstractly.

## Confirmed: all 348 resolved rows are HIGH-side, zero LOW-side

Checked directly: `weather_model_accuracy` shows `variable = 'tmax_f'` for all 348 resolved rows, no `tmin_f` rows at all. This is consistent with (not a contradiction of) the earlier LOW-side scoping finding that CP4 has never written a `'low'` row — there is currently **zero LOW-side exposure** to this bug, because no LOW-side trade has ever been settled. The entire exposure is HIGH-side, which is the mechanically lower-risk case per item 2's own finding (LAX highs almost always land before the 5pm preliminary-report cutoff; LOW would have been much worse, since ~16% of lows land after 8pm). Doesn't eliminate the need to verify actual settlement impact (item 4 below), but narrows the real-world risk considerably from what it could have been.

## Not yet checked

- Whether this same 95%-preliminary pattern holds for the other 5 stations with genuine CLI data (KDEN, KMIA, KPHX, KDFW, KNYC) — only KLAX has been checked directly against raw product text.
- The exact magnitude of preliminary-vs-finalized divergence isolated from the (separate, already-identified) data-gap confound in our own reconstruction — the validation work this doc's companion task is doing (ASOS-derived CLI-equivalent labels) will produce clean-day comparisons that can inform this once available for all 6 stations, but that's a byproduct, not a substitute for actually re-fetching the true finalized reports where possible.
