-- 2026-07-19-add-entry-source-forecast-columns.sql
--
-- Extends the frozen-at-entry pattern (entry_edge_cents/entry_predicted_tmax_f/
-- entry_sigma_used etc., 2026-07-17/17b/d/e) to the individual forecast source
-- inputs, not just the final blended prediction. Confirmed before building
-- (2026-07-19) that only 3 of the 4 originally-proposed columns are genuinely
-- distinct values in the current pipeline:
--   - entry_nws_maxt_f  = cp3_inference.py's raw nws_forecast_f
--   - entry_gfs_maxt_f  = cp3_inference.py's raw om_tmax_f (Open-Meteo GFS-seamless)
--   - entry_model_maxt_f = predicted_tmax_f when mode='xgboost' (the model's own
--     raw output, no further blending applied on top in this codebase); NULL when
--     mode='emergency_fallback' (no real model prediction exists in that path --
--     predicted_tmax_f is a NWS-bias-corrected fallback value instead, never
--     conflated with a model output).
--   - "entry_ensemble_maxt_f" was proposed but NOT built -- it would be a pure
--     duplicate of the already-existing entry_predicted_tmax_f column (same
--     source, same value, no new information).
--
-- Backfill: weather_gold_city_day_features has full om_tmax_f coverage and good
-- (though not complete) nws_tmax_f coverage. For our actual 101 settled trades
-- specifically: 90/101 have both available via (station_code, target_date) join.
-- This is a best-available APPROXIMATION, same caveat as every other entry_*
-- backfill this project has done -- the gold table's snapshot may not exactly
-- match what was live at the specific decision moment, only the closest
-- available reconstruction. entry_model_maxt_f is NOT backfilled at all -- no
-- way to retroactively know which historical trades ran in xgboost vs
-- emergency_fallback mode; left NULL for pre-existing rows rather than guessed,
-- populated correctly going forward via exit_audit_logger.py.
--
-- Run on LA:
--   psql -U postgres eventhorizon -f sql/migrations/2026-07-19-add-entry-source-forecast-columns.sql

\set ON_ERROR_STOP on

BEGIN;

ALTER TABLE weather_position_exits
    ADD COLUMN IF NOT EXISTS entry_nws_maxt_f   NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS entry_gfs_maxt_f    NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS entry_model_maxt_f  NUMERIC(6,2);

COMMENT ON COLUMN weather_position_exits.entry_nws_maxt_f IS
    'Raw NWS forecast max temp at entry (cp3_inference.py nws_forecast_f), frozen at first qualification -- same posture as entry_predicted_tmax_f. Backfilled from weather_gold_city_day_features where available (approximation, not exact reconstruction -- see migration 2026-07-19 header).';
COMMENT ON COLUMN weather_position_exits.entry_gfs_maxt_f IS
    'Raw Open-Meteo/GFS-seamless forecast max temp at entry (cp3_inference.py om_tmax_f), frozen at first qualification. Backfilled from weather_gold_city_day_features (full coverage there).';
COMMENT ON COLUMN weather_position_exits.entry_model_maxt_f IS
    'The XGBoost model''s own raw predicted max temp at entry -- equals entry_predicted_tmax_f whenever mode=xgboost (no further blending exists in this codebase), NULL when mode=emergency_fallback (no real model prediction was made). NOT backfilled for pre-existing rows -- historical mode is not retroactively knowable; only populated going forward.';

-- Backfill entry_nws_maxt_f / entry_gfs_maxt_f from weather_gold_city_day_features.
UPDATE weather_position_exits pe
SET entry_nws_maxt_f = g.nws_tmax_f,
    entry_gfs_maxt_f = g.om_tmax_f
FROM weather_gold_city_day_features g
WHERE g.station_code = pe.station_code
  AND g.target_date = pe.target_date
  AND pe.entry_nws_maxt_f IS NULL
  AND pe.entry_gfs_maxt_f IS NULL;

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
    weather_position_exits.entry_hours_to_avg_dailyhigh AS final_entry_hours_to_avg_dailyhigh,
    weather_position_exits.side,
    weather_position_exits.fee_usd,
    weather_position_exits.entry_nws_maxt_f,
    weather_position_exits.entry_gfs_maxt_f,
    weather_position_exits.entry_model_maxt_f
   FROM weather_position_exits;

COMMENT ON VIEW weather_position_exits_clean IS
    'Dashboard-facing view over weather_position_exits. "final_" columns are the trustworthy version to chart -- final_entry_* columns are frozen at entry; the un-prefixed edge_cents/model_prob_no_cents/predicted_tmax_f/hours_to_settle/sigma_used are all live-refreshed and reflect the latest cycle, not entry conditions -- do not use them for entry-time/backtest analysis. entry_nws_maxt_f/entry_gfs_maxt_f/entry_model_maxt_f added 2026-07-19 -- individual forecast-source inputs at entry, frozen the same way, no live-drifting counterpart exists so no final_ prefix needed (matches entry_no_ask_cents/entry_captured_at''s existing precedent).';

GRANT SELECT ON weather_position_exits_clean TO grafana_reader, ehuser, bhn_trader;

COMMIT;
