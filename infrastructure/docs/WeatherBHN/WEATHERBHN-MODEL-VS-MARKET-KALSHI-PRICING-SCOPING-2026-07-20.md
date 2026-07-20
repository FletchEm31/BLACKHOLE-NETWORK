# Project: Model vs. Market — Kalshi pricing layer — 2026-07-20

**This is a task write-up / scoping doc, not a build. Nothing here has been implemented.**

## Objective

Join the validated weather ground-truth/model-probability work (this session's LAX/MIA ASOS reconstruction, the multivariate probability model, the ASOS-derived CLI labels) against Kalshi's actual pricing history to see where the model's estimated probability diverges from what the market is charging — scoped to what the data can actually support right now, not what it will support once more history accumulates.

## Status of two open threads — read before starting, don't treat as resolved

Two data-integrity issues found this session are **scoped/documented only, not fixed**, by explicit operator decision (work on them paused to start this project, not because they were closed):

- `WEATHERBHN-CLI-PRELIMINARY-VS-FINALIZED-SCOPING-2026-07-20.md` — 95% of KLAX's genuine CLI rows are preliminary 5pm-cutoff reports, not finalized, and `is_final=TRUE` doesn't distinguish them. Touches real settled trades (confirmed HIGH-side only, KDEN/KMIA, 348 rows).
- `WEATHERBHN-KDEN-BRONZE-SILVER-DIVERGENCE-SCOPING-2026-07-20.md` — KDEN 2026-06-11 bronze (77.0°F, matches NOAA) and silver (90.0°F, `nws_cli`) disagree with no audit trail; this exact date is one of the two confirmed real settlement-outcome flips found this session.

**Implication for this project**: any ground-truth value pulled for KDEN or KMIA from `weather_silver_actuals_conformed` (`actual_source='nws_cli'`) during the exact window this Kalshi pricing project will use (2026-06-11 onward — see below) carries unresolved risk from both issues. Don't silently trust `nws_cli` rows as ground truth for this comparison without cross-checking against NOAA daily actuals or the ASOS-derived labels (KLAX/KMIA only) first, the same way this session's settlement-flip check did. This project can proceed in parallel with those two threads staying open, but its conclusions for KDEN/KMIA should be treated as provisional until they're actually fixed.

## Data inventory (confirmed this session, don't re-derive)

**Market pricing**: `weather_silver_market_conformed` — ~26.7M snapshot rows, 2026-06-11 to present (still live). KLAX/KDEN/KMIA, both HIGH and LOW contract sides (KLAX 246/126 tickers, KDEN 246/126, KMIA 246/120 for high/low). Snapshot grain, not aggregated — every price poll is its own row, `UNIQUE(market_ticker, snapshot_time)`. Fields: `market_ticker` (parseable into `station_code`/`contract_side`/`bucket_floor`/`bucket_cap`/`bucket_label`), `snapshot_time`, `yes_mid`/`yes_bid`/`yes_ask`, `implied_prob` (currently just `yes_mid` relabeled, not independently computed — that's the standard interpretation for a binary contract's mid-price, not a red flag, just worth knowing there's no extra modeling layer behind that column name), `volume`, `open_interest`, `market_liquidity_flag`, `is_latest_snapshot`. **No settlement outcome column** — ground truth has to come from elsewhere, the join is not self-contained.

**LOW-side pricing is a real, usable asset already**: 120-126 tickers/station tracked continuously even though CP4 has never executed a LOW-side trade (confirmed separately this session — `weather_gold_contract_ledger`'s LOW rows are storage-only). That means whenever LOW-side execution logic eventually gets built, there's already weeks of real pricing history to backtest against, not a cold start. Worth exploiting in this project even though it can't validate against real LOW-side trade outcomes yet.

**Ground truth sources available**:
- `weather_model_accuracy` — 348 resolved rows, **HIGH-side only** (`variable='tmax_f'`, confirmed), KDEN (174) + KMIA (174). This is the only source with BHN's own predicted probability, the market's implied probability at bet time, and the recorded outcome/correctness/P&L already computed — but see the caveat above about its `nws_cli`-derived ground truth for this exact window.
- `weather_silver_actuals_conformed` — `nws_cli` (40/station, 2026-06-10 to 2026-07-19, use with the caveat above), `visual_crossing` (continuous, 2023-2026), `asos_derived_cli_algorithm` (KLAX/KMIA only, validated this session, 2010-2026 — **not available for KDEN**, no historical ASOS archive was ever sourced for Denver).
- `weather_bronze_noaa_daily_actuals` — decades-deep, all 8 stations now loaded (including KDEN/KPHX added this session), validated against genuine CLI for KLAX/KMIA/etc.

## Honest scope limitation before starting

Market pricing history only goes back to **2026-06-11** — about 6 weeks as of this write-up. This is fundamentally a short-window, recent-history comparison, unlike the 15-year ASOS characterization work — don't expect (or claim) the same statistical depth. Whatever gets built here should say explicitly how many days/contracts it's drawing from, every time it reports a finding, the same discipline that caught the analog-matching baseline problem earlier this session.

## Core question

Where does the market's implied probability (`yes_mid`) diverge from a properly calibrated model probability, and — using validated ground truth, not just recorded settlement — was the market or the model actually right when they diverged? This is the same "fair baseline" discipline from the analog-matching work: don't just check if the model beat the market on paper (`weather_model_accuracy` already does that, with the caveat above); check whether the *market's own pricing* was well-calibrated against the true outcome, which nothing has tested yet.

## "What did our model think" — reuse the existing function, don't re-derive

`calculate_bucket_probability()` in `scripts/weather/cp4_kelly_sizer.py:271`:

```python
def calculate_bucket_probability(predicted_tmax_f: float, sigma: float,
                                  bucket_floor: Optional[float], bucket_cap: Optional[float],
                                  blended_mean: float) -> tuple[float, str, float]:
    # prob_yes = P(tmax falls in [bucket_floor, bucket_cap])
    # Within 2-sigma of blended_mean -> Gaussian CDF
    # Beyond 2-sigma (tail bracket)  -> Student-t CDF (df=5, heavier tails)
```

This is the live ladder-display computation — use it directly (import and call, don't reimplement the CDF logic) for every model-probability value this project needs. Whatever feeds `blended_mean`/`sigma` into it in production (CP3/CP4's own inputs) should be traced and reused the same way, not reconstructed independently.

## Build, in order

1. **Join** `weather_silver_market_conformed` snapshots to the model's contemporaneous bucket probability at matching `(station, date, bucket, snapshot_time)` — via `calculate_bucket_probability()` above. Compute `divergence = model_probability − market_yes_mid` for every snapshot in the full window, both HIGH and LOW sides, all three cities (KLAX, KDEN, KMIA).
2. **Backtest, where a resolved outcome exists** (the 348 HIGH-side rows only): test a simple rule — e.g. "take the model's side when divergence exceeds some threshold" — against the real settlement. **This is the only piece that's a genuine backtest.** Everything else in step 1 is a cross-sectional divergence study, not validated against outcomes, and must not be described as if it were.

For LOW-side and any HIGH-side snapshot without a resolved outcome yet: report divergence patterns only, explicitly labeled **"no realized-outcome validation"** in every summary that includes them. Still worth reporting — it's free information, and LOW-side pricing has been tracked the whole time even without live LOW trades — but it is not the same category of evidence as step 2, and must never be blended into the same summary statistic as the resolved-outcome backtest.

**Split every result by `market_liquidity_flag`.** A theoretical edge on an illiquid contract isn't real edge — don't average liquid and illiquid together in any summary number, the same way item 3's multivariate conditioning work kept per-city results separate rather than pooling them into one misleading average.

## Scope discipline

- **This is a ~40-day window covering a single season (summer).** Treat any result as a preliminary, season-specific read, not a validated strategy — the same correction already applied once this session to the analog-matching claim. Don't let a clean-looking summer number get treated as a year-round edge.
- **Watch the small-sample/multiple-comparisons trap.** 348 resolved rows is not a lot once split by city, bucket, or time of day. If a pattern only shows up in one narrow slice, that's a hypothesis to keep watching as the sample grows, not a result to act on — same discipline as the item-3 bivariate tests, most of which turned out too weak to stand alone.
- **Characterization only.** Nothing here touches CP1-4 without a separate, explicit decision later — same standing rule as every other piece of this session's work.
