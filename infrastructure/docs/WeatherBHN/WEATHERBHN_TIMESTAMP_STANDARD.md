# WeatherBHN Timestamp Standardization
## Version 1.0 — June 12, 2026
## Status: ACTIVE — apply to all WeatherBHN queries and tables

---

## OVERVIEW

All WeatherBHN timestamps follow a strict naming convention to ensure
clarity across Bronze, Silver, and Gold pipeline layers, Metabase
queries, and automated trading logic.

Format: [vocab]_time_[zone]

Every timestamp in every query must be displayed in THREE columns:
  1. [vocab]_time_utc  — raw UTC timestamp (server time)
  2. [vocab]_time_pt   — Pacific time (operator local time)
  3. mins_ago          — integer minutes between now and that timestamp

---

## TIMEZONE CONVERSION STANDARD

UTC → Pacific:
  [column] AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'

Note: America/Los_Angeles automatically handles PST (UTC-8) and
PDT (UTC-7) daylight saving transitions. Never hardcode an offset.

mins_ago calculation:
  ROUND(EXTRACT(EPOCH FROM (NOW() - [column])) / 60)

Negative mins_ago means the timestamp is in the future relative to
NOW() — this indicates a timezone misconfiguration or clock drift
on the server. Flag as a bug if seen consistently.

---

## TIMESTAMP VOCABULARY

### retrieved
Definition: The moment raw data physically arrived and was written
            into a Bronze table from an external API or source.
            This is the timestamp of first contact with the data.
Layer:      Bronze
Tables:     weather_bronze_nws_forecast_snapshots
            weather_bronze_openmeteo_forecast_snapshots
            weather_bronze_kalshi_market_snapshots
            weather_bronze_nws_actuals
Column:     retrieved_at (source) →
            retrieved_time_utc / retrieved_time_pt / mins_ago (display)
Example:    NWS API responded at 23:54 UTC — retrieved_time_utc = 23:54 UTC
                                           — retrieved_time_pt  = 16:54 PT

---

### processed
Definition: The moment Silver cleaned, standardized, validated, and
            conformed raw Bronze data into the analytical layer.
            Marks when data became trustworthy for model consumption.
Layer:      Silver
Tables:     weather_silver_forecast_conformed
            weather_silver_market_conformed
            weather_silver_actuals_conformed
            weather_silver_forecast_error
            weather_silver_calibration_training_set
            weather_silver_model_base
Column:     forecast_run_time / snapshot_time / report_issued_at (source) →
            processed_time_utc / processed_time_pt / mins_ago (display)
Example:    Silver conformation job ran at 00:01 UTC — processed_time_utc = 00:01 UTC
                                                      — processed_time_pt  = 17:01 PT

---

### calculated
Definition: The moment the BHN edge calculator ran and wrote model
            outputs (calibrated probabilities, edge, Kelly stakes,
            recommended actions) into a Gold table.
            This is when BHN made a trading recommendation.
Layer:      Gold
Tables:     weather_gold_daily_edge_sheet
            weather_gold_calibrated_probabilities
            weather_gold_city_day_features
Column:     last_updated (source) →
            calculated_time_utc / calculated_time_pt / mins_ago (display)
Example:    Edge calc ran at 00:05 UTC — calculated_time_utc = 00:05 UTC
                                       — calculated_time_pt  = 17:05 PT

---

### snapshot
Definition: The moment a Kalshi market price was captured by the
            price poller. Represents the state of the market at
            that exact instant. Multiple snapshots exist per day
            as prices move continuously.
Layer:      Bronze (Kalshi specific)
Tables:     weather_bronze_kalshi_market_snapshots
            weather_contract_prices
Column:     retrieved_at / captured_at (source) →
            snapshot_time_utc / snapshot_time_pt / mins_ago (display)
Example:    Poller hit Kalshi API at 23:54:32 UTC — snapshot_time_utc = 23:54:32 UTC
                                                   — snapshot_time_pt  = 16:54:32 PT
Note:       Price poller runs every 5 seconds. snapshot_time_utc will
            differ from retrieved_time_utc by milliseconds only.

---

### settled
Definition: The moment the NWS published the official Daily Climate
            Report (CLI) for a target date. This is the moment
            Kalshi contracts become eligible for settlement.
            The settled timestamp determines which CLI report
            version is used for contract resolution.
Layer:      Bronze + Silver (actuals specific)
Tables:     weather_bronze_nws_actuals
            weather_silver_actuals_conformed
Column:     report_issued_at (source) →
            settled_time_utc / settled_time_pt / mins_ago (display)
Example:    NWS published Jun 12 CLI at 03:28 UTC Jun 13 →
            settled_time_utc = 2026-06-13 03:28:00 UTC
            settled_time_pt  = 2026-06-12 20:28:00 PT
Note:       NWS typically publishes CLI between 3-8 AM UTC the
            following day. Kalshi settles contracts after this
            report is available.

---

### observed
Definition: The moment a weather observation (temperature, precip,
            wind) was physically recorded at the station.
            Distinct from settled — the observation happens
            throughout the day, settlement happens after the
            CLI report is published.
Layer:      Supporting
Tables:     weather_observations
Column:     observed_at (source) →
            observed_time_utc / observed_time_pt / mins_ago (display)
Example:    KMIA recorded 90F at 13:49 local time →
            observed_time_utc = 2026-06-12 17:49:00 UTC
            observed_time_pt  = 2026-06-12 10:49:00 PT

