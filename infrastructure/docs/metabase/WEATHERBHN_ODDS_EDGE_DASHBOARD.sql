-- ============================================================
-- WeatherBHN: Odds/Edge Dashboard
-- Presentation layer only — reads weather_gold_contract_ledger (model
-- side) joined against weather_bronze_kalshi_market_snapshots (live
-- pricing + ticker). No new ingestion.
--
-- NOTE (2026-07-02): originally built against weather_gold_daily_edge_sheet,
-- which was retired 2026-06-30 in favor of weather_gold_contract_ledger
-- (written by cp4_kelly_sizer.py's write_to_ledger() as part of the new
-- CP1->CP2->CP3->CP4 orchestrator pipeline, core_trading_orchestrator.py).
-- Column names are nearly identical; two renames: raw_forecast_f ->
-- nws_forecast_f, last_updated -> ledger_updated_at.
--
-- Ticker is always taken from the bronze snapshot (m.market_ticker),
-- never constructed synthetically — matches the standing
-- WEATHERBHN-TICKER-ARCHITECTURE rule.
--
-- *** PROVISIONAL THRESHOLDS — NOT YET CONFIRMED WITH FLETCH ***
-- Two constants below are placeholders pending sign-off:
--   - boundary-proximity threshold: 1.0°F (flags when the model's
--     forecast sits within 1 degree of the bucket's floor/cap — a small
--     forecast error could flip the outcome)
--   - "No edge" threshold: 5.0 percentage points (matches the existing
--     "Marginal Edge 5-10%" tier boundary used elsewhere in the
--     WeatherBHN queries, chosen as a reasonable default, NOT confirmed)
-- Do not treat Flag/Recommendation as trade-actionable until these are
-- reviewed. Both are exposed as visible columns (not just baked into
-- the CASE logic) so a viewer can sanity-check the raw numbers.
--
-- *** DATA NOTE (2026-07-02): as of this writing, weather_gold_contract_ledger
-- has no rows with target_date >= CURRENT_DATE — the CP1-CP4 pipeline runs
-- cleanly (no errors) but isn't currently producing fresh forward-looking
-- signals, most likely because the strategy is still in its documented
-- DRY_RUN=true / enabled=false calibration phase. This query is correct
-- and will populate once CP1-CP4 resumes writing current signals — left
-- untouched per standing instruction not to modify CP1-CP4 behavior.
-- ============================================================

WITH market_latest AS (
    SELECT DISTINCT ON (market_ticker)
        market_ticker,
        city,
        contract_side,
        bucket_floor,
        bucket_cap,
        bucket_label,
        target_date,
        yes_bid,
        yes_ask,
        no_bid,
        no_ask,
        yes_mid,
        volume,
        open_interest,
        market_status,
        retrieved_at
    FROM weather_bronze_kalshi_market_snapshots
    WHERE market_status = 'active'
    ORDER BY market_ticker, retrieved_at DESC
),
open_positions AS (
    -- "Hold" = a position already open (not yet settled) on this ticker
    SELECT DISTINCT COALESCE(real_market_ticker, contract_ticker) AS ticker
    FROM weather_position_exits
    WHERE scored_at IS NULL
)
SELECT
    m.market_ticker                                                        AS ticker,
    g.target_date                                                          AS bet_date,
    g.city,
    g.contract_side,
    g.bucket_label,
    ROUND(m.yes_ask * 100, 1)                                              AS kalshi_yes_ask_cents,
    ROUND(m.no_ask * 100, 1)                                               AS kalshi_no_ask_cents,
    ROUND(g.calibrated_prob * 100, 1)                                      AS model_prob_pct,
    ROUND(ABS(g.calibrated_prob - g.market_implied_prob) * 100, 1)         AS edge_pts,
    ROUND((m.yes_ask - m.yes_bid) * 100, 1)                                AS spread_cents,
    ROUND(m.open_interest, 0)                                              AS open_interest_contracts,
    ROUND(m.volume, 0)                                                     AS daily_volume_contracts,
    ROUND(LEAST(m.open_interest * m.yes_mid * 0.10, m.volume * m.yes_mid * 0.05), 2)
                                                                            AS liquidity_cap_dollars,
    ROUND(LEAST(ABS(g.nws_forecast_f - g.bucket_floor), ABS(g.nws_forecast_f - g.bucket_cap)), 1)
                                                                            AS boundary_distance_f,
    (op.ticker IS NOT NULL)                                                AS position_already_open,
    CASE
        WHEN (m.yes_ask - m.yes_bid) * 100 > 20
            OR LEAST(m.open_interest * m.yes_mid * 0.10, m.volume * m.yes_mid * 0.05) <= 0
            THEN 'Skip'
        WHEN op.ticker IS NOT NULL
            THEN 'Hold'
        WHEN LEAST(ABS(g.nws_forecast_f - g.bucket_floor), ABS(g.nws_forecast_f - g.bucket_cap)) <= 1.0
            THEN 'Boundary risk'
        WHEN ABS(g.calibrated_prob - g.market_implied_prob) * 100 < 5.0
            THEN 'No edge'
        ELSE 'Clear'
    END                                                                     AS flag,
    CASE
        WHEN (m.yes_ask - m.yes_bid) * 100 > 20
            OR LEAST(m.open_interest * m.yes_mid * 0.10, m.volume * m.yes_mid * 0.05) <= 0
            THEN 'Skip'
        WHEN op.ticker IS NOT NULL
            THEN 'Hold'
        WHEN LEAST(ABS(g.nws_forecast_f - g.bucket_floor), ABS(g.nws_forecast_f - g.bucket_cap)) <= 1.0
            THEN 'Caution'
        WHEN ABS(g.calibrated_prob - g.market_implied_prob) * 100 < 5.0
            THEN 'No edge'
        ELSE 'Buy No'
    END                                                                     AS recommendation,
    g.ledger_updated_at                                                    AS calculated_time_utc,
    g.ledger_updated_at AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'
                                                                            AS calculated_time_pt,
    m.retrieved_at                                                         AS snapshot_time_utc,
    m.retrieved_at AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'  AS snapshot_time_pt
FROM weather_gold_contract_ledger g
JOIN market_latest m
    ON m.market_ticker = g.contract_ticker
LEFT JOIN open_positions op
    ON op.ticker = m.market_ticker
WHERE g.is_active = true
  AND g.target_date >= CURRENT_DATE
  -- Every Low-side row currently has NULL edge_pts (no calibrated model yet,
  -- storage-only scope as of 2026-07-02) — exclude placeholder rows entirely
  -- rather than let them float to the top of a DESC sort (Postgres sorts
  -- NULL first on DESC by default). Mirrors the edge_pts expression exactly
  -- (can't reference a SELECT-list alias in WHERE).
  AND ABS(g.calibrated_prob - g.market_implied_prob) IS NOT NULL
ORDER BY edge_pts DESC NULLS LAST;
