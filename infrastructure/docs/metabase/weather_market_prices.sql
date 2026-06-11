-- Live Kalshi market prices — what the market currently implies per contract
-- Paste as Native Query in Metabase
-- implied_probability = probability that the contract resolves YES

SELECT
    pc.station_code,
    CASE WHEN pc.variable = 'tmax_f' THEN 'high' ELSE 'low' END AS side,
    pc.title,
    pc.threshold_op,
    pc.threshold_value,
    pc.resolution_date,

    wcp.implied_probability,
    wcp.yes_price,
    wcp.no_price,
    wcp.volume_24h,
    wcp.open_interest,
    wcp.captured_at,

    ROUND(EXTRACT(EPOCH FROM (NOW() - wcp.captured_at)) / 60.0, 1)
                                                AS price_age_minutes

FROM weather_contract_prices wcp
JOIN prediction_contracts pc ON pc.contract_id = wcp.contract_id

-- Only the most recent price snapshot per contract
WHERE wcp.captured_at = (
    SELECT MAX(captured_at)
    FROM weather_contract_prices
    WHERE contract_id = wcp.contract_id
)
AND pc.is_active = true

ORDER BY pc.resolution_date, pc.station_code, pc.variable, pc.threshold_value;
