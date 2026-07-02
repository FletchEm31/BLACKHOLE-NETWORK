-- WeatherBHN - Performance by City
-- BHN win rate, average edge, and P&L broken down by city.
-- Shows which cities BHN has the most edge in.
-- High win rate + high avg edge = strong calibration.
-- Low win rate = needs more data or model adjustment.
-- Source: WeatherBHN_Performance_Queries.txt (June 12, 2026) — Query 22
-- Tab: FORMULA/MODELS
--
-- UPDATED 2026-07-02: weather_model_accuracy -> weather_gold_contract_ledger
-- (same compat CTE as BHN_OVERALL_SCORECARD.sql). This standalone file had
-- drifted out of sync with CLEAN_QUERIES.sql (was still querying the
-- retired weather_model_accuracy table directly) — re-synced.
--
-- PERMANENT FIX 2026-07-02: reads from
-- weather_gold_contract_ledger_performance — legacy pre-CP4-pipeline rows
-- permanently excluded via is_legacy_row, see
-- sql/migrations/2026-07-02-ledger-exclude-legacy-rows.sql and
-- BHN_OVERALL_SCORECARD.sql's note for full context.

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
    region                                             AS city,
    variable,

    -- Volume
    COUNT(*)                                           AS total_signals,
    COUNT(*) FILTER (WHERE bhn_position_taken = true) AS trades_placed,

    -- Accuracy
    COUNT(*) FILTER (WHERE bhn_was_correct = true)    AS bhn_correct,
    ROUND(
        COUNT(*) FILTER (WHERE bhn_was_correct = true)::numeric
        / NULLIF(COUNT(*), 0) * 100, 1
    )                                                  AS bhn_win_rate_pct,
    ROUND(
        COUNT(*) FILTER (WHERE market_was_correct = true)::numeric
        / NULLIF(COUNT(*), 0) * 100, 1
    )                                                  AS market_win_rate_pct,

    -- BHN advantage per city
    ROUND(
        (
            COUNT(*) FILTER (WHERE bhn_was_correct = true)::numeric
            / NULLIF(COUNT(*), 0)
            -
            COUNT(*) FILTER (WHERE market_was_correct = true)::numeric
            / NULLIF(COUNT(*), 0)
        ) * 100, 1
    )                                                  AS bhn_advantage_pct,

    -- Edge stats
    ROUND(AVG(edge * 100), 1)                          AS avg_edge_pct,
    ROUND(MAX(edge * 100), 1)                          AS max_edge_pct,

    -- P&L
    ROUND(SUM(pnl_dollar), 2)                          AS total_pnl,
    ROUND(AVG(pnl_dollar), 2)                          AS avg_pnl_per_signal,

    -- City verdict
    CASE
        WHEN COUNT(*) < 5 THEN '⏳ Insufficient Data'
        WHEN COUNT(*) FILTER (WHERE bhn_was_correct = true)::numeric
             / NULLIF(COUNT(*), 0) >= 0.65 THEN '✅ Strong Edge City'
        WHEN COUNT(*) FILTER (WHERE bhn_was_correct = true)::numeric
             / NULLIF(COUNT(*), 0) >= 0.50 THEN '🟡 Developing Edge'
        ELSE '🔴 Weak Edge — Review Calibration'
    END                                                AS city_verdict,

    -- Date range
    MIN(resolved_at) AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'
                                                       AS first_signal_pt,
    MAX(resolved_at) AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'
                                                       AS latest_signal_pt

FROM weather_model_accuracy
WHERE actual_outcome IS NOT NULL
GROUP BY region, variable
ORDER BY bhn_win_rate_pct DESC NULLS LAST, total_signals DESC;
