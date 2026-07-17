-- Add real frozen entry-time columns to weather_position_exits.
--
-- Context: edge_cents and model_prob_no_cents are NOT frozen at entry --
-- exit_audit_logger.py's ON CONFLICT DO UPDATE SET overwrites both every
-- cycle a signal keeps re-qualifying (same as no_ask_cents). Only
-- entry_no_ask_cents/entry_captured_at were ever frozen (migration 003,
-- 2026-07-03, fix_entry_price_integrity). Confirmed same night (2026-07-16/17)
-- via a log-derived reconstruction backtest: for 49%+ of the 87 settled
-- trades, the drift between entry-time and latest-stored edge_cents exceeds
-- 20c, in one case entry 76c -> latest 4c (edge_cents ballooning from ~6c to
-- 79.42c as the market crashed toward the losing outcome post-entry). A
-- bucketed backtest built on the drifted column produced a spurious
-- "high edge -> losses" pattern (and a spurious "high confidence -> strong
-- ROI" pattern on model_prob_no_cents) that inverted/disappeared once
-- re-measured at the correct entry-time value. A 25c edge ceiling was
-- deployed and then rolled back the same night as a direct result.
--
-- This migration adds the two columns as a permanent, re-checkable source
-- of truth going forward, and exposes them through
-- weather_position_exits_clean under the view's existing "final_" naming
-- convention. The historical backfill for the 87 existing settled trades is
-- a separate one-time script: scripts/weather/backfill_entry_edge_2026_07_17.py
--
-- edge_cents/model_prob_no_cents are NOT removed or modified -- they remain
-- legitimate as "current live state of an open position" for monitoring.

ALTER TABLE weather_position_exits
    ADD COLUMN IF NOT EXISTS entry_edge_cents NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS entry_model_prob_no_cents NUMERIC(6,2);

COMMENT ON COLUMN weather_position_exits.entry_edge_cents IS
    'edge_cents frozen at first qualification (same moment as entry_captured_at/entry_no_ask_cents). NOT touched by the ON CONFLICT DO UPDATE SET in exit_audit_logger.py -- unlike edge_cents, which is live-refreshed every cycle a signal keeps re-qualifying. Added 2026-07-17 after a same-night backtest found edge_cents drift fabricated a spurious high-edge/losses pattern. Use this column, not edge_cents, for any entry-time analysis or backtest.';

COMMENT ON COLUMN weather_position_exits.entry_model_prob_no_cents IS
    'model_prob_no_cents frozen at first qualification, same pattern as entry_edge_cents. NOT touched by ON CONFLICT DO UPDATE SET. Added 2026-07-17. Use this column, not model_prob_no_cents, for any entry-time analysis or backtest.';

-- weather_position_exits_clean previously existed only as an uncommitted
-- ad-hoc script on LA (same gap as other /tmp-only SQL noted in
-- ARCHITECTURE-DOC-PENDING-EDITS-2026-07-03.md) -- captured here for the
-- first time via pg_get_viewdef against the live definition, plus the two
-- new columns.
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
    weather_position_exits.entry_model_prob_no_cents AS final_entry_model_prob_no_cents
   FROM weather_position_exits;

COMMENT ON VIEW weather_position_exits_clean IS
    'Dashboard-facing view over weather_position_exits. "final_" columns are the trustworthy version to chart -- final_entry_edge_cents/final_entry_model_prob_no_cents are frozen at entry (added 2026-07-17); the un-prefixed edge_cents/model_prob_no_cents are live-refreshed and reflect the latest cycle, not entry conditions -- do not use them for entry-time/backtest analysis.';
