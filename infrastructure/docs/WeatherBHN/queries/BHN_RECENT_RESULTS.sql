-- WeatherBHN - Recent Recommendations + Results
-- Every BHN signal with contract ticker, outcome, and P&L.
-- Shows whether BHN was right or wrong on each trade.
-- Raw trade log — use to spot patterns and debug model errors.
-- Source: WeatherBHN_Performance_Queries.txt (June 12, 2026) — Query 21
-- (that file removed 2026-07-15 as a stale, unreferenced duplicate)
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
--
-- REBUILT 2026-07-15: same compat-CTE rebuild as BHN_OVERALL_SCORECARD.sql
-- — weather_gold_contract_ledger's settlement columns are retired/frozen
-- since 2026-07-07 (no new value since 2026-06-30), so this trade log was
-- silently frozen too. CTE now FULL OUTER JOINs weather_gold_contract_
-- ledger_performance against the live, corrected weather_position_exits;
-- bhn_position_taken is keyed off presence in weather_position_exits, not
-- the ledger's continuously re-evaluated recommended_action (confirmed 67
-- of 98 real positions now show SKIP in the live ledger snapshot after
-- market conditions moved post-entry). FULL OUTER JOIN (not LEFT) also
-- picks up 9 real settled 2026-07-01 trades with no ledger row at all —
-- important for a raw trade log specifically, since a LEFT JOIN would have
-- silently dropped exactly the rows this query exists to show. Full
-- rationale and the accuracy_score re-derivation in
-- BHN_OVERALL_SCORECARD.sql — identical CTE, not repeated here.

WITH weather_model_accuracy AS (
    SELECT
        COALESCE(g.contract_ticker, pe.contract_ticker)        AS contract_id,
        COALESCE(g.contract_ticker, pe.contract_ticker)        AS contract_title,
        COALESCE(g.city, CASE pe.station_code
            WHEN 'KDEN' THEN 'Denver' WHEN 'KLAX' THEN 'Los Angeles' WHEN 'KMIA' THEN 'Miami'
            ELSE pe.station_code END)                          AS region,
        COALESCE(g.contract_side, 'high')                      AS variable,
        COALESCE(g.calibrated_prob, pe.model_prob_no_cents / 100.0)
                                                                AS bhn_predicted_probability,
        COALESCE(g.market_implied_prob, COALESCE(pe.entry_no_ask_cents, pe.no_ask_cents) / 100.0)
                                                                AS market_implied_probability,
        COALESCE(g.edge, pe.edge_cents / 100.0)                AS edge,
        (pe.contract_ticker IS NOT NULL)                       AS bhn_position_taken,
        COALESCE(g.stake_usd, pe.corrected_stake_usd_recommended, pe.stake_usd_recommended)
                                                                AS bhn_position_value,
        CASE WHEN pe.contract_ticker IS NOT NULL THEN 'no' END AS bhn_position_side,
        COALESCE(pe.corrected_actual_outcome, pe.actual_outcome)
                                                                AS actual_outcome,
        (COALESCE(pe.corrected_actual_outcome, pe.actual_outcome) = 'NO_WIN')
                                                                AS bhn_was_correct,
        (COALESCE(g.market_implied_prob, COALESCE(pe.entry_no_ask_cents, pe.no_ask_cents) / 100.0) >= 0.5)
            = (COALESCE(pe.corrected_actual_outcome, pe.actual_outcome) = 'NO_WIN')
                                                                AS market_was_correct,
        COALESCE(pe.corrected_realized_pnl_usd, pe.realized_pnl_usd)
                                                                AS pnl_dollar,
        CASE
            WHEN pe.contract_ticker IS NULL
              OR COALESCE(pe.corrected_actual_outcome, pe.actual_outcome) IS NULL
              OR COALESCE(g.market_implied_prob, COALESCE(pe.entry_no_ask_cents, pe.no_ask_cents) / 100.0) IS NULL
                THEN NULL
            ELSE
                (CASE WHEN COALESCE(pe.corrected_actual_outcome, pe.actual_outcome) = 'NO_WIN' THEN 1 ELSE 0 END)
                - COALESCE(g.market_implied_prob, COALESCE(pe.entry_no_ask_cents, pe.no_ask_cents) / 100.0)
        END                                                    AS accuracy_score,
        pe.scored_at                                           AS resolved_at,
        COALESCE(g.signal_generated_at, pe.decision_timestamp) AS created_at
    FROM weather_gold_contract_ledger_performance g
    FULL OUTER JOIN weather_position_exits pe ON pe.contract_ticker = g.contract_ticker
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
