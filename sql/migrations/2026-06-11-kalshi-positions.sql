-- Migration: 2026-06-11-kalshi-positions
-- Creates kalshi_positions table for live portfolio snapshots.
--
-- Each row is a snapshot of one open position at collection time.
-- Append-only — each collector run inserts a new row per position.
-- avg_price is in fractional dollars (0.01–0.99), NOT cents.
--
-- Apply:
--   sudo -u postgres psql -d eventhorizon -P pager=off -f 2026-06-11-kalshi-positions.sql
--
-- Rollback:
--   DROP TABLE IF EXISTS kalshi_positions;

BEGIN;

CREATE TABLE IF NOT EXISTS kalshi_positions (
    id                  bigserial    PRIMARY KEY,
    contract_ticker     text         NOT NULL,
    contract_title      text,
    side                text         NOT NULL CHECK (side IN ('yes','no')),
    contracts           integer      NOT NULL DEFAULT 0,
    avg_price           numeric,                -- fractional dollars per contract (0.01–0.99)
    cost_usd            numeric,                -- total_traded / 100
    market_value_usd    numeric,                -- market_exposure / 100
    unrealized_pnl_usd  numeric,
    payout_if_right_usd numeric,
    captured_at         timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS kalshi_positions_ticker_idx
    ON kalshi_positions (contract_ticker);

CREATE INDEX IF NOT EXISTS kalshi_positions_captured_idx
    ON kalshi_positions (captured_at DESC);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_trader') THEN
        GRANT SELECT, INSERT ON kalshi_positions TO bhn_trader;
        GRANT USAGE, SELECT ON SEQUENCE kalshi_positions_id_seq TO bhn_trader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'horizon_agent_reader') THEN
        GRANT SELECT ON kalshi_positions TO horizon_agent_reader;
    END IF;
END $$;

COMMIT;
