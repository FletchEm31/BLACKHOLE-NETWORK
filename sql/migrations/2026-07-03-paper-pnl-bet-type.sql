-- 2026-07-03-paper-pnl-bet-type.sql
--
-- Add an explicit bet_type column to weather_open_positions and
-- weather_paper_pnl_dashboard so "active + historical positions" shows the
-- side of every trade without the viewer having to know the strategy is
-- currently NO-only.
--
-- IMPORTANT: this is hardcoded to 'NO', not derived from a column, because
-- weather_position_exits itself has no side/contract_side column at all —
-- exit_audit_logger.py only ever inserts qualifying BET_NO buckets
-- (core_trading_orchestrator.py never calls record_paper_trade() for a
-- BET_YES signal; cp4_kelly_sizer.py's recommended_action is architecturally
-- only ever 'BET_NO' or 'SKIP'). If/when YES-side trading launches
-- (per WEATHERBHN_TRADING_STRATEGY.md: "deferred until >=60 live ledger
-- entries validate NO-side calibration"), weather_position_exits will need
-- an actual side column added and this view updated to read it — this
-- migration does not attempt to anticipate that.
--
-- Run on LA:
--   psql -U postgres eventhorizon -f sql/migrations/2026-07-03-paper-pnl-bet-type.sql

\set ON_ERROR_STOP on

BEGIN;

DROP VIEW IF EXISTS weather_paper_pnl_dashboard CASCADE;
DROP VIEW IF EXISTS weather_open_positions CASCADE;

CREATE VIEW weather_open_positions AS
WITH entry AS (
    SELECT DISTINCT ON (station_code, target_date, bucket_label)
        station_code,
        target_date,
        bucket_label,
        contract_ticker,
        bucket_floor,
        bucket_cap,
        no_ask_cents          AS entry_no_ask_cents,
        edge_cents            AS entry_edge_cents,
        contracts_recommended,
        stake_usd_recommended AS stake_usd,
        decision_timestamp    AS first_captured_at
    FROM weather_position_exits
    WHERE scored_at IS NULL
    ORDER BY station_code, target_date, bucket_label, decision_timestamp ASC
),
latest AS (
    SELECT DISTINCT ON (station_code, target_date, bucket_label)
        station_code,
        target_date,
        bucket_label,
        no_ask_cents          AS current_no_ask_cents,
        edge_cents            AS current_edge_cents,
        hours_to_settle,
        predicted_tmax_f,
        model_prob_no_cents,
        sigma_used,
        decision_timestamp    AS last_updated_at
    FROM weather_position_exits
    WHERE scored_at IS NULL
    ORDER BY station_code, target_date, bucket_label, decision_timestamp DESC
)
SELECT
    'NO'::text                                                                 AS bet_type,
    e.station_code,
    e.target_date,
    e.bucket_label,
    e.contract_ticker,
    e.bucket_floor,
    e.bucket_cap,
    e.entry_no_ask_cents,
    l.current_no_ask_cents,
    round(l.current_no_ask_cents - e.entry_no_ask_cents, 1)                   AS ask_drift_cents,
    e.entry_edge_cents,
    l.current_edge_cents,
    e.contracts_recommended,
    e.stake_usd,
    l.hours_to_settle,
    l.predicted_tmax_f,
    l.model_prob_no_cents,
    l.sigma_used,
    round(e.contracts_recommended * (100 - e.entry_no_ask_cents) / 100.0, 2)  AS hypothetical_win_usd,
    round(e.contracts_recommended * e.entry_no_ask_cents / 100.0, 2)           AS hypothetical_loss_usd,
    snap.market_ticker                                                          AS kalshi_market_ticker,
    round(snap.no_ask * 100, 2)                                                AS current_no_ask_live,
    round(snap.yes_bid * 100, 2)                                               AS current_yes_bid_live,
    snap.retrieved_at                                                           AS last_snapshot_at,
    round((snap.no_ask * 100) - e.entry_no_ask_cents, 1)                       AS price_drift_cents,
    round(e.contracts_recommended *
          ((snap.no_ask * 100) - e.entry_no_ask_cents) / 100.0, 2)             AS unrealized_pnl_usd,
    e.first_captured_at,
    l.last_updated_at
FROM entry e
JOIN latest l
    ON  l.station_code = e.station_code
    AND l.target_date  = e.target_date
    AND l.bucket_label = e.bucket_label
LEFT JOIN LATERAL (
    SELECT market_ticker, no_ask, yes_bid, retrieved_at
    FROM weather_bronze_kalshi_market_snapshots
    WHERE station_code = e.station_code
      AND bucket_label  = e.bucket_label
      AND target_date   = e.target_date
      AND no_ask IS NOT NULL
    ORDER BY retrieved_at DESC
    LIMIT 1
) snap ON true
ORDER BY e.target_date, e.station_code, e.bucket_label;


CREATE VIEW weather_paper_pnl_dashboard AS

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
    o.stake_usd,
    o.hours_to_settle,
    o.predicted_tmax_f,
    o.model_prob_no_cents,
    o.hypothetical_win_usd,
    o.hypothetical_loss_usd,
    NULL::numeric                 AS actual_tmax_f,
    NULL::text                    AS actual_outcome,
    NULL::numeric                 AS realized_pnl_usd,
    NULL::timestamptz             AS scored_at,
    o.last_snapshot_at,
    o.first_captured_at,
    o.last_updated_at
FROM weather_open_positions o

UNION ALL

SELECT
    CASE s.actual_outcome
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
    s.no_ask_cents                AS entry_no_ask_cents,
    s.no_ask_cents                AS current_no_ask_cents,
    NULL::numeric                 AS current_no_ask_live,
    NULL::numeric                 AS current_yes_bid_live,
    0::numeric                    AS ask_drift_cents,
    0::numeric                    AS price_drift_cents,
    NULL::numeric                 AS unrealized_pnl_usd,
    s.edge_cents                  AS entry_edge_cents,
    s.edge_cents                  AS current_edge_cents,
    s.contracts_recommended,
    s.stake_usd_recommended       AS stake_usd,
    0::numeric                    AS hours_to_settle,
    s.predicted_tmax_f,
    s.model_prob_no_cents,
    round(s.contracts_recommended * (100 - s.no_ask_cents) / 100.0, 2)  AS hypothetical_win_usd,
    round(s.contracts_recommended * s.no_ask_cents / 100.0, 2)           AS hypothetical_loss_usd,
    s.actual_tmax_f,
    s.actual_outcome,
    s.realized_pnl_usd,
    s.scored_at,
    NULL::timestamptz             AS last_snapshot_at,
    s.decision_timestamp          AS first_captured_at,
    s.scored_at                   AS last_updated_at
FROM weather_position_exits s
WHERE s.scored_at IS NOT NULL;

GRANT SELECT ON weather_open_positions      TO grafana_reader, ehuser;
GRANT SELECT ON weather_paper_pnl_dashboard TO grafana_reader, ehuser;

COMMIT;
