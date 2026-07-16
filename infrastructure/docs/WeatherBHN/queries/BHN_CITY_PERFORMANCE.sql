-- WeatherBHN - Performance by City
-- BHN win rate, average edge, and P&L broken down by city.
-- Shows which cities BHN has the most edge in.
-- High win rate + high avg edge = strong calibration.
-- Low win rate = needs more data or model adjustment.
-- Source: WeatherBHN_Performance_Queries.txt (June 12, 2026) — Query 22
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
-- permanently excluded via is_legacy_row, see
-- sql/migrations/2026-07-02-ledger-exclude-legacy-rows.sql and
-- BHN_OVERALL_SCORECARD.sql's note for full context.
--
-- FIXED 2026-07-02: win-rate denominator was COUNT(*) over ALL rows
-- including SKIP (bhn_correct always NULL for skips) — now COUNT(*)
-- FILTER (WHERE bhn_position_taken = true) throughout, the true
-- trade-only win rate per city.
--
-- REBUILT 2026-07-15: same compat-CTE rebuild as BHN_OVERALL_SCORECARD.sql
-- — weather_gold_contract_ledger's settlement columns are retired/frozen
-- since 2026-07-07 (no new value since 2026-06-30). CTE now FULL OUTER
-- JOINs weather_gold_contract_ledger_performance against the live,
-- corrected weather_position_exits; bhn_position_taken is keyed off
-- presence in weather_position_exits, not the ledger's continuously
-- re-evaluated recommended_action (confirmed 67 of 98 real positions now
-- show SKIP in the live ledger snapshot after market conditions moved
-- post-entry). FULL OUTER JOIN (not LEFT) also picks up 9 real settled
-- 2026-07-01 trades with no ledger row at all. Full rationale and the
-- accuracy_score re-derivation in BHN_OVERALL_SCORECARD.sql — identical
-- CTE, not repeated here.
--
-- FIXED 2026-07-15 (2nd pass): region for the 9 orphan rows now maps
-- station_code -> city name (Denver/Los Angeles/Miami) instead of showing
-- the raw code -- was silently splitting each city into two rows (e.g.
-- "Denver" 23 signals + "KDEN" 4 signals as separate cities), defeating
-- the point of a by-city rollup. Also removed the trailing `WHERE
-- actual_outcome IS NOT NULL`, inherited unchanged since 2026-07-02 --
-- gated the whole per-city GROUP BY to settled rows only before it ran,
-- structurally zeroing out total_signals' skip-inclusive volume. Same
-- underlying bug as BHN_OVERALL_SCORECARD.sql, same fix.

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
    region                                             AS city,
    variable,

    -- Volume
    COUNT(*)                                           AS total_signals,
    COUNT(*) FILTER (WHERE bhn_position_taken = true) AS trades_placed,

    -- Accuracy
    COUNT(*) FILTER (WHERE bhn_was_correct = true)    AS bhn_correct,
    ROUND(
        COUNT(*) FILTER (WHERE bhn_was_correct = true)::numeric
        / NULLIF(COUNT(*) FILTER (WHERE bhn_position_taken = true), 0) * 100, 1
    )                                                  AS bhn_win_rate_pct,
    ROUND(
        COUNT(*) FILTER (WHERE market_was_correct = true)::numeric
        / NULLIF(COUNT(*) FILTER (WHERE bhn_position_taken = true), 0) * 100, 1
    )                                                  AS market_win_rate_pct,

    -- BHN advantage per city
    ROUND(
        (
            COUNT(*) FILTER (WHERE bhn_was_correct = true)::numeric
            / NULLIF(COUNT(*) FILTER (WHERE bhn_position_taken = true), 0)
            -
            COUNT(*) FILTER (WHERE market_was_correct = true)::numeric
            / NULLIF(COUNT(*) FILTER (WHERE bhn_position_taken = true), 0)
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
        WHEN COUNT(*) FILTER (WHERE bhn_position_taken = true) < 5 THEN '⏳ Insufficient Data'
        WHEN COUNT(*) FILTER (WHERE bhn_was_correct = true)::numeric
             / NULLIF(COUNT(*) FILTER (WHERE bhn_position_taken = true), 0) >= 0.65 THEN '✅ Strong Edge City'
        WHEN COUNT(*) FILTER (WHERE bhn_was_correct = true)::numeric
             / NULLIF(COUNT(*) FILTER (WHERE bhn_position_taken = true), 0) >= 0.50 THEN '🟡 Developing Edge'
        ELSE '🔴 Weak Edge — Review Calibration'
    END                                                AS city_verdict,

    -- Date range
    MIN(resolved_at) AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'
                                                       AS first_signal_pt,
    MAX(resolved_at) AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'
                                                       AS latest_signal_pt

FROM weather_model_accuracy
GROUP BY region, variable
ORDER BY bhn_win_rate_pct DESC NULLS LAST, total_signals DESC;
