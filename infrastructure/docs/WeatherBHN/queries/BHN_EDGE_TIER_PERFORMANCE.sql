-- WeatherBHN - Signal Performance by Edge Tier
-- Breaks down BHN win rate by edge tier. Proves whether the edge model
-- actually predicts outcomes — strong edge signals (>20%) should win 70%+.
-- If strong edge isn't winning at 70%+, something is wrong with calibration.
-- Source: WeatherBHN_Performance_Queries.txt (June 12, 2026) — Query 20
-- Tab: FORMULA/MODELS

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
    COUNT(*) FILTER (WHERE bhn_was_correct = true)    AS bhn_correct,
    COUNT(*) FILTER (WHERE market_was_correct = true)  AS market_correct,

    -- Win rates
    ROUND(
        COUNT(*) FILTER (WHERE bhn_was_correct = true)::numeric
        / NULLIF(COUNT(*), 0) * 100, 1
    )                                                  AS bhn_win_rate_pct,
    ROUND(
        COUNT(*) FILTER (WHERE market_was_correct = true)::numeric
        / NULLIF(COUNT(*), 0) * 100, 1
    )                                                  AS market_win_rate_pct,

    -- BHN advantage per tier
    ROUND(
        (
            COUNT(*) FILTER (WHERE bhn_was_correct = true)::numeric
            / NULLIF(COUNT(*), 0)
            -
            COUNT(*) FILTER (WHERE market_was_correct = true)::numeric
            / NULLIF(COUNT(*), 0)
        ) * 100, 1
    )                                                  AS bhn_advantage_pct,

    -- P&L per tier
    ROUND(SUM(pnl_dollar), 2)                          AS total_pnl,
    ROUND(AVG(pnl_dollar), 2)                          AS avg_pnl,
    ROUND(AVG(edge * 100), 1)                          AS avg_edge_pct,

    -- Verdict
    CASE
        WHEN COUNT(*) FILTER (WHERE bhn_was_correct = true)::numeric
             / NULLIF(COUNT(*), 0) >= 0.70 THEN '✅ Model Working'
        WHEN COUNT(*) FILTER (WHERE bhn_was_correct = true)::numeric
             / NULLIF(COUNT(*), 0) >= 0.55 THEN '🟡 Model Marginal'
        WHEN COUNT(*) < 10                 THEN '⏳ Insufficient Data'
        ELSE                                    '🔴 Model Underperforming'
    END                                                AS model_verdict

FROM weather_model_accuracy
WHERE actual_outcome IS NOT NULL
GROUP BY edge_tier
ORDER BY
    CASE edge_tier
        WHEN '🔥 Strong Edge >20%'       THEN 1
        WHEN '🟢 Good Edge 10-20%'       THEN 2
        WHEN '🟡 Marginal Edge 5-10%'    THEN 3
        WHEN '⚪ No Edge 0-5%'           THEN 4
        WHEN '🔴 Negative Edge -10-0%'   THEN 5
        ELSE                                  6
    END;
