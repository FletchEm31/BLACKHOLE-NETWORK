-- Migration: 2026-06-11-kalshi-fills
-- Creates kalshi_fills table for settled trade history.
--
-- Each row is one fill (matched order leg) from /portfolio/fills.
-- ON CONFLICT DO NOTHING — fills are immutable once settled.
--
-- Apply:
--   sudo -u postgres psql -d eventhorizon -P pager=off -f 2026-06-11-kalshi-fills.sql
--
-- Rollback:
--   DROP TABLE IF EXISTS kalshi_fills;

BEGIN;

CREATE TABLE IF NOT EXISTS kalshi_fills (
    id               bigserial    PRIMARY KEY,
    contract_ticker  text         NOT NULL,
    contract_title   text,
    side             text         NOT NULL CHECK (side IN ('yes','no')),
    action           text         NOT NULL CHECK (action IN ('buy','sell')),
    count            integer      NOT NULL DEFAULT 0,
    price_cents      integer,                -- Kalshi price 1..99 cents
    cost_usd         numeric,                -- count * price_cents / 100
    is_taker         boolean,
    created_time     timestamptz,
    inserted_at      timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS kalshi_fills_ticker_idx
    ON kalshi_fills (contract_ticker);

CREATE INDEX IF NOT EXISTS kalshi_fills_time_idx
    ON kalshi_fills (created_time DESC);

-- Prevent duplicate inserts of the same fill
CREATE UNIQUE INDEX IF NOT EXISTS kalshi_fills_unique_idx
    ON kalshi_fills (contract_ticker, side, action, count, price_cents, created_time)
    WHERE created_time IS NOT NULL;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_trader') THEN
        GRANT SELECT, INSERT ON kalshi_fills TO bhn_trader;
        GRANT USAGE, SELECT ON SEQUENCE kalshi_fills_id_seq TO bhn_trader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'horizon_agent_reader') THEN
        GRANT SELECT ON kalshi_fills TO horizon_agent_reader;
    END IF;
END $$;

COMMIT;
