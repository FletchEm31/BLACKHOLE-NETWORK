-- BHN forecast vs Kalshi market implied probability
-- Core edge detection view — paste as Native Query in Metabase
-- Shows open contracts with BHN model temp forecast alongside market price

SELECT
    pc.city,
    pc.side,
    pc.title,
    pc.strike_low,
    pc.strike_high,
    pc.resolution_date,

    -- Kalshi market implied probability (latest price)
    wcp.yes_price                                       AS market_implied_prob,
    wcp.volume_24h,
    wcp.captured_at                                     AS price_as_of,

    -- NWS forecast for this city/date
    wf_nws.value                                        AS nws_forecast_f,
    wf_nws.predicted_at                                 AS nws_as_of,

    -- GFS (open-meteo) forecast for this city/date
    wf_gfs.value                                        AS gfs_forecast_f,
    wf_gfs.predicted_at                                 AS gfs_as_of,

    -- Simple edge: positive = BHN thinks contract should be priced higher
    ROUND(
        CAST(wcp.yes_price AS numeric) -
        CASE
            WHEN pc.side = 'high' AND wf_nws.value IS NOT NULL
                THEN (CASE WHEN wf_nws.value >= pc.strike_high THEN 0.95
                           WHEN wf_nws.value <= pc.strike_low  THEN 0.05
                           ELSE 0.5 END)
            ELSE NULL
        END
    , 3)                                                AS raw_edge

FROM prediction_contracts pc

-- Latest Kalshi market price per contract
JOIN weather_contract_prices wcp
    ON wcp.contract_id = pc.id
    AND wcp.captured_at = (
        SELECT MAX(captured_at)
        FROM weather_contract_prices
        WHERE contract_id = pc.id
    )

-- NWS forecast (latest run) for matching station + date + variable
LEFT JOIN weather_forecasts wf_nws
    ON wf_nws.station_code = pc.station_code
    AND wf_nws.variable     = CASE WHEN pc.side = 'high' THEN 'tmax_f' ELSE 'tmin_f' END
    AND wf_nws.source_model = 'nws'
    AND wf_nws.target_date  = pc.resolution_date
    AND wf_nws.predicted_at = (
        SELECT MAX(predicted_at)
        FROM weather_forecasts
        WHERE station_code  = pc.station_code
          AND source_model  = 'nws'
          AND target_date   = pc.resolution_date
          AND variable      = CASE WHEN pc.side = 'high' THEN 'tmax_f' ELSE 'tmin_f' END
    )

-- GFS forecast (latest run)
LEFT JOIN weather_forecasts wf_gfs
    ON wf_gfs.station_code = pc.station_code
    AND wf_gfs.variable     = CASE WHEN pc.side = 'high' THEN 'tmax_f' ELSE 'tmin_f' END
    AND wf_gfs.source_model = 'open-meteo:gfs_seamless'
    AND wf_gfs.target_date  = pc.resolution_date
    AND wf_gfs.predicted_at = (
        SELECT MAX(predicted_at)
        FROM weather_forecasts
        WHERE station_code  = pc.station_code
          AND source_model  = 'open-meteo:gfs_seamless'
          AND target_date   = pc.resolution_date
          AND variable      = CASE WHEN pc.side = 'high' THEN 'tmax_f' ELSE 'tmin_f' END
    )

WHERE pc.status = 'open'
ORDER BY pc.resolution_date, pc.city, pc.strike_low;
