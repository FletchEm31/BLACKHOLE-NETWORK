-- Extends 2026-07-17-add-entry-time-edge-columns.sql /
-- 2026-07-17b-add-entry-tmax-and-lead-time-columns.sql to the fifth column
-- confirmed to share the same live-drift bug (in exit_audit_logger.py's
-- ON CONFLICT DO UPDATE SET, same as edge_cents/model_prob_no_cents/
-- predicted_tmax_f/hours_to_settle were):
--   - sigma_used  (line ~97 of exit_audit_logger.py before this change)
--
-- sigma_used is calculate_time_decayed_sigma()'s output (cp4_kelly_sizer.py
-- line ~314): it mechanically compresses toward zero as hours_remaining
-- shrinks toward settlement (sqrt(hours_remaining/24), floored at 20% of
-- base_sigma) -- the same shape of bug as hours_to_settle, fixed in
-- 2026-07-17b. The stored value at scoring/exit time reflects sigma near
-- settlement, not the uncertainty actually priced in at entry.
--
-- A same-night manual reconstruction (entry-time predicted_tmax_f +
-- sigma_used, log-derived, 73/87 coverage -- see
-- infrastructure/docs/WeatherBHN/WEATHERBHN-SIGMA-ZONE-ANALYSIS-2026-07-17.md)
-- found one zone, -2sigma to -1sigma (n=8), reproduced identically across
-- two independent computations at +36.6% ROI -- the one backtest finding
-- that survived entry-time correction while every other drifted-column
-- finding tonight evaporated or inverted. That doc is an ad-hoc read-only
-- report; this migration makes the same reconstruction a permanent,
-- re-checkable column instead.
--
-- Named "d", not "c": 2026-07-17c was already used as a deploy label for
-- the daily-bucket-cap change (code-only, no migration file -- see
-- cp4_kelly_sizer.py's DAILY_BUCKET_CAP comment).
--
-- entry_sigma_used: frozen at first qualification, same pattern as
-- entry_edge_cents/entry_predicted_tmax_f. Backfilled via the same log-
-- reconstruction method used for entry_predicted_tmax_f (see
-- scripts/weather/backfill_entry_sigma_2026_07_17.py) -- approximate,
-- ~84% expected coverage on existing rows, exact going forward.
--
-- sigma_used itself is NOT removed or modified -- remains legitimate as
-- "current live state of an open position" for monitoring.

ALTER TABLE weather_position_exits
    ADD COLUMN IF NOT EXISTS entry_sigma_used NUMERIC(6,4);

COMMENT ON COLUMN weather_position_exits.entry_sigma_used IS
    'sigma_used frozen at first qualification (same moment as entry_captured_at/entry_no_ask_cents/entry_edge_cents). NOT touched by the ON CONFLICT DO UPDATE SET in exit_audit_logger.py -- unlike sigma_used, which is live-refreshed every cycle a signal keeps re-qualifying and mechanically decays toward settlement via calculate_time_decayed_sigma(). Added 2026-07-17d after a same-night backtest found the -2sigma to -1sigma zone (n=8, entry-time reconstructed) reproduced identically across two independent computations at +36.6% ROI -- see WEATHERBHN-SIGMA-ZONE-ANALYSIS-2026-07-17.md. Use this column, not sigma_used, for any entry-time analysis or backtest.';

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
    weather_position_exits.entry_sigma_used AS final_entry_sigma_used
   FROM weather_position_exits;

COMMENT ON VIEW weather_position_exits_clean IS
    'Dashboard-facing view over weather_position_exits. "final_" columns are the trustworthy version to chart -- final_entry_* columns are frozen at entry (entry_edge_cents/entry_model_prob_no_cents added 2026-07-17, entry_predicted_tmax_f/entry_hours_to_settle added 2026-07-17b, entry_sigma_used added 2026-07-17d); the un-prefixed edge_cents/model_prob_no_cents/predicted_tmax_f/hours_to_settle/sigma_used are all live-refreshed and reflect the latest cycle, not entry conditions -- do not use them for entry-time/backtest analysis.';
