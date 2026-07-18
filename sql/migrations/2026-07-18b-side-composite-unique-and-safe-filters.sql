-- 2026-07-18b-side-composite-unique-and-safe-filters.sql
--
-- Two fixes discovered auditing the 2026-07-18 side-column addition
-- (Yes-side trading infrastructure Phase 1), bundled as one complete unit
-- rather than shipped piecemeal:
--
-- 1. weather_position_exits' UNIQUE(contract_ticker) constraint predates
--    the side column and would let a NO and a YES bet on the literal same
--    bucket contract collide as one row via exit_audit_logger.py's
--    ON CONFLICT (contract_ticker) DO UPDATE -- silently overwriting one
--    side's row with the other's data instead of erroring. Replaced with a
--    composite UNIQUE(contract_ticker, side): a real Kalshi contract_ticker
--    stays a clean identifier (not side-qualified into the string itself),
--    while NO/YES bets on the same contract now correctly coexist as two
--    rows. exit_audit_logger.py's ON CONFLICT target updated to match in
--    the same commit as this migration -- an ON CONFLICT target must name
--    an existing unique constraint exactly, so these two must move together.
--
-- 2. Safe WHERE side='NO' filters added to every places identified in
--    tonight's downstream-query audit as needing one to keep showing
--    EXACTLY what they show today (100% NO-side data) once YES rows can
--    exist, without yet doing the real side-aware redesign those need
--    long-term:
--      - weather_paper_pnl_dashboard's SETTLED branch (already hardcodes
--        'NO'::text AS bet_type for this branch -- the filter makes that
--        already-true; the OPEN branch's bet_type comes from
--        weather_open_positions, a different source table, untouched here)
--      - weather_paper_trading_summary (had no WHERE at all -- every
--        aggregate in it pools whatever's in weather_position_exits)
--    NOT touched here (deliberately deferred, per operator decision):
--      - exit_audit_logger.py's score_settled_positions()/main.py's
--        get_ladder() query filters -- those are Python/app-code changes,
--        not SQL objects, done alongside this migration in the same commit
--      - dashboard get_sigma_performance() -- explicitly deferred for a
--        real side-aware (NO vs YES comparison) redesign, not a filter
--
-- Run on LA:
--   psql -U postgres eventhorizon -f sql/migrations/2026-07-18b-side-composite-unique-and-safe-filters.sql

\set ON_ERROR_STOP on

BEGIN;

-- ── 1. Composite unique constraint ─────────────────────────────────────────
ALTER TABLE weather_position_exits
    DROP CONSTRAINT weather_position_exits_contract_ticker_key;

ALTER TABLE weather_position_exits
    ADD CONSTRAINT weather_position_exits_contract_ticker_side_key UNIQUE (contract_ticker, side);

-- ── 2. weather_paper_pnl_dashboard: filter the SETTLED branch to NO ───────
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
WHERE s.scored_at IS NOT NULL
  AND s.side = 'NO';

GRANT SELECT ON weather_paper_pnl_dashboard TO grafana_reader, ehuser;

-- ── 3. weather_paper_trading_summary: filter to NO ────────────────────────
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
WHERE weather_position_exits.side = 'NO'
GROUP BY weather_position_exits.station_code, weather_position_exits.is_paper_trade,
         (date_trunc('week'::text, weather_position_exits.target_date::timestamp with time zone))
ORDER BY weather_position_exits.station_code,
         (date_trunc('week'::text, weather_position_exits.target_date::timestamp with time zone)) DESC;

GRANT SELECT ON weather_paper_trading_summary TO grafana_reader, ehuser;

COMMIT;
