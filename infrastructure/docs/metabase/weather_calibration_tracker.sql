-- Calibration progress tracker — counts forecast/observation pairs per city
-- Paste as Native Query in Metabase
-- Need 30+ paired days before strategy goes live (target: July 10, 2026)
-- Calibration started: June 10, 2026

SELECT
    wf.station_code,
    wf.variable,

    COUNT(DISTINCT wf.target_date)              AS forecast_days,
    COUNT(DISTINCT wo.observed_at::date)        AS observation_days,

    -- Paired days = dates where both forecast AND observation exist
    COUNT(DISTINCT CASE
        WHEN wo.observed_at::date IS NOT NULL THEN wf.target_date
    END)                                        AS paired_days,

    30                                          AS days_needed,
    GREATEST(0, 30 - COUNT(DISTINCT CASE
        WHEN wo.observed_at::date IS NOT NULL THEN wf.target_date
    END))                                       AS days_remaining,

    ROUND(
        COUNT(DISTINCT CASE
            WHEN wo.observed_at::date IS NOT NULL THEN wf.target_date
        END) / 30.0 * 100
    , 1)                                        AS pct_complete,

    MIN(wf.target_date)                         AS earliest_forecast,
    MAX(wf.target_date)                         AS latest_forecast

FROM weather_forecasts wf

-- Match observations by station + variable + date
LEFT JOIN weather_observations wo
    ON wo.station_code   = wf.station_code
    AND wo.variable      = wf.variable
    AND wo.observed_at::date = wf.target_date

WHERE wf.source_model = 'nws_gridpoints'

GROUP BY wf.station_code, wf.variable
ORDER BY wf.station_code, wf.variable;
