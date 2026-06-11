-- Collector health — data freshness per city and source
-- Paste as Native Query in Metabase
-- Any source with minutes_ago > 120 needs investigation (collectors run every 30 min)

SELECT
    source_label,
    station_code,
    variable,
    MAX(latest_row)                             AS latest_row,
    ROUND(EXTRACT(EPOCH FROM (NOW() - MAX(latest_row))) / 60.0, 1)
                                                AS minutes_ago
FROM (

    -- Weather forecasts (NWS + open-meteo models)
    SELECT
        source_model                            AS source_label,
        station_code,
        variable,
        MAX(predicted_at)                       AS latest_row
    FROM weather_forecasts
    GROUP BY source_model, station_code, variable

    UNION ALL

    -- ASOS observations
    SELECT
        'ASOS observations'                     AS source_label,
        station_code,
        variable,
        MAX(observed_at)                        AS latest_row
    FROM weather_observations
    GROUP BY station_code, variable

    UNION ALL

    -- Kalshi market prices (join on text contract_id)
    SELECT
        'Kalshi prices'                         AS source_label,
        pc.station_code,
        pc.variable,
        MAX(wcp.captured_at)                    AS latest_row
    FROM weather_contract_prices wcp
    JOIN prediction_contracts pc ON pc.contract_id = wcp.contract_id
    GROUP BY pc.station_code, pc.variable

) t
GROUP BY source_label, station_code, variable
ORDER BY minutes_ago DESC, source_label, station_code, variable;
