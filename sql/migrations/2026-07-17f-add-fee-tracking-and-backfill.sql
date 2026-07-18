-- Fee-omission bug fix (2026-07-17f) -- confirmed CP4 sizing
-- (cp4_kelly_sizer.py), weather_position_exits, and exit_audit_logger.py's
-- exit scoring never accounted for Kalshi's maker fee anywhere. Entry
-- sizing, stored stake/contracts, and every settled trade's
-- realized_pnl_usd were all gross (fee-free), not net. See
-- cp4_kelly_sizer.py's _maker_fee() docstring and
-- exit_audit_logger.py's score_settled_positions() docstring for the
-- code-side half of this fix -- this migration is the schema + backfill
-- half.
--
-- Quantified impact at the time of this fix, across the 101 already-
-- settled paper trades: $98.52 total overstatement (~$0.98/trade average).
--
-- fee_usd: populated going forward at entry time by cp4_kelly_sizer.py's
-- _maker_fee(), stored via exit_audit_logger.py's record_paper_trade().
-- Backfilled below for existing rows using the same maker-fee formula
-- (ceil(0.0175 * p * (1-p) * n * 100) / 100), applied to each row's own
-- entry price (entry_no_ask_cents, falling back to no_ask_cents for rows
-- predating the entry-price-integrity fix) and contracts_recommended.
ALTER TABLE weather_position_exits
    ADD COLUMN IF NOT EXISTS fee_usd NUMERIC(8,4);

COMMENT ON COLUMN weather_position_exits.fee_usd IS
    'Kalshi maker fee charged at entry, USD (ceil(0.0175*p*(1-p)*n*100)/100). Added 2026-07-17f after confirming CP4 sizing and exit scoring never accounted for fees anywhere -- every settled realized_pnl_usd before this fix was gross, not net. Populated going forward by cp4_kelly_sizer.py/exit_audit_logger.py; backfilled for pre-existing rows by this same migration.';

-- Backfill: compute fee_usd for every row that has enough data to compute
-- it (contracts_recommended set, an entry price available), regardless of
-- scored_at -- both open and settled positions get a real fee_usd so it's
-- correct once they do settle. Only settled rows also get
-- corrected_realized_pnl_usd written (see below) since that's the only
-- column downstream consumers (weather_position_exits_clean) actually read
-- for P&L.
UPDATE weather_position_exits
SET fee_usd = CEIL(
        0.0175
        * (COALESCE(entry_no_ask_cents, no_ask_cents) / 100.0)
        * (1 - COALESCE(entry_no_ask_cents, no_ask_cents) / 100.0)
        * contracts_recommended * 100
      ) / 100.0
WHERE fee_usd IS NULL
  AND contracts_recommended IS NOT NULL
  AND contracts_recommended > 0
  AND COALESCE(entry_no_ask_cents, no_ask_cents) IS NOT NULL;

-- Follows the table's existing corrected_* convention (same pattern as
-- corrected_actual_tmax_f/corrected_actual_outcome/etc.) rather than
-- overwriting the raw realized_pnl_usd audit trail -- weather_position_
-- exits_clean.final_realized_pnl_usd already COALESCEs to this column.
UPDATE weather_position_exits
SET corrected_realized_pnl_usd = realized_pnl_usd - fee_usd
WHERE scored_at IS NOT NULL
  AND realized_pnl_usd IS NOT NULL
  AND fee_usd IS NOT NULL
  AND corrected_realized_pnl_usd IS NULL;

-- View re-created to expose fee_usd (pass-through, no corrected_fee_usd
-- counterpart planned -- unlike the other final_* columns, this one has no
-- separate "raw vs corrected" concept, it's just a fact). Based on the
-- view's current live definition (includes side, added 2026-07-18b, and
-- the entry_hours_to_avg_dailyhigh column added 2026-07-17e).
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
    weather_position_exits.fee_usd
   FROM weather_position_exits;

COMMENT ON VIEW weather_position_exits_clean IS
    'Dashboard-facing view over weather_position_exits. "final_" columns are the trustworthy version to chart -- final_entry_* columns are frozen at entry; the un-prefixed edge_cents/model_prob_no_cents/predicted_tmax_f/hours_to_settle/sigma_used are all live-refreshed and reflect the latest cycle, not entry conditions. final_realized_pnl_usd is fee-adjusted as of 2026-07-17f (COALESCE(corrected_realized_pnl_usd, realized_pnl_usd) -- corrected_realized_pnl_usd is fee-backfilled for pre-2026-07-17f rows, realized_pnl_usd is fee-adjusted at the source for every row scored from 2026-07-17f onward). fee_usd is the Kalshi maker fee charged at entry, USD.';
