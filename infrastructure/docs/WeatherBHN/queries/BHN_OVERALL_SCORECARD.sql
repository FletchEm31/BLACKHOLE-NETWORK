-- WeatherBHN - BHN Overall Scorecard
-- High level BHN signal performance summary. Shows total recommendations,
-- win rate, P&L, and how BHN compares to simply following the market.
-- Headline number: is BHN actually adding value over the market?
-- Populates after settlement reconciler runs nightly at 15:00 UTC.
-- Source: WeatherBHN_Performance_Queries.txt (June 12, 2026) — Query 19
-- Tab: FORMULA/MODELS — PIN at top

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
    COUNT(DISTINCT DATE(resolved_at))                  AS trading_days

FROM weather_model_accuracy
WHERE actual_outcome IS NOT NULL;
