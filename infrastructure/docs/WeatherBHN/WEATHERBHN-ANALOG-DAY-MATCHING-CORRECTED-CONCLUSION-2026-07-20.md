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

## What has NOT yet been checked (tuning sweep queued, results to be appended here)

Whether the narrow early-morning HIGH edge (and the total absence of a LOW edge) is a structural finding about this problem, or an artifact of this specific implementation:
- k=30 neighbors — untested against other values (e.g. k=10, k=100)
- Season-only z-score normalization — untested against per-(season, checkpoint-hour) normalization, which might preserve more signal than the current season-pooled approach

If a quick sweep on these doesn't change the shape of the result above, this conclusion stands as the closing scope of item 4. If it does change materially, this doc needs a follow-up correction — the same discipline that produced this correction in the first place.
