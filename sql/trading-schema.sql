-- trading-schema.sql
-- BHN trading framework — 7 tables for the 5-strategy paper-trading system.
--
-- Architecture: Python scripts on NJ execute strategies + run reconciliation.
-- LA PostgreSQL is the state-of-record. NJ caches state locally for resilience.
-- HORIZON reads (never writes) via agent_reader. Grafana panel reads via
-- grafana_reader. Mutations come from bhn_trader role (Python scripts on NJ
-- + n8n rules-mutator workflow on LA).
--
-- Tables:
--   1. trading_strategies      — per-strategy metadata + state (5 strats + 'system')
--   2. signals_log             — every signal evaluated, acted on or not
--   3. paper_trades            — all executions, open AND closed (status column)
--   4. strategy_performance    — daily P&L aggregate per strategy
--   5. circuit_breaker_log     — every halt event (breakers + reconciliation + manual)
--   6. rules_change_log        — every rules.json mutation (audit trail)
--   7. reconciliation_heartbeat — NJ daemon liveness pulse
--
-- Apply on LA hub:
--   sudo -u postgres psql -d eventhorizon -f sql/trading-schema.sql
--
-- Pre-req: weather_snapshots, news_articles, memories tables exist; agent_reader
-- and grafana_reader roles exist (from prior schemas).

\set ON_ERROR_STOP on

BEGIN;

