-- Migration: 2026-06-13-nbm-and-ensemble-probs
-- Adds per-member ensemble storage, NWS NBM bronze table, and
-- nws_high_prob_pct + gfs_high_prob_pct to the gold edge sheet.
--
-- Apply:
--   sudo -u postgres psql -d eventhorizon -P pager=off \
--     -f sql/migrations/2026-06-13-nbm-and-ensemble-probs.sql
--
-- Rollback:
--   ALTER TABLE weather_bronze_openmeteo_forecast_snapshots DROP COLUMN IF EXISTS member_highs_json;
--   ALTER TABLE weather_gold_daily_edge_sheet DROP COLUMN IF EXISTS nws_high_prob_pct, DROP COLUMN IF EXISTS gfs_high_prob_pct;
--   DROP TABLE IF EXISTS weather_bronze_nbm_snapshots;

BEGIN;

-- ── 1. Store individual ensemble member daily highs on existing bronze rows ──
-- member_highs_json: JSON array of per-member daily max temps [72.1, 73.4, ...]
-- Populated on new rows after this migration; old rows will remain NULL.
ALTER TABLE weather_bronze_openmeteo_forecast_snapshots
    ADD COLUMN IF NOT EXISTS member_highs_json JSONB;

-- ── 2. NWS NBM bronze table — probabilistic temperature percentiles ──
CREATE TABLE IF NOT EXISTS weather_bronze_nbm_snapshots (
    id                  BIGSERIAL PRIMARY KEY,
    retrieved_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    station_code        TEXT NOT NULL,
    city                TEXT NOT NULL,
    nws_office          TEXT NOT NULL,
    forecast_run_time   TIMESTAMPTZ NOT NULL,
    target_date         DATE NOT NULL,
    -- Daily max temperature percentiles (°F)
    -- Derived from max(hourly P{N}) across target_date local hours
    p10_tmax_f          NUMERIC,
    p25_tmax_f          NUMERIC,
    p50_tmax_f          NUMERIC,
    p75_tmax_f          NUMERIC,
    p90_tmax_f          NUMERIC,
    member_count        INTEGER,     -- number of ensemble members used
    source              TEXT NOT NULL DEFAULT 'nws_probabilistic_quantile',
    raw_payload_json    JSONB,

    CONSTRAINT nbm_station_run_date_unique
        UNIQUE (station_code, forecast_run_time, target_date)
);

CREATE INDEX IF NOT EXISTS nbm_station_date_idx
    ON weather_bronze_nbm_snapshots (station_code, target_date DESC);

CREATE INDEX IF NOT EXISTS nbm_retrieved_idx
    ON weather_bronze_nbm_snapshots (retrieved_at DESC);

-- ── 3. New probability columns on gold edge sheet ──
ALTER TABLE weather_gold_daily_edge_sheet
    ADD COLUMN IF NOT EXISTS nws_high_prob_pct NUMERIC,
    ADD COLUMN IF NOT EXISTS gfs_high_prob_pct NUMERIC;

COMMENT ON COLUMN weather_gold_daily_edge_sheet.nws_high_prob_pct IS
    'P(daily high lands in bucket) from NWS NBM temperature percentiles. Piecewise linear CDF. NULL until NBM collector runs and populates weather_bronze_nbm_snapshots.';
COMMENT ON COLUMN weather_gold_daily_edge_sheet.gfs_high_prob_pct IS
    'P(daily high lands in bucket) computed by counting Open-Meteo GFS ensemble members whose daily high falls in [bucket_floor, bucket_cap). NULL until member_highs_json is populated.';

-- ── 4. Grants ──
GRANT SELECT, INSERT ON weather_bronze_nbm_snapshots TO bhn_trader;
GRANT USAGE, SELECT ON SEQUENCE weather_bronze_nbm_snapshots_id_seq TO bhn_trader;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_reader') THEN
        GRANT SELECT ON weather_bronze_nbm_snapshots TO agent_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader') THEN
        GRANT SELECT ON weather_bronze_nbm_snapshots TO grafana_reader;
    END IF;
END $$;

COMMIT;
