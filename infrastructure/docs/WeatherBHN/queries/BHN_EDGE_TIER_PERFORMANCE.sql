-- WeatherBHN - Signal Performance by Edge Tier
-- Breaks down BHN win rate by edge tier. Proves whether the edge model
-- actually predicts outcomes — strong edge signals (>20%) should win 70%+.
-- If strong edge isn't winning at 70%+, something is wrong with calibration.
-- Source: WeatherBHN_Performance_Queries.txt (June 12, 2026) — Query 20
-- (that file removed 2026-07-15 as a stale, unreferenced duplicate)
-- Tab: FORMULA/MODELS
--
-- UPDATED 2026-07-02: weather_model_accuracy -> weather_gold_contract_ledger
-- (same compat CTE as BHN_OVERALL_SCORECARD.sql). ALSO fixes a pre-existing
-- bug (present even against the old table): `ORDER BY CASE edge_tier WHEN
-- ...` referenced a SELECT-list alias inside a simple-CASE input position,
-- which Postgres does not resolve — moved the sort rank into a subquery
-- computed directly off the real `edge` column instead.
--
-- FIXED 2026-07-02: the "Strong Edge >20%" tier's apparently-legitimate
-- 73.9% win rate was 42 of the same flat-stake ($125, zero variance) legacy
-- BET_NO rows described in the Scorecard's note (91% of that tier), not
-- evidence the strategy thesis holds. The tiers that looked concerning
-- (Good 10-20%, Marginal 5-10%) were entirely clean Kelly-sized BET_YES
-- rows — the opposite of contaminated.
--
-- PERMANENT FIX 2026-07-02: same structural fix as the Scorecard — reads
-- from weather_gold_contract_ledger_performance (legacy rows already
-- excluded via is_legacy_row, see
-- sql/migrations/2026-07-02-ledger-exclude-legacy-rows.sql) instead of an
-- ad-hoc WHERE filter. Do NOT use this card's Strong Edge tier to justify
-- tightening the edge threshold without separately re-validating the
-- current-pipeline-only pattern holds up over time.
--
-- FIXED 2026-07-02: win-rate denominator was COUNT(*) over ALL rows
-- including SKIP (bhn_correct always NULL for skips) — this is exactly
-- the number used to judge whether a tier justifies tightening the
-- trading threshold, so the dilution wasn't cosmetic. Denominator is now
-- COUNT(*) FILTER (WHERE bhn_position_taken = true) throughout.
--
-- FIXED 2026-07-15: the inner subquery's `WHERE actual_outcome IS NOT NULL`
-- (inherited unchanged since 2026-07-02) gated the whole tier breakdown to
-- settled rows only before GROUP BY ran, structurally zeroing out any
-- skip-inclusive volume metric. Removed — see BHN_OVERALL_SCORECARD.sql's
-- note for the full explanation, same underlying bug, same fix.
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
    -- Edge tier classification
    CASE
        WHEN edge >= 0.20  THEN '🔥 Strong Edge >20%'
        WHEN edge >= 0.10  THEN '🟢 Good Edge 10-20%'
        WHEN edge >= 0.05  THEN '🟡 Marginal Edge 5-10%'
        WHEN edge >= 0.00  THEN '⚪ No Edge 0-5%'
        WHEN edge >= -0.10 THEN '🔴 Negative Edge -10-0%'
        ELSE                    '🔴 Strong Negative <-10%'
    END                                                AS edge_tier,

    COUNT(*)                                           AS total_signals,
    COUNT(*) FILTER (WHERE bhn_position_taken = true) AS trades_placed,
    COUNT(*) FILTER (WHERE bhn_was_correct = true)    AS bhn_correct,
    COUNT(*) FILTER (WHERE market_was_correct = true)  AS market_correct,

    -- Win rates
    ROUND(
        COUNT(*) FILTER (WHERE bhn_was_correct = true)::numeric
        / NULLIF(COUNT(*) FILTER (WHERE bhn_position_taken = true), 0) * 100, 1
    )                                                  AS bhn_win_rate_pct,
    ROUND(
        COUNT(*) FILTER (WHERE market_was_correct = true)::numeric
        / NULLIF(COUNT(*) FILTER (WHERE bhn_position_taken = true), 0) * 100, 1
    )                                                  AS market_win_rate_pct,

    -- BHN advantage per tier
    ROUND(
        (
            COUNT(*) FILTER (WHERE bhn_was_correct = true)::numeric
            / NULLIF(COUNT(*) FILTER (WHERE bhn_position_taken = true), 0)
            -
            COUNT(*) FILTER (WHERE market_was_correct = true)::numeric
            / NULLIF(COUNT(*) FILTER (WHERE bhn_position_taken = true), 0)
        ) * 100, 1
    )                                                  AS bhn_advantage_pct,

    -- P&L per tier
    ROUND(SUM(pnl_dollar), 2)                          AS total_pnl,
    ROUND(AVG(pnl_dollar), 2)                          AS avg_pnl,
    ROUND(AVG(edge * 100), 1)                          AS avg_edge_pct,

    -- Verdict
    CASE
        WHEN COUNT(*) FILTER (WHERE bhn_was_correct = true)::numeric
             / NULLIF(COUNT(*) FILTER (WHERE bhn_position_taken = true), 0) >= 0.70 THEN '✅ Model Working'
        WHEN COUNT(*) FILTER (WHERE bhn_was_correct = true)::numeric
             / NULLIF(COUNT(*) FILTER (WHERE bhn_position_taken = true), 0) >= 0.55 THEN '🟡 Model Marginal'
        WHEN COUNT(*) FILTER (WHERE bhn_position_taken = true) < 10 THEN '⏳ Insufficient Data'
        ELSE                                    '🔴 Model Underperforming'
    END                                                AS model_verdict

FROM (
    SELECT *,
        CASE
            WHEN edge >= 0.20  THEN 1
            WHEN edge >= 0.10  THEN 2
            WHEN edge >= 0.05  THEN 3
            WHEN edge >= 0.00  THEN 4
            WHEN edge >= -0.10 THEN 5
            ELSE 6
        END AS edge_tier_sort
    FROM weather_model_accuracy
) tiered
GROUP BY edge_tier, edge_tier_sort
ORDER BY edge_tier_sort;
