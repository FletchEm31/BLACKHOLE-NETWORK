-- NWS forecast accuracy — how many degrees off was NWS vs actual observed temp
-- Paste as Native Query in Metabase
--
-- forecast_error_f = observed_value - predicted_value
--   Negative = NWS ran COLD (predicted too low), Positive = NWS ran HOT
-- Only shows rows where both a forecast AND an observation exist for the same date
-- Useful for detecting systematic bias before calibration is complete

SELECT
    wf.station_code,
    wf.variable,
    wf.target_date,
    wf.lead_time_hours,
    wf.predicted_value                          AS nws_forecast_f,
    wo.observed_value                           AS actual_observed_f,
    ROUND(CAST(wo.observed_value - wf.predicted_value AS numeric), 2)
                                                AS forecast_error_f,
    wf.predicted_at,
    wo.observed_at

FROM weather_forecasts wf
JOIN weather_observations wo
    ON wo.station_code      = wf.station_code
    AND wo.variable         = wf.variable
    AND wo.observed_at::date = wf.target_date
    AND wo.source           = 'asos'

WHERE wf.source_model = 'nws_gridpoints'
  AND wf.variable IN ('tmax_f', 'tmin_f')

ORDER BY wf.target_date DESC, wf.station_code, wf.variable, wf.lead_time_hours;