-- ────────────────────────────────────────────────────────────────────────
-- Helper: auto-update updated_at on row mutation
-- ────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION bhn_trading_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ────────────────────────────────────────────────────────────────────────
-- 1. trading_strategies — metadata + operational state
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trading_strategies (
    id                          TEXT PRIMARY KEY,
    name                        TEXT NOT NULL,
    description                 TEXT,
    capital_allocation          NUMERIC(12,2) NOT NULL CHECK (capital_allocation >= 0),
    status                      TEXT NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active', 'paused', 'halted', 'error')),
    live_mode_approved          BOOLEAN NOT NULL DEFAULT FALSE,
    cadence_seconds             INTEGER,
    last_run_at                 TIMESTAMPTZ,
    last_status_change_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_status_change_reason   TEXT,
    notes                       TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DROP TRIGGER IF EXISTS trading_strategies_touch_updated_at ON trading_strategies;
CREATE TRIGGER trading_strategies_touch_updated_at
    BEFORE UPDATE ON trading_strategies
    FOR EACH ROW EXECUTE FUNCTION bhn_trading_touch_updated_at();

COMMENT ON TABLE  trading_strategies IS
    'BHN trading framework — per-strategy metadata + operational state. 6 rows: 5 real strategies + "system" virtual row used by kill switch.';
COMMENT ON COLUMN trading_strategies.status IS
    'active = strategy may run; paused = circuit breaker tripped (auto-resets daily); halted = manual or weekly-loss/drawdown breaker (operator must reset); error = exception during run, needs investigation.';
COMMENT ON COLUMN trading_strategies.live_mode_approved IS
    'Paper-only enforcement gate. Must be true PER STRATEGY before live trading. Operator flips via direct PG UPDATE; never auto-flipped.';
COMMENT ON COLUMN trading_strategies.cadence_seconds IS
    'For polling strategies (1, 3, 5). NULL for cron-scheduled strategies (2, 4) — see notes for actual schedule.';


-- ────────────────────────────────────────────────────────────────────────
-- 2. signals_log — every signal evaluated by any strategy
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS signals_log (
    id              BIGSERIAL PRIMARY KEY,
    strategy_id     TEXT NOT NULL REFERENCES trading_strategies(id),
    evaluated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ticker          TEXT NOT NULL,
    action          TEXT NOT NULL CHECK (action IN ('buy', 'sell', 'hold')),
    acted_on        BOOLEAN NOT NULL DEFAULT FALSE,
    reason          TEXT,
    value           NUMERIC,
    raw_payload     JSONB,
    trade_id        BIGINT
);

CREATE INDEX IF NOT EXISTS signals_log_strategy_time_idx
    ON signals_log (strategy_id, evaluated_at DESC);
CREATE INDEX IF NOT EXISTS signals_log_ticker_idx
    ON signals_log (ticker, evaluated_at DESC);
CREATE INDEX IF NOT EXISTS signals_log_acted_idx
    ON signals_log (acted_on, evaluated_at DESC) WHERE acted_on = true;

COMMENT ON TABLE  signals_log IS
    'Every signal evaluated. acted_on=false captures rejected signals (failed circuit breaker, strategy paused, etc.) — important for post-hoc strategy analysis.';
COMMENT ON COLUMN signals_log.trade_id IS
    'Soft FK to paper_trades.id when acted_on=true. NULL otherwise. Not a hard FK because of circular reference with paper_trades.signal_id.';
COMMENT ON COLUMN signals_log.value IS
    'The metric value that triggered the signal (e.g. P/E ratio for strat_2, transaction USD for strat_1, odds-move % for strat_5).';


-- ────────────────────────────────────────────────────────────────────────
-- 3. paper_trades — all executions (open + closed)
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS paper_trades (
    id                      BIGSERIAL PRIMARY KEY,
    strategy_id             TEXT NOT NULL REFERENCES trading_strategies(id),
    ticker                  TEXT NOT NULL,
    side                    TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    qty                     INTEGER NOT NULL CHECK (qty > 0),
    entry_price             NUMERIC(12,4) NOT NULL CHECK (entry_price > 0),
    entry_time              TIMESTAMPTZ NOT NULL,
    exit_price              NUMERIC(12,4),
    exit_time               TIMESTAMPTZ,
    exit_reason             TEXT,
    pnl_dollar              NUMERIC(12,4),
    pnl_pct                 NUMERIC(8,4),
    status                  TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    signal_id               BIGINT REFERENCES signals_log(id),
    stop_loss               NUMERIC(12,4),
    target                  NUMERIC(12,4),
    trailing_stop_pct       NUMERIC(5,2),
    alpaca_order_id_entry   TEXT,
    alpaca_order_id_exit    TEXT,
    metadata                JSONB,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DROP TRIGGER IF EXISTS paper_trades_touch_updated_at ON paper_trades;
CREATE TRIGGER paper_trades_touch_updated_at
    BEFORE UPDATE ON paper_trades
    FOR EACH ROW EXECUTE FUNCTION bhn_trading_touch_updated_at();

CREATE INDEX IF NOT EXISTS paper_trades_strategy_status_idx
    ON paper_trades (strategy_id, status, entry_time DESC);
CREATE INDEX IF NOT EXISTS paper_trades_open_idx
    ON paper_trades (status, entry_time DESC) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS paper_trades_ticker_time_idx
    ON paper_trades (ticker, entry_time DESC);
CREATE INDEX IF NOT EXISTS paper_trades_entry_date_idx
    ON paper_trades ((entry_time::date));
CREATE INDEX IF NOT EXISTS paper_trades_exit_date_idx
    ON paper_trades ((exit_time::date)) WHERE exit_time IS NOT NULL;

COMMENT ON TABLE  paper_trades IS
    'All executions, open + closed via status column. Source of truth for "what BHN believes is open" — Alpaca is execution layer + reconciliation check.';
COMMENT ON COLUMN paper_trades.exit_reason IS
    'stop_loss | target | trailing_stop | time_exit | manual | breaker_halt | system_halt | end_of_day | reconcile_close';
COMMENT ON COLUMN paper_trades.pnl_dollar IS
    'Realized P&L on close. NULL while status=open. Computed: (exit_price - entry_price) * qty * (1 if side=buy else -1).';
COMMENT ON COLUMN paper_trades.metadata IS
    'Strategy-specific extras: entry rationale, sector mapping for strat_5, congress member for strat_1, etc.';


-- ────────────────────────────────────────────────────────────────────────
-- 4. strategy_performance — daily P&L aggregate per strategy
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS strategy_performance (
    id                      BIGSERIAL PRIMARY KEY,
    strategy_id             TEXT NOT NULL REFERENCES trading_strategies(id),
    date                    DATE NOT NULL,
    daily_pnl               NUMERIC(12,4) NOT NULL DEFAULT 0,
    cumulative_pnl          NUMERIC(12,4),
    trades_today            INTEGER NOT NULL DEFAULT 0,
    trades_winning          INTEGER NOT NULL DEFAULT 0,
    trades_losing           INTEGER NOT NULL DEFAULT 0,
    win_rate_pct            NUMERIC(5,2),
    positions_open_at_eod   INTEGER NOT NULL DEFAULT 0,
    high_water_mark         NUMERIC(12,4),
    current_drawdown_pct    NUMERIC(8,4),
    computed_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (strategy_id, date)
);

CREATE INDEX IF NOT EXISTS strategy_performance_date_idx
    ON strategy_performance (date DESC, strategy_id);
CREATE INDEX IF NOT EXISTS strategy_performance_strategy_date_idx
    ON strategy_performance (strategy_id, date DESC);
CREATE INDEX IF NOT EXISTS strategy_performance_drawdown_idx
    ON strategy_performance (current_drawdown_pct DESC) WHERE current_drawdown_pct IS NOT NULL;

COMMENT ON TABLE  strategy_performance IS
    'Daily P&L snapshot per strategy. Written by daily_summary.py at 16:30 ET. ON CONFLICT (strategy_id, date) DO UPDATE so re-running same day overwrites.';
COMMENT ON COLUMN strategy_performance.high_water_mark IS
    'Peak cumulative_pnl ever reached for this strategy. Used by tier-3 system drawdown circuit breaker.';
COMMENT ON COLUMN strategy_performance.current_drawdown_pct IS
    'Negative if below peak. (cumulative_pnl - high_water_mark) / high_water_mark * 100. -15 or below triggers system halt.';


-- ────────────────────────────────────────────────────────────────────────
-- 5. circuit_breaker_log — every halt event (breakers, reconciliation, manual)
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS circuit_breaker_log (
    id                  BIGSERIAL PRIMARY KEY,
    triggered_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_class         TEXT NOT NULL CHECK (event_class IN ('circuit_breaker', 'reconciliation', 'manual_halt', 'system_event')),
    event_type          TEXT NOT NULL,
    severity            TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'paused', 'halted')),
    strategy_id         TEXT REFERENCES trading_strategies(id),
    affects_scope       TEXT NOT NULL CHECK (affects_scope IN ('strategy', 'system')),
    reason              TEXT NOT NULL,
    value_at_trigger    NUMERIC,
    threshold           NUMERIC,
    details             JSONB,
    alert_sent          BOOLEAN NOT NULL DEFAULT FALSE,
    halt_triggered      BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_at         TIMESTAMPTZ,
    resolved_by         TEXT,
    notes               TEXT
);

