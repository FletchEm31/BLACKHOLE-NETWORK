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

## New §2.3 — NO-Side Qualification Logic (cp4_kelly_sizer.py)

CP4's NO-side qualification runs in `run_cp4_kelly()`. As of 7/2 only the edge threshold and pre-open filter were live; the liquidity-based checks below were dead code until fixed 7/3.

- **Pre-open filter**: skip if volume ≤ 100 contracts, market not active, or snapshot older than 45 min.
- **Edge threshold**: ≥5¢ if liquid (volume > 100), ≥8¢ if illiquid. LIVE as of 7/3 — previously hardcoded to always use the 8¢ illiquid threshold regardless of real liquidity (dead code, stale comment claimed volume data didn't exist).
- **Bid-ask spread check**: skip if yes_ask − yes_bid > 20¢. NEW, deployed 7/3. Real yes_bid/yes_ask, never the unrelated `ensemble_spread` (NWS-vs-GFS forecast divergence) field.
- **Open-interest cap**: position capped at 10% of the contract's open_interest. NEW, deployed 7/3.
- **Daily volume cap**: position capped at 5% of current volume. NEW, deployed 7/3.
- If either cap reduces the position to 0 contracts, the bucket becomes SKIP (`skip_reason=ILLIQUID_CAP`), not a zero-size BET_NO.
- Shared logic: `apply_liquidity_caps()` — explicitly shared between NO and future YES qualification, not duplicated per side.

**Verification (7/3, real rows, before deploying):**
- Thin: `KXHIGHDEN-26JUL03-B90.5` (KDEN), volume=289, open_interest=219 → hypothetical 149-contract position caps to 14 (~90% reduction).
- Liquid: `KXHIGHCHI-26JUL02-T93` (KORD), volume=130,415, open_interest=128,185 → caps compute to ~6,500-12,800 contracts, far above any real position size (largest ever seen: 3,333) — caps correctly don't bind.
- Deployed to `/opt/bhn/trading/cp4_kelly_sizer.py`; live orchestrator cycle (06:39:26 UTC) completed with zero errors; ledger confirmed `market_liquidity` now reflects real LIQUID/ILLIQUID (was hardcoded ILLIQUID) and two new `skip_reason` values are wired in alongside the existing ones.
