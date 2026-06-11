-- Migration: 2026-06-10-kalshi-positions
-- Creates kalshi_positions table for the portfolio poller.
--
-- Snapshot table — each row is one position at one point in time.
-- Not a CDC log; each poll inserts fresh rows. Query by MAX(captured_at)
-- per contract_ticker for the current view.
--
-- Apply:
--   sudo -u postgres psql -d eventhorizon -P pager=off -f 2026-06-10-kalshi-positions.sql
--
-- Rollback:
--   DROP TABLE IF EXISTS kalshi_positions;

BEGIN;

CREATE TABLE IF NOT EXISTS kalshi_positions (
    id                  bigserial       PRIMARY KEY,
    contract_ticker     text            NOT NULL,
    contract_title      text,
    side                text            NOT NULL CHECK (side IN ('yes','no')),
    contracts           integer         NOT NULL DEFAULT 0,
    avg_price           numeric,                -- fractional dollars per contract (0-1)
    cost_usd            numeric,                -- total_traded / 100
    market_value_usd    numeric,                -- market_exposure / 100
    unrealized_pnl_usd  numeric,                -- unrealized_pnl / 100
    payout_if_right_usd numeric,                -- contracts × $1.00 if YES wins
    captured_at         timestamptz     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS kalshi_positions_ticker_idx
    ON kalshi_positions (contract_ticker);

CREATE INDEX IF NOT EXISTS kalshi_positions_time_idx
    ON kalshi_positions (captured_at DESC);

-- Grant read access to the grafana reader role (if it exists)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'horizon_agent_reader') THEN
        GRANT SELECT ON kalshi_positions TO horizon_agent_reader;
    END IF;
END $$;

COMMIT;
