-- 2026-07-17e-create-station-climatology-and-entry-hours-to-avg-dailyhigh.sql
--
-- Two additions, same night as entry_sigma_used (2026-07-17d), both aimed at
-- making the "trade after the day's high has likely already occurred"
-- hypothesis from last night's investigation permanently backtestable:
--
-- 1. weather_station_climatology — new small reference table (one row per
--    station/month, 96 rows max at full 8-city scope). Stores the
--    climatological average daily-high time-of-day, both as a UTC clock
--    time (canonical, cross-city-comparable) and as that station's own
--    civil local clock time (DST-aware — see note below). This is
--    station-level climatology, not per-trade data, so it lives in its own
--    table rather than being duplicated across every weather_position_exits
--    row (duplicating a constant across a live trading history table is
--    its own update/consistency risk).
--
-- 2. entry_hours_to_avg_dailyhigh on weather_position_exits — frozen at
--    first qualification, same pattern as entry_sigma_used/
--    entry_predicted_tmax_f (added 2026-07-17d/2026-07-17b): computed once
--    by joining weather_station_climatology on (station_code,
--    EXTRACT(MONTH FROM target_date)), excluded from the ON CONFLICT DO
--    UPDATE SET in exit_audit_logger.py's _RECORD_SQL. Sign convention:
--    positive = trade placed BEFORE the climatological average daily-high
--    instant (more uncertainty remaining); negative = placed AFTER (closer
--    to "today's high has likely already happened").
--
-- ── Hour-convention note (verified empirically 2026-07-17, not assumed) ──
-- weather_bronze_noaa_hourly_normals' "hour" column is Local Standard Time
-- (LST) — i.e. the station's fixed, non-DST-observing UTC offset, applied
-- year-round regardless of season. Confirmed by comparing KJFK (Eastern)
-- and KLAX (Pacific) July peak hours: both cluster at hour 12-13 despite a
-- real 3-hour UTC offset difference between the two stations — if the
-- column were UTC, their peak hours would differ by ~3; if it were civil
-- (DST-aware) local time, July peaks would show ~13-14 given typical
-- afternoon heating; LST at hour 12-13 is exactly consistent with a true
-- civil (EDT/PDT) peak of ~13-14 read back one hour earlier under standard
-- time, which is what LST does by definition. See build_station_
-- climatology_2026_07_17.py for the full conversion (LST → fixed-offset
-- UTC → DST-aware civil local via a mid-month reference date and IANA
-- zoneinfo, so March/November DST-transition months resolve correctly).
--
-- ── KPHX correction ──
-- KPHX (Phoenix) does NOT observe DST (permanent Mountain Standard Time,
-- IANA zone America/Phoenix) — it is NOT grouped with Pacific stations.
-- Using each station's real IANA zone (not a manual city-to-region
-- grouping) makes zoneinfo apply the correct DST rule automatically,
-- Arizona included, with no special-casing needed in the conversion code.
--
-- ── KDEN proxy note ──
-- weather_bronze_noaa_hourly_normals has no exact KDEN (Denver Intl)
-- station. The nearest loaded station is KAFF (Buckley AFB, ~10mi away) —
-- load-noaa-actuals.py's own HOURLY_ICAO_MAP comment already warns KAFF is
-- NOT KDEN. Per operator decision 2026-07-17: use KAFF as an explicit,
-- labeled proxy — weather_station_climatology.source_station_note flags
-- this so it is never silently mistaken for exact Denver Intl data.
--
-- Run on LA:
--   psql -U postgres eventhorizon -f sql/migrations/2026-07-17e-create-station-climatology-and-entry-hours-to-avg-dailyhigh.sql

\set ON_ERROR_STOP on

BEGIN;

-- ── 1. weather_station_climatology ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_station_climatology (
    station_code                  TEXT    NOT NULL,
    month                          INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    average_dailyhigh_hour_lst    INTEGER NOT NULL CHECK (average_dailyhigh_hour_lst BETWEEN 0 AND 23),
    average_dailyhigh_time_utc    TIME    NOT NULL,
    average_dailyhigh_time_local  TIME    NOT NULL,
    local_timezone                TEXT    NOT NULL,
    source_station_icao           TEXT    NOT NULL,
    source_station_note           TEXT,
    source_normal_period           TEXT,
    computed_at                    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (station_code, month)
);

COMMENT ON TABLE weather_station_climatology IS
    'Station-level (not per-trade) reference: climatological average daily-high time-of-day, by month. Built from weather_bronze_noaa_hourly_normals argmax(temp_normal_f) per hour, LST convention (verified empirically, see migration file header). average_dailyhigh_time_utc is canonical/cross-city-comparable; average_dailyhigh_time_local is that station''s DST-aware civil clock time (resolved via a mid-month reference date, so March/November transition months use the correct side of the DST boundary). source_station_note flags any station using a proxy (non-exact) source, e.g. KDEN using KAFF/Buckley AFB.';

