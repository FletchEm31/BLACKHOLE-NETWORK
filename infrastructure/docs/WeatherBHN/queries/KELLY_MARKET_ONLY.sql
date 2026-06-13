-- WeatherBHN - Kelly Sizing (Market Only + Liquidity)
-- Pure Kelly Criterion sizing based on Kalshi market implied probabilities
-- with full liquidity pre-trade check. trade_signal = go/no-go.
-- Source: WeatherBHN_Kelly_Liquidity_v2.txt (June 12, 2026)

WITH market_latest AS (
    -- Get most recent snapshot for each contract
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
        g.city,
        g.station_code,
        g.target_date,
        g.contract_side,
        g.bucket_floor,
        g.bucket_cap,
        g.market_implied_prob,
        g.market_yes_mid,
        g.calibrated_prob,
        g.edge,
        g.recommended_action,
        g.last_updated
    FROM weather_gold_daily_edge_sheet g
    WHERE g.target_date >= CURRENT_DATE
      AND g.is_active = true
)

SELECT
    e.city,
    e.station_code,
    e.target_date,
    e.contract_side,
    e.bucket_floor,
    e.bucket_cap,

    -- ══════════════════════════════════════════
    -- SECTION 1: MARKET PRICING
    -- ══════════════════════════════════════════
    ROUND(m.yes_bid * 100, 1)              AS bid_cents,
    ROUND(m.yes_ask * 100, 1)              AS ask_cents,
    ROUND(m.yes_mid * 100, 1)              AS mid_cents,
    ROUND(e.market_implied_prob * 100, 1)  AS market_prob_pct,

    -- ══════════════════════════════════════════
    -- SECTION 2: LIQUIDITY METRICS
    -- ══════════════════════════════════════════
    ROUND(m.spread * 100, 1)               AS spread_cents,
    ROUND(m.spread / NULLIF(m.yes_mid, 0) * 100, 1) AS spread_pct_of_mid,
    ROUND(m.volume, 0)                     AS daily_volume_contracts,
    ROUND(m.volume * m.yes_mid, 2)         AS daily_volume_dollars,
    ROUND(m.open_interest, 0)              AS open_interest_contracts,
    ROUND(m.open_interest * m.yes_mid, 2)  AS open_interest_dollars,

    -- ══════════════════════════════════════════
    -- SECTION 3: SPREAD FLAG
    -- ══════════════════════════════════════════
    CASE
        WHEN m.spread > 0.20 THEN '🔴 SKIP — Spread > 20¢'
        WHEN m.spread > 0.10 THEN '🟡 CAUTION — Spread 10-20¢ (reduce 50%)'
        WHEN m.spread > 0.05 THEN '🟡 WATCH — Spread 5-10¢ (reduce 25%)'
        WHEN m.spread > 0.02 THEN '🟢 OK — Spread 2-5¢'
        ELSE                      '🟢 TIGHT — Spread < 2¢'
    END AS spread_flag,

    -- ══════════════════════════════════════════
    -- SECTION 4: VOLUME FLAG
    -- ══════════════════════════════════════════
    CASE
        WHEN m.volume * m.yes_mid < 50   THEN '🔴 ILLIQUID — Volume < $50'
        WHEN m.volume * m.yes_mid < 200  THEN '🟡 THIN — Volume $50-200'
        WHEN m.volume * m.yes_mid < 1000 THEN '🟡 MODERATE — Volume $200-1000'
        WHEN m.volume * m.yes_mid < 5000 THEN '🟢 LIQUID — Volume $1K-5K'
        ELSE                                  '🟢 DEEP — Volume > $5K'
    END AS volume_flag,

    -- ══════════════════════════════════════════
    -- SECTION 5: OPEN INTEREST FLAG
    -- ══════════════════════════════════════════
    CASE
        WHEN m.open_interest < 10   THEN '🔴 SKIP — OI < 10 contracts'
        WHEN m.open_interest < 50   THEN '🟡 THIN — OI 10-50 contracts'
        WHEN m.open_interest < 500  THEN '🟡 MODERATE — OI 50-500'
        WHEN m.open_interest < 2000 THEN '🟢 LIQUID — OI 500-2000'
        ELSE                             '🟢 DEEP — OI > 2000'
    END AS open_interest_flag,

    -- ══════════════════════════════════════════
    -- SECTION 6: PRICE IMPACT ESTIMATE
    -- How much will YOUR order move the market?
    -- Assumes your order = 10% of open interest
    -- ══════════════════════════════════════════
    ROUND(
        CASE
            WHEN m.open_interest > 0
            THEN (0.10 / NULLIF(m.open_interest, 0)) * 100
            ELSE NULL
        END, 3
    ) AS est_price_impact_pct_at_10pct_oi,

    CASE
        WHEN m.open_interest < 10   THEN '🔴 >10% impact — Skip'
        WHEN m.open_interest < 50   THEN '🔴 5-10% impact — Very risky'
        WHEN m.open_interest < 200  THEN '🟡 2-5% impact — Reduce size'
        WHEN m.open_interest < 1000 THEN '🟡 1-2% impact — Watch'
        ELSE                             '🟢 <1% impact — Safe'
    END AS price_impact_flag,

    -- ══════════════════════════════════════════
    -- SECTION 7: KELLY CALCULATION (Market Only)
    -- ══════════════════════════════════════════
    -- Net odds b = (1 - price) / price
    ROUND((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 4) AS net_odds_b,

    -- Full Kelly fraction
    ROUND(
        (e.market_implied_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
        - (1 - e.market_implied_prob))
        / NULLIF((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 0)
    , 4) AS full_kelly_fraction,

    -- Half Kelly fraction
    ROUND(
        (e.market_implied_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
        - (1 - e.market_implied_prob))
        / NULLIF((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 0)
        * 0.5
    , 4) AS half_kelly_fraction,

    -- ══════════════════════════════════════════
    -- SECTION 8: RAW KELLY STAKES (before liquidity adjustment)
    -- ══════════════════════════════════════════
    ROUND(GREATEST(0,
        (e.market_implied_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
        - (1 - e.market_implied_prob))
        / NULLIF((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 0)
        * 0.5
    ) * 14, 2)  AS raw_stake_14,
    ROUND(GREATEST(0,
        (e.market_implied_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
        - (1 - e.market_implied_prob))
        / NULLIF((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 0)
        * 0.5
    ) * 50, 2)  AS raw_stake_50,
    ROUND(GREATEST(0,
        (e.market_implied_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
        - (1 - e.market_implied_prob))
        / NULLIF((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 0)
        * 0.5
    ) * 500, 2) AS raw_stake_500,

    -- ══════════════════════════════════════════
    -- SECTION 9: SPREAD MULTIPLIER
    -- ══════════════════════════════════════════
    CASE
        WHEN m.spread > 0.20 THEN 0.00
        WHEN m.spread > 0.10 THEN 0.50
        WHEN m.spread > 0.05 THEN 0.75
        ELSE 1.00
    END AS spread_multiplier,

    -- ══════════════════════════════════════════
    -- SECTION 10: LIQUIDITY CAP
    -- Never deploy more than 10% of open interest
    -- or 5% of daily volume — whichever is lower
    -- ══════════════════════════════════════════
    ROUND(
        LEAST(
            m.open_interest * m.yes_mid * 0.10,
            m.volume * m.yes_mid * 0.05
        ), 2
    ) AS liquidity_cap_dollars,

    -- ══════════════════════════════════════════
    -- SECTION 11: FINAL LIQUIDITY-ADJUSTED STAKES
    -- ══════════════════════════════════════════
    ROUND(
        LEAST(
            GREATEST(0,
                (e.market_implied_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
                - (1 - e.market_implied_prob))
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
                (e.market_implied_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
                - (1 - e.market_implied_prob))
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
                (e.market_implied_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
                - (1 - e.market_implied_prob))
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
    -- SECTION 12: OVERALL TRADE SIGNAL
    -- ══════════════════════════════════════════
    CASE
        WHEN m.spread > 0.20                    THEN '🔴 NO TRADE — Spread too wide'
        WHEN m.open_interest < 10               THEN '🔴 NO TRADE — Illiquid'
        WHEN m.volume * m.yes_mid < 50          THEN '🔴 NO TRADE — No volume'
        WHEN (e.market_implied_prob * ((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0))
            - (1 - e.market_implied_prob))
            / NULLIF((1.0 - m.yes_mid) / NULLIF(m.yes_mid, 0), 0) <= 0
                                                THEN '⛔ NO TRADE — Negative Kelly'
        WHEN m.spread > 0.10                    THEN '🟡 REDUCED SIZE — Wide spread'
        WHEN m.volume * m.yes_mid < 200         THEN '🟡 REDUCED SIZE — Thin volume'
        WHEN m.open_interest < 50               THEN '🟡 REDUCED SIZE — Low OI'
        ELSE                                         '✅ TRADE — Full liquidity-adjusted size'
    END AS trade_signal,

    m.retrieved_at AS price_as_of

FROM edge_data e
LEFT JOIN market_latest m
    ON e.city = m.city
    AND e.contract_side = m.contract_side
    AND e.bucket_floor = m.bucket_floor
    AND e.bucket_cap = m.bucket_cap
WHERE e.market_implied_prob IS NOT NULL
  AND m.yes_mid IS NOT NULL
  AND m.yes_mid > 0
ORDER BY
    CASE
        WHEN m.spread > 0.20 THEN 3
        WHEN m.open_interest < 10 THEN 3
        WHEN m.volume * m.yes_mid < 50 THEN 3
        ELSE 1
    END,
    e.market_implied_prob DESC,
    e.target_date,
    e.city;
