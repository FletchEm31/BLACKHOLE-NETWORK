-- WeatherBHN - Pre-Trade Liquidity Scanner
-- Dedicated pre-trade liquidity check for ALL active weather contracts.
-- Run this BEFORE placing any trade. liquidity_score 0-100: >70 safe, 40-70 careful, <40 avoid.
-- Source: WeatherBHN_Kelly_Liquidity_v2.txt (June 12, 2026)

WITH market_latest AS (
    SELECT DISTINCT ON (market_ticker)
        market_ticker,
        city,
        contract_side,
        bucket_floor,
        bucket_cap,
        yes_bid,
        yes_ask,
        (yes_bid + yes_ask) / 2.0  AS yes_mid,
        yes_ask - yes_bid          AS spread,
        volume,
        open_interest,
        market_status,
        retrieved_at
    FROM weather_bronze_kalshi_market_snapshots
    WHERE market_status = 'active'
    ORDER BY market_ticker, retrieved_at DESC
)

SELECT
    city,
    contract_side,
    bucket_floor,
    bucket_cap,

    -- Current pricing
    ROUND(yes_bid * 100, 1)   AS bid_cents,
    ROUND(yes_ask * 100, 1)   AS ask_cents,
    ROUND(yes_mid * 100, 1)   AS mid_cents,
    ROUND(spread * 100, 1)    AS spread_cents,

    -- Liquidity metrics
    ROUND(volume, 0)                    AS volume_contracts,
    ROUND(volume * yes_mid, 2)          AS volume_dollars,
    ROUND(open_interest, 0)             AS open_interest_contracts,
    ROUND(open_interest * yes_mid, 2)   AS open_interest_dollars,

    -- Max safe position size (10% of OI, 5% of volume)
    ROUND(
        LEAST(
            open_interest * yes_mid * 0.10,
            volume * yes_mid * 0.05
        ), 2
    ) AS max_safe_position_dollars,

    -- Contracts you can safely buy
    ROUND(
        LEAST(
            open_interest * 0.10,
            volume * 0.05
        ), 0
    ) AS max_safe_contracts,

    -- Individual flags
    CASE
        WHEN spread > 0.20 THEN '🔴 SKIP'
        WHEN spread > 0.10 THEN '🟡 WIDE'
        WHEN spread > 0.05 THEN '🟡 OK'
        ELSE '🟢 TIGHT'
    END AS spread_flag,

    CASE
        WHEN volume * yes_mid < 50   THEN '🔴 ILLIQUID'
        WHEN volume * yes_mid < 200  THEN '🟡 THIN'
        WHEN volume * yes_mid < 1000 THEN '🟡 MODERATE'
        ELSE '🟢 LIQUID'
    END AS volume_flag,

    CASE
        WHEN open_interest < 10  THEN '🔴 SKIP'
        WHEN open_interest < 50  THEN '🟡 THIN'
        WHEN open_interest < 500 THEN '🟢 OK'
        ELSE '🟢 DEEP'
    END AS oi_flag,

    -- Composite liquidity score (0-100)
    -- Higher = more liquid = safer to trade
    ROUND(
        (
            -- Spread component (40 points max)
            CASE
                WHEN spread > 0.20 THEN 0
                WHEN spread > 0.10 THEN 10
                WHEN spread > 0.05 THEN 25
                WHEN spread > 0.02 THEN 35
                ELSE 40
            END
            +
            -- Volume component (30 points max)
            CASE
                WHEN volume * yes_mid < 50   THEN 0
                WHEN volume * yes_mid < 200  THEN 10
                WHEN volume * yes_mid < 1000 THEN 20
                WHEN volume * yes_mid < 5000 THEN 25
                ELSE 30
            END
            +
            -- Open interest component (30 points max)
            CASE
                WHEN open_interest < 10   THEN 0
                WHEN open_interest < 50   THEN 10
                WHEN open_interest < 500  THEN 20
                WHEN open_interest < 2000 THEN 25
                ELSE 30
            END
        )
    , 0) AS liquidity_score,

    -- Overall verdict
    CASE
        WHEN spread > 0.20 OR open_interest < 10 OR volume * yes_mid < 50
            THEN '🔴 DO NOT TRADE'
        WHEN spread > 0.10 OR volume * yes_mid < 200 OR open_interest < 50
            THEN '🟡 TRADE WITH CAUTION — Reduce size 50%'
        WHEN spread > 0.05 OR volume * yes_mid < 1000
            THEN '🟡 TRADE CAREFULLY — Reduce size 25%'
        ELSE
            '🟢 CLEAR TO TRADE — Full size OK'
    END AS liquidity_verdict,

    ROUND(EXTRACT(EPOCH FROM (NOW() - retrieved_at)) / 60) AS price_age_mins

FROM market_latest
WHERE yes_mid > 0
ORDER BY
    liquidity_score DESC,
    city,
    contract_side,
    bucket_floor;
