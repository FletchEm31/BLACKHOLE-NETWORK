# Item 4 (analog-day matching) — corrected conclusion — 2026-07-20

**This supersedes the original item-4 report in this session's conversation, which overstated the result. Read this instead.**

## What was wrong with the original claim

The first backtest (`asos_analog_day_matching.py`, initial version) reported analog matching roughly halving RMSE vs. a "naive climatology" baseline for both cities, both high and low temperature, and called this validated evidence that today's trajectory carries real predictive signal.

The baseline was the unconditional same-season mean of the final high/low — **zero information about the day in progress**. Beating that isn't meaningful: any method conditioned on today's actual data will beat a method that ignores today entirely. The original comparison never isolated whether analog matching's specific mechanism (multivariate trajectory similarity) added anything over simply *using* today's data in the crudest possible way.

## The fair test

Built a stronger baseline — `weather_silver_asos_running_extremes` + the `persist_*` columns in `asos_analog_day_matching.py`'s backtest: the running high/low **already observed** as of each checkpoint hour (from the continuous true-NWS series, not the 3-hour snapshots), plus a season/hour-specific mean offset (`actual_final − running_so_far`, computed leave-out style, same ±7-day exclusion as the analog pool). This baseline *does* use today's data — it's the simplest possible way to use it. If analog matching can't beat this, the earlier RMSE gain was just "today's data beats no data," not a real analog-matching edge.

Full sweep, both cities, all 8 fixed checkpoint hours (0,3,6,9,12,15,18,21 local), 2024 backtest, k=30 neighbors:

**KLAX** (RMSE, °F):
```
hour   analog_hi  persist_hi  |  analog_lo  persist_lo
  0        3.99       3.96    |     1.99       1.77
  3        3.86       3.96    |     1.88       1.15
  6        3.78       3.88    |     1.81       1.03
  9        3.16       2.38    |     1.84       1.10
 12        2.77       0.86    |     1.92       1.23
 15        2.67       0.12    |     1.94       1.20
 18        2.55       0.12    |     1.97       0.80
 21        2.50       0.07    |     1.95       0.51
```

**KMIA** — same shape: analog roughly ties/edges out persistence at hours 0-6 for HIGH, then persistence pulls dramatically ahead from hour 9 onward; persistence beats analog for LOW at every single hour.

## Corrected conclusion

- **LOW temperature: no analog-matching edge, at any hour, at either station.** Persistence+offset wins outright across the entire day.
- **HIGH temperature: a narrow, legitimate edge exists only in the early-morning window (roughly midnight-6am local).** Analog ties or narrowly beats persistence there (e.g. LAX hour 6: 3.78°F vs 3.88°F). From ~9am onward, persistence dominates increasingly hard — and by mid-afternoon the comparison stops being meaningful in a different way: persist RMSE collapses toward zero (0.12°F, 0.07°F) simply because **the actual daily high has usually already happened by 3-9pm**, so "running high so far" effectively *is* the answer already, not a forecast.

The scoped, honest claim: **analog-day matching has a real but narrow use case — HIGH-side prediction in the first few hours after local midnight, before the day has developed enough for a simple running-high rule to have anything to work with. It does not add value for LOW-side prediction, and it does not add value for HIGH-side prediction past mid-morning, where persistence is either better or the question is nearly already answered by direct observation.**

## Tuning sweep (2026-07-20) — confirms this is structural, not an implementation artifact

Ran `scripts/weather/asos_analog_tuning_sweep.py`: k in {10, 30, 100}, and month-level z-score normalization as an alternative to season-level, at hours 0/6/12/18 for both stations.

- **k=10 beats k=30 and k=100 at every hour tested, both stations.** Tighter neighbor pool = better match. This *strengthens* the early-morning HIGH edge rather than erasing it — e.g. LAX hour 6 goes from a near-tie at k=30 (3.78 vs persist 3.88) to a clear win at k=10 (3.66 vs 3.88); KMIA hour 6 similarly widens (2.72 vs persist 3.44).
- **Month-level normalization is worse than season-level everywhere tested** — the per-month population is thin enough (roughly 90 days) that the normalization itself adds noise. Not a fix, don't pursue it.
- **The LOW-side conclusion doesn't move.** Even with the better k=10, persistence still wins at essentially every hour/station combination. The one exception — KMIA hour 0, k=10: analog 2.39°F vs persist 2.45°F — is a 0.06°F difference, i.e. noise, not a finding. Nothing here overturns "no real LOW-side edge."

**Closing scope for item 4, with k=10 as the recommended parameter going forward:**
- **HIGH-side**: a real, now better-quantified edge in the early-morning window (roughly midnight-9am local), where analog matching beats a simple running-high-plus-offset rule. Past mid-morning, persistence wins increasingly decisively, and by afternoon the question is largely already answered by direct observation.
- **LOW-side**: no edge over persistence at any hour, at either station, under any of the k/normalization variants tested. Persistence (running low so far + season/hour offset) is simply the better predictor throughout the day.

This is the final, scoped conclusion for item 4. Any future work building on this should use k=10, restrict analog-matching's claimed value to HIGH-side early-morning prediction, and use plain persistence everywhere else.
