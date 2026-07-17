# WeatherBHN sigma-zone analysis — 2026-07-17

**Status: promising hypothesis, not a proven strategy.** Nothing in this document has been
implemented or deployed. No code, thresholds, or CP4 logic were changed as part of this
analysis. This is a read-only backtest report, saved for reference before any future decision
to act on it.

## Background

This analysis grew out of the same-night investigation that found and fixed five real
data-integrity bugs in the WeatherBHN pipeline: `edge_cents`, `model_prob_no_cents`,
`predicted_tmax_f`, and `hours_to_settle` all live-drift every cycle a signal keeps
re-qualifying (via `exit_audit_logger.py`'s `ON CONFLICT DO UPDATE SET`), and the
settlement-time assumption used to compute `hours_remaining` (and therefore sigma decay)
is based on the wrong "4PM local, same day" clock (see the 2026-07-17/2026-07-17b/c commits
and the `ARCHITECTURE-DOC-PENDING-EDITS-2026-07-03.md` decision log for the full detail on
each). `sigma_used` was checked the same way: confirmed live-drifted (`sigma_used =
EXCLUDED.sigma_used`) and confirmed dependent on the still-unfixed broken settlement clock
(`calculate_time_decayed_sigma()` calls `_settlement_dt()` directly).

Every "exciting" backtest finding tonight that was built on the *drifted* columns evaporated
or inverted once re-measured against properly reconstructed entry-time values — except one.
This document is that one surviving finding, refined twice since discovery (folded/absolute
sigma buckets → signed buckets → signed buckets with the z=0 straddle case split out), each
refinement changing the picture materially. Treat it with the same caution applied to
everything else tonight: real pattern in the data, not yet a validated trading rule.

## Method

- **Entry-time reconstruction**: `predicted_tmax_f` and `sigma_used` at the actual decision
  moment were reconstructed from the orchestrator log (`/var/log/bhn-trading/weather-orchestrator.log`,
  unrotated, covers the full settled-trade window), matched to each trade's frozen
  `entry_captured_at` by nearest timestamp (cycle-start-anchored for the bare `[DRY RUN]`
  sigma print line, since that line has no timestamp of its own). Tolerance 120s;
  observed match gaps averaged under 1s. **73 of 87 settled trades matched** (84% coverage;
  14 early trades predate reliable log coverage and are excluded, not guessed at).
- **Signed z-score**: `z = signed_distance(predicted_tmax_f, bucket_floor, bucket_cap) / sigma_used`,
  computed from the reconstructed entry-time values only (never the live-drifted columns).
  Sign convention: **positive = the bucket sits entirely above the prediction** (hotter than
  forecast), **negative = the bucket sits entirely below the prediction** (cooler than
  forecast), **zero = the prediction falls inside the bucket** (the model itself considers
  this bucket likely — the structurally worst case for a NO bet).
- Verified against the earlier folded (`LEAST(ABS(...), ABS(...))`/sigma) + separate
  above/below split query: the signed remap reproduces those numbers exactly per zone
  (see verification table below) — confirms this is a relabeling, not a different sample.

## Results — 7 signed zones, entry-time reconstructed values only

| zone | n | wins | win% | staked | pnl | ROI% | confidence |
|---|---|---|---|---|---|---|---|
| −3σ to −2σ | 2 | 2 | 100.0% | $150.64 | +$17.36 | +11.5% | too thin to trust |
| **−2σ to −1σ** | 8 | 8 | 100.0% | $597.38 | +$218.62 | **+36.6%** | robust — see note |
| −1σ to 0 | 17 | 12 | 70.6% | $1,367.52 | −$117.52 | −8.6% | moderate sample |
| z=0 exact (prediction inside bucket) | 16 | 10 | 62.5% | $1,333.35 | −$47.35 | −3.6% | moderate sample |
| **0 to +1σ, excluding z=0** | 19 | 9 | 47.4% | $1,734.97 | **−$720.97** | **−41.6%** | worst zone in the dataset |
| +1σ to +2σ | 9 | 7 | 77.8% | $802.90 | −$88.90 | −11.1% | moderate sample |
| +2σ to +3σ | 1 | 1 | 100.0% | $99.19 | +$9.81 | +9.9% | single trade, noise |

**"Robust" for −2σ to −1σ means**: this zone's numbers were independently reproduced twice —
once via the original folded-distance query + a separate above/below split, and once via a
direct signed z-score recomputation from scratch — with identical trade-for-trade results
both times. It does **not** mean the sample is large (n=8) or that causation is understood.

### What changed between refinements (for the record)

1. **First pass** (folded/absolute distance, `LEAST(ABS(predicted-floor), ABS(predicted-cap))/sigma`):
   found "1-2σ profitable, 0-1σ = entire loss." This conflated both directions of the bell
   curve into one number.
2. **Second pass** (above/below split on the folded zones): found the 1-2σ zone was actually
   asymmetric — 10/10 wins below prediction, 4/5 wins but net-negative above prediction.
   Checked whether NWS's temperature-rounding convention ("round half up asymmetric",
   confirmed from NCEI's National Data Stewardship Team Rounding Advice — a genuine primary
   source, verified directly) could explain this. **It does not**: for positive Fahrenheit
   summer temperatures (all of this dataset), "asymmetric" rounding is mathematically
   identical to ordinary round-half-up — the asymmetric/symmetric distinction only diverges
   for negative values. No distinctive upward bias exists in NWS's convention for this data,
   so it isn't a plausible mechanism for the observed asymmetry. Also found the same-shaped
   asymmetry in the 2-3σ zone did **not** survive entry-time reconstruction (collapsed to
   n=1 vs n=2 and reversed sign) — a reminder that a thin sample built on the wrong basis can
   look like a pattern and not be one.
3. **Third pass** (this document): re-expressed as one unified signed structure (6 zones),
   then split the z=0 "prediction inside bucket" case out from "0 to +1σ" into its own row.
   This split was the most consequential change: the blended "0 to +1σ" zone had looked
   moderately bad (−25.0% ROI, n=35); splitting revealed the z=0 cases are only mildly
   negative (−3.6%) while the remaining just-outside-the-bucket cases are the single worst
   zone in the whole dataset (−41.6%, n=19). Blending had been masking exactly how bad that
   specific band is.

## Open questions / not yet done

- **−1σ to 0 and 0 to +1σ (excl. z=0) are large enough to look meaningful but haven't been
  independently stress-tested the way −2σ to −1σ / +1σ to +2σ have.** No reason yet to
  distrust them, but they haven't been cross-verified against a second independent
  computation the way the ±1-2σ zones have.
- The mechanism behind the −2σ/−1σ vs +1σ/+2σ asymmetry (below-prediction profitable,
  above-prediction not) remains **unexplained**. NWS rounding was ruled out. Nothing else
  has been tested.
- Coverage is 73/87 (84%) — the 14 unmatched trades are early in the window and structurally
  excluded from all of the above, not folded in as unknowns.
- This analysis predates, and is independent of, the daily-bucket-cap harm-reduction fix
  (`DAILY_BUCKET_CAP = 2`, deployed 2026-07-17) and the still-paused settlement-time fix
  (blocked on confirming Last Trading Time for KDEN/KMIA/KORD/KAUS). Once real
  single-or-double-bucket-day data accumulates under the cap, this sigma-zone breakdown
  should be re-run against that cleaner data, not treated as final on tonight's
  correlated-multi-bucket dataset.
