-- CLEAN_QUERIES.sql
-- BHN Weather Trading — BSG Schema Metabase Queries
-- Validated against live DB 2026-06-11
-- All 7 queries use the Bronze/Silver/Gold schema only (no legacy tables).
-- ============================================================

-- ============================================================
-- QUERY 1: Daily Edge Sheet (MAIN TRADING VIEW)
-- Shows what to trade today and tomorrow.
-- ============================================================
SELECT
    city,
    target_date,
    contract_side,
    bucket_label,
    contract_ticker,
    raw_forecast_f                              AS nws_forecast,
    gfs_forecast_f                              AS gfs_forecast,
    model_delta_f                               AS model_delta,
    model_confidence,
    ROUND(calibrated_prob * 100, 1)             AS model_prob_pct,
    ROUND(market_implied_prob * 100, 1)         AS market_prob_pct,
    ROUND(edge_pct, 1)                          AS edge_pct,
    market_volume,
    market_liquidity,
    recommended_action,
    stake_usd,
    skip_reason,
    last_updated
FROM weather_gold_daily_edge_sheet
WHERE target_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 1
  AND is_active = TRUE
ORDER BY target_date, edge DESC;


-- ============================================================
-- QUERY 2: Latest NWS vs GFS Forecasts
-- ============================================================
SELECT
    city,
    station_code,
    target_date,
    source_name,
    tmax_f,
    tmin_f,
    cloud_cover_pct,
    pop_pct,
    wind_speed_mph,
    is_latest_run,
    forecast_run_time
FROM weather_silver_forecast_conformed
WHERE target_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 2
  AND is_latest_run = TRUE
ORDER BY target_date, station_code, source_name;


-- ============================================================
-- QUERY 3: Live Kalshi Market Prices
-- ============================================================
SELECT
    city,
    station_code,
    contract_side,
    bucket_label,
    market_ticker,
    target_date,
    ROUND(implied_prob * 100, 1)    AS implied_prob_pct,
    yes_bid,
    yes_ask,
    yes_mid,
    volume,
    market_liquidity_flag,
    snapshot_time
FROM weather_silver_market_conformed
WHERE is_latest_snapshot = TRUE
  AND target_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 1
ORDER BY target_date, station_code, contract_side, bucket_floor;


-- ============================================================
-- QUERY 4: Forecast Accuracy — NWS Error by City
-- ============================================================
SELECT
    city,
    station_code,
    feature_name,
    source_name,
    COUNT(*)                                        AS sample_size,
    ROUND(AVG(forecast_error_f), 2)                 AS avg_bias_f,
    ROUND(AVG(ABS(forecast_error_f)), 2)            AS mae_f,
    ROUND(STDDEV(forecast_error_f), 2)              AS std_f,
    SUM(CASE WHEN error_sign = 'hot'  THEN 1 ELSE 0 END) AS times_ran_hot,
    SUM(CASE WHEN error_sign = 'cold' THEN 1 ELSE 0 END) AS times_ran_cold
FROM weather_silver_forecast_error
WHERE target_date > CURRENT_DATE - 30
GROUP BY city, station_code, feature_name, source_name
ORDER BY station_code, feature_name, source_name;


-- ============================================================
-- QUERY 5: Calibration Progress (30-day window tracker)
-- ============================================================
SELECT
    sfc.station_code,
    sfc.city,
    COUNT(DISTINCT sfc.target_date)                     AS forecast_days,
    COUNT(DISTINCT sa.target_date)                      AS settled_days,
    COUNT(DISTINCT sfe.target_date)                     AS error_pairs,
    CASE
        WHEN COUNT(DISTINCT sfe.target_date) >= 30
        THEN 'READY TO CALIBRATE'
        ELSE CONCAT(
            CAST(30 - COUNT(DISTINCT sfe.target_date) AS TEXT),
            ' days remaining'
        )
    END                                                  AS calibration_status,
    MIN(sfc.target_date)                                 AS earliest_forecast,
    MAX(sa.target_date)                                  AS latest_settlement
FROM weather_silver_forecast_conformed sfc
LEFT JOIN weather_silver_actuals_conformed sa
    ON  sa.station_code = sfc.station_code
    AND sa.target_date  = sfc.target_date
LEFT JOIN weather_silver_forecast_error sfe
    ON  sfe.station_code = sfc.station_code
    AND sfe.target_date  = sfc.target_date
WHERE sfc.is_latest_run = TRUE
  AND sfc.source_name   = 'nws'
GROUP BY sfc.station_code, sfc.city
ORDER BY sfc.station_code;


-- ============================================================
-- QUERY 6: Data Freshness by Source + Station
-- ============================================================
SELECT
    source_label,
    station_code,
    MAX(last_update)                                                  AS last_update,
    ROUND(EXTRACT(EPOCH FROM (NOW() - MAX(last_update))) / 60, 0)    AS minutes_ago
FROM (
    SELECT 'NWS Forecast'  AS source_label,
           station_code,
           MAX(retrieved_at) AS last_update
    FROM weather_bronze_nws_forecast_snapshots
    GROUP BY station_code

    UNION ALL
    SELECT 'Open-Meteo GFS',
           station_code,
           MAX(retrieved_at)
    FROM weather_bronze_openmeteo_forecast_snapshots
    WHERE model = 'gfs_seamless'
    GROUP BY station_code

    UNION ALL
    SELECT 'Kalshi Prices',
           station_code,
           MAX(retrieved_at)
    FROM weather_bronze_kalshi_market_snapshots
    GROUP BY station_code

    UNION ALL
    SELECT 'NWS Actuals',
           station_code,
           MAX(retrieved_at)
    FROM weather_bronze_nws_actuals
    GROUP BY station_code
) t
GROUP BY source_label, station_code
ORDER BY source_label, station_code;


-- ============================================================
-- QUERY 7: Kalshi P&L and Active Positions
-- ============================================================
SELECT
    contract_ticker,
    contract_title,
    side,
    contracts                                                       AS num_contracts,
    ROUND(avg_price::numeric, 4)                                    AS avg_price,
    cost_usd,
    market_value_usd,
    unrealized_pnl_usd,
    payout_if_right_usd,
    ROUND((unrealized_pnl_usd / NULLIF(cost_usd, 0)) * 100, 1)    AS return_pct,
    captured_at
FROM kalshi_positions
ORDER BY captured_at DESC;
