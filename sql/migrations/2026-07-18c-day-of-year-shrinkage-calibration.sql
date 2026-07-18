-- 2026-07-18c-day-of-year-shrinkage-calibration.sql
--
-- New table for a day-of-year (not season-bucket) sigma estimate, blended
-- via empirical-Bayes / James-Stein-style precision-weighted shrinkage
-- between:
--   - the "sample" component: real NWS forecast-error RMSE from
--     weather_silver_forecast_error, windowed +-window_days (circular)
--     around each day-of-year. Currently thin (~6 weeks of history, since
--     the forecast-snapshot archive only started 2026-06-11) -- this is
--     the actual quantity we want (forecast uncertainty), but not enough
--     samples yet to trust alone for a narrow day-of-year window.
--   - the "prior" component: climatological day-to-day stddev of actual
--     tmax_f from weather_bronze_noaa_daily_actuals, same day-of-year
--     window, but using up to 80 years of history (KLAX back to 1944,
--     KMIA/KORD/KNYC(via its own icao_code, NOT the longer KJFK series --
--     see note below) to 1946-1948). This is a different quantity
--     (climate variability, not forecast error) but is a defensible upper
--     bound: a forecast with zero skill (always predicts the
--     climatological mean) has error variance exactly equal to climate
--     variance, so shrinking toward it is conservative, not arbitrary.
--
-- Blend: blended_var = (prior_pseudo_n * prior_stddev^2 + sample_size *
-- sample_rmse^2) / (prior_pseudo_n + sample_size), blended_sigma =
-- sqrt(blended_var). prior_pseudo_n = 30, matching the existing
-- MIN_SAMPLE_SIZE convention in scripts/trading/weather_calibration.py
-- (the never-implemented stub) -- at sample_size=30 the two components
-- get roughly equal weight; as real forecast-error data accumulates week
-- over week, the blend naturally shifts from climatology-anchored toward
-- the empirical forecast-error estimate, with zero manual retuning needed.
--
-- rmse formula matches the live weather_calibration_build.py exactly:
-- sqrt(AVG(forecast_error_f^2)) -- raw RMS, not de-meaned (bias is a
-- separate, already-tracked quantity, mean_bias).
--
-- KNYC note: weather_bronze_noaa_daily_actuals has BOTH 'KNYC' (2020-
-- present, ~2385 rows) and 'KJFK' (1948-present, ~28465 rows) as distinct
-- icao_code values. Per the known KNYC/JFK settlement-station mixup found
-- during 2026-07-15 CP3 retrain scoping (NOAA actuals mislabeled 'KNYC'
-- were really JFK, not Kalshi's real Central Park settlement station),
-- this table's 'KNYC' rows are used for KNYC's prior, NOT the much-longer
-- 'KJFK' series -- shorter history, but the right station. Do not silently
-- swap in KJFK for more years; that reintroduces the exact bug already
-- found and fixed.
--
-- Deliberately NOT wired into cp4_kelly_sizer.py or main.py in this
-- migration -- schema + computation only, per operator decision to check
-- in separately before touching the live sigma lookup (cp4_kelly_sizer.py
-- has had other same-night uncommitted work from a parallel session).
--
-- Run on LA:
--   psql -U postgres eventhorizon -f sql/migrations/2026-07-18c-day-of-year-shrinkage-calibration.sql

\set ON_ERROR_STOP on

BEGIN;

CREATE TABLE IF NOT EXISTS weather_model_calibration_daily (
    station_code      TEXT    NOT NULL,
    variable          TEXT    NOT NULL,
    day_of_year       INTEGER NOT NULL CHECK (day_of_year BETWEEN 1 AND 366),
    lead_time_hours   INTEGER NOT NULL,
    source_model      TEXT    NOT NULL,
    window_days       INTEGER NOT NULL,

    -- Sample component (real forecast-error, thin/recent)
    sample_size       INTEGER NOT NULL,
    sample_mean_bias  NUMERIC,
    sample_rmse       NUMERIC,

    -- Prior component (climatological, deep/stable)
    prior_n           INTEGER NOT NULL,
    prior_mean_tmax_f NUMERIC,
    prior_stddev      NUMERIC,

    -- Blended output (what CP4 would actually consume)
    prior_pseudo_n       INTEGER NOT NULL,
    blend_weight_sample   NUMERIC NOT NULL,
    blended_sigma         NUMERIC NOT NULL,

    computed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (station_code, variable, day_of_year, lead_time_hours, source_model)
);

COMMENT ON TABLE weather_model_calibration_daily IS
    'Day-of-year (not season-bucket) sigma estimate, shrinkage-blended between real-but-thin NWS forecast-error RMSE (weather_silver_forecast_error, ~6wk history) and a deep climatological stddev prior (weather_bronze_noaa_daily_actuals, up to 80yr history). Not yet wired into any live trading logic -- schema + computation only (2026-07-18c). See migration file header for the full blending rationale and the KNYC/KJFK station-identity caveat.';

CREATE INDEX IF NOT EXISTS wmcd_lookup_idx
    ON weather_model_calibration_daily (station_code, variable, day_of_year, lead_time_hours, source_model);

GRANT SELECT ON weather_model_calibration_daily TO grafana_reader;
GRANT SELECT, INSERT, UPDATE ON weather_model_calibration_daily TO bhn_trader, ehuser;

COMMIT;
