# Pending edits for "WeatherBHN - Architecture and Design Overview.docx"

Word COM automation failed to complete twice (file-lock conflict, then repeated hangs) —
docx was never modified (confirmed: still 7/2 6:04 PM, 34,944 bytes). Paste these in manually.

## Table 5 (§4.1 Live, Trustworthy Tables)

**Row: weather_silver_forecast_conformed, Notes column** — replace with:
> Both sides populated, all 8 cities. INCIDENT (7/1-7/3): bhn_weather_collector role had zero INSERT/UPDATE grants on this table after the 7/1 collector-node split - silent, non-fatal failure, 47+ hrs stale before caught 7/3. Fixed via GRANT; verified fresh writes for all 8 cities including Austin/NYC/Chicago at 06:31 UTC. See Section 6.

**Row: weather_silver_forecast_error, Notes column** — replace with:
> Healthy, verified as a positive control 7/2. Also affected by the 7/1-7/3 bhn_weather_collector grant bug - not independently reconfirmed post-fix.

**Row: weather_position_exits, Source/Depends On column** — replace with:
> Parallel to weather_gold_contract_ledger, keyed by contract_ticker. CONFIRMED 7/3: written by exit_audit_logger.py's record_paper_trade(), called from core_trading_orchestrator.py. Scored nightly via bhn-exit-audit.timer (00:30 UTC). Live, not legacy/frozen.

**Row: weather_position_exits, Notes column** — replace with:
> Kelly-sizing bug fixed 7/2. Currently blocked from settling by weather_silver_actuals_conformed's nws_cli staleness - 0 of 30 open rows scored as of 7/3.

## Table 6 (§4.2 Retired / Do-Not-Use Tables)

**Row: weather_gold_daily_edge_sheet, Reason column** — replace with:
> Superseded for WRITES 6/30. CORRECTION 7/3: still READ nightly by weather_settlement_reconciliation.py's refresh_contract_ledger(), feeding frozen 6/30 data into the live ledger. Not inert.

## §4.3 Tables Requiring Caution (bullet, not a table)

Replace the `weather_model_accuracy` bullet with:
> weather_model_accuracy - CORRECTED 7/3: actively written every night by weather_settlement_reconciliation.py (240 rows, fresh 7/3 00:39-00:41 UTC). Not stale. Q19-22 migration off it may need re-review.

## Table 7 (§6 Decision & Fix Log) — add 3 rows

