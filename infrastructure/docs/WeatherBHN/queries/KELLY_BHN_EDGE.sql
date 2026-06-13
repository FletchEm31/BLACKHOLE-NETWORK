-- WeatherBHN - Kelly Sizing (BHN Edge + Liquidity)
-- Kelly Criterion using BHN calibrated probability vs market implied probability.
-- This is the PRIMARY automated trading signal. kelly_signal = BET = place order.
-- Will populate after Visual Crossing backfill runs (30+ days needed).
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
        (yes_bid + yes_ask) / 2.0                    AS yes_mid,
        yes_ask - yes_bid                             AS spread,
        volume,
        open_interest,
        market_status,
        retrieved_at
    FROM weather_bronze_kalshi_market_snapshots
    WHERE market_status = 'active'
    ORDER BY market_ticker, retrieved_at DESC
)

SELECT
    g.city,
    g.station_code,
    g.target_date,
    g.contract_side,
    g.bucket_floor,
    g.bucket_cap,

    -- ══════════════════════════════════════════
    -- SECTION 1: PROBABILITY COMPARISON
    -- ══════════════════════════════════════════
    ROUND(m.yes_bid * 100, 1)             AS bid_cents,
    ROUND(m.yes_ask * 100, 1)             AS ask_cents,
    ROUND(m.yes_mid * 100, 1)             AS mid_cents,
    ROUND(g.market_implied_prob * 100, 1) AS market_prob_pct,
    ROUND(g.calibrated_prob * 100, 1)     AS bhn_prob_pct,
    ROUND((g.calibrated_prob - g.market_implied_prob) * 100, 1) AS edge_pct,

    -- Edge classification
    CASE
        WHEN (g.calibrated_prob - g.market_implied_prob) >= 0.20 THEN '🔥 STRONG EDGE >20%'
        WHEN (g.calibrated_prob - g.market_implied_prob) >= 0.10 THEN '🟢 GOOD EDGE 10-20%'
        WHEN (g.calibrated_prob - g.market_implied_prob) >= 0.05 THEN '🟡 MARGINAL EDGE 5-10%'
        WHEN (g.calibrated_prob - g.market_implied_prob) >= 0.00 THEN '⚪ NO EDGE'
        ELSE '🔴 NEGATIVE EDGE — Market smarter than BHN'
    END AS edge_classification,

    -- ══════════════════════════════════════════
    -- SECTION 2: LIQUIDITY METRICS
    -- ══════════════════════════════════════════
    ROUND(m.spread * 100, 1)              AS spread_cents,
    ROUND(m.spread / NULLIF(m.yes_mid, 0) * 100, 1) AS spread_pct_of_mid,
    ROUND(m.volume, 0)                    AS daily_volume_contracts,
    ROUND(m.volume * m.yes_mid, 2)        AS daily_volume_dollars,
    ROUND(m.open_interest, 0)             AS open_interest_contracts,
    ROUND(m.open_interest * m.yes_mid, 2) AS open_interest_dollars,

    -- ══════════════════════════════════════════
    -- SECTION 3: LIQUIDITY FLAGS
    -- ══════════════════════════════════════════
    CASE
        WHEN m.spread > 0.20 THEN '🔴 SKIP'
        WHEN m.spread > 0.10 THEN '🟡 REDUCE 50%'
        WHEN m.spread > 0.05 THEN '🟡 REDUCE 25%'
        ELSE '🟢 OK'
    END AS spread_flag,

    CASE
        WHEN m.volume * m.yes_mid < 50   THEN '🔴 ILLIQUID'
        WHEN m.volume * m.yes_mid < 200  THEN '🟡 THIN'
        WHEN m.volume * m.yes_mid < 1000 THEN '🟡 MODERATE'
        ELSE '🟢 LIQUID'
    END AS volume_flag,

    CASE
        WHEN m.open_interest < 10  THEN '🔴 SKIP'
        WHEN m.open_interest < 50  THEN '🟡 THIN'
        WHEN m.open_interest < 500 THEN '🟡 MODERATE'
        ELSE '🟢 LIQUID'
    END AS oi_flag,

    -- Price impact at 10% of OI
    CASE
        WHEN m.open_interest < 10   THEN '🔴 >10% impact'
        WHEN m.open_interest < 50   THEN '🔴 5-10% impact'
        WHEN m.open_interest < 200  THEN '🟡 2-5% impact'
        WHEN m.open_interest < 1000 THEN '🟡 1-2% impact'
        ELSE '🟢 <1% impact'
    END AS price_impact_flag,

    -- ══════════════════════════════════════════
    -- SECTION 4: KELLY CALCULATIONS
    -- Market Kelly vs BHN Kelly side by side
    -- ══════════════════════════════════════════
    ROUND((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 4) AS net_odds_b,

    -- Market half Kelly
    ROUND(
        (g.market_implied_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
        - (1 - g.market_implied_prob))
        / NULLIF((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 0)
        * 0.5
    , 4) AS market_half_kelly,

    -- BHN half Kelly
    ROUND(
        (g.calibrated_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
        - (1 - g.calibrated_prob))
        / NULLIF((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 0)
        * 0.5
    , 4) AS bhn_half_kelly,

    -- Kelly edge advantage (BHN - Market)
    ROUND(
        (
            (g.calibrated_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
            - (1 - g.calibrated_prob))
            / NULLIF((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 0)
            * 0.5
        ) - (
            (g.market_implied_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
            - (1 - g.market_implied_prob))
            / NULLIF((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 0)
            * 0.5
        )
    , 4) AS kelly_edge_advantage,

    -- ══════════════════════════════════════════
    -- SECTION 5: LIQUIDITY CAP
    -- ══════════════════════════════════════════
    ROUND(
        LEAST(
            m.open_interest * m.yes_mid * 0.10,
            m.volume * m.yes_mid * 0.05
        ), 2
    ) AS liquidity_cap_dollars,

    -- Spread multiplier
    CASE
        WHEN m.spread > 0.20 THEN 0.00
        WHEN m.spread > 0.10 THEN 0.50
        WHEN m.spread > 0.05 THEN 0.75
        ELSE 1.00
    END AS spread_multiplier,

    -- ══════════════════════════════════════════
    -- SECTION 6: FINAL BHN EDGE-ADJUSTED STAKES
    -- ══════════════════════════════════════════
    ROUND(
        LEAST(
            GREATEST(0,
                (g.calibrated_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
                - (1 - g.calibrated_prob))
                / NULLIF((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 0)
                * 0.5 * 14
            ),
            LEAST(
                m.open_interest * m.yes_mid * 0.10,
                m.volume * m.yes_mid * 0.05
            )
        ) * CASE
            WHEN m.spread > 0.20 THEN 0.00
            WHEN m.spread > 0.10 THEN 0.50
            WHEN m.spread > 0.05 THEN 0.75
            ELSE 1.00
        END
    , 2) AS final_stake_14,

    ROUND(
        LEAST(
            GREATEST(0,
                (g.calibrated_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
                - (1 - g.calibrated_prob))
                / NULLIF((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 0)
                * 0.5 * 50
            ),
            LEAST(
                m.open_interest * m.yes_mid * 0.10,
                m.volume * m.yes_mid * 0.05
            )
        ) * CASE
            WHEN m.spread > 0.20 THEN 0.00
            WHEN m.spread > 0.10 THEN 0.50
            WHEN m.spread > 0.05 THEN 0.75
            ELSE 1.00
        END
    , 2) AS final_stake_50,

    ROUND(
        LEAST(
            GREATEST(0,
                (g.calibrated_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
                - (1 - g.calibrated_prob))
                / NULLIF((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 0)
                * 0.5 * 500
            ),
            LEAST(
                m.open_interest * m.yes_mid * 0.10,
                m.volume * m.yes_mid * 0.05
            )
        ) * CASE
            WHEN m.spread > 0.20 THEN 0.00
            WHEN m.spread > 0.10 THEN 0.50
            WHEN m.spread > 0.05 THEN 0.75
            ELSE 1.00
        END
    , 2) AS final_stake_500,

    -- ══════════════════════════════════════════
    -- SECTION 7: FINAL TRADE SIGNAL
    -- Combines Kelly + Edge + Liquidity
    -- ══════════════════════════════════════════
    CASE
        WHEN m.spread > 0.20
            THEN '🔴 NO TRADE — Spread too wide'
        WHEN m.open_interest < 10
            THEN '🔴 NO TRADE — Illiquid'
        WHEN m.volume * m.yes_mid < 50
            THEN '🔴 NO TRADE — No volume'
        WHEN g.calibrated_prob IS NULL
            THEN '⏳ PENDING — No calibration data yet'
        WHEN (g.calibrated_prob - g.market_implied_prob) < 0
            THEN '⛔ NO TRADE — Negative edge'
        WHEN (g.calibrated_prob - g.market_implied_prob) < 0.05
            THEN '⚪ SKIP — Edge too small (<5%)'
        WHEN m.spread > 0.10
            THEN '🟡 REDUCED — Wide spread, edge exists'
        WHEN m.volume * m.yes_mid < 200
            THEN '🟡 REDUCED — Thin volume, edge exists'
        WHEN (g.calibrated_prob - g.market_implied_prob) >= 0.20
            THEN '🔥 STRONG BET — Max liquidity-adjusted size'
        WHEN (g.calibrated_prob - g.market_implied_prob) >= 0.10
            THEN '✅ BET — Full liquidity-adjusted size'
        ELSE
            '🟡 MARGINAL — Half liquidity-adjusted size'
    END AS kelly_signal,

    g.edge_rank,
    g.recommended_action,
    m.retrieved_at AS price_as_of

FROM weather_gold_daily_edge_sheet g
LEFT JOIN market_latest m
    ON g.city = m.city
    AND g.contract_side = m.contract_side
    AND g.bucket_floor = m.bucket_floor
    AND g.bucket_cap = m.bucket_cap
WHERE g.target_date >= CURRENT_DATE
  AND g.is_active = true
  AND g.calibrated_prob IS NOT NULL
  AND m.yes_mid IS NOT NULL
  AND m.yes_mid > 0
ORDER BY
    -- Sort: strong signals first, then by edge size
    CASE
        WHEN m.spread > 0.20 THEN 5
        WHEN m.open_interest < 10 THEN 5
        WHEN g.calibrated_prob IS NULL THEN 4
        WHEN (g.calibrated_prob - g.market_implied_prob) < 0.05 THEN 3
        WHEN (g.calibrated_prob - g.market_implied_prob) >= 0.20 THEN 1
        ELSE 2
    END,
    (g.calibrated_prob - g.market_implied_prob) DESC,
    g.target_date,
    g.city;
