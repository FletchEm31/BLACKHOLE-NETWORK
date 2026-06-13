-- WeatherBHN - Performance by City
-- BHN win rate, average edge, and P&L broken down by city.
-- Shows which cities BHN has the most edge in.
-- High win rate + high avg edge = strong calibration.
-- Low win rate = needs more data or model adjustment.
-- Source: WeatherBHN_Performance_Queries.txt (June 12, 2026) — Query 22
-- Tab: FORMULA/MODELS

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
