-- BHN forecast vs Kalshi market implied probability
-- Core edge detection view — paste as Native Query in Metabase
-- Shows open contracts with NWS + GFS model forecast vs market price
--
-- Column fix notes (schema differs from initial assumptions):
--   prediction_contracts: no city/side/strike_low/strike_high columns
--   threshold_op/threshold_value encode the contract condition
--   is_active=true replaces status='open'
--   join key: wcp.contract_id (text) = pc.contract_id (text)
--   weather_forecasts: predicted_value (not "value"), nws_gridpoints (not "nws")

SELECT
    pc.station_code,
    CASE WHEN pc.variable = 'tmax_f' THEN 'high' ELSE 'low' END AS side,
    pc.title,
    pc.threshold_op,
    pc.threshold_value,
    pc.resolution_date,

    -- Kalshi market implied probability (latest price snapshot)
    wcp.implied_probability                             AS market_implied_prob,
    wcp.yes_price,
    wcp.volume_24h,
    wcp.captured_at                                     AS price_as_of,

    -- NWS forecast for this city/date (latest run)
    wf_nws.predicted_value                              AS nws_forecast_f,
    wf_nws.predicted_at                                 AS nws_as_of,

    -- GFS (open-meteo) forecast for this city/date (latest run)
    wf_gfs.predicted_value                              AS gfs_forecast_f,
    wf_gfs.predicted_at                                 AS gfs_as_of,

    -- Raw edge: positive = BHN model says event is more likely than market prices
    ROUND(
        CAST(wcp.implied_probability AS numeric) -
        CASE
            WHEN wf_nws.predicted_value IS NOT NULL AND pc.threshold_op = '>'
                THEN (CASE WHEN wf_nws.predicted_value > pc.threshold_value THEN 0.92
                           ELSE 0.08 END)
            WHEN wf_nws.predicted_value IS NOT NULL AND pc.threshold_op = '<'
                THEN (CASE WHEN wf_nws.predicted_value < pc.threshold_value THEN 0.92
                           ELSE 0.08 END)
            ELSE NULL
        END
    , 3)                                                AS raw_edge

FROM prediction_contracts pc

-- Latest Kalshi market price per contract (join on text contract_id)
JOIN weather_contract_prices wcp
    ON wcp.contract_id = pc.contract_id
    AND wcp.captured_at = (
        SELECT MAX(captured_at)
        FROM weather_contract_prices
        WHERE contract_id = pc.contract_id
    )

-- NWS forecast — latest run for this station + variable + target_date
LEFT JOIN weather_forecasts wf_nws
    ON wf_nws.station_code = pc.station_code
    AND wf_nws.variable     = pc.variable
    AND wf_nws.source_model = 'nws_gridpoints'
    AND wf_nws.target_date  = pc.resolution_date
    AND wf_nws.predicted_at = (
        SELECT MAX(predicted_at)
        FROM weather_forecasts
        WHERE station_code  = pc.station_code
          AND source_model  = 'nws_gridpoints'
          AND target_date   = pc.resolution_date
          AND variable      = pc.variable
    )

-- GFS (open-meteo) forecast — latest run
LEFT JOIN weather_forecasts wf_gfs
    ON wf_gfs.station_code = pc.station_code
    AND wf_gfs.variable     = pc.variable
    AND wf_gfs.source_model = 'open-meteo:gfs_seamless'
    AND wf_gfs.target_date  = pc.resolution_date
    AND wf_gfs.predicted_at = (
        SELECT MAX(predicted_at)
        FROM weather_forecasts
        WHERE station_code  = pc.station_code
          AND source_model  = 'open-meteo:gfs_seamless'
          AND target_date   = pc.resolution_date
          AND variable      = pc.variable
    )

WHERE pc.is_active = true
ORDER BY pc.resolution_date, pc.station_code, pc.threshold_value;
