-- Migration: extend weather_bronze_synoptic_asos with additional Synoptic vars
-- 2026-07-20
--
-- fetch_synoptic() previously only requested vars=air_temp. Variable names
-- confirmed live against GET /v2/variables and live timeseries calls on
-- 2026-07-20 (do not trust IEM/METAR mnemonics like tmpf/dwpf/relh/drct/sknt
-- -- Synoptic's real names are dew_point_temperature, relative_humidity,
-- wind_direction, wind_speed, etc). First pass adds: dew point, RH, wind
-- speed/direction/gust, station + sea-level pressure, cloud layer 1
-- (sky_condition + height_agl), weather_condition, and 1hr precip.
-- Skipped for this pass: visibility, snow_depth, metar (raw text).
--
-- Units note: units=english converts air_temp/altimeter/visibility but NOT
-- wind -- wind_speed/wind_gust come back in knots and are converted to mph
-- in weather_data_collector.py before insert. precip_accum_one_hour and
-- pressure/sea_level_pressure already come back in inches/mb respectively
-- under units=english, no conversion needed.
--
-- Cadence caveat (confirmed live 2026-07-20, unrelated to this migration but
-- found while confirming var names): KDEN and KNYC only report hourly on
-- this trial token, not every 5 minutes like the other 6 cities. KNYC is
-- Kalshi's real settlement station. Not addressed here -- recent=15 will
-- legitimately return 0 rows for those two most polling cycles.
--
-- source_payload_json is NOT dropped -- still stores the full raw
-- per-observation object alongside the new typed columns.
--
-- Run on the collector host (Hillsboro, per bhn-weather-collector-synoptic.service):
--   sudo -u postgres psql eventhorizon -f sql/migrations/2026-07-20-synoptic-asos-extended-vars.sql

ALTER TABLE weather_bronze_synoptic_asos
    ADD COLUMN IF NOT EXISTS dew_point_f             NUMERIC,
    ADD COLUMN IF NOT EXISTS relative_humidity_pct   NUMERIC,
    ADD COLUMN IF NOT EXISTS wind_speed_mph          NUMERIC,
    ADD COLUMN IF NOT EXISTS wind_direction_deg      NUMERIC,
    ADD COLUMN IF NOT EXISTS wind_gust_mph           NUMERIC,
    ADD COLUMN IF NOT EXISTS pressure_mb             NUMERIC,
    ADD COLUMN IF NOT EXISTS sea_level_pressure_mb   NUMERIC,
    ADD COLUMN IF NOT EXISTS cloud_layer_1_condition TEXT,
    ADD COLUMN IF NOT EXISTS cloud_layer_1_height_ft NUMERIC,
    ADD COLUMN IF NOT EXISTS weather_condition       TEXT,
    ADD COLUMN IF NOT EXISTS precip_1hr_in           NUMERIC;

-- Permissions -- re-asserted here, in the same migration as the schema
-- change, on purpose (not split into a separate migration): the grant-lag
-- bug has bitten this project multiple times already (weather_silver_*
-- 2026-07-01, weather_model_calibration_daily, and the original
-- weather_bronze_synoptic_asos table itself), and ALTER TABLE ADD COLUMN
-- doesn't need new grants, but re-asserting costs nothing and closes the
-- door on this table ever silently losing them.
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
--   1. \d weather_bronze_synoptic_asos                    -- new columns exist
--   2. SELECT grantee, privilege_type FROM information_schema.role_table_grants
--        WHERE table_name = 'weather_bronze_synoptic_asos';
--      -- must still show bhn_weather_collector with INSERT + SELECT
--   3. python3 weather_data_collector.py --source synoptic --dry-run
--      -- confirms the new vars= list + parsing work before the timer writes
--   4. After the systemd timer has fired at least once:
--      SELECT station_code, dew_point_f, wind_speed_mph, pressure_mb,
--             cloud_layer_1_condition, weather_condition, precip_1hr_in
--        FROM weather_bronze_synoptic_asos
--        ORDER BY observed_at DESC LIMIT 20;
--      -- confirm non-NULL values for the always-on fields (dew point, RH,
--         wind, pressure); wind_gust_mph/precip_1hr_in will be NULL most of
--         the time (event-based), that's expected.
