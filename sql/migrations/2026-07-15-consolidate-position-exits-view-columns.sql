-- 2026-07-15-consolidate-position-exits-view-columns.sql
--
-- Fixes the raw/corrected column ambiguity discovered during tonight's
-- model_prob_no_cents floor analysis: querying weather_position_exits.
-- realized_pnl_usd (raw) showed +$12,949.45 total PnL across 69 settled
-- trades; corrected_realized_pnl_usd showed -$758.12 for the SAME 69
-- trades. 15 of 69 (22%) flip WIN/LOSS between the two. Both columns are
-- named plausibly enough that picking the wrong one silently inverts the
-- read on whether the strategy is profitable.
--
-- Decision (Fletch, 2026-07-15): option 2 -- fix the view/query layer only.
-- Base-table columns (realized_pnl_usd, actual_outcome, actual_tmax_f) are
-- NOT renamed or dropped -- they stay exactly as the 2026-07-05 migration
-- decided ("do not overwrite realized_pnl_usd -- it's the historical
-- record of what was actually reported at the time"). This migration only
-- extends the _superseded_kept_for_audit_only pattern that already exists
-- for contracts_recommended/stake_usd_recommended (added 2026-07-06/07) to
-- the three remaining raw/corrected pairs, so every *view* consumer sees
-- the clean column name resolve to the correct (corrected) value by
-- default, with the raw figure still available but unambiguously labeled.
-- corrected_actual_tmax_f/corrected_actual_outcome/corrected_realized_pnl_usd
-- are left in the view unchanged (not removed) so nothing that already
-- reads them by that name breaks -- they now simply agree with the
-- clean-named column instead of being the only correct source.
--
-- Also fixes a bug found while writing this: weather_paper_pnl_dashboard's
-- position_status CASE (SETTLED_WIN/SETTLED_LOSS/SETTLED) was keyed off
-- the RAW s.actual_outcome, so any of the 15 rows whose outcome flips
-- between raw and corrected would have shown the wrong status label even
-- after fixing the PnL/outcome columns themselves. Now keyed off
-- COALESCE(corrected_actual_outcome, actual_outcome), same as everything
-- else here.
--
-- Second, unrelated fix in the same migration: weather_paper_trading_summary
-- (a weekly rollup view, not tracked in the repo before this -- it was
-- created directly on the DB, outside migration history) sums raw
-- realized_pnl_usd and counts raw actual_outcome directly, with NO
-- correction applied at all -- confirmed live-wrong right now, independent
-- of the option-2/option-1 naming debate above. Fixed unconditionally.
--
-- CREATE OR REPLACE VIEW is used throughout (not DROP...CASCADE) --
-- column list for both views is preserved in the same order for all
-- existing columns, with new _superseded_kept_for_audit_only columns
-- appended at the end only, so this is safe for any dependent object or
-- saved Metabase question that already references these views by column
-- name. weather_open_positions is untouched (OPEN rows have no
-- corrected_* data to reconcile -- nothing to fix there).
--
-- Run on LA:
--   psql -U postgres eventhorizon -f sql/migrations/2026-07-15-consolidate-position-exits-view-columns.sql

\set ON_ERROR_STOP on

BEGIN;

-- ── 1. weather_paper_pnl_dashboard: extend the audit-column pattern ───────
CREATE OR REPLACE VIEW weather_paper_pnl_dashboard AS

SELECT
    'OPEN'::text                  AS position_status,
    o.bet_type,
    o.station_code,
    o.target_date,
    o.bucket_label,
    o.contract_ticker,
    o.kalshi_market_ticker,
    o.entry_no_ask_cents,
    o.current_no_ask_cents,
    o.current_no_ask_live,
    o.current_yes_bid_live,
    o.ask_drift_cents,
    o.price_drift_cents,
    o.unrealized_pnl_usd,
    o.entry_edge_cents,
    o.current_edge_cents,
    o.contracts_recommended,
    o.contracts_recommended AS contracts_recommended_superseded_kept_for_audit_only,
    o.stake_usd,
    o.stake_usd AS stake_usd_superseded_kept_for_audit_only,
    o.hours_to_settle,
    o.predicted_tmax_f,
    o.model_prob_no_cents,
    o.hypothetical_win_usd,
    o.hypothetical_loss_usd,
    NULL::numeric                 AS actual_tmax_f,
    NULL::text                    AS actual_outcome,
    NULL::numeric                 AS corrected_actual_tmax_f,
    NULL::text                    AS corrected_actual_outcome,
    NULL::numeric                 AS realized_pnl_usd,
    NULL::numeric                 AS corrected_realized_pnl_usd,
    NULL::numeric                 AS running_balance_from_5000_start,
    NULL::timestamptz             AS scored_at,
    o.last_snapshot_at,
    o.first_captured_at,
    o.last_updated_at,
    NULL::numeric                 AS actual_tmax_f_superseded_kept_for_audit_only,
    NULL::text                    AS actual_outcome_superseded_kept_for_audit_only,
    NULL::numeric                 AS realized_pnl_usd_superseded_kept_for_audit_only
FROM weather_open_positions o

UNION ALL

SELECT
    -- FIXED 2026-07-15: was keyed off raw s.actual_outcome -- any of the
    -- 15 rows whose outcome flips between raw and corrected showed the
    -- wrong SETTLED_WIN/SETTLED_LOSS label. Now uses the same corrected
    -- value as everything else in this row.
    CASE COALESCE(s.corrected_actual_outcome, s.actual_outcome)
        WHEN 'NO_WIN'  THEN 'SETTLED_WIN'
        WHEN 'NO_LOSS' THEN 'SETTLED_LOSS'
        ELSE                'SETTLED'
    END                           AS position_status,
    'NO'::text                    AS bet_type,
    s.station_code,
    s.target_date,
    s.bucket_label,
    s.contract_ticker,
    NULL::text                    AS kalshi_market_ticker,
    COALESCE(s.entry_no_ask_cents, s.no_ask_cents)
                                   AS entry_no_ask_cents,
    s.no_ask_cents                AS current_no_ask_cents,
    NULL::numeric                 AS current_no_ask_live,
    NULL::numeric                 AS current_yes_bid_live,
    0::numeric                    AS ask_drift_cents,
    0::numeric                    AS price_drift_cents,
    NULL::numeric                 AS unrealized_pnl_usd,
    s.edge_cents                  AS entry_edge_cents,
    s.edge_cents                  AS current_edge_cents,
    COALESCE(s.corrected_contracts_recommended, s.contracts_recommended)  AS contracts_recommended,
    s.contracts_recommended       AS contracts_recommended_superseded_kept_for_audit_only,
    COALESCE(s.corrected_stake_usd_recommended, s.stake_usd_recommended) AS stake_usd,
    s.stake_usd_recommended       AS stake_usd_superseded_kept_for_audit_only,
    0::numeric                    AS hours_to_settle,
    s.predicted_tmax_f,
    s.model_prob_no_cents,
    round(COALESCE(s.corrected_contracts_recommended, s.contracts_recommended) * (100 - COALESCE(s.entry_no_ask_cents, s.no_ask_cents)) / 100.0, 2)
                                   AS hypothetical_win_usd,
    round(COALESCE(s.corrected_contracts_recommended, s.contracts_recommended) * COALESCE(s.entry_no_ask_cents, s.no_ask_cents) / 100.0, 2)
                                   AS hypothetical_loss_usd,
    -- FIXED 2026-07-15: clean column names now resolve to the corrected
    -- value by default (COALESCE falls back to raw only when no
    -- correction exists -- e.g. the 18 rows with no independently
    -- verifiable corrected_actual_outcome). Raw figures are preserved,
    -- unambiguously labeled, at the end of the column list -- same
    -- pattern as contracts_recommended/stake_usd above.
    COALESCE(s.corrected_actual_tmax_f, s.actual_tmax_f)      AS actual_tmax_f,
    COALESCE(s.corrected_actual_outcome, s.actual_outcome)    AS actual_outcome,
    s.corrected_actual_tmax_f,
    s.corrected_actual_outcome,
    COALESCE(s.corrected_realized_pnl_usd, s.realized_pnl_usd) AS realized_pnl_usd,
    s.corrected_realized_pnl_usd,
    5000 + SUM(COALESCE(s.corrected_realized_pnl_usd, s.realized_pnl_usd)) OVER (
        ORDER BY s.scored_at, s.id
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    )                              AS running_balance_from_5000_start,
    s.scored_at,
    NULL::timestamptz             AS last_snapshot_at,
    s.decision_timestamp          AS first_captured_at,
    s.scored_at                   AS last_updated_at,
    s.actual_tmax_f                AS actual_tmax_f_superseded_kept_for_audit_only,
    s.actual_outcome               AS actual_outcome_superseded_kept_for_audit_only,
    s.realized_pnl_usd             AS realized_pnl_usd_superseded_kept_for_audit_only
FROM weather_position_exits s
WHERE s.scored_at IS NOT NULL;

GRANT SELECT ON weather_paper_pnl_dashboard TO grafana_reader, ehuser;

-- ── 2. weather_paper_trading_summary: fix unconditionally ─────────────────
-- Was not tracked in the repo before this -- created directly on the DB.
-- Definition below is the pre-fix live definition with COALESCE wrapping
-- added to actual_outcome and realized_pnl_usd; everything else preserved
-- exactly (same columns, same order, same station/week grouping) so
-- CREATE OR REPLACE VIEW succeeds without a CASCADE.
CREATE OR REPLACE VIEW weather_paper_trading_summary AS
SELECT
    weather_position_exits.station_code,
    weather_position_exits.is_paper_trade,
    date_trunc('week'::text, weather_position_exits.target_date::timestamp with time zone) AS week,
    count(*) AS total_trades,
    count(*) FILTER (WHERE COALESCE(weather_position_exits.corrected_actual_outcome, weather_position_exits.actual_outcome) = 'NO_WIN'::text) AS wins,
    count(*) FILTER (WHERE COALESCE(weather_position_exits.corrected_actual_outcome, weather_position_exits.actual_outcome) = 'NO_LOSS'::text) AS losses,
    round(100.0 * count(*) FILTER (WHERE COALESCE(weather_position_exits.corrected_actual_outcome, weather_position_exits.actual_outcome) = 'NO_WIN'::text)::numeric
          / NULLIF(count(*) FILTER (WHERE weather_position_exits.scored_at IS NOT NULL), 0)::numeric, 1) AS win_rate_pct,
    round(sum(COALESCE(weather_position_exits.corrected_realized_pnl_usd, weather_position_exits.realized_pnl_usd)), 2) AS total_pnl_usd,
    round(avg(weather_position_exits.edge_cents), 1) AS avg_edge_cents,
    round(avg(weather_position_exits.no_ask_cents), 1) AS avg_no_ask_cents,
    count(*) FILTER (WHERE weather_position_exits.scored_at IS NULL) AS pending_score
FROM weather_position_exits
GROUP BY weather_position_exits.station_code, weather_position_exits.is_paper_trade,
         (date_trunc('week'::text, weather_position_exits.target_date::timestamp with time zone))
ORDER BY weather_position_exits.station_code,
         (date_trunc('week'::text, weather_position_exits.target_date::timestamp with time zone)) DESC;

GRANT SELECT ON weather_paper_trading_summary TO grafana_reader, ehuser;

-- ── 3. Verify ────────────────────────────────────────────────────────────
\echo 'weather_paper_trading_summary total_pnl_usd should now sum to approx -758.12 across all rows:'
\echo 'SELECT ROUND(SUM(total_pnl_usd), 2) FROM weather_paper_trading_summary;'
\echo 'weather_paper_pnl_dashboard SETTLED rows: realized_pnl_usd and corrected_realized_pnl_usd should now always agree:'
\echo 'SELECT COUNT(*) FROM weather_paper_pnl_dashboard WHERE position_status LIKE ''SETTLED%'' AND realized_pnl_usd IS DISTINCT FROM corrected_realized_pnl_usd;'

COMMIT;
