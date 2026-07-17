-- Extends 2026-07-17-add-entry-time-edge-columns.sql to the two remaining
-- fields confirmed to share the same live-drift bug (both are in
-- exit_audit_logger.py's ON CONFLICT DO UPDATE SET, same as edge_cents/
-- model_prob_no_cents were):
--   - predicted_tmax_f  (line ~86 of exit_audit_logger.py before this change)
--   - hours_to_settle   (line ~92)
--
-- entry_predicted_tmax_f: frozen at first qualification, same pattern as
-- entry_edge_cents. Backfilled via log reconstruction (see
-- scripts/weather/backfill_entry_tmax_and_leadtime_2026_07_17.py) --
-- approximate, ~84% coverage on existing rows, exact going forward.
--
-- entry_hours_to_settle: NOT reconstructed from the log. Settlement time is
-- a deterministic function of (station_code, target_date) -- see
-- cp4_kelly_sizer.py's _settlement_dt(). entry_hours_to_settle is computed
-- directly as settlement_dt - entry_captured_at, both already frozen/exact,
-- zero reconstruction error, 100% backfill coverage on all 87 existing
-- rows. Going forward, computed the same way at first-qualification time in
-- exit_audit_logger.py using the same _settlement_dt() import cp4_kelly_sizer.py
-- itself uses, to avoid duplicating/drifting the settlement-time rule.

ALTER TABLE weather_position_exits
    ADD COLUMN IF NOT EXISTS entry_predicted_tmax_f NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS entry_hours_to_settle NUMERIC(6,2);

COMMENT ON COLUMN weather_position_exits.entry_predicted_tmax_f IS
    'predicted_tmax_f frozen at first qualification, same pattern as entry_edge_cents. NOT touched by ON CONFLICT DO UPDATE SET. Added 2026-07-17. predicted_tmax_f itself remains live-refreshed and reflects the latest forecast run, not the entry-time one -- do not use it for entry-time/bucket-proximity analysis.';

COMMENT ON COLUMN weather_position_exits.entry_hours_to_settle IS
    'Hours between entry_captured_at and settlement, computed exactly from _settlement_dt(station_code, target_date) -- not log-reconstructed, zero approximation error. hours_to_settle itself is live-refreshed (mechanically decays toward 0 every cycle) and does NOT represent lead time at entry. Added 2026-07-17.';

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
    weather_position_exits.entry_hours_to_settle AS final_entry_hours_to_settle
   FROM weather_position_exits;

COMMENT ON VIEW weather_position_exits_clean IS
    'Dashboard-facing view over weather_position_exits. "final_" columns are the trustworthy version to chart -- final_entry_* columns are frozen at entry (entry_edge_cents/entry_model_prob_no_cents added 2026-07-17, entry_predicted_tmax_f/entry_hours_to_settle added 2026-07-17b); the un-prefixed edge_cents/model_prob_no_cents/predicted_tmax_f/hours_to_settle are all live-refreshed and reflect the latest cycle, not entry conditions -- do not use them for entry-time/backtest analysis.';
