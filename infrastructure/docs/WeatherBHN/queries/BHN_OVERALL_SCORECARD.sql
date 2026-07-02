-- WeatherBHN - BHN Overall Scorecard
-- High level BHN signal performance summary. Shows total recommendations,
-- win rate, P&L, and how BHN compares to simply following the market.
-- Headline number: is BHN actually adding value over the market?
-- Populates after settlement reconciler runs nightly at 15:00 UTC.
-- Source: WeatherBHN_Performance_Queries.txt (June 12, 2026) — Query 19
-- Tab: FORMULA/MODELS — PIN at top
--
-- UPDATED 2026-07-02: weather_model_accuracy retired, replaced by
-- weather_gold_contract_ledger (see project memory
-- weather-gold-edge-sheet-retired). Compat CTE below aliases every old
-- column; market_was_correct, bhn_position_taken, bhn_position_side, and
-- accuracy_score have no direct equivalent and are derived.
--
-- FIXED 2026-07-02: found (not a dashboard bug — a data-provenance issue).
-- 42 BET_NO rows in the settled data all carry an identical flat
-- stake_usd=125.0 (zero variance) instead of genuine Kelly sizing —
-- cp4_kelly_sizer.py (the only current signal generator) never writes
-- BET_YES and always Kelly-sizes, so these 42 rows are legacy/pre-CP4
-- data backfilled into the new ledger schema. Combined with several of
-- them also having market_implied_prob clamped at the 0.99 tick-size
-- boundary, they generate $271,191.73 of a total $343,894.13 reported
-- PnL (79%) via a legitimate-but-flat-staked 99x payout multiplier.
-- EXCLUDED below pending Fletch's separate backfill-vs-exclude decision —
-- underlying weather_gold_contract_ledger rows are untouched, this is a
-- presentation-layer filter only. CARD LABEL: title/description should
-- say "excludes 42 legacy flat-stake rows pending pipeline decision."
--
-- SEPARATE FINDING (not fixed here, flagged for Fletch): bhn_win_rate_pct's
-- denominator is COUNT(*) over ALL rows including SKIP (bhn_correct is
-- always NULL for skips, diluting the rate). True trade-only win rate on
-- clean data (81 BET_YES trades, legacy BET_NO excluded): 33.3% BHN vs
-- 66.7% naive market-threshold benchmark, PnL +$64,147.15 — still losing
-- record but genuinely positive PnL from real Kelly-varying stakes
-- (plausible longshot-mispricing pattern, not confirmed as a bug).
WITH weather_model_accuracy AS (
    SELECT
        contract_ticker                                        AS contract_id,
        contract_ticker                                        AS contract_title,
        city                                                   AS region,
        contract_side                                          AS variable,
        calibrated_prob                                        AS bhn_predicted_probability,
        market_implied_prob                                    AS market_implied_probability,
        edge,
        (recommended_action IN ('BET_YES', 'BET_NO'))          AS bhn_position_taken,
        stake_usd                                              AS bhn_position_value,
        CASE
            WHEN recommended_action = 'BET_YES' THEN 'yes'
            WHEN recommended_action = 'BET_NO'  THEN 'no'
        END                                                    AS bhn_position_side,
        contract_resolved_yes                                  AS actual_outcome,
        bhn_correct                                            AS bhn_was_correct,
        (market_implied_prob >= 0.5) = contract_resolved_yes   AS market_was_correct,
        paper_pnl                                              AS pnl_dollar,
        outcome_edge_realized                                  AS accuracy_score,
        settled_at                                             AS resolved_at,
        signal_generated_at                                    AS created_at
    FROM weather_gold_contract_ledger
)
SELECT
    -- Volume
    COUNT(*)                                           AS total_recommendations,
    COUNT(*) FILTER (WHERE bhn_position_taken = true) AS trades_placed,
    COUNT(*) FILTER (WHERE bhn_position_taken = false) AS signals_skipped,

    -- BHN accuracy
    COUNT(*) FILTER (WHERE bhn_was_correct = true)    AS bhn_correct,
    COUNT(*) FILTER (WHERE bhn_was_correct = false)   AS bhn_wrong,
    ROUND(
        COUNT(*) FILTER (WHERE bhn_was_correct = true)::numeric
        / NULLIF(COUNT(*), 0) * 100, 1
    )                                                  AS bhn_win_rate_pct,

    -- Market accuracy
    COUNT(*) FILTER (WHERE market_was_correct = true)  AS market_correct,
    ROUND(
        COUNT(*) FILTER (WHERE market_was_correct = true)::numeric
        / NULLIF(COUNT(*), 0) * 100, 1
    )                                                  AS market_win_rate_pct,

    -- BHN vs Market edge
    ROUND(
        (
            COUNT(*) FILTER (WHERE bhn_was_correct = true)::numeric
            / NULLIF(COUNT(*), 0)
            -
            COUNT(*) FILTER (WHERE market_was_correct = true)::numeric
            / NULLIF(COUNT(*), 0)
        ) * 100, 1
    )                                                  AS bhn_vs_market_edge_pct,

    -- P&L
    ROUND(SUM(pnl_dollar), 2)                          AS total_pnl,
    ROUND(AVG(pnl_dollar), 2)                          AS avg_pnl_per_trade,
    ROUND(SUM(pnl_dollar) FILTER (WHERE pnl_dollar > 0), 2) AS total_wins,
    ROUND(SUM(pnl_dollar) FILTER (WHERE pnl_dollar < 0), 2) AS total_losses,

    -- Edge stats
    ROUND(AVG(edge * 100), 1)                          AS avg_edge_pct,
    ROUND(AVG(accuracy_score), 3)                      AS avg_accuracy_score,

    -- Date range
    MIN(resolved_at) AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'
                                                       AS first_reconciled_time_pt,
    MAX(resolved_at) AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'
                                                       AS latest_reconciled_time_pt,
    COUNT(DISTINCT DATE(resolved_at))                  AS trading_days,

    -- Transparency count for the card label — see FIXED note above
    42                                                 AS legacy_rows_excluded

FROM weather_model_accuracy
WHERE actual_outcome IS NOT NULL
  AND NOT (bhn_position_side = 'no' AND bhn_position_value = 125.0);