CREATE INDEX IF NOT EXISTS circuit_breaker_log_time_idx
    ON circuit_breaker_log (triggered_at DESC);
CREATE INDEX IF NOT EXISTS circuit_breaker_log_strategy_idx
    ON circuit_breaker_log (strategy_id, triggered_at DESC);
CREATE INDEX IF NOT EXISTS circuit_breaker_log_unresolved_idx
    ON circuit_breaker_log (resolved_at, triggered_at DESC) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS circuit_breaker_log_event_class_idx
    ON circuit_breaker_log (event_class, triggered_at DESC);

COMMENT ON TABLE  circuit_breaker_log IS
    'Every halt event: 3-tier circuit breakers, reconciliation mismatches, manual killswitch. HORIZON reads this when operator asks "what happened" post-incident.';
COMMENT ON COLUMN circuit_breaker_log.event_class IS
    'circuit_breaker = daily/weekly/drawdown breakers; reconciliation = state mismatch; manual_halt = killswitch; system_event = other halts (e.g. service crash).';
COMMENT ON COLUMN circuit_breaker_log.event_type IS
    'Specific subtype. circuit_breaker: daily_loss_5pct/weekly_loss_10pct/drawdown_15pct. reconciliation: unknown_position/missing_position/sync_drift. manual_halt: manual_killswitch.';
COMMENT ON COLUMN circuit_breaker_log.severity IS
    'info | warning | paused | halted. Reconciliation always uses "halted" per BHN spec (any mismatch = halt).';
COMMENT ON COLUMN circuit_breaker_log.details IS
    'JSONB blob with event-specific data: mismatch deltas, breaker math, drawdown calc. HORIZON formats this for operator on demand.';


-- ────────────────────────────────────────────────────────────────────────
-- 6. rules_change_log — every rules.json mutation
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rules_change_log (
    id                  BIGSERIAL PRIMARY KEY,
    changed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    version_before      INTEGER,
    version_after       INTEGER NOT NULL,
    strategy_id         TEXT,
    field_changed       TEXT,
    old_value           JSONB,
    new_value           JSONB,
    reason              TEXT,
    changed_by          TEXT NOT NULL,
    propagated_to_nj    BOOLEAN NOT NULL DEFAULT FALSE,
    propagated_at       TIMESTAMPTZ,
    propagation_error   TEXT
);

CREATE INDEX IF NOT EXISTS rules_change_log_time_idx
    ON rules_change_log (changed_at DESC);
CREATE INDEX IF NOT EXISTS rules_change_log_strategy_idx
    ON rules_change_log (strategy_id, changed_at DESC) WHERE strategy_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS rules_change_log_unpropagated_idx
    ON rules_change_log (propagated_to_nj, changed_at DESC) WHERE propagated_to_nj = false;

