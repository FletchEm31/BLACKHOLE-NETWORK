-- Live Kalshi market prices — what the market currently implies per contract
-- Paste as Native Query in Metabase
-- yes_price = implied probability that high/low exceeds the strike

SELECT
    pc.city,
    pc.side,
    pc.title,
    pc.strike_low,
    pc.strike_high,
    pc.resolution_date,
    pc.station_code,

    wcp.yes_price                               AS implied_prob,
    wcp.no_price,
    wcp.volume_24h,
    wcp.open_interest,
    wcp.captured_at,

    ROUND(EXTRACT(EPOCH FROM (NOW() - wcp.captured_at)) / 60.0, 1)
                                                AS price_age_minutes

FROM weather_contract_prices wcp
JOIN prediction_contracts pc ON pc.id = wcp.contract_id

-- Only the most recent price snapshot per contract
WHERE wcp.captured_at = (
    SELECT MAX(captured_at)
    FROM weather_contract_prices
    WHERE contract_id = wcp.contract_id
)
AND pc.status = 'open'

ORDER BY pc.resolution_date, pc.city, pc.side, pc.strike_low;
