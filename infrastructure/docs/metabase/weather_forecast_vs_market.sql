-- BHN vs Market Edge — working query (updated 2026-06-11)
-- Paste as Native Query in Metabase
-- Shows Kalshi implied probability vs NWS forecast temp per active contract
-- station_code + resolution_date now populated from ticker parsing

SELECT
    wcp.contract_title,
    wcp.contract_id                             AS ticker,
    pc.station_code,
    pc.resolution_date,
    pc.threshold_op,
    pc.threshold_value,
    wcp.yes_price                               AS market_implied_prob,
    ROUND(AVG(wf.predicted_value), 1)           AS nws_forecast_f,
    -- edge_degrees: positive = NWS forecast is above what market implies
    -- (e.g. +5 = NWS says 5°F hotter than the market is pricing in)
    ROUND(
        AVG(wf.predicted_value) - (wcp.yes_price * 100)
    , 1)                                        AS edge_degrees,
    wcp.captured_at                             AS price_as_of
FROM weather_contract_prices wcp
JOIN prediction_contracts pc
    ON pc.station_code IS NOT NULL
    AND wcp.contract_id LIKE pc.contract_id
LEFT JOIN weather_forecasts wf
    ON wf.station_code  = pc.station_code
    AND wf.source_model = 'nws_gridpoints'
    AND wf.target_date  = pc.resolution_date
WHERE wcp.captured_at > NOW() - INTERVAL '35 minutes'
AND pc.is_active = true
GROUP BY
    wcp.contract_title, wcp.contract_id,
    pc.station_code, pc.resolution_date,
    pc.threshold_op, pc.threshold_value,
    wcp.yes_price, wcp.captured_at
ORDER BY pc.resolution_date, pc.station_code, pc.threshold_value;