---

### forecast
Definition: The moment the NWS issued a forecast for a future
            target date. The forecast_run_time represents when
            NWS ran their model, not when BHN retrieved it.
            Lead time is calculated from forecast_run_time to
            target_date.
Layer:      Bronze + Silver (forecast specific)
Tables:     weather_bronze_nws_forecast_snapshots
            weather_bronze_openmeteo_forecast_snapshots
            weather_silver_forecast_conformed
Column:     forecast_run_time (source) →
            forecast_time_utc / forecast_time_pt / mins_ago (display)
Example:    NWS issued 5-day forecast at 12:00 UTC →
            forecast_time_utc = 2026-06-12 12:00:00 UTC
            forecast_time_pt  = 2026-06-12 05:00:00 PT

---

### reconciled
Definition: The moment BHN matched a Gold recommendation against
            an actual NWS settlement outcome and wrote the result
            to weather_model_accuracy. Marks when a trade signal
            was graded as correct or incorrect.
Layer:      Gold (accuracy tracking)
Tables:     weather_model_accuracy
Column:     resolved_at (source) →
            reconciled_time_utc / reconciled_time_pt / mins_ago (display)
Example:    Settlement reconciler ran at 04:00 UTC →
            reconciled_time_utc = 2026-06-13 04:00:00 UTC
            reconciled_time_pt  = 2026-06-12 21:00:00 PT

---

## QUERY DISPLAY TEMPLATE

Use this pattern in every Metabase query for each timestamp column:

-- UTC (raw)
[source_column]                                                    AS [vocab]_time_utc,

-- Pacific time (operator display)
[source_column] AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'
                                                                   AS [vocab]_time_pt,

-- Minutes since now (freshness check)
ROUND(EXTRACT(EPOCH FROM (NOW() - [source_column])) / 60)          AS mins_ago

---

## FRESHNESS THRESHOLDS BY VOCAB

| Vocab | Green (Fresh) | Yellow (Stale) | Red (Dead) |
|-------|--------------|----------------|------------|
| snapshot | < 1 min | 1-5 min | > 5 min |
| retrieved | < 35 min | 35-70 min | > 70 min |
| calculated | < 6 min | 6-15 min | > 15 min |
| processed | < 35 min | 35-70 min | > 70 min |
| forecast | < 35 min | 35-120 min | > 120 min |
| settled | < 60 min | 60-240 min | > 240 min |
| observed | < 60 min | 60-240 min | > 240 min |
| reconciled | < 24 hrs | 24-48 hrs | > 48 hrs |

---

## FULL COLUMN REFERENCE BY TABLE

| Table | Source Column | Display Vocab |
|-------|--------------|---------------|
| weather_bronze_nws_forecast_snapshots | retrieved_at | retrieved |
| weather_bronze_openmeteo_forecast_snapshots | retrieved_at | retrieved |
| weather_bronze_kalshi_market_snapshots | retrieved_at | snapshot |
| weather_bronze_nws_actuals | retrieved_at | retrieved |
| weather_silver_forecast_conformed | forecast_run_time | forecast |
| weather_silver_market_conformed | snapshot_time | snapshot |
| weather_silver_actuals_conformed | report_issued_at | settled |
| weather_silver_forecast_error | forecast_run_time | forecast |
| weather_silver_calibration_training_set | target_date | forecast |
| weather_silver_model_base | forecast_run_time | forecast |
| weather_gold_daily_edge_sheet | last_updated | calculated |
| weather_gold_calibrated_probabilities | target_date | calculated |
| weather_gold_city_day_features | target_date | calculated |
| weather_observations | observed_at | observed |
| weather_model_accuracy | resolved_at | reconciled |
| weather_contract_prices | captured_at | snapshot |
| weather_forecasts | predicted_at | forecast |
| weather_kalshi_contract_catalog | last_seen_at | snapshot |
| weather_snapshots | fetched_at | retrieved |

---

## IMPLEMENTATION NOTES FOR CC

1. Apply this standard to all 18 queries in CLEAN_QUERIES.sql
2. Apply to all individual .sql files in WeatherBHN/queries/
3. Do NOT rename source columns in the DB tables — only rename
   in the SELECT output using AS
4. Always show all three display columns together as a group
5. Order: utc first, pt second, mins_ago third
6. If a table has multiple timestamps, show all three for each
7. mins_ago should always be an INTEGER (use ROUND())
8. Negative mins_ago = bug — flag in the query with a CASE statement

---

## NEGATIVE MINS_AGO BUG FLAG

Add this to any query where negative mins_ago has been observed:

CASE
    WHEN ROUND(EXTRACT(EPOCH FROM (NOW() - [column])) / 60) < 0
    THEN '⚠️ FUTURE TIMESTAMP — Clock/TZ bug'
    ELSE ROUND(EXTRACT(EPOCH FROM (NOW() - [column])) / 60)::text
END AS mins_ago

Known affected tables (June 12, 2026):
  weather_observations              → observed_at showing -1,086 mins
  weather_gold_calibrated_probs     → showing -1,087 mins
  Cause: rows inserted with future timestamps — investigate insert logic

---

*Save to: infrastructure/docs/WeatherBHN/WEATHERBHN_TIMESTAMP_STANDARD.md*
*Apply to: all CLEAN_QUERIES.sql queries + WeatherBHN/queries/*.sql*
*Owner: operator*
*Version: 1.0 — June 12, 2026*
