-- Quick data check — no complex joins, works with sparse early-calibration data
-- Paste each block separately as individual Native Query questions in Metabase
-- Use these during Day 1-7 when the complex join queries may return 0 rows

-- ═══════════════════════════════════════════════════
-- BLOCK 1: Latest forecasts per city (all models)
-- ═══════════════════════════════════════════════════
SELECT
    station_code,
    source_model,
    variable,
    target_date,
    predicted_value,
    predicted_at
FROM weather_forecasts
WHERE predicted_at >= NOW() - INTERVAL '6 hours'
ORDER BY station_code, source_model, variable, target_date
LIMIT 100;

-- ═══════════════════════════════════════════════════
-- BLOCK 2: Row counts per city — calibration progress snapshot
-- ═══════════════════════════════════════════════════
SELECT
    station_code,
    source_model,
    variable,
    COUNT(*)                                    AS total_rows,
    COUNT(DISTINCT target_date)                 AS distinct_days,
    MIN(target_date)                            AS earliest_date,
    MAX(target_date)                            AS latest_date,
    MAX(predicted_at)                           AS last_updated
FROM weather_forecasts
GROUP BY station_code, source_model, variable
ORDER BY station_code, source_model, variable;

-- ═══════════════════════════════════════════════════
-- BLOCK 3: Latest ASOS observations
-- ═══════════════════════════════════════════════════
SELECT
    station_code,
    variable,
    observed_value,
    observed_at,
    source
FROM weather_observations
WHERE observed_at >= NOW() - INTERVAL '24 hours'
ORDER BY station_code, variable, observed_at DESC;

-- ═══════════════════════════════════════════════════
-- BLOCK 4: Latest Kalshi price snapshots
-- ═══════════════════════════════════════════════════
SELECT
    contract_id,
    contract_title,
    implied_probability,
    yes_price,
    no_price,
    volume_24h,
    resolution_date,
    captured_at
FROM weather_contract_prices
WHERE captured_at >= NOW() - INTERVAL '4 hours'
ORDER BY resolution_date, contract_id
LIMIT 100;

-- ═══════════════════════════════════════════════════
-- BLOCK 5: All known prediction contracts (open)
-- ═══════════════════════════════════════════════════
SELECT
    station_code,
    variable,
    threshold_op,
    threshold_value,
    resolution_date,
    contract_id,
    title,
    is_active,
    discovered_at
FROM prediction_contracts
WHERE is_active = true
ORDER BY resolution_date, station_code, variable, threshold_value;
