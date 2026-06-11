-- BHN Strategy 9 — Migrate Old Tables → Bronze Layer
-- Preserves historical calibration data by copying to new bronze tables.
--
-- Run AFTER 2026-06-11-weather-bsg-tables.sql:
--   sudo -u postgres psql -d eventhorizon -f sql/migrations/2026-06-11-migrate-old-to-bronze.sql
--
-- Source tables (NOT dropped here — kept in parallel):
--   weather_forecasts, weather_contract_prices, prediction_contracts
--
-- All inserts use ON CONFLICT DO NOTHING — safe to run multiple times.

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1) weather_forecasts → weather_bronze_nws_forecast_snapshots
--    NWS rows only. Pivot tmax_f/tmin_f from separate rows into one row per
--    (station, date, minute-rounded predicted_at).
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO weather_bronze_nws_forecast_snapshots
    (city, station_code, forecast_run_time, target_date, lead_hours,
     tmax_f, tmin_f, retrieved_at)
SELECT
    COALESCE(station_code, region)              AS city,
    COALESCE(station_code, region)              AS station_code,
    date_trunc('minute', MIN(predicted_at))     AS forecast_run_time,
    target_date,
    AVG(lead_time_hours)::INTEGER               AS lead_hours,
    MAX(CASE WHEN variable = 'tmax_f' THEN predicted_value END) AS tmax_f,
    MAX(CASE WHEN variable = 'tmin_f' THEN predicted_value END) AS tmin_f,
    MIN(predicted_at)                           AS retrieved_at
FROM weather_forecasts
WHERE source_model IN ('nws_gridpoints', 'nws')
  AND COALESCE(station_code, region) IS NOT NULL
GROUP BY
    COALESCE(station_code, region),
    target_date,
    date_trunc('minute', predicted_at)
ON CONFLICT (station_code, forecast_run_time, target_date) DO NOTHING;

\echo 'NWS rows migrated to bronze_nws_forecast_snapshots'
SELECT COUNT(*) AS bronze_nws_count FROM weather_bronze_nws_forecast_snapshots;


-- ─────────────────────────────────────────────────────────────────────────────
-- 2) weather_forecasts → weather_bronze_openmeteo_forecast_snapshots
--    Open-Meteo rows. One row per (station, model, run_time_bucket, date).
--    round down to nearest 6h GFS cycle boundary.
--    hour = NULL (daily aggregate rows).
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO weather_bronze_openmeteo_forecast_snapshots
    (city, station_code, lat, lon, model,
     forecast_run_time, target_date, hour,
     temperature_2m, retrieved_at)
SELECT
    COALESCE(station_code, region) AS city,
    COALESCE(station_code, region) AS station_code,
    -- lat/lon not available in old table; use known coords
    CASE COALESCE(station_code, region)
        WHEN 'KMIA' THEN 25.7959
        WHEN 'KDEN' THEN 39.8561
        WHEN 'KPHX' THEN 33.4373
        WHEN 'KLAX' THEN 33.9425
        WHEN 'KDFW' THEN 32.8998
        WHEN 'KNYC' THEN 40.7128
        WHEN 'KORD' THEN 41.9742
        WHEN 'KAUS' THEN 30.1945
        ELSE NULL
    END AS lat,
    CASE COALESCE(station_code, region)
        WHEN 'KMIA' THEN -80.2870
        WHEN 'KDEN' THEN -104.6737
        WHEN 'KPHX' THEN -112.0078
        WHEN 'KLAX' THEN -118.4081
        WHEN 'KDFW' THEN -97.0403
        WHEN 'KNYC' THEN -74.0060
        WHEN 'KORD' THEN -87.9073
        WHEN 'KAUS' THEN -97.6699
        ELSE NULL
    END AS lon,
    source_model AS model,
    -- round predicted_at down to nearest 6h GFS cycle
    predicted_at - (EXTRACT(EPOCH FROM predicted_at)::BIGINT % 21600) * INTERVAL '1 second' AS forecast_run_time,
    target_date,
    NULL::INTEGER AS hour,
    MAX(CASE WHEN variable = 'tmax_f' THEN predicted_value END) AS temperature_2m,
    MIN(predicted_at) AS retrieved_at
FROM weather_forecasts
WHERE source_model IN ('open_meteo', 'gfs_seamless', 'ecmwf_ifs', 'ecmwf_ifs04')
  AND COALESCE(station_code, region) IS NOT NULL
