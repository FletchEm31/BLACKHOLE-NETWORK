-- WeatherBHN Trading Dashboard — manual trade journal.
--
-- Standalone, read/write-only-by-the-dashboard table for the operator's
-- informal 0-sigma-bucket Yes-bet track record (per operator request
-- 2026-07-17). NOT wired into any trading logic, CP1-4, or
-- weather_position_exits — purely a scratchpad the dashboard reads/writes.
--
-- Run on LA:
--   psql -U postgres eventhorizon -f services/weatherbhn-dashboard/sql/weatherbhn-dashboard-journal-schema.sql

\set ON_ERROR_STOP on

BEGIN;

CREATE TABLE IF NOT EXISTS weatherbhn_dashboard_journal (
    id              BIGSERIAL PRIMARY KEY,
    station_code    TEXT        NOT NULL,
    target_date     DATE        NOT NULL,
    bucket_label    TEXT        NOT NULL,
    side            TEXT        NOT NULL CHECK (side IN ('yes', 'no')),
    price_cents     NUMERIC(5,2) NOT NULL CHECK (price_cents > 0 AND price_cents < 100),
    investment_usd  NUMERIC(10,2) NOT NULL CHECK (investment_usd > 0),
    contracts       INTEGER      NOT NULL CHECK (contracts >= 0),
    outcome         TEXT         CHECK (outcome IN ('win', 'loss', 'pending')) DEFAULT 'pending',
    pnl_usd         NUMERIC(10,2),
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS journal_station_date_idx
    ON weatherbhn_dashboard_journal (station_code, target_date DESC);

COMMENT ON TABLE weatherbhn_dashboard_journal IS
    'Manual trade journal for the WeatherBHN dashboard — operator-entered rows only, never written by any trading pipeline script. Used to build an informal track record (e.g. daily 0-sigma-bucket Yes-bet simulations) before deciding whether a pattern is worth formalizing into real trading logic.';

-- Dedicated minimal role for the dashboard app (created here if it doesn't
-- already exist — see services/weatherbhn-dashboard/README.md for the
-- password-provisioning step, done manually, never committed to git).
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'weatherbhn_dashboard') THEN
        CREATE ROLE weatherbhn_dashboard LOGIN;
    END IF;
END
$$;

GRANT SELECT, INSERT, UPDATE, DELETE ON weatherbhn_dashboard_journal TO weatherbhn_dashboard;
GRANT USAGE, SELECT ON SEQUENCE weatherbhn_dashboard_journal_id_seq TO weatherbhn_dashboard;

-- Read-only access to everything the dashboard needs to render the ladder,
-- sigma reference strip, and simulation panel. No write access to any
-- trading table.
GRANT SELECT ON weather_bronze_kalshi_market_snapshots TO weatherbhn_dashboard;
GRANT SELECT ON weather_position_exits_clean            TO weatherbhn_dashboard;
GRANT SELECT ON weather_station_climatology              TO weatherbhn_dashboard;
GRANT SELECT ON weather_gold_city_day_features            TO weatherbhn_dashboard;

COMMIT;