COMMENT ON TABLE  rules_change_log IS
    'Every rules.json mutation. Written by the LA-side systemd path unit on file change. propagated_to_nj flips when rsync to NJ succeeds; Grafana alert if rows stay unpropagated for >5min.';
COMMENT ON COLUMN rules_change_log.changed_by IS
    'operator-direct | horizon-confirmed-by-operator | system-rollback';
COMMENT ON COLUMN rules_change_log.field_changed IS
    'Dot-path notation like "strat_2.filters.pe_max". NULL for full-file replace.';


-- ────────────────────────────────────────────────────────────────────────
-- 7. reconciliation_heartbeat — NJ daemon liveness
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reconciliation_heartbeat (
    id                      BIGSERIAL PRIMARY KEY,
    beat_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    interval_used           INTEGER NOT NULL,
    mismatches_found        INTEGER NOT NULL DEFAULT 0,
    cycle_duration_ms       INTEGER
);

CREATE INDEX IF NOT EXISTS reconciliation_heartbeat_beat_idx
    ON reconciliation_heartbeat (beat_at DESC);

COMMENT ON TABLE  reconciliation_heartbeat IS
    'Liveness pulse from the NJ reconciliation daemon. Inserted every cycle. Grafana alert fires if MAX(beat_at) < NOW() - 90s. Pruned to last 7 days by daily_summary.py.';


-- ────────────────────────────────────────────────────────────────────────
-- Role: bhn_trader — used by Python scripts on NJ
-- ────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_trader') THEN
        CREATE ROLE bhn_trader WITH LOGIN PASSWORD 'CHANGE_ME_AT_FIRST_USE';
        RAISE NOTICE 'bhn_trader role created with placeholder password — operator must rotate before deploy.';
    END IF;
END
$$;

-- bhn_trader: full read/write on trading_* tables (state-of-record mutations)
GRANT SELECT, INSERT, UPDATE                         ON trading_strategies        TO bhn_trader;
GRANT SELECT, INSERT                                 ON signals_log               TO bhn_trader;
GRANT USAGE, SELECT                                  ON SEQUENCE signals_log_id_seq               TO bhn_trader;
GRANT SELECT, INSERT, UPDATE                         ON paper_trades              TO bhn_trader;
GRANT USAGE, SELECT                                  ON SEQUENCE paper_trades_id_seq              TO bhn_trader;
GRANT SELECT, INSERT, UPDATE                         ON strategy_performance      TO bhn_trader;
GRANT USAGE, SELECT                                  ON SEQUENCE strategy_performance_id_seq      TO bhn_trader;
GRANT SELECT, INSERT, UPDATE                         ON circuit_breaker_log       TO bhn_trader;
GRANT USAGE, SELECT                                  ON SEQUENCE circuit_breaker_log_id_seq       TO bhn_trader;
GRANT SELECT, INSERT                                 ON rules_change_log          TO bhn_trader;
GRANT USAGE, SELECT                                  ON SEQUENCE rules_change_log_id_seq          TO bhn_trader;
GRANT SELECT, INSERT, DELETE                         ON reconciliation_heartbeat  TO bhn_trader;
GRANT USAGE, SELECT                                  ON SEQUENCE reconciliation_heartbeat_id_seq  TO bhn_trader;

-- bhn_trader: read access to context data tables (strategies may correlate
-- with weather, news headlines, prior memories)
GRANT SELECT ON weather_snapshots, news_articles, memories TO bhn_trader;

-- agent_reader: HORIZON's read-only role gets SELECT on all trading_* tables.
-- Per BHN architecture, HORIZON only reads; mutations route through confirmation
-- gate via the bhn-rules-mutator workflow.
GRANT SELECT ON trading_strategies        TO agent_reader;
GRANT SELECT ON signals_log               TO agent_reader;
GRANT SELECT ON paper_trades              TO agent_reader;
GRANT SELECT ON strategy_performance      TO agent_reader;
GRANT SELECT ON circuit_breaker_log       TO agent_reader;
GRANT SELECT ON rules_change_log          TO agent_reader;
GRANT SELECT ON reconciliation_heartbeat  TO agent_reader;

-- grafana_reader: Grafana panels (P&L charts, breaker timelines, heartbeat liveness alert)
GRANT SELECT ON trading_strategies        TO grafana_reader;
GRANT SELECT ON signals_log               TO grafana_reader;
GRANT SELECT ON paper_trades              TO grafana_reader;
GRANT SELECT ON strategy_performance      TO grafana_reader;
GRANT SELECT ON circuit_breaker_log       TO grafana_reader;
GRANT SELECT ON rules_change_log          TO grafana_reader;
GRANT SELECT ON reconciliation_heartbeat  TO grafana_reader;


