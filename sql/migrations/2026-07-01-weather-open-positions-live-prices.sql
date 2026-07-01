-- 2026-07-01-weather-open-positions-live-prices.sql
-- Update weather_open_positions to join live Kalshi snapshot prices.
-- Update weather_paper_pnl_dashboard to pass through the new columns.
--
-- New columns on weather_open_positions:
--   kalshi_market_ticker   real Kalshi market_ticker (≠ BHN contract_ticker; see TICKET-W1)
--   current_no_ask_live    latest no_ask from bronze snapshot (× 100, cents scale)
--   current_yes_bid_live   latest yes_bid from bronze snapshot (× 100, cents scale)
--   last_snapshot_at       timestamp of most recent Kalshi snapshot for this contract
--   price_drift_cents      current_no_ask_live − entry_no_ask_cents
--                          negative = market moved against the NO position
--   unrealized_pnl_usd     contracts × (current_no_ask_live − entry_no_ask_cents) / 100
--                          negative = open loss, positive = open gain
--
-- Join key: (station_code, bucket_label, target_date) — confirmed correct via
--   diagnostic 2026-07-01. snapshot.target_date = exits.target_date (both use
--   settlement / next-day date). market_ticker format differs (see TICKET-W1).
--
-- Run on LA:
--   psql -U postgres eventhorizon -f sql/migrations/2026-07-01-weather-open-positions-live-prices.sql

\set ON_ERROR_STOP on

BEGIN;

-- Drop both views first: CREATE OR REPLACE cannot reorder/rename existing columns.
-- CASCADE drops weather_paper_pnl_dashboard (which depends on weather_open_positions).
DROP VIEW IF EXISTS weather_paper_pnl_dashboard CASCADE;
DROP VIEW IF EXISTS weather_open_positions CASCADE;

-- ─────────────────────────────────────────────────────────────────────────────
-- View 1: weather_open_positions + live Kalshi prices
-- ─────────────────────────────────────────────────────────────────────────────
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
    e.station_code,
    e.target_date,
    e.bucket_label,
    e.contract_ticker,
    e.bucket_floor,
    e.bucket_cap,
    -- Cost basis (from exits — last DO UPDATE value; true entry preserved once
    -- entry_no_ask_cents column is added per dedup fix task)
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
    -- Hypothetical P&L at entry price (settlement-day view)
    round(e.contracts_recommended * (100 - e.entry_no_ask_cents) / 100.0, 2)  AS hypothetical_win_usd,
    round(e.contracts_recommended * e.entry_no_ask_cents / 100.0, 2)           AS hypothetical_loss_usd,
    -- ── Live Kalshi snapshot columns ──────────────────────────────────────────
    -- kalshi_market_ticker differs from contract_ticker (see TICKET-W1 in
    -- weatherbhn-overview.md). Use this for any Kalshi API calls, NOT contract_ticker.
    snap.market_ticker                                                          AS kalshi_market_ticker,
    round(snap.no_ask * 100, 2)                                                AS current_no_ask_live,
    round(snap.yes_bid * 100, 2)                                               AS current_yes_bid_live,
    snap.retrieved_at                                                           AS last_snapshot_at,
    -- price_drift_cents: negative = market moved against the NO position
    --   (no_ask dropped → YES more likely → NO position losing value)
    round((snap.no_ask * 100) - e.entry_no_ask_cents, 1)                       AS price_drift_cents,
    -- unrealized_pnl_usd: negative = open loss, positive = open gain
    --   uses entry_no_ask_cents as cost basis (exits value, not true first-capture)
    round(e.contracts_recommended *
          ((snap.no_ask * 100) - e.entry_no_ask_cents) / 100.0, 2)             AS unrealized_pnl_usd,
    -- ─────────────────────────────────────────────────────────────────────────
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


-- ─────────────────────────────────────────────────────────────────────────────
-- View 2: weather_paper_pnl_dashboard — pass through new columns
-- ─────────────────────────────────────────────────────────────────────────────
CREATE VIEW weather_paper_pnl_dashboard AS

SELECT
    'OPEN'::text                  AS position_status,
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


-- Grants unchanged — views are replaced in place, grants persist on the objects
-- but re-state for clarity after DROP/CREATE (CREATE OR REPLACE preserves grants).
GRANT SELECT ON weather_open_positions      TO grafana_reader, ehuser;
GRANT SELECT ON weather_paper_pnl_dashboard TO grafana_reader, ehuser;

COMMIT;
