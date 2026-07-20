# LAX/MIA multivariate probability model — findings and recommendations — 2026-07-20

Summary of the four methods built and tested this session against the 15-year LAX/MIA 1-minute ASOS archive. Scope discipline held throughout: everything below is characterization and backtesting only — nothing has been wired into CP1-4. Recommendations describe what's worth promoting into the live pipeline and what isn't, for review before any of it touches trading logic.

## Method 1: Degree-crossing extraction — reference data, not a predictor

Reconstructed NWS's actual methodology (trailing 5-minute average, recomputed every minute) and logged every whole-degree transition. Confirmed as a side effect: the archive's 5-min table is a pure snapshot of the raw reading (100% exact match against 1-min at matching timestamps) — not an average — so it was never a valid proxy for the true series; the reconstruction was necessary, not optional.

**Finding**: rate of temperature change (minutes per degree) varies meaningfully by city/season/direction — LAX winter fastest (6 min/°F both directions, dry offshore-influenced swings), LAX summer slowest falling (18 min/°F, marine-layer moderation), MIA winter falling slowest of all (14 min/°F, gradual frontal cooling).

**Recommendation**: keep as reference/diagnostic data, not a direct trading input. Useful for sanity-checking other signals ("is today's rate of movement unusual for the season") and for setting realistic expectations in any timing-sensitive logic (e.g. stop-loss windows), but it doesn't itself produce a forecast.

## Method 2: Time-of-occurrence distribution — usable for HIGH, needs a fix for LOW

Built the full time-of-day distribution for daily high/low from the continuous reconstructed series, season-conditioned.

**Finding, HIGH**: clean, sensible, directly usable — e.g. LAX summer P90 high-time is 14:16, meaning ~90% of historical summer highs have landed by 2:16pm.

**Finding, LOW — a real methodological gotcha**: ~14-16% of days (both cities) have their "low" pinned to just before local midnight rather than the expected pre-dawn window. This is an artifact of the fixed-midnight calendar-day boundary — the true overnight trough often falls just after midnight, which gets assigned to the *next* day.

**Recommendation**: safe to use HIGH-side time-of-occurrence percentiles as-is for a live "how much probability is left" signal. Do **not** use the raw LOW-side percentiles without addressing the boundary artifact first — either recompute LOW timing against a shifted day convention (e.g. noon-to-noon) or explicitly filter/flag the ~15% of affected days before those percentiles inform anything live.

## Method 3: Multivariate conditioning — one usable signal, three that don't hold up alone

Four bivariate hypothesis tests:

| Test | Result | Verdict |
|---|---|---|
| Morning precip → suppressed high | **-5°F at LAX, -2°F at MIA**, minimal timing shift, clean signal | **Usable standalone** |
| Dewpoint spread → afternoon heating rate | r=-0.176 (MIA), r=-0.049 (LAX) — correctly signed, modest at best | Not strong enough alone |
| Sea-breeze onset timing → high timing/magnitude (LAX) | No clean monotonic relationship; likely masked by marine-layer dynamics we can't measure retroactively (no historical cloud-cover data) | Not usable as tested |
| Evening pressure trend → next-day low timing (MIA) | r=-0.064 — negligible | Not usable |

**Recommendation**: implement the morning-precip → suppressed-high adjustment as a simple standalone flag (check for precip in the morning trajectory, shade the high prediction down accordingly) — it's the cleanest, cheapest win in this whole session. The other three variables aren't strong enough in isolation to justify standalone rules, but don't discard them entirely: they may still carry value as *additional features* inside a fuller model (e.g. XGBoost) rather than as isolated correlations — that's a different, larger undertaking than what was tested here.

## Method 4: Analog-day matching — a narrow, real edge, not the broad one first reported

Original backtest overstated the result (compared against an unconditional climatology baseline with zero same-day information — beating it isn't meaningful). Corrected against a fair "persistence + season/hour offset" baseline (running high/low already observed, plus a leave-out-validated adjustment) and swept all 8 checkpoint hours, both cities. Tuning sweep (k=10/30/100, month- vs season-normalization) confirmed the result is structural, not an artifact — k=10 is the better parameter and *strengthens* the finding.

**Final finding**:
- **HIGH-side**: real edge in the early-morning window only (roughly midnight-9am local). Analog matching (k=10) beats persistence there. From ~9am on, persistence wins increasingly decisively, and by afternoon the comparison stops being meaningful — the actual high has usually already happened by then.
- **LOW-side**: no edge over persistence at any hour, either station, under any tested variant.

**Recommendation**: implement persistence + season/hour offset as the **primary predictor for both HIGH and LOW at every hour** — it won essentially every test in this session and is far simpler than analog matching. Layer analog-day matching (k=10) on top **only** for HIGH-side prediction in the early-morning window, where it's demonstrably better. Do not build out analog matching as a general-purpose all-day, both-metrics system — that was the original plan and the data doesn't support it.

## Overall recommendation, ranked

1. **Persistence + season/hour offset** — implement first. Simplest, cheapest, wins almost every comparison in this session.
2. **Morning-precip → high-suppression adjustment** — implement alongside it. Cheap, clean, additive.
3. **Analog-day matching (k=10), HIGH-side, early-morning hours only** — implement as a targeted enhancement on top of (1), not a replacement for it.
4. **Time-of-occurrence percentiles (HIGH-side)** — usable now for a "probability remaining" live signal. LOW-side needs the midnight-boundary fix first.
5. **Degree-crossing rate data** — keep as diagnostic/reference, not a direct input.
6. **Wind onset timing, pressure trend, dewpoint spread (standalone)** — do not implement as standalone rules; revisit only as candidate features in a proper multivariate model, not bivariate correlations.

None of this is wired into CP1-4. That remains a separate decision, to be made with the operator once this write-up has been reviewed.