-- ────────────────────────────────────────────────────────────────────────
-- Seed rows: 5 strategies + 'system' virtual strategy
-- ON CONFLICT preserves any operator edits if this file is re-run
-- ────────────────────────────────────────────────────────────────────────
INSERT INTO trading_strategies
    (id, name, description, capital_allocation, status, live_mode_approved, cadence_seconds, notes)
VALUES
    ('strat_1_congress',  'Congress Trade Following',
     'Quiver Quantitative congressional disclosures. Buy purchases >$10k transaction size within 48h of disclosure. Weight by transaction size + member seniority + committee relevance.',
     20000.00, 'active', false, 900,
     'Polls Quiver API every 15 min. Weekly rebalance. 30-day hold, 15% stop, max 10% of portfolio per position. Quiver API key required.'),

    ('strat_2_value',     'Buffett Value Screening',
     'Classic Buffett/Graham value criteria via FMP API. ALL must be true: P/E<15, P/B<1.5, D/E<0.5, ROE>15%, 52w decline>10%, no earnings in next 7d.',
     25000.00, 'active', false, NULL,
     'Daily post-close (17:00 ET) via cron. Equal weight, max 10 positions. Exit: P/E>25 OR 20% stop OR 90-day hold. FMP API key required. FMP free tier 250 req/day — use bulk screener endpoint where possible.'),

    ('strat_3_scalp',     'Mean Reversion Limit Order Scalping',
     'Bollinger Band touches on liquid ETFs. Buy at lower band via LIMIT order, exit at 20-period MA or 1.5% stop.',
     20000.00, 'active', false, 300,
     'Intraday only — closed by 15:45 ET. Universe: SPY/QQQ/XLK/XLF/XLE/XLV. 20-period MA on intraday bars. 2-sigma bands. $3k per trade, max 6 positions (one per instrument). LIMIT orders only — never market.'),

    ('strat_4_momentum',  'Momentum Trend Following',
     '50/200 SMA crossover with volume confirmation on top S&P 500 names. Golden cross BUY, death cross SELL.',
     20000.00, 'active', false, NULL,
     'Daily post-close (17:00 ET) via cron. Universe: top 50 S&P by 20d avg volume. Volume confirm: signal-day vol > 1.5x 20d avg. 8% trailing stop below highest close. Equal weight, max 5 positions. Hold until death cross OR trailing stop.'),

    ('strat_5_pred_mkt',  'Prediction Market Arbitrage',
     'Polymarket/Kalshi odds movements >10% in 1h on macro events → buy correlated sector ETF.',
     15000.00, 'active', false, 600,
     'Polls every 10 min during market hours, hourly off-hours. Sector mapping in rules.json. Buy within 30 min of signal. $2k per signal. 48h hold, 5% TP, 3% SL. Max 3 simultaneous positions.'),

    ('system',            'System (virtual)',
     'Not a real strategy. Used by master_killswitch.py to halt all trading at once. Every real strategy_should_run() checks this row first.',
     0.00, 'active', false, NULL,
     'Killswitch sets status=halted. All strategies skip their run while this row is halted. Operator resets via master_killswitch.py reset.')
ON CONFLICT (id) DO NOTHING;


COMMIT;

-- ────────────────────────────────────────────────────────────────────────
-- Post-deploy operator action items
-- ────────────────────────────────────────────────────────────────────────
-- 1. Rotate bhn_trader password (do this before NJ scripts connect):
--      sudo -u postgres psql -d eventhorizon \
--        -c "ALTER ROLE bhn_trader WITH PASSWORD '<random-44-char>';"
--    Store new password in Proton Pass as "BHN-PG-bhn_trader".
--    Place in /etc/bhn/trading.env on NJ as PG_PASSWORD (mode 0600 root:root,
--    then chgrp bhn-trading + chmod 0640 once that user exists).
--
-- 2. Extend pg_hba.conf on LA to allow bhn_trader from NJ's tunnel IP:
--      host  eventhorizon  bhn_trader  10.8.0.5/32  scram-sha-256
--    Then reload PG: systemctl reload postgresql
--
-- 3. Verify connectivity from NJ:
--      ssh nj 'PGPASSWORD="<pw>" psql -h 10.8.0.1 -U bhn_trader -d eventhorizon -c "SELECT id, status FROM trading_strategies;"'
--    Should return 6 rows.
--
-- 4. Schema is now ready for trading_core.py to consume.
