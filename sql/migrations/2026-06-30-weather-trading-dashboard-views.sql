-- 2026-06-30-weather-trading-dashboard-views.sql
-- WeatherBHN paper trading dashboard views.
--
-- Creates:
--   weather_open_positions        — active (unscored) positions, entry vs current pricing
--   weather_paper_pnl_dashboard   — unified view: open + settled, one complete P&L picture
--
-- Depends on: weather_position_exits (v1 schema — CP4 paper trade recorder columns)
--
-- Run on LA:
--   psql -U postgres eventhorizon -f sql/migrations/2026-06-30-weather-trading-dashboard-views.sql

\set ON_ERROR_STOP on

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- View 1: weather_open_positions
--
-- One row per active contract (scored_at IS NULL). Uses two DISTINCT ON CTEs to
-- pull the first signal ever captured (entry price = cost basis) and the most
-- recent signal (current market view) for each (station_code, target_date, bucket_label).
--
-- NOTE: After migration 002 deduplicates to one row per contract_ticker and the
-- DO UPDATE path goes live, entry_* and current_* columns will converge to the
-- same values. A future schema addition of a protected entry_no_ask_cents column
-- (excluded from the ON CONFLICT DO UPDATE SET list) will restore the split.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW weather_open_positions AS
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
    -- Cost basis vs live market
    e.entry_no_ask_cents,
    l.current_no_ask_cents,
    round(l.current_no_ask_cents - e.entry_no_ask_cents, 1)                   AS ask_drift_cents,
    -- Edge at entry vs now
    e.entry_edge_cents,
    l.current_edge_cents,
    -- Sizing (locked at entry)
    e.contracts_recommended,
    e.stake_usd,
    -- Time and model signal
    l.hours_to_settle,
    l.predicted_tmax_f,
    l.model_prob_no_cents,
    l.sigma_used,
    -- Hypothetical P&L if the contract settled right now
    -- Win:  contracts × (100 − entry_ask) / 100  → payout minus cost
    -- Loss: contracts × entry_ask / 100           → full stake forfeited
    round(e.contracts_recommended * (100 - e.entry_no_ask_cents) / 100.0, 2)  AS hypothetical_win_usd,
    round(e.contracts_recommended * e.entry_no_ask_cents / 100.0, 2)           AS hypothetical_loss_usd,
    e.first_captured_at,
    l.last_updated_at
FROM entry e
JOIN latest l
    ON  l.station_code = e.station_code
    AND l.target_date  = e.target_date
    AND l.bucket_label = e.bucket_label
ORDER BY e.target_date, e.station_code, e.bucket_label;


-- ─────────────────────────────────────────────────────────────────────────────
-- View 2: weather_paper_pnl_dashboard
--
-- Unified paper trading picture. OPEN rows (position_status = 'OPEN') come from
-- weather_open_positions. Settled rows ('SETTLED_WIN' / 'SETTLED_LOSS') come from
-- scored weather_position_exits rows where scored_at IS NOT NULL.
--
-- For settled rows no_ask_cents reflects the price at last orchestrator refresh
-- before settlement (best available cost-basis proxy; not guaranteed to match the
-- exact entry price until entry_no_ask_cents is added as a protected column).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW weather_paper_pnl_dashboard AS

SELECT
    'OPEN'::text                  AS position_status,
    o.station_code,
    o.target_date,
    o.bucket_label,
    o.contract_ticker,
    o.entry_no_ask_cents,
    o.current_no_ask_cents,
    o.ask_drift_cents,
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
    s.no_ask_cents                AS entry_no_ask_cents,
    s.no_ask_cents                AS current_no_ask_cents,
    0::numeric                    AS ask_drift_cents,
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
    s.decision_timestamp          AS first_captured_at,
    s.scored_at                   AS last_updated_at
FROM weather_position_exits s
WHERE s.scored_at IS NOT NULL

ORDER BY
    CASE position_status WHEN 'OPEN' THEN 0 ELSE 1 END,
    hours_to_settle ASC NULLS LAST,
    scored_at DESC NULLS LAST,
    target_date,
    station_code;


-- ─────────────────────────────────────────────────────────────────────────────
-- Grants
-- grafana_reader — Grafana dashboard queries
-- ehuser        — n8n workflow reads
-- ─────────────────────────────────────────────────────────────────────────────
GRANT SELECT ON weather_open_positions      TO grafana_reader, ehuser;
GRANT SELECT ON weather_paper_pnl_dashboard TO grafana_reader, ehuser;

COMMIT;
