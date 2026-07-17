-- WeatherBHN Trading Dashboard — per-city scratch notepad.
--
-- Freeform notes, separate from weatherbhn_dashboard_journal (structured
-- trade logging). One overwritable note per station -- simplest thing that
-- persists across page refresh and city switches, chosen over a
-- timestamped running log for build speed since the operator asked for
-- "quick notes on the fly," not a log. Not wired into any trading logic.
--
-- Run on LA:
--   psql -U postgres eventhorizon -f services/weatherbhn-dashboard/sql/weatherbhn-dashboard-notes-schema.sql

\set ON_ERROR_STOP on

BEGIN;

CREATE TABLE IF NOT EXISTS weatherbhn_dashboard_notes (
    station_code TEXT PRIMARY KEY,
    note_text    TEXT NOT NULL DEFAULT '',
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE weatherbhn_dashboard_notes IS
    'Freeform per-city scratch notepad for the WeatherBHN dashboard -- one overwritable note per station_code. Separate from weatherbhn_dashboard_journal (structured trade log). Never written by any trading pipeline script.';

GRANT SELECT, INSERT, UPDATE ON weatherbhn_dashboard_notes TO weatherbhn_dashboard;

COMMIT;