GRANT SELECT ON weather_station_climatology TO grafana_reader;
GRANT SELECT, INSERT, UPDATE ON weather_station_climatology TO bhn_trader, ehuser;

-- ── 2. entry_hours_to_avg_dailyhigh on weather_position_exits ─────────────
ALTER TABLE weather_position_exits
    ADD COLUMN IF NOT EXISTS entry_hours_to_avg_dailyhigh NUMERIC(7,3);

COMMENT ON COLUMN weather_position_exits.entry_hours_to_avg_dailyhigh IS
    'Hours between entry_captured_at and that station/month''s climatological average-daily-high UTC instant (weather_station_climatology.average_dailyhigh_time_utc, on target_date). Frozen at first qualification -- same pattern as entry_sigma_used/entry_predicted_tmax_f, NOT touched by the ON CONFLICT DO UPDATE SET in exit_audit_logger.py. Sign: positive = entry BEFORE the climatological peak instant (more uncertainty remaining); negative = entry AFTER (closer to "today''s high has likely already occurred"). Added 2026-07-17e to make that hypothesis backtestable -- see WEATHERBHN-SIGMA-ZONE-ANALYSIS-2026-07-17.md for the investigation this followed from.';

-- ── 3. weather_position_exits_clean: expose the new column ────────────────
CREATE OR REPLACE VIEW weather_position_exits_clean AS
 SELECT weather_position_exits.id,
    weather_position_exits.station_code,
    weather_position_exits.target_date,
    weather_position_exits.contract_ticker,
    weather_position_exits.bucket_label,
    weather_position_exits.bucket_floor,
    weather_position_exits.bucket_cap,
    weather_position_exits.decision_timestamp,
    weather_position_exits.predicted_tmax_f,
    weather_position_exits.model_prob_no_cents,
    weather_position_exits.no_ask_cents,
    weather_position_exits.edge_cents,
    COALESCE(weather_position_exits.corrected_contracts_recommended, weather_position_exits.contracts_recommended) AS final_contracts_recommended,
    COALESCE(weather_position_exits.corrected_stake_usd_recommended, weather_position_exits.stake_usd_recommended) AS final_stake_usd_recommended,
    weather_position_exits.hours_to_settle,
    weather_position_exits.sigma_used,
    weather_position_exits.is_paper_trade,
    COALESCE(weather_position_exits.corrected_actual_tmax_f, weather_position_exits.actual_tmax_f) AS final_actual_tmax_f,
    COALESCE(weather_position_exits.corrected_actual_outcome, weather_position_exits.actual_outcome::text) AS final_outcome,
    COALESCE(weather_position_exits.corrected_realized_pnl_usd, weather_position_exits.realized_pnl_usd) AS final_realized_pnl_usd,
    weather_position_exits.scored_at,
    weather_position_exits.created_at,
    weather_position_exits.entry_no_ask_cents,
    weather_position_exits.entry_captured_at,
    weather_position_exits.real_market_ticker,
    weather_position_exits.target_date_before_fix,
    weather_position_exits.entry_edge_cents AS final_entry_edge_cents,
    weather_position_exits.entry_model_prob_no_cents AS final_entry_model_prob_no_cents,
    weather_position_exits.entry_predicted_tmax_f AS final_entry_predicted_tmax_f,
    weather_position_exits.entry_hours_to_settle AS final_entry_hours_to_settle,
    weather_position_exits.entry_sigma_used AS final_entry_sigma_used,
    weather_position_exits.entry_hours_to_avg_dailyhigh AS final_entry_hours_to_avg_dailyhigh
   FROM weather_position_exits;

COMMENT ON VIEW weather_position_exits_clean IS
    'Dashboard-facing view over weather_position_exits. "final_" columns are the trustworthy version to chart -- final_entry_* columns are frozen at entry (entry_edge_cents/entry_model_prob_no_cents added 2026-07-17, entry_predicted_tmax_f/entry_hours_to_settle added 2026-07-17b, entry_sigma_used added 2026-07-17d, entry_hours_to_avg_dailyhigh added 2026-07-17e); the un-prefixed edge_cents/model_prob_no_cents/predicted_tmax_f/hours_to_settle/sigma_used are all live-refreshed and reflect the latest cycle, not entry conditions -- do not use them for entry-time/backtest analysis.';

GRANT SELECT ON weather_position_exits_clean TO grafana_reader, ehuser, bhn_trader;

COMMIT;
