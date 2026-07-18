-- 2026-07-18-add-side-column-to-position-exits.sql
--
-- Phase 1 (schema + design only, no order placement) of Yes-side trading
-- infrastructure: adds a `side` column to weather_position_exits so a
-- future YES-side bet can be distinguished from CP4's current NO-side-only
-- ("Tail-No") strategy. Motivated by tonight's sigma-zone finding: the
-- 0-sigma bucket (model's own prediction falls inside the bucket) is the
-- single largest dollar loss on the No-side (pooled: 58.3% win rate,
-- -18.6% ROI, -$558 net, n=36) -- a candidate for a YES-side bet instead,
-- to be designed and built in a dedicated Phase 2 session (deliberately
-- NOT this one -- live-execution risk gets its own pass).
--
-- 100% of trades to date are NO-side (CP4 has never placed a YES bet), so
-- every existing row backfills to 'NO'. New column enforced NOT NULL with
-- a CHECK constraint (no existing column in this table uses one, e.g.
-- actual_outcome is a bare varchar(10) -- adding it here since this is new,
-- not legacy, and it's cheap insurance against a typo'd side value).
--
-- Run on LA:
--   psql -U postgres eventhorizon -f sql/migrations/2026-07-18-add-side-column-to-position-exits.sql

\set ON_ERROR_STOP on

BEGIN;

ALTER TABLE weather_position_exits
    ADD COLUMN IF NOT EXISTS side VARCHAR(4);

UPDATE weather_position_exits SET side = 'NO' WHERE side IS NULL;

ALTER TABLE weather_position_exits
    ALTER COLUMN side SET NOT NULL,
    ALTER COLUMN side SET DEFAULT 'NO';

ALTER TABLE weather_position_exits
    ADD CONSTRAINT weather_position_exits_side_check CHECK (side IN ('NO', 'YES'));

COMMENT ON COLUMN weather_position_exits.side IS
    'Which side of the contract this position bet: NO or YES. Added 2026-07-18 for Yes-side trading infrastructure Phase 1 (schema + design only -- CP4 remains NO-side-only, "Tail-No", until Phase 2 builds and reviews the actual YES trigger/order-placement logic in its own dedicated session). All rows before this migration backfilled to NO (100% of historical trades). Set once at first INSERT in exit_audit_logger.py''s _RECORD_SQL, never touched by its ON CONFLICT DO UPDATE SET -- same "never drifts after entry" posture as the entry_* columns, though side cannot actually change mid-position so this is belt-and-suspenders, not a fix for an observed bug.';

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
    weather_position_exits.side
   FROM weather_position_exits;

COMMENT ON VIEW weather_position_exits_clean IS
    'Dashboard-facing view over weather_position_exits. "final_" columns are the trustworthy version to chart -- final_entry_* columns are frozen at entry (entry_edge_cents/entry_model_prob_no_cents added 2026-07-17, entry_predicted_tmax_f/entry_hours_to_settle added 2026-07-17b, entry_sigma_used added 2026-07-17d, entry_hours_to_avg_dailyhigh added 2026-07-17e); the un-prefixed edge_cents/model_prob_no_cents/predicted_tmax_f/hours_to_settle/sigma_used are all live-refreshed and reflect the latest cycle, not entry conditions -- do not use them for entry-time/backtest analysis. `side` added 2026-07-18 (NO/YES) -- every query built before this date implicitly assumed side=NO; see the 2026-07-18 downstream-query audit before trusting any aggregate here once YES-side rows exist.';

GRANT SELECT ON weather_position_exits_clean TO grafana_reader, ehuser, bhn_trader;

COMMIT;
