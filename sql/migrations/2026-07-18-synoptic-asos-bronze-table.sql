-- Migration: add weather_bronze_synoptic_asos
-- 2026-07-18
--
-- New bronze source: Synoptic Weather API standard station timeseries
-- (api.synopticdata.com/v2/stations/timeseries). Confirmed live via curl
-- 2026-07-18 that the free-trial (non-1M-network) endpoint already blends
-- the HF-METAR subset into normal station queries, giving ~5-minute
-- resolution for KDEN/KLAX/KMIA (and the rest of the 8-city set) with no
-- special network access needed. The dedicated 1-minute "1M" network is
-- separately gated and NOT available on the trial -- this table is built
-- against the working standard endpoint only.
--
-- Natural key (station_code, observed_at) -- observed_at is the UTC
-- timestamp from the API's date_time field, not retrieval time. Upsert
-- with ON CONFLICT DO NOTHING: the collector polls every 5 minutes but
-- requests recent=15 (last 15 minutes of observations), so overlapping
-- pulls self-heal any single missed cycle instead of leaving a silent gap.
--
-- IMPORTANT: SYNOPTIC_API_TOKEN is a 14-day trial token (issued 2026-07-18).
-- It will stop authenticating once the trial expires unless upgraded to a
-- paid plan -- see the warning in weather_data_collector.py::fetch_synoptic()
-- and scripts/weather-collectors/.env.example. If this table silently stops
-- filling in ~2 weeks, check the token first, not the API/network path.
--
-- Run on LA (<BHN_WG_LA_IP>):
--   sudo -u postgres psql eventhorizon -f sql/migrations/2026-07-18-synoptic-asos-bronze-table.sql

CREATE TABLE IF NOT EXISTS weather_bronze_synoptic_asos (
    id                  BIGSERIAL       PRIMARY KEY,
    station_code        TEXT            NOT NULL,           -- ICAO e.g. 'KDEN' (Synoptic STID)
    observed_at          TIMESTAMPTZ     NOT NULL,           -- UTC, from API date_time field
    air_temp_f          NUMERIC,                             -- raw air_temp value (requested in °F)
    source_payload_json JSONB,          -- raw per-observation object for this station/timestamp
    retrieved_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT weather_bronze_synoptic_asos_unique
        UNIQUE (station_code, observed_at)
);

CREATE INDEX IF NOT EXISTS brsyn_station_observed_idx
    ON weather_bronze_synoptic_asos (station_code, observed_at DESC);

CREATE INDEX IF NOT EXISTS brsyn_retrieved_idx
    ON weather_bronze_synoptic_asos (retrieved_at DESC);

-- Permissions -- granted in the same migration as table creation, on
-- purpose: bhn_weather_collector missed grants twice already this cycle
-- (weather_silver_forecast_conformed/actuals_conformed/forecast_error on
-- 2026-07-01, weather_model_calibration_daily separately) and both went
-- unnoticed for days because bronze writes fail silently (logged warning,
-- no crash). Do not split table creation and grants across migrations.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_weather_collector') THEN
        GRANT INSERT, SELECT ON weather_bronze_synoptic_asos TO bhn_weather_collector;
        GRANT USAGE, SELECT ON SEQUENCE weather_bronze_synoptic_asos_id_seq TO bhn_weather_collector;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'horizon_agent_reader') THEN
        GRANT SELECT ON weather_bronze_synoptic_asos TO horizon_agent_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader') THEN
        GRANT SELECT ON weather_bronze_synoptic_asos TO grafana_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_reader') THEN
        GRANT SELECT ON weather_bronze_synoptic_asos TO agent_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'n8n_user') THEN
        GRANT SELECT ON weather_bronze_synoptic_asos TO n8n_user;
    END IF;
END $$;

-- Verification checklist (run after applying, before calling this done):
--   1. \d weather_bronze_synoptic_asos              -- table + unique constraint exist
--   2. SELECT grantee, privilege_type FROM information_schema.role_table_grants
--        WHERE table_name = 'weather_bronze_synoptic_asos';
--      -- must show bhn_weather_collector with INSERT + SELECT at minimum
--   3. python3 weather_data_collector.py --source synoptic --dry-run
--      -- confirms the API token + station parsing work before the timer
--         actually starts writing
--   4. After the systemd timer has fired at least once:
--      SELECT station_code, count(*), max(observed_at)
--        FROM weather_bronze_synoptic_asos GROUP BY station_code;