| Date | Item | Summary |
|---|---|---|
| 2026-07-03 | Silver-table permission bug fixed | bhn_weather_collector had zero grants on 3 silver tables since 7/1 migration. Fixed via GRANT. forecast_conformed resumed all 8 cities; actuals_conformed only 1/8 stations recovered so far - recheck in 24h. |
| 2026-07-03 | NO-side liquidity caps deployed | Wired up dead liquid/illiquid edge split; added open-interest cap (10%), volume cap (5%), spread check (20¢ max) as shared `apply_liquidity_caps()` for NO + future YES. Verified against real KDEN (thin, caps bind) and KORD (liquid, caps don't bind) rows before deploying. Live cycle clean, zero errors. |
| 2026-07-03 | Architecture doc corrections | 7/2 snapshot had factual errors: edge_sheet marked fully retired (still read nightly), model_accuracy marked stale (actively written), position_exits writer marked unconfirmed (now confirmed). Corrected in Sections 4.1-4.3. |
| 2026-07-16 | Edge ceiling added (`EDGE_CEILING_CENTS = 25.0`) | Backtested +21.4% ROI vs -13.3% unbounded (87 settled trades, 3 cities: KDEN/KMIA/KLAX, ~16 days). Applied uniformly to both liquid/illiquid `edge_threshold` branches — new `EDGE_TOO_HIGH` skip_reason. Deployed to `/opt/bhn/trading/cp4_kelly_sizer.py` 02:51:50 UTC; DRY_RUN unchanged (still true). **25¢ edge ceiling deployed 2026-07-16 — revisit once (a) trade count grows meaningfully beyond 87, (b) other 5 cities (KPHX, KDFW, KNYC, KORD, KAUS) begin producing settled trades, or (c) a full season/quarter of data exists, whichever comes first. Current ceiling is based on a 3-city, ~16-day sample and should not be treated as final.** Pre_open/is_liquid boundary bug (illiquid branch unreachable) explicitly NOT touched — separate task. |
| 2026-07-16/17 | Edge ceiling rolled back; `edge_cents`/`model_prob_no_cents`/`predicted_tmax_f`/`hours_to_settle`/`sigma_used` found live-drifted | Same-night reconstruction of the 2026-07-16 ceiling backtest against true entry-time values (log-derived, matched to frozen `entry_captured_at`) found the "+21.4% ROI" result was lookahead bias — no ceiling is profitable on correctly-measured data. Ceiling reverted on LA and in git (`4e7c95d`). `entry_edge_cents`/`entry_model_prob_no_cents`/`entry_predicted_tmax_f`/`entry_hours_to_settle` columns added as permanent frozen-at-entry fields (migrations `2026-07-17`, `2026-07-17b`) so this class of bug can't recur silently. Settlement-time assumption ("4PM local, same day") also found wrong for every verified city (real rule is 7-8AM or 10AM ET the *next* day) — fix designed (hybrid: real Kalshi rule for new-template cities, NWS-data-release-check capped at 8AM ET for legacy-template cities) but **not yet implemented**, paused pending confirmation of Last Trading Time (a separate field from Expiration time) for KDEN/KMIA/KORD/KAUS. |
| 2026-07-17 | Multi-bucket correlated betting identified as a likely primary loss driver; `DAILY_BUCKET_CAP = 2` deployed as harm reduction | 25/26 multi-bucket trading days showed exactly N-1 wins/1 loss (pigeonhole principle — one actual temperature, multiple NO bets against overlapping bucket ranges). Capping at top-2-by-confidence backtested best of cap=1/2/3/4/5 (-6.7% ROI vs -13.3% uncapped) — explicitly harm reduction, not a profitability fix, still net-negative. Deployed to `/opt/bhn/trading/cp4_kelly_sizer.py` as a pure post-processing filter (`git` `09dbb60`) — per-bucket edge/Kelly math untouched. Joint-probability-distribution redesign (treating a city/day as one coordinated decision) remains the real long-term fix, tracked separately, not started. |
| 2026-07-17 | Sigma-zone backtest — one finding survived reconstruction where four others didn't | Full writeup: `WEATHERBHN-SIGMA-ZONE-ANALYSIS-2026-07-17.md`. Headline: predictions 1-2σ below the bucket are profitable (+36.6% ROI, n=8, reconstruction-verified), while predictions just above the bucket by less than 1σ are the worst zone in the dataset (-41.6% ROI, n=19). Promising hypothesis, not implemented, mechanism unexplained (NWS rounding convention checked and ruled out as a cause). |

## New §2.3 — NO-Side Qualification Logic (cp4_kelly_sizer.py)

CP4's NO-side qualification runs in `run_cp4_kelly()`. As of 7/2 only the edge threshold and pre-open filter were live; the liquidity-based checks below were dead code until fixed 7/3.

- **Pre-open filter**: skip if volume ≤ 100 contracts, market not active, or snapshot older than 45 min.
- **Edge threshold**: ≥5¢ if liquid (volume > 100), ≥8¢ if illiquid. LIVE as of 7/3 — previously hardcoded to always use the 8¢ illiquid threshold regardless of real liquidity (dead code, stale comment claimed volume data didn't exist). NOTE (7/16): the illiquid branch is itself unreachable today — pre-open's `volume ≤ 100` cutoff and `is_liquid`'s `volume > 100` threshold share the same boundary, so no trade has ever qualified as illiquid. Not fixed here; see 7/16 log entry below.
- **Edge ceiling**: ≤25¢, both liquid and illiquid. NEW, deployed 7/16 — see Table 7 for backtest basis and revisit checkpoint. Skip reason `EDGE_TOO_HIGH`.
- **Bid-ask spread check**: skip if yes_ask − yes_bid > 20¢. NEW, deployed 7/3. Real yes_bid/yes_ask, never the unrelated `ensemble_spread` (NWS-vs-GFS forecast divergence) field.
- **Open-interest cap**: position capped at 10% of the contract's open_interest. NEW, deployed 7/3.
- **Daily volume cap**: position capped at 5% of current volume. NEW, deployed 7/3.
- If either cap reduces the position to 0 contracts, the bucket becomes SKIP (`skip_reason=ILLIQUID_CAP`), not a zero-size BET_NO.
- Shared logic: `apply_liquidity_caps()` — explicitly shared between NO and future YES qualification, not duplicated per side.

**Verification (7/3, real rows, before deploying):**
- Thin: `KXHIGHDEN-26JUL03-B90.5` (KDEN), volume=289, open_interest=219 → hypothetical 149-contract position caps to 14 (~90% reduction).
- Liquid: `KXHIGHCHI-26JUL02-T93` (KORD), volume=130,415, open_interest=128,185 → caps compute to ~6,500-12,800 contracts, far above any real position size (largest ever seen: 3,333) — caps correctly don't bind.
- Deployed to `/opt/bhn/trading/cp4_kelly_sizer.py`; live orchestrator cycle (06:39:26 UTC) completed with zero errors; ledger confirmed `market_liquidity` now reflects real LIQUID/ILLIQUID (was hardcoded ILLIQUID) and two new `skip_reason` values are wired in alongside the existing ones.
