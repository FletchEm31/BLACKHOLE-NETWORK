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
    ROUND(nws_high_prob_pct * 100, 1)           AS nws_prob_pct,
    ROUND(gfs_high_prob_pct * 100, 1)           AS gfs_ens_prob_pct,
    market_volume,
    market_liquidity,
    recommended_action,
    stake_usd,
    skip_reason,
    last_updated                                                           AS calculated_time_utc,
    last_updated AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'    AS calculated_time_pt,
    ROUND(EXTRACT(EPOCH FROM (NOW() - last_updated)) / 60)                AS mins_ago
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
    forecast_run_time                                                           AS forecast_time_utc,
    forecast_run_time AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'    AS forecast_time_pt,
    ROUND(EXTRACT(EPOCH FROM (NOW() - forecast_run_time)) / 60)                AS mins_ago
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
    snapshot_time                                                           AS snapshot_time_utc,
    snapshot_time AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'    AS snapshot_time_pt,
    ROUND(EXTRACT(EPOCH FROM (NOW() - snapshot_time)) / 60)                AS mins_ago
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
    MAX(last_update)                                                              AS retrieved_time_utc,
    MAX(last_update) AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'       AS retrieved_time_pt,
    ROUND(EXTRACT(EPOCH FROM (NOW() - MAX(last_update))) / 60)                   AS mins_ago
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
    captured_at                                                           AS snapshot_time_utc,
    captured_at AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'    AS snapshot_time_pt,
    ROUND(EXTRACT(EPOCH FROM (NOW() - captured_at)) / 60)                AS mins_ago
FROM kalshi_positions
ORDER BY captured_at DESC;


