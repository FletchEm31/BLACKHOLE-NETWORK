# WeatherBHN Session Audit — 2026-07-17 evening through 2026-07-18 night

Consolidated write-up of every finding and fix from this session. Two Claude Code
sessions ran in parallel for part of the night (referred to here as CC1/CC2 per the
operator's own naming); this document covers the full session, noting which items
were independently investigated/verified by this session (CC1, this document's author)
versus items built by the parallel session and only spot-verified here.

---

## 1. KLAX ERA5 bronze table load

**Found:** No historical ERA5 (reanalysis) data existed for KLAX — only KMIA had a
loaded `weather_bronze_era5_kmia` table.

**Fixed:** One-time historical load, mirroring the KMIA pattern minus marine-only
variables (KLAX's box is inland, not coastal): new `weather_bronze_era5_klax` table,
new `weather_era5_klax_ingest.py` script, `CDS-API - ERA5 (LOS ANGELES
INTERNATIONAL).txt` request definition. 13,392 rows loaded (2024-12-31 through
2026-07-12).

**Commit:** `633082b`

**Verified:** Dry-run showed correct variable coverage (~100% for 8/9 vars, 49.6% for
`cbh` — expected, NULL when no cloud base detected). Live run matched dry-run
row/timestamp counts. Spot-checked temperatures (68-78°F for mid-July LA) against
known real-world range.

**Deployed:** Live on LA, table populated.

---

## 2. Dashboard `data_stale` false positive

**Found:** `/api/ladder`'s `data_stale` flag fired whenever a station/date had zero
fresh snapshot rows, without distinguishing "Kalshi hasn't listed this market yet"
(expected, e.g. tomorrow's contract before it opens) from "market is listed but the
collector stopped reporting" (a real problem). Confirmed via a live false alarm: a
"collector stale >45min" report turned out to be a market that simply hadn't opened.

**Fixed:** Added a `prediction_contracts` check (populated by every discovery poll,
independent of price-snapshot success) before flagging `data_stale`. New
`market_listed` field in the API response.

**Commit:** `633082b`

**Verified:** Live-tested against KDEN 2026-07-19 (not yet listed) — `data_stale:
false`, `market_listed: false`, correct. KDEN 2026-07-18 (real market) — `data_stale:
false`, `market_listed: true`, 6 buckets returned, unaffected.

**Deployed:** Live on LA.

---

## 3. Kalshi collector "stale >45min" alert — investigated, no bug found

**Found:** Dashboard showed a stale-collector banner. Full triage: both collector
services healthy (`bhn-kalshi-prices.service` running 1+ week uninterrupted,
`bhn-weather-collector.service` last run exit 0/SUCCESS), zero gaps in
`weather_bronze_kalshi_market_snapshots` over a 3-hour window, zero errors in
journalctl. Root cause was exactly item #2 above (not-yet-listed market
mis-flagged as stale).

**Fixed:** No collector-side fix needed — resolved by item #2's dashboard logic fix.

**Verified:** Confirmed live after the item #2 fix deployed — banner cleared for the
same station/date that had shown it.

---

## 4. Yes-side trading infrastructure, Phase 1 (schema + data-model only)

**Found:** No mechanism existed to distinguish a future YES-side bet from CP4's
current NO-side-only ("Tail-No") trades. Motivated by the sigma-zone finding that
0σ buckets are the largest dollar loss on the NO-side (pooled: -18.6% ROI, -$558 net,
n=36) — a candidate for a YES-side bet instead, deliberately deferred to a separate
Phase 2 session.

**Fixed:**
- `side` column on `weather_position_exits` (VARCHAR(4), CHECK IN ('NO','YES'),
  default 'NO'), all 111 pre-existing rows backfilled to 'NO'.
- `exit_audit_logger.py`'s `record_paper_trade()` takes a `side` parameter
  (default 'NO'); `_RECORD_SQL` binds it as a real parameter, not hardcoded text.
- **Bug found and fixed before it could bite:** the original `UNIQUE(contract_ticker)`
  constraint would have let a NO and a YES bet on the same bucket collide as one row
  via `ON CONFLICT DO UPDATE`. Replaced with `UNIQUE(contract_ticker, side)`.
- Safe `side='NO'` filters added to `score_settled_positions()`,
  `weather_paper_pnl_dashboard`, `weather_paper_trading_summary`, and the dashboard's
  `get_ladder()` signal query — preserve today's 100%-NO behavior exactly.
  `get_sigma_performance()` deliberately left unfiltered/unfixed — that's the one
  that should become side-*aware* (NO vs YES comparison) once real YES data exists,
  not just filtered.

**Migrations:** `2026-07-18-add-side-column-to-position-exits.sql`,
`2026-07-18b-side-composite-unique-and-safe-filters.sql`

**Commits:** `fc7658c` (side column), `c708c3d` (composite constraint + filters)

**Verified:** `test_side_collision_2026_07_18.py` — inserts a NO and a YES row on the
same fake `contract_ticker`, asserts both persist as distinct rows, rolls back (never
commits) so it's safe against production data. Ran live: **PASS**. Backups taken
before both migrations (`/root/db-backups/` on LA).

**Deployed:** Live on LA, table + constraint + filters all active.

**Known gap, correctly not built:** `score_settled_positions()`'s outcome/P&L math is
still hardcoded NO-side (see item #8 below for the *unrelated* but adjacent boundary
bug found in the same function). A stray YES row today would sit permanently
unscored (fails safe) rather than get mis-scored — verified this is the actual
behavior, not just assumed.

---

## 5. Day-of-year shrinkage sigma calibration

**Found:** CP4's `model_rmse` (which becomes `sigma` for every trade) was a
season-bucket (4 buckets/year) RMSE from `model_calibration`. A proposal to compute
day-of-year-specific sigma from "5 years of NWS data" was infeasible as stated —
`weather_bronze_nws_forecast_snapshots` (the forecast archive) only goes back to
2026-06-10, about 5-6 weeks; NWS doesn't publish a retroactive forecast archive.

**Fixed:** New `weather_model_calibration_daily` table + `build_daily_shrinkage_
calibration_2026_07_18.py`: day-of-year (±12 day circular window) RMSE,
empirical-Bayes shrunk between the real-but-thin forecast-error sample
(`weather_silver_forecast_error`) and an 80-year climatological stddev prior
(`weather_bronze_noaa_daily_actuals` — KLAX back to 1944, KMIA/KORD to
1946-1948). Blend weight shifts from prior-anchored toward the empirical estimate
as real data accumulates, no manual retuning needed. `prior_pseudo_n=30`.

**Wired in:** `cp3_inference.py`'s real `model_rmse` lookup (the one that becomes
CP4's sigma) switched from `model_calibration` (season) to
`weather_model_calibration_daily` (day-of-year), with a season-bucket fallback for
uncovered station/day combos. The two OTHER `model_calibration` lookups in the same
file (feeding the trained XGBoost model's own input features) were deliberately
left untouched — the model expects that exact season-bucket input distribution.
Dashboard's parallel "computed fresh" sigma preview (`main.py`) updated to match.

**Migrations:** `2026-07-18c-day-of-year-shrinkage-calibration.sql`

**Commits:** `91fed1a` (table + script), `98aaede` (wired into CP4)

**Verified:** KDEN day-of-year 199 (today): 550 real samples, sample RMSE 1.64°F vs
climatological prior 6.92°F, blend gives 94.8% weight to the sample →
`blended_sigma=2.24` — close to the live `entry_frozen` sigma (2.55) for a real open
position, and far tighter than the prior alone. Data-sparse days (e.g. Jan 1, no
forecast-archive coverage) correctly fall back to the pure climatological estimate.
Verified end-to-end against the real deployed model (KDEN, KMIA) in an isolated
scratch copy before deploying — `model_rmse` output matched the pre-computed
`blended_sigma` exactly in both cases.

**Deployed:** Live on LA, wired into CP4's real signal path.

---

## 6. Position Summary panel enhancements

**Found:** The dashboard's real-trades panel (distinct from the manual-entry
Simulation Summary) was missing several fields useful for auditing individual
trades against the sigma-zone research: entry price, contract count, entry sigma
distance, model-predicted temp, and actual settled temp side by side.

**Fixed:** Added, in this column order: Station, Ticker, Bucket, Model Predicted
Temp, Final Actual Temp, Side, Investment, Price, Contracts, Sigma, Fee, Result,
P&L, ROI%. `entry_sigma_distance` reuses the exact same signed-distance-to-near-edge
formula as the reference strip's sigma markers (`_raw_sigma_distance()`, refactored
out of `_sigma_marker_for_trade()` so both share one implementation), unrounded to 1
decimal for a precise per-trade figure.

**Commits:** `1279e30`, `c436397`

**Verified:** Live-checked against known rows — KMIA 2026-07-16 T97
(`entry_sigma_distance: None` — genuinely null, trade predates the `entry_sigma_used`
column by one day, not a bug). KDEN open positions today showing real computed
values (-0.8σ, +0.4σ, 0.0σ).

**Deployed:** Live on LA.

---

## 7. Max-stake-on-losses / edge-formula root-cause investigation (analysis, no code fix)

**Found:** Real, confirmed pattern (not just impression) — settled NO-side trades:
loss stakes cluster tightly near the $100 cap (p10=$99.22, 93.8% at ≥$90), win
stakes are much more spread out (p10=$32.13, 60.9% at ≥$90). Tiered by
`final_entry_edge_cents` (the frozen, drift-corrected column — confirmed this is
NOT a rehash of the entry-time-drift bug fixed the previous night): the highest
edge tier (25¢+) has the *worst* win rate (42.9%, n=21, after excluding 5 legacy
NULL-edge rows my first pass had silently mis-bucketed), while the middle tier
(8-15¢) is best (84.6%).

**Root cause identified:** Decomposed `edge_cents = model_prob_no_cents −
no_ask_cents`. The model's own probability estimate is *not* more overconfident
on losses (85.49 vs 90.12 for wins) and tracks realized win rate reasonably well on
its own (74.92→95.83 as win rate rises 60%→100% across market-price tiers). The
actual driver is the market price: losses average a 10.6¢ cheaper `entry_no_ask_cents`
than wins. Win rate rises monotonically with market price, hitting 100% when the
market agrees strongly. **Conclusion: the edge formula treats a cheap, informative
market price as "opportunity" rather than signal — betting big against a market
that's usually right on this specific contract type is the actual loss driver, not
model overconfidence or a market-data bug.**

**Status:** Investigation only. No code changed. This reframes what a fix would need
to look like (weight edge down when driven by an extreme market price rather than a
confident model, or require agreement between the two) but that redesign was not
built this session.

---

## 8. `|z|<1.0` no-trade exclusion zone — verified working correctly, no live bug

**Found (initially flagged as urgent):** Position Summary showed multiple rows with
`entry_sigma_distance` inside the supposed hard exclusion zone (`c8040fe`,
`NO_TRADE_SIGMA_ZONE = 1.0`), which should be structurally impossible if enforced.

**Investigated:** Two distinct issues found during triage, both resolved as benign:
1. The dashboard's `entry_sigma_distance` (near-edge convention) differs from CP4's
   actual enforcement metric (`sigma_dist`, distance to bucket *midpoint*) — a real
   formula mismatch, but not itself a bug (just two different, both-legitimate
   metrics).
2. The apparent "post-fix violations" were comparing against `decision_timestamp`
   (which refreshes on every re-qualifying cycle for an already-open position) instead
   of `entry_captured_at` (the true, immutable first-qualification moment, frozen at
   INSERT and never touched by `ON CONFLICT DO UPDATE`). Re-checked using
   `entry_captured_at`: **zero genuine violations exist** — all apparent ones were
   pre-`c8040fe` trades whose `decision_timestamp` kept refreshing as they stayed
   open. Exactly 3 rows have `entry_captured_at` after the commit; none violate.

**Separately found (real, low-priority, not fixed):** Threshold buckets (T-tickers)
can never be excluded by the zone check at all — CP4's midpoint-distance formula
substitutes a ±9999 sentinel for a threshold's missing side, making the "distance to
midpoint" always ≫1.0 regardless of true proximity to the model's prediction.
Flagged for future attention, not acted on this session.

**Status:** No fix needed for the main question (exclusion zone is enforced
correctly). The threshold-bucket sentinel issue remains open.

---

## 9. Threshold-bucket exact-boundary scoring bug — real bug, fixed

**Found:** Investigating a reported "97° or above" vs "98° or above" label
mismatch surfaced a real settlement-scoring bug underneath it. The 2026-07-07 fix
(`_determine_outcome()`) made "between" buckets inclusive on the YES side — correct,
evidenced by a real settled market (`KXHIGHNY-26JUN10-B81.5`). It generalized that
to threshold buckets too ("T95 means >=95°F") **without independent verification**.
Confirmed wrong against raw Kalshi contract text pulled directly from
`weather_bronze_kalshi_market_snapshots` for `KXHIGHMIA-26JUL16-T97`
(`strike_type='greater'`, `rules_primary`: "...is greater than 97°...", `subtitle`:
"98° or above") and `-T90` (`strike_type='less'`, "...is less than 90°...", "89° or
below") — both **strict** inequalities, not inclusive.

**Impact:** An exact-boundary hit (actual temp == strike value exactly) was scored
`NO_LOSS` when it should be `NO_WIN` (YES never actually triggered). Confirmed on 2
real historical rows.

**Fixed:** `_determine_outcome()` rewritten to branch by bucket shape — "between"
logic completely unchanged (still strict-outside/inclusive-inside, matching its own
real evidence); threshold buckets now use the correct strict rule (exact boundary =
`NO_WIN`).

**Also fixed:** `bucketRangeLabel()` (dashboard) had the same off-by-one — displayed
the raw strike directly ("97° or above") instead of the Kalshi-subtitle-matching
phrase ("98° or above"). This was the original symptom that triggered the whole
investigation; the root-cause fix to `_determine_outcome()` was deployed first, but
the label fix itself was initially identified and *not* deployed until a follow-up
pass caught the gap.

**Commits:** `78e19bf` (scoring fix), `858fa35` (label fix)

**Verified:** 11-case test suite (all 3 bucket shapes, every boundary) run before
deploying — all pass, between-bucket behavior provably unchanged. Comprehensive
audit of all 7 historical settled threshold-bucket trades against the corrected
rule: exactly 2 mismatches (both already known), the other 5 were never affected
(none happened to land exactly on their strike). Cross-checked the 2 flagged
tickers from the operator's spot-check list (T85, T86) — both correctly scored as
recorded. Independently re-confirmed KLAX's real settlement temps (81.0, 84.0)
directly from `weather_silver_actuals_conformed`, matching what was stored.
Checked for a data-shape edge case (unopened threshold rows storing
`bucket_floor == bucket_cap`) — none exist. Checked for other stale/incorrect
raw-vs-corrected disagreements beyond the 2 found — the other 13 are the
pre-existing, already-documented, unrelated 2026-07-07 between-bucket fix.

**Historical correction applied** (backed up first, `corrected_*` pattern, raw
`realized_pnl_usd`/`actual_outcome` never overwritten):
- Row 6937 (KMIA T94, 2026-07-05): raw scoring was actually correct all along
  (`NO_WIN`/+$35.10); an earlier retroactive correction pass had wrongly set
  `corrected_actual_outcome='NO_LOSS'`. Tonight's fix reversed that error, restoring
  `corrected_actual_outcome='NO_WIN'`, `corrected_realized_pnl_usd=$35.10`.
- Row 12459 (KMIA T97, 2026-07-16): wrong from the original scoring
  (`NO_LOSS`/−$71.87). Corrected to `NO_WIN`/+$6.24.

**Deployed:** Both fixes live on LA (`exit_audit_logger.py`, `app.js`), historical
correction applied directly to `weather_position_exits`.

---

## Portfolio P&L reconciliation (full scope: all 101 settled trades, side='NO')

| Metric | Value |
|---|---|
| Gross realized P&L (post threshold-fix) | −$981.76 |
| Total fees (all 101 rows, none missing) | $98.52 |
| **True net effective P&L** | **−$1,080.28** |
| Swing from tonight's threshold-bucket fix alone | +$213.57 (two trades) |

A separately-reported figure ("$2,470.91 across 51 settled trades") could not be
reconciled against this session's data — trade count and total don't match anything
queryable in `weather_position_exits` as of this write-up. Possible explanations not
yet confirmed: a different panel (the Simulation Summary is manual what-if entries,
entirely separate from real trades), a snapshot from earlier in the session before
more trades settled, or a pre-fee-fix number. Flagged for the operator to identify
the source rather than guessed at here.

---

## Open items (stock-take, not fixed this session)

- **`weather_gold_daily_edge_sheet` and `weather_gold_contract_ledger`** — not
  audited for the same class of live-drift issue fixed elsewhere tonight
  (entry-time-frozen vs. live-refreshed columns). Unknown whether either has a
  similar gap.
- **Yes-side Phase 2** — order-placement/trigger logic, side-aware settlement P&L
  math (`score_settled_positions()` is still hardcoded NO-side — see item #4),
  and `_get_open_entry_prices()`'s ticker-only (not ticker+side) keying all remain
  unbuilt, by design (deliberately deferred to a dedicated session).
- **ERA5 KDEN status** — not checked this session; KMIA and KLAX are loaded, KDEN's
  status is unknown/unverified.
- **ERA5 pipeline has no scheduled job** — both KMIA's and KLAX's ERA5 loads are
  one-time manual pulls; confirmed (earlier finding, unresolved) that KMIA's data has
  gone stale (~24 day gap at last check) because nothing re-runs the collector.
- **Sigma redesign — correction to operator's own note:** item #5 above (day-of-year
  window + climatological shrinkage blend) was **already designed, built, and wired
  into CP4's live sigma path this session** (commits `91fed1a`, `98aaede`), not still
  pending. Flagging this discrepancy rather than silently treating it as open.
- **Entry-source dashboard columns** (model/NWS/GFS/ensemble maxT breakdown) — this
  request wasn't fully specified in a way this session could act on; not built.
- **Threshold-bucket exclusion-zone sentinel bug** (item #8) — real, low-priority,
  not fixed.
- **`weather_edge_calculator.py`'s boundary convention** — confirmed dead code
  (disabled timer), not audited in depth per operator deprioritization.
- **P&L reconciliation gap** — the "$2,470.91 / 51 trades" figure remains
  unreconciled, source unknown.
