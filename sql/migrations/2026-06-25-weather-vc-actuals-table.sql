-- Migration: add weather_bronze_visual_crossing_actuals
-- 2026-06-25
--
-- Visual Crossing historical actuals need their own bronze table.
-- The existing weather_bronze_nws_actuals has UNIQUE (station_code, target_date),
-- which would block NWS CLI inserts for any date already backfilled by VC.
-- Separate tables lets both sources coexist cleanly; silver ties them together
-- via the actual_source discriminator.
--
-- Run on LA (<BHN_WG_LA_IP>):
--   sudo -u postgres psql eventhorizon -f sql/migrations/2026-06-25-weather-vc-actuals-table.sql

CREATE TABLE IF NOT EXISTS weather_bronze_visual_crossing_actuals (
    id                  BIGSERIAL       PRIMARY KEY,
    city                TEXT            NOT NULL,           -- human name e.g. 'Miami'
    station_code        TEXT            NOT NULL,           -- ICAO e.g. 'KMIA'
    vc_querylocation    TEXT,                               -- location string sent to VC API
    target_date         DATE            NOT NULL,
    final_tmax_f        NUMERIC,
    final_tmin_f        NUMERIC,
    precip_in           NUMERIC,        -- daily precipitation total (inches)
    humidity_pct        NUMERIC,        -- mean relative humidity (%)
    source_payload_json JSONB,          -- raw VC day object
    retrieved_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT weather_bronze_vc_actuals_unique
        UNIQUE (station_code, target_date)
);

CREATE INDEX IF NOT EXISTS brvc_station_date_idx
    ON weather_bronze_visual_crossing_actuals (station_code, target_date DESC);

CREATE INDEX IF NOT EXISTS brvc_retrieved_idx
    ON weather_bronze_visual_crossing_actuals (retrieved_at DESC);

-- Permissions
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader') THEN
        GRANT SELECT ON weather_bronze_visual_crossing_actuals TO grafana_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_reader') THEN
        GRANT SELECT ON weather_bronze_visual_crossing_actuals TO agent_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'horizon_agent_reader') THEN
        GRANT SELECT ON weather_bronze_visual_crossing_actuals TO horizon_agent_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'n8n_user') THEN
        GRANT SELECT ON weather_bronze_visual_crossing_actuals TO n8n_user;
    END IF;
END $$;
