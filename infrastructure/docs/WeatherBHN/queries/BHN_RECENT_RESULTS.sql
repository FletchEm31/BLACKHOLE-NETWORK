-- WeatherBHN - Recent Recommendations + Results
-- Every BHN signal with contract ticker, outcome, and P&L.
-- Shows whether BHN was right or wrong on each trade.
-- Raw trade log — use to spot patterns and debug model errors.
-- Source: WeatherBHN_Performance_Queries.txt (June 12, 2026) — Query 21
-- Tab: FORMULA/MODELS
--
-- UPDATED 2026-07-02: weather_model_accuracy -> weather_gold_contract_ledger
-- (same compat CTE as BHN_OVERALL_SCORECARD.sql). This standalone file had
-- drifted out of sync with CLEAN_QUERIES.sql (was still querying the
-- retired weather_model_accuracy table directly) — re-synced.
--
-- PERMANENT FIX 2026-07-02: reads from
-- weather_gold_contract_ledger_performance — legacy pre-CP4-pipeline rows
-- (BET_YES rows; flat-$125-stake BET_NO rows) permanently excluded via
-- is_legacy_row, see sql/migrations/2026-07-02-ledger-exclude-legacy-rows.sql
-- and BHN_OVERALL_SCORECARD.sql's note for full context. This is a raw
-- trade log, so showing legacy rows here would be actively misleading for
-- "spot patterns and debug model errors" against the current pipeline.

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
    FROM weather_gold_contract_ledger_performance
)
SELECT
    -- Contract identification
    contract_id                                        AS contract_ticker,
    contract_title,
    region                                             AS city,
    variable,

    -- Signal details
    ROUND(bhn_predicted_probability * 100, 1)          AS bhn_prob_pct,
    ROUND(market_implied_probability * 100, 1)         AS market_prob_pct,
    ROUND(edge * 100, 1)                               AS edge_pct,

    -- Edge classification
    CASE
        WHEN edge >= 0.20  THEN '🔥 Strong'
        WHEN edge >= 0.10  THEN '🟢 Good'
        WHEN edge >= 0.05  THEN '🟡 Marginal'
        WHEN edge >= 0.00  THEN '⚪ None'
        ELSE                    '🔴 Negative'
    END                                                AS edge_tier,

    -- Position taken
    bhn_position_taken,
    bhn_position_side,
    ROUND(bhn_position_value, 2)                       AS position_value,

    -- Outcome
    actual_outcome,
    CASE
        WHEN bhn_was_correct = true  THEN '✅ CORRECT'
        WHEN bhn_was_correct = false THEN '❌ WRONG'
        ELSE '⏳ PENDING'
    END                                                AS bhn_result,
    CASE
        WHEN market_was_correct = true  THEN '✅ CORRECT'
        WHEN market_was_correct = false THEN '❌ WRONG'
        ELSE '⏳ PENDING'
    END                                                AS market_result,

    -- P&L
    ROUND(pnl_dollar, 2)                               AS pnl_dollar,
    ROUND(accuracy_score, 3)                           AS accuracy_score,

    -- Timestamps (timestamp standard: reconciled_time_utc/pt/mins_ago)
    resolved_at                                        AS reconciled_time_utc,
    resolved_at AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'
                                                       AS reconciled_time_pt,
    ROUND(EXTRACT(EPOCH FROM (NOW() - resolved_at)) / 60) AS mins_ago,

    created_at                                         AS signal_time_utc,
    created_at AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'
                                                       AS signal_time_pt

FROM weather_model_accuracy
WHERE actual_outcome IS NOT NULL
ORDER BY resolved_at DESC
LIMIT 100;