GROUP BY
    COALESCE(station_code, region),
    source_model,
    target_date,
    predicted_at - (EXTRACT(EPOCH FROM predicted_at)::BIGINT % 21600) * INTERVAL '1 second'
ON CONFLICT (station_code, model, forecast_run_time, target_date, hour) DO NOTHING;

\echo 'Open-Meteo rows migrated to bronze_openmeteo_forecast_snapshots'
SELECT COUNT(*) AS bronze_openmeteo_count FROM weather_bronze_openmeteo_forecast_snapshots;


-- ─────────────────────────────────────────────────────────────────────────────
-- 3) prediction_contracts → weather_kalshi_contract_catalog
--    Parses bucket_floor / bucket_cap from threshold_op + threshold_value.
--    contract_side parsed from contract_id (KXHIGH → 'high', KXLOW → 'low').
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO weather_kalshi_contract_catalog
    (market_ticker, station_code, contract_side, bucket_type,
     bucket_floor, bucket_cap, target_date, is_active,
     first_seen_at, last_seen_at, source_payload_json)
SELECT
    contract_id                                     AS market_ticker,
    station_code,
    CASE
        WHEN contract_id ILIKE '%KXHIGH%' THEN 'high'
        WHEN contract_id ILIKE '%KXLOW%'  THEN 'low'
        ELSE NULL
    END                                             AS contract_side,
    CASE threshold_op
        WHEN 'between' THEN 'between'
        WHEN '>'       THEN 'above'
        WHEN '<'       THEN 'below'
        ELSE threshold_op
    END                                             AS bucket_type,
    CASE threshold_op
        WHEN 'between' THEN threshold_value - 0.5
        WHEN '>'       THEN threshold_value
        ELSE NULL
    END                                             AS bucket_floor,
    CASE threshold_op
        WHEN 'between' THEN threshold_value + 0.5
        WHEN '<'       THEN threshold_value
        ELSE NULL
    END                                             AS bucket_cap,
    resolution_date                                 AS target_date,
    is_active,
    COALESCE(discovered_at, NOW())                  AS first_seen_at,
    COALESCE(last_seen_at, NOW())                   AS last_seen_at,
    raw_payload                                     AS source_payload_json
FROM prediction_contracts
WHERE exchange = 'kalshi'
  AND contract_id IS NOT NULL
ON CONFLICT (market_ticker) DO NOTHING;

\echo 'prediction_contracts migrated to weather_kalshi_contract_catalog'
SELECT COUNT(*) AS catalog_count FROM weather_kalshi_contract_catalog;


-- ─────────────────────────────────────────────────────────────────────────────
-- 4) weather_contract_prices → weather_bronze_kalshi_market_snapshots
--    Best-effort: yes_bid/ask from yes_price/no_price (old schema inconsistency).
--    Bucket fields will be NULL — enriched by new collector going forward.
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO weather_bronze_kalshi_market_snapshots
    (market_ticker, yes_bid, yes_ask, volume, open_interest,
     market_status, source_payload_json, retrieved_at)
SELECT
    contract_id,
    yes_price   AS yes_bid,
    no_price    AS yes_ask,     -- old schema stored no_price in this column
    volume_24h  AS volume,
    open_interest,
    'unknown'   AS market_status,
    raw_payload AS source_payload_json,
    captured_at AS retrieved_at
FROM weather_contract_prices
WHERE exchange = 'kalshi'
  AND contract_id IS NOT NULL
ON CONFLICT DO NOTHING;

\echo 'weather_contract_prices migrated to bronze_kalshi_market_snapshots'
SELECT COUNT(*) AS bronze_kalshi_count FROM weather_bronze_kalshi_market_snapshots;


-- ─────────────────────────────────────────────────────────────────────────────
-- Final counts
-- ─────────────────────────────────────────────────────────────────────────────
\echo ''
\echo '=== FINAL BRONZE COUNTS ==='
SELECT
    'bronze_nws_forecast'   AS tbl, COUNT(*) AS rows FROM weather_bronze_nws_forecast_snapshots
UNION ALL
SELECT 'bronze_openmeteo',         COUNT(*) FROM weather_bronze_openmeteo_forecast_snapshots
UNION ALL
SELECT 'bronze_kalshi_mkt',        COUNT(*) FROM weather_bronze_kalshi_market_snapshots
UNION ALL
SELECT 'bronze_actuals',           COUNT(*) FROM weather_bronze_nws_actuals
UNION ALL
SELECT 'contract_catalog',         COUNT(*) FROM weather_kalshi_contract_catalog
ORDER BY 1;

COMMIT;