-- ============================================================
-- QUERY 16: WeatherBHN - Kelly Sizing (Market Only + Liquidity)
-- Pure Kelly sizing from market-implied prob. Works now.
-- trade_signal = go/no-go. Use during manual trading phase.
-- Full query: infrastructure/docs/WeatherBHN/queries/KELLY_MARKET_ONLY.sql
-- ============================================================
WITH market_latest AS (
    SELECT DISTINCT ON (market_ticker)
        market_ticker,
        city,
        contract_side,
        bucket_floor,
        bucket_cap,
        yes_bid,
        yes_ask,
        (yes_bid + yes_ask) / 2.0                    AS yes_mid,
        yes_ask - yes_bid                             AS spread,
        volume,
        open_interest,
        market_status,
        retrieved_at
    FROM weather_bronze_kalshi_market_snapshots
    WHERE market_status = 'active'
    ORDER BY market_ticker, retrieved_at DESC
),
edge_data AS (
    SELECT
        g.city, g.station_code, g.target_date, g.contract_side,
        g.bucket_floor, g.bucket_cap, g.market_implied_prob,
        g.market_yes_mid, g.calibrated_prob, g.edge,
        g.recommended_action, g.last_updated
    FROM weather_gold_daily_edge_sheet g
    WHERE g.target_date >= CURRENT_DATE AND g.is_active = true
)
SELECT
    e.city, e.station_code, e.target_date, e.contract_side,
    e.bucket_floor, e.bucket_cap,
    ROUND(m.yes_bid * 100, 1)              AS bid_cents,
    ROUND(m.yes_ask * 100, 1)              AS ask_cents,
    ROUND(m.yes_mid * 100, 1)              AS mid_cents,
    ROUND(e.market_implied_prob * 100, 1)  AS market_prob_pct,
    ROUND(m.spread * 100, 1)               AS spread_cents,
    ROUND(m.volume, 0)                     AS daily_volume_contracts,
    ROUND(m.volume * m.yes_mid, 2)         AS daily_volume_dollars,
    ROUND(m.open_interest, 0)              AS open_interest_contracts,
    ROUND((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 4) AS net_odds_b,
    ROUND(
        (e.market_implied_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
        - (1 - e.market_implied_prob))
        / NULLIF((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 0) * 0.5
    , 4) AS half_kelly_fraction,
    ROUND(LEAST(
        m.open_interest * m.yes_mid * 0.10,
        m.volume * m.yes_mid * 0.05
    ), 2) AS liquidity_cap_dollars,
    ROUND(LEAST(GREATEST(0,
        (e.market_implied_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
        - (1 - e.market_implied_prob))
        / NULLIF((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 0) * 0.5 * 14
    ), LEAST(m.open_interest * m.yes_mid * 0.10, m.volume * m.yes_mid * 0.05))
    * CASE WHEN m.spread > 0.20 THEN 0.00 WHEN m.spread > 0.10 THEN 0.50
           WHEN m.spread > 0.05 THEN 0.75 ELSE 1.00 END, 2) AS final_stake_14,
    ROUND(LEAST(GREATEST(0,
        (e.market_implied_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
        - (1 - e.market_implied_prob))
        / NULLIF((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 0) * 0.5 * 50
    ), LEAST(m.open_interest * m.yes_mid * 0.10, m.volume * m.yes_mid * 0.05))
    * CASE WHEN m.spread > 0.20 THEN 0.00 WHEN m.spread > 0.10 THEN 0.50
           WHEN m.spread > 0.05 THEN 0.75 ELSE 1.00 END, 2) AS final_stake_50,
    CASE
        WHEN m.spread > 0.20                THEN '🔴 NO TRADE — Spread too wide'
        WHEN m.open_interest < 10           THEN '🔴 NO TRADE — Illiquid'
        WHEN m.volume * m.yes_mid < 50      THEN '🔴 NO TRADE — No volume'
        WHEN (e.market_implied_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
            - (1 - e.market_implied_prob))
            / NULLIF((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 0) <= 0
                                            THEN '⛔ NO TRADE — Negative Kelly'
        WHEN m.spread > 0.10                THEN '🟡 REDUCED SIZE — Wide spread'
        WHEN m.volume * m.yes_mid < 200     THEN '🟡 REDUCED SIZE — Thin volume'
        WHEN m.open_interest < 50           THEN '🟡 REDUCED SIZE — Low OI'
        ELSE                                     '✅ TRADE — Full liquidity-adjusted size'
    END AS trade_signal,
    m.retrieved_at                                                           AS snapshot_time_utc,
    m.retrieved_at AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'    AS snapshot_time_pt,
    ROUND(EXTRACT(EPOCH FROM (NOW() - m.retrieved_at)) / 60)                AS mins_ago,
    e.last_updated                                                           AS calculated_time_utc,
    e.last_updated AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'    AS calculated_time_pt
FROM edge_data e
LEFT JOIN market_latest m
    ON e.city = m.city AND e.contract_side = m.contract_side
    AND e.bucket_floor = m.bucket_floor AND e.bucket_cap = m.bucket_cap
WHERE e.market_implied_prob IS NOT NULL AND m.yes_mid IS NOT NULL AND m.yes_mid > 0
ORDER BY
    CASE WHEN m.spread > 0.20 THEN 3 WHEN m.open_interest < 10 THEN 3
         WHEN m.volume * m.yes_mid < 50 THEN 3 ELSE 1 END,
    e.market_implied_prob DESC, e.target_date, e.city;


-- ============================================================
-- QUERY 17: WeatherBHN - Kelly Sizing (BHN Edge + Liquidity)
-- PRIMARY automated trading signal. kelly_signal = BET = place order.
-- Requires calibrated_prob — shows PENDING until VC backfill completes.
-- Full query: infrastructure/docs/WeatherBHN/queries/KELLY_BHN_EDGE.sql
-- ============================================================
WITH market_latest AS (
    SELECT DISTINCT ON (market_ticker)
        market_ticker, city, contract_side, bucket_floor, bucket_cap,
        yes_bid, yes_ask,
        (yes_bid + yes_ask) / 2.0 AS yes_mid,
        yes_ask - yes_bid         AS spread,
        volume, open_interest, market_status, retrieved_at
    FROM weather_bronze_kalshi_market_snapshots
    WHERE market_status = 'active'
    ORDER BY market_ticker, retrieved_at DESC
)
SELECT
    g.city, g.station_code, g.target_date, g.contract_side,
    g.bucket_floor, g.bucket_cap,
    ROUND(m.yes_bid * 100, 1)             AS bid_cents,
    ROUND(m.yes_ask * 100, 1)             AS ask_cents,
    ROUND(m.yes_mid * 100, 1)             AS mid_cents,
    ROUND(g.market_implied_prob * 100, 1) AS market_prob_pct,
    ROUND(g.calibrated_prob * 100, 1)     AS bhn_prob_pct,
    ROUND((g.calibrated_prob - g.market_implied_prob) * 100, 1) AS edge_pct,
    CASE
        WHEN (g.calibrated_prob - g.market_implied_prob) >= 0.20 THEN '🔥 STRONG EDGE >20%'
        WHEN (g.calibrated_prob - g.market_implied_prob) >= 0.10 THEN '🟢 GOOD EDGE 10-20%'
        WHEN (g.calibrated_prob - g.market_implied_prob) >= 0.05 THEN '🟡 MARGINAL EDGE 5-10%'
        WHEN (g.calibrated_prob - g.market_implied_prob) >= 0.00 THEN '⚪ NO EDGE'
        ELSE '🔴 NEGATIVE EDGE — Market smarter than BHN'
    END AS edge_classification,
    ROUND(m.spread * 100, 1)              AS spread_cents,
    ROUND(m.volume, 0)                    AS daily_volume_contracts,
    ROUND(m.open_interest, 0)             AS open_interest_contracts,
    ROUND((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 4) AS net_odds_b,
    ROUND(
        (g.calibrated_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
        - (1 - g.calibrated_prob))
        / NULLIF((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 0) * 0.5
    , 4) AS bhn_half_kelly,
    ROUND(
        (
            (g.calibrated_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
            - (1 - g.calibrated_prob))
            / NULLIF((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 0) * 0.5
        ) - (
            (g.market_implied_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
            - (1 - g.market_implied_prob))
            / NULLIF((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 0) * 0.5
        )
    , 4) AS kelly_edge_advantage,
    ROUND(LEAST(
        m.open_interest * m.yes_mid * 0.10,
        m.volume * m.yes_mid * 0.05
    ), 2) AS liquidity_cap_dollars,
    ROUND(LEAST(GREATEST(0,
        (g.calibrated_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
        - (1 - g.calibrated_prob))
        / NULLIF((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 0) * 0.5 * 14
    ), LEAST(m.open_interest * m.yes_mid * 0.10, m.volume * m.yes_mid * 0.05))
    * CASE WHEN m.spread > 0.20 THEN 0.00 WHEN m.spread > 0.10 THEN 0.50
           WHEN m.spread > 0.05 THEN 0.75 ELSE 1.00 END, 2) AS final_stake_14,
    ROUND(LEAST(GREATEST(0,
        (g.calibrated_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
        - (1 - g.calibrated_prob))
        / NULLIF((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 0) * 0.5 * 50
    ), LEAST(m.open_interest * m.yes_mid * 0.10, m.volume * m.yes_mid * 0.05))
    * CASE WHEN m.spread > 0.20 THEN 0.00 WHEN m.spread > 0.10 THEN 0.50
           WHEN m.spread > 0.05 THEN 0.75 ELSE 1.00 END, 2) AS final_stake_50,
    CASE
        WHEN m.spread > 0.20            THEN '🔴 NO TRADE — Spread too wide'
        WHEN m.open_interest < 10       THEN '🔴 NO TRADE — Illiquid'
        WHEN m.volume * m.yes_mid < 50  THEN '🔴 NO TRADE — No volume'
        WHEN g.calibrated_prob IS NULL  THEN '⏳ PENDING — No calibration data yet'
        WHEN (g.calibrated_prob - g.market_implied_prob) < 0  THEN '⛔ NO TRADE — Negative edge'
        WHEN (g.calibrated_prob - g.market_implied_prob) < 0.05 THEN '⚪ SKIP — Edge too small (<5%)'
        WHEN m.spread > 0.10            THEN '🟡 REDUCED — Wide spread, edge exists'
        WHEN m.volume * m.yes_mid < 200 THEN '🟡 REDUCED — Thin volume, edge exists'
        WHEN (g.calibrated_prob - g.market_implied_prob) >= 0.20 THEN '🔥 STRONG BET — Max liquidity-adjusted size'
        WHEN (g.calibrated_prob - g.market_implied_prob) >= 0.10 THEN '✅ BET — Full liquidity-adjusted size'
        ELSE '🟡 MARGINAL — Half liquidity-adjusted size'
    END AS kelly_signal,
    g.edge_rank, g.recommended_action,
    m.retrieved_at                                                           AS snapshot_time_utc,
    m.retrieved_at AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'    AS snapshot_time_pt,
    ROUND(EXTRACT(EPOCH FROM (NOW() - m.retrieved_at)) / 60)                AS mins_ago,
    g.last_updated                                                           AS calculated_time_utc,
    g.last_updated AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'    AS calculated_time_pt
FROM weather_gold_daily_edge_sheet g
LEFT JOIN market_latest m
    ON g.city = m.city AND g.contract_side = m.contract_side
    AND g.bucket_floor = m.bucket_floor AND g.bucket_cap = m.bucket_cap
WHERE g.target_date >= CURRENT_DATE AND g.is_active = true
  AND g.calibrated_prob IS NOT NULL AND m.yes_mid IS NOT NULL AND m.yes_mid > 0
ORDER BY
    CASE WHEN m.spread > 0.20 THEN 5 WHEN m.open_interest < 10 THEN 5
         WHEN g.calibrated_prob IS NULL THEN 4
         WHEN (g.calibrated_prob - g.market_implied_prob) < 0.05 THEN 3
         WHEN (g.calibrated_prob - g.market_implied_prob) >= 0.20 THEN 1
         ELSE 2 END,
    (g.calibrated_prob - g.market_implied_prob) DESC,
    g.target_date, g.city;


-- ============================================================
-- QUERY 18: WeatherBHN - Pre-Trade Liquidity Scanner
-- Run this FIRST before any trade. liquidity_score 0-100.
-- >70 = safe, 40-70 = careful, <40 = avoid.
-- Pin ABOVE the Edge Sheet on Metabase WeatherBHN tab.
-- Full query: infrastructure/docs/WeatherBHN/queries/LIQUIDITY_SCANNER.sql
-- ============================================================
WITH market_latest AS (
    SELECT DISTINCT ON (market_ticker)
        market_ticker, city, contract_side, bucket_floor, bucket_cap,
        yes_bid, yes_ask,
        (yes_bid + yes_ask) / 2.0 AS yes_mid,
        yes_ask - yes_bid         AS spread,
        volume, open_interest, market_status, retrieved_at
    FROM weather_bronze_kalshi_market_snapshots
    WHERE market_status = 'active'
    ORDER BY market_ticker, retrieved_at DESC
)
SELECT
    city, contract_side, bucket_floor, bucket_cap,
    ROUND(yes_bid * 100, 1)  AS bid_cents,
    ROUND(yes_ask * 100, 1)  AS ask_cents,
    ROUND(yes_mid * 100, 1)  AS mid_cents,
    ROUND(spread * 100, 1)   AS spread_cents,
    ROUND(volume, 0)                  AS volume_contracts,
    ROUND(volume * yes_mid, 2)        AS volume_dollars,
    ROUND(open_interest, 0)           AS open_interest_contracts,
    ROUND(open_interest * yes_mid, 2) AS open_interest_dollars,
    ROUND(LEAST(
        open_interest * yes_mid * 0.10,
        volume * yes_mid * 0.05
    ), 2) AS max_safe_position_dollars,
    ROUND(LEAST(open_interest * 0.10, volume * 0.05), 0) AS max_safe_contracts,
    CASE WHEN spread > 0.20 THEN '🔴 SKIP'
         WHEN spread > 0.10 THEN '🟡 WIDE'
         WHEN spread > 0.05 THEN '🟡 OK'
         ELSE '🟢 TIGHT' END AS spread_flag,
    CASE WHEN volume * yes_mid < 50   THEN '🔴 ILLIQUID'
         WHEN volume * yes_mid < 200  THEN '🟡 THIN'
         WHEN volume * yes_mid < 1000 THEN '🟡 MODERATE'
         ELSE '🟢 LIQUID' END AS volume_flag,
    CASE WHEN open_interest < 10  THEN '🔴 SKIP'
         WHEN open_interest < 50  THEN '🟡 THIN'
         WHEN open_interest < 500 THEN '🟢 OK'
         ELSE '🟢 DEEP' END AS oi_flag,
    ROUND((
        CASE WHEN spread > 0.20 THEN 0 WHEN spread > 0.10 THEN 10
             WHEN spread > 0.05 THEN 25 WHEN spread > 0.02 THEN 35 ELSE 40 END
        + CASE WHEN volume * yes_mid < 50   THEN 0 WHEN volume * yes_mid < 200  THEN 10
               WHEN volume * yes_mid < 1000 THEN 20 WHEN volume * yes_mid < 5000 THEN 25
               ELSE 30 END
        + CASE WHEN open_interest < 10   THEN 0 WHEN open_interest < 50   THEN 10
               WHEN open_interest < 500  THEN 20 WHEN open_interest < 2000 THEN 25
               ELSE 30 END
    ), 0) AS liquidity_score,
    CASE
        WHEN spread > 0.20 OR open_interest < 10 OR volume * yes_mid < 50
            THEN '🔴 DO NOT TRADE'
        WHEN spread > 0.10 OR volume * yes_mid < 200 OR open_interest < 50
            THEN '🟡 TRADE WITH CAUTION — Reduce size 50%'
        WHEN spread > 0.05 OR volume * yes_mid < 1000
            THEN '🟡 TRADE CAREFULLY — Reduce size 25%'
        ELSE '🟢 CLEAR TO TRADE — Full size OK'
    END AS liquidity_verdict,
    retrieved_at                                                           AS snapshot_time_utc,
    retrieved_at AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'    AS snapshot_time_pt,
    ROUND(EXTRACT(EPOCH FROM (NOW() - retrieved_at)) / 60)                AS mins_ago
FROM market_latest
WHERE yes_mid > 0
ORDER BY liquidity_score DESC, city, contract_side, bucket_floor;


-- ============================================================
-- QUERY 19: WeatherBHN - BHN Overall Scorecard
-- High level BHN signal performance summary. Shows total
-- recommendations, win rate, P&L, and BHN vs market comparison.
-- Headline card — is BHN actually adding value over the market?
-- Populates after settlement reconciler runs nightly at 15:00 UTC.
-- Tab: FORMULA/MODELS — PIN at top
-- Full query: infrastructure/docs/WeatherBHN/queries/BHN_OVERALL_SCORECARD.sql
-- ============================================================
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


-- ============================================================
-- QUERY 20: WeatherBHN - Signal Performance by Edge Tier
-- Breaks down BHN win rate by edge tier. Strong edge (>20%)
-- should win 70%+. If not, calibration model needs review.
-- Tab: FORMULA/MODELS
-- Full query: infrastructure/docs/WeatherBHN/queries/BHN_EDGE_TIER_PERFORMANCE.sql
-- ============================================================
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


-- ============================================================
-- QUERY 21: WeatherBHN - Recent Recommendations + Results
-- Every BHN signal with contract ticker, outcome, and P&L.
-- Raw trade log — use to spot patterns and debug model errors.
-- Tab: FORMULA/MODELS
-- Full query: infrastructure/docs/WeatherBHN/queries/BHN_RECENT_RESULTS.sql
-- ============================================================
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


-- ============================================================
-- QUERY 22: WeatherBHN - Performance by City
-- BHN win rate, avg edge, and P&L by city. Shows where BHN
-- has the most edge. High win rate + high avg edge = strong
-- calibration. Low win rate = needs more data or adjustment.
-- Tab: FORMULA/MODELS
-- Full query: infrastructure/docs/WeatherBHN/queries/BHN_CITY_PERFORMANCE.sql
-- ============================================================
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
