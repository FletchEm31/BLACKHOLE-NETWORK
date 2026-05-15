-- horizon-financial-schema.sql
-- HORIZON financial intelligence layer — 7 tables + view + grants.
--
-- Architecture: collectors run on LA (same node as PG). Outbound API calls
-- (Alpaca, FRED, alternative.me, CBOE, OpenInsider, AAII, FMP) egress
-- through Hillsboro per the LA egress isolation policy.
--
-- Tables:
--   1. market_daily        — daily OHLCV + 14 computed indicators per ticker
--   2. macro_daily         — 23 FRED macro series, dense (one row per business day)
--   3. market_regimes      — daily 5-regime classification with confidence
--   4. market_sentiment    — daily F&G + put/call + insider + AAII
--   5. market_events       — FOMC + earnings + opex + macro releases (forward-looking)
--   6. pattern_library     — discovered correlations + win-rate buckets (read-only for HORIZON)
--   7. investment_signals  — LLM-curated investment ideas (separate abstraction from signals_log)
--
-- View:
--   v_ticker_analysis      — denormalized per-(ticker, date) join across the first 4 tables
--
-- Apply on LA hub:
--   sudo -u postgres psql -d eventhorizon -f sql/horizon-financial-schema.sql
--
-- Pre-req: bhn_trader, agent_reader, grafana_reader roles exist (from trading-schema.sql).

\set ON_ERROR_STOP on

BEGIN;


-- ────────────────────────────────────────────────────────────────────────
-- 1. market_daily — daily OHLCV + computed indicators
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_daily (
    id                  BIGSERIAL PRIMARY KEY,
    ticker              VARCHAR(10) NOT NULL,
    date                DATE NOT NULL,
    open                NUMERIC(14, 4),
    high                NUMERIC(14, 4),
    low                 NUMERIC(14, 4),
    close               NUMERIC(14, 4),
    volume              BIGINT,
    sma_20              NUMERIC(14, 4),
    sma_50              NUMERIC(14, 4),
    sma_100             NUMERIC(14, 4),
    sma_200             NUMERIC(14, 4),
    rsi_14              NUMERIC(8, 4),
    atr_14              NUMERIC(14, 4),
    bb_upper            NUMERIC(14, 4),
    bb_lower            NUMERIC(14, 4),
    bb_width            NUMERIC(10, 6),
    roc_9               NUMERIC(10, 6),
    roc_21              NUMERIC(10, 6),
    roc_63              NUMERIC(10, 6),
    volume_ratio        NUMERIC(10, 4),
    high_52w            NUMERIC(14, 4),
    low_52w             NUMERIC(14, 4),
    pct_from_52w_high   NUMERIC(8, 6),
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, date)
);

CREATE INDEX IF NOT EXISTS market_daily_ticker_date_idx
    ON market_daily (ticker, date DESC);
CREATE INDEX IF NOT EXISTS market_daily_date_idx
    ON market_daily (date DESC);

COMMENT ON TABLE  market_daily IS
    'Daily OHLCV + 14 computed indicators per ticker for HORIZON''s 29-ticker strategy universe. Written by scripts/horizon/market_data_collector.py at 16:30 ET. Idempotent via (ticker, date) UNIQUE constraint.';
COMMENT ON COLUMN market_daily.rsi_14 IS
    'Wilder''s RSI (alpha = 1/14). 0-100 range.';
COMMENT ON COLUMN market_daily.bb_width IS
    'Normalized Bollinger band width: (upper - lower) / SMA20. Higher = more volatile regime.';
COMMENT ON COLUMN market_daily.pct_from_52w_high IS
    'Negative pct distance from 52-week high. 0 at high, -0.07 means 7% below high.';
COMMENT ON COLUMN market_daily.volume_ratio IS
    'Today''s volume / 20-day average volume. >1 = above-average activity.';


-- ────────────────────────────────────────────────────────────────────────
-- 2. macro_daily — FRED macro indicators, dense (one row per business day)
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS macro_daily (
    id                      SERIAL PRIMARY KEY,
    date                    DATE NOT NULL UNIQUE,
    vix                     NUMERIC(10, 4),
    yield_curve_10y2y       NUMERIC(8, 4),
    yield_curve_10y3m       NUMERIC(8, 4),
    fed_funds_rate          NUMERIC(8, 4),
    cpi                     NUMERIC(12, 4),
    unemployment            NUMERIC(6, 3),
    gdp                     NUMERIC(14, 4),
    consumer_sentiment      NUMERIC(8, 3),
    high_yield_spread       NUMERIC(8, 4),
    dollar_index            NUMERIC(10, 4),
    treasury_1m             NUMERIC(8, 4),
    treasury_3m             NUMERIC(8, 4),
    treasury_6m             NUMERIC(8, 4),
    treasury_1y             NUMERIC(8, 4),
    treasury_2y             NUMERIC(8, 4),
    treasury_5y             NUMERIC(8, 4),
    treasury_7y             NUMERIC(8, 4),
    treasury_10y            NUMERIC(8, 4),
    treasury_30y            NUMERIC(8, 4),
    mortgage_15y_fixed      NUMERIC(8, 4),
    mortgage_30y_fixed      NUMERIC(8, 4),
    gold_spot_usd           NUMERIC(12, 4),
    silver_spot_usd         NUMERIC(12, 4),
    fetched_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS macro_daily_date_idx ON macro_daily (date DESC);

COMMENT ON TABLE  macro_daily IS
    'FRED 23-series macro snapshot. Dense forward-fill: every business day has a row, slow-moving series (CPI/UNRATE/GDP, MORTGAGE15US/30US weekly) carry last published value until FRED publishes new data. Written by scripts/horizon/macro_collector.py at 17:00 ET.';


-- ────────────────────────────────────────────────────────────────────────
-- 3. market_regimes — daily 5-regime classification
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_regimes (
    id                  SERIAL PRIMARY KEY,
    date                DATE NOT NULL UNIQUE,
    regime              VARCHAR(20) NOT NULL
                        CHECK (regime IN ('BULL_CALM', 'BULL_VOLATILE', 'BULL_STRESSED',
                                          'BEAR_PANIC', 'BEAR_GRIND')),
    spy_close           NUMERIC(14, 4),
    spy_vs_200ma        NUMERIC(8, 6),
    vix                 NUMERIC(10, 4),
    yield_curve         NUMERIC(8, 4),
    confidence_score    NUMERIC(5, 4),
    notes               TEXT,
    classified_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS market_regimes_date_idx ON market_regimes (date DESC);
CREATE INDEX IF NOT EXISTS market_regimes_regime_idx ON market_regimes (regime, date DESC);

COMMENT ON TABLE  market_regimes IS
    'Daily 5-regime classification. 5 regimes: BULL_CALM, BULL_VOLATILE, BULL_STRESSED, BEAR_PANIC, BEAR_GRIND. Written by scripts/horizon/regime_classifier.py at 17:15 ET. Depends on market_daily + macro_daily being current.';
COMMENT ON COLUMN market_regimes.spy_vs_200ma IS
    'SPY close minus its 200-day SMA, as a percentage of the SMA. Positive when SPY above 200MA.';
COMMENT ON COLUMN market_regimes.yield_curve IS
    'Snapshot of yield_curve_10y2y from macro_daily on this date. Negative = inverted.';
COMMENT ON COLUMN market_regimes.confidence_score IS
    'Distance from regime-boundary thresholds, clamped 0-1. Low values flag days near a regime transition.';
COMMENT ON COLUMN market_regimes.notes IS
    'Auto-generated rationale string, e.g. "SPY 3.2% above 200MA, VIX=12.4, curve=0.4 -> BULL_CALM".';


-- ────────────────────────────────────────────────────────────────────────
-- 4. market_sentiment — F&G + put/call + insider + AAII
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_sentiment (
    id                          SERIAL PRIMARY KEY,
    date                        DATE NOT NULL UNIQUE,
    fear_greed_index            INTEGER CHECK (fear_greed_index BETWEEN 0 AND 100),
    fear_greed_label            VARCHAR(20),
    put_call_ratio              NUMERIC(8, 4),
    insider_buy_sell_ratio      NUMERIC(10, 4),
    aaii_bull_pct               NUMERIC(5, 2),
    aaii_bear_pct               NUMERIC(5, 2),
    aaii_neutral_pct            NUMERIC(5, 2),
    aaii_week_ending            DATE,
    fetched_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS market_sentiment_date_idx ON market_sentiment (date DESC);

COMMENT ON TABLE  market_sentiment IS
    'Daily sentiment indicators: alternative.me F&G, CBOE put/call, OpenInsider 5d dollar-weighted, AAII weekly survey (forward-filled into daily rows). Written by scripts/horizon/sentiment_collector.py at 17:30 ET. Best-effort per source — partial-row writes allowed if one scrape fails.';
COMMENT ON COLUMN market_sentiment.insider_buy_sell_ratio IS
    'Dollar-weighted ratio over trailing 5 trading days, S&P 500 only: sum(buy_$) / sum(sell_$). >1 = net insider buying.';
COMMENT ON COLUMN market_sentiment.aaii_week_ending IS
    'The Thursday this AAII reading was published. Identical across daily rows until next Thursday.';


-- ────────────────────────────────────────────────────────────────────────
-- 5. market_events — FOMC + earnings + opex + macro releases
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_events (
    id                  SERIAL PRIMARY KEY,
    event_date          DATE NOT NULL,
    event_type          VARCHAR(50) NOT NULL
                        CHECK (event_type IN ('fomc', 'earnings', 'options_expiry',
                                              'cpi_release', 'nfp_release',
                                              'pce_release', 'gdp_release',
                                              'unemployment_release')),
    ticker              VARCHAR(10),
    description         TEXT,
    expected_impact     VARCHAR(10) CHECK (expected_impact IN ('high', 'medium', 'low')),
    actual_result       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Idempotent upsert key: (event_date, event_type, ticker). NULLs distinct in PG
-- UNIQUE — coerce via partial index with COALESCE so macro events (ticker=NULL)
-- still dedupe cleanly across re-runs.
CREATE UNIQUE INDEX IF NOT EXISTS market_events_uq_idx
    ON market_events (event_date, event_type, COALESCE(ticker, ''));

CREATE INDEX IF NOT EXISTS market_events_date_idx ON market_events (event_date);
CREATE INDEX IF NOT EXISTS market_events_type_date_idx ON market_events (event_type, event_date);
-- Partial-index on CURRENT_DATE rejected as non-IMMUTABLE; the plain
-- event_date index above is sufficient for upcoming-events queries
-- (BRIN-free range scan on a DATE column).

COMMENT ON TABLE  market_events IS
    'Forward-looking calendar of market-moving events. FOMC dates hardcoded for 12 months. Earnings from FMP API for the strategy universe. Monthly options expiry = 3rd Friday. Major macro releases (CPI/NFP/PCE/GDP/UE) hardcoded with annual regeneration. Written by scripts/horizon/events_calendar.py (no timer — invoked from morning_brief_generator pre-fetch).';
COMMENT ON COLUMN market_events.ticker IS
    'NULL for macro events (FOMC, CPI, NFP). Set for earnings + ticker-specific events.';
COMMENT ON COLUMN market_events.actual_result IS
    'Filled retroactively by operator or n8n hook. NULL until event resolves.';


-- ────────────────────────────────────────────────────────────────────────
-- 6. pattern_library — discovered correlations + win-rate buckets
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pattern_library (
    id                      SERIAL PRIMARY KEY,
    identified_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    pattern_type            VARCHAR(50) NOT NULL,
    description             TEXT,
    conditions              JSONB,
    historical_outcomes     JSONB,
    sample_size             INTEGER NOT NULL DEFAULT 0,
    win_rate                NUMERIC(5, 4),
    avg_return              NUMERIC(10, 6),
    avg_hold_days           NUMERIC(6, 2),
    confidence_score        NUMERIC(5, 4),
    strategies_affected     TEXT[],
    active                  BOOLEAN NOT NULL DEFAULT TRUE,
    last_triggered_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS pattern_library_type_idx ON pattern_library (pattern_type);
CREATE INDEX IF NOT EXISTS pattern_library_active_idx ON pattern_library (active, confidence_score DESC)
    WHERE active = TRUE;
CREATE INDEX IF NOT EXISTS pattern_library_last_triggered_idx ON pattern_library (last_triggered_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS pattern_library_strategies_idx ON pattern_library USING GIN (strategies_affected);
CREATE INDEX IF NOT EXISTS pattern_library_conditions_idx ON pattern_library USING GIN (conditions);

COMMENT ON TABLE  pattern_library IS
    'Patterns discovered by scripts/horizon/pattern_detector.py (weekly Sunday 21:00 ET). Read-only for HORIZON — analytics only, no feedback into strategy execution per self-contained-strategies rule. Scaffold self-gates if any ticker has <63 rows in market_daily.';
COMMENT ON COLUMN pattern_library.conditions IS
    'JSONB describing what market state triggers this pattern, e.g. {"regime": "BULL_CALM", "vix_max": 14}.';
COMMENT ON COLUMN pattern_library.historical_outcomes IS
    'JSONB summarizing what happened next, e.g. {"win_rate": 0.78, "avg_return_pct": 8.3, "n": 18, "lookforward_days": 5}.';
COMMENT ON COLUMN pattern_library.strategies_affected IS
    'Postgres array of strategy_id strings this pattern is relevant to (e.g. {strat_6_nasdaq_long, strat_8_sector_rotation}).';
COMMENT ON COLUMN pattern_library.active IS
    'FALSE retires a stale pattern without deletion (preserves history). Soft-retire only — never DELETE.';


-- ────────────────────────────────────────────────────────────────────────
-- 7. operator_config — single-row operator preference store
--
-- New table (no prior schema). Single-row enforced via CHECK (id=1). The
-- morning-brief trigger model uses this for:
--   brief_schedule          — NULL = no recurring brief; "HH:MM TZ" sets daily
--                              recurring fire; written by HORIZON SMS handler
--   brief_auto_on_incident  — TRUE = Grafana P2+ overnight triggers a 7am brief
--   last_brief_sent_at      — dedup guard so the schedule-poll can't fire twice
--                              the same day if n8n or systemd reruns the check
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS operator_config (
    id                          INTEGER PRIMARY KEY CHECK (id = 1),
    brief_schedule              VARCHAR(50),
    brief_auto_on_incident      BOOLEAN NOT NULL DEFAULT TRUE,
    last_brief_sent_at          TIMESTAMPTZ,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO operator_config (id, brief_schedule, brief_auto_on_incident)
VALUES (1, NULL, TRUE)
ON CONFLICT (id) DO NOTHING;

COMMENT ON TABLE  operator_config IS
    'Single-row operator preferences. Defaults: brief_schedule=NULL (no recurring brief), brief_auto_on_incident=TRUE (Grafana P2+ fires 7am brief). HORIZON''s SMS handler writes brief_schedule on "BRIEF DAILY 8AM" / "BRIEF CANCEL".';
COMMENT ON COLUMN operator_config.brief_schedule IS
    'NULL = no recurring brief. Format: "HH:MM TZ" e.g. "08:00 PT". The schedule-poller (n8n cron or wrapper script) reads this every minute and fires the brief when local-time matches.';
COMMENT ON COLUMN operator_config.last_brief_sent_at IS
    'Set to NOW() each time a brief is actually emitted (any trigger path). Schedule-poller compares against this to avoid duplicate firings.';


-- ────────────────────────────────────────────────────────────────────────
-- 8. investment_signals — LLM-curated investment ideas
-- ────────────────────────────────────────────────────────────────────────
-- Distinct from signals_log (per-strategy execution signals). This is the
-- HORIZON-facing layer: aggregated ideas with rationale + counter-argument,
-- written by future n8n workflow (deferred this session, table only).
CREATE TABLE IF NOT EXISTS investment_signals (
    id                      SERIAL PRIMARY KEY,
    generated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    signal_type             VARCHAR(50) NOT NULL
                            CHECK (signal_type IN ('regime_change', 'pattern_match',
                                                    'weather_edge', 'arb_opportunity',
                                                    'macro_shift', 'sentiment_extreme')),
    ticker                  VARCHAR(10),
    direction               VARCHAR(10) NOT NULL
                            CHECK (direction IN ('long', 'short', 'cash', 'rotate')),
    confidence_score        NUMERIC(5, 4),
    edge_pct                NUMERIC(8, 4),
    supporting_patterns     TEXT[],
    market_regime           VARCHAR(20),
    vix_at_signal           NUMERIC(10, 4),
    description             TEXT,
    recommendation          TEXT,
    counter_argument        TEXT,
    acted_on                BOOLEAN NOT NULL DEFAULT FALSE,
    outcome_pct             NUMERIC(8, 4),
    outcome_days            INTEGER,
    settled_at              TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS investment_signals_generated_idx ON investment_signals (generated_at DESC);
CREATE INDEX IF NOT EXISTS investment_signals_ticker_idx ON investment_signals (ticker, generated_at DESC);
CREATE INDEX IF NOT EXISTS investment_signals_acted_idx ON investment_signals (acted_on, generated_at DESC);
CREATE INDEX IF NOT EXISTS investment_signals_unsettled_idx ON investment_signals (generated_at)
    WHERE acted_on = TRUE AND settled_at IS NULL;
CREATE INDEX IF NOT EXISTS investment_signals_supporting_idx ON investment_signals USING GIN (supporting_patterns);

COMMENT ON TABLE  investment_signals IS
    'HORIZON-curated investment ideas, distinct from per-strategy execution signals (signals_log). Inserts by future n8n workflow (deferred). counter_argument intentionally always non-NULL when generated by HORIZON — bear case discipline.';
COMMENT ON COLUMN investment_signals.supporting_patterns IS
    'Array of stringified pattern_library.id values, e.g. {''42'', ''57''}. Cast to bigint[] in queries when joining.';
COMMENT ON COLUMN investment_signals.outcome_pct IS
    'Realized outcome after outcome_days. Filled by future settlement job: (price_after_N - price_at_signal) / price_at_signal, sign-adjusted by direction.';


-- ────────────────────────────────────────────────────────────────────────
-- View: v_ticker_analysis — denormalized per-(ticker, date) joins
-- ────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_ticker_analysis AS
SELECT
    md.ticker,
    md.date,
    md.open, md.high, md.low, md.close, md.volume,
    md.sma_20, md.sma_50, md.sma_100, md.sma_200,
    md.rsi_14, md.atr_14,
    md.bb_upper, md.bb_lower, md.bb_width,
    md.roc_9, md.roc_21, md.roc_63,
    md.volume_ratio,
    md.high_52w, md.low_52w, md.pct_from_52w_high,
    mac.vix,
    mac.yield_curve_10y2y,
    mac.yield_curve_10y3m,
    mac.fed_funds_rate,
    mac.cpi,
    mac.unemployment,
    mac.consumer_sentiment,
    mac.high_yield_spread,
    mac.dollar_index,
    mac.treasury_1m,
    mac.treasury_3m,
    mac.treasury_6m,
    mac.treasury_1y,
    mac.treasury_2y,
    mac.treasury_5y,
    mac.treasury_7y,
    mac.treasury_10y,
    mac.treasury_30y,
    mac.mortgage_15y_fixed,
    mac.mortgage_30y_fixed,
    mac.gold_spot_usd,
    mac.silver_spot_usd,
    reg.regime,
    reg.confidence_score   AS regime_confidence,
    reg.spy_vs_200ma,
    sent.fear_greed_index,
    sent.fear_greed_label,
    sent.put_call_ratio,
    sent.insider_buy_sell_ratio,
    sent.aaii_bull_pct,
    sent.aaii_bear_pct
FROM market_daily md
LEFT JOIN macro_daily      mac  ON mac.date  = md.date
LEFT JOIN market_regimes   reg  ON reg.date  = md.date
LEFT JOIN market_sentiment sent ON sent.date = md.date;

COMMENT ON VIEW v_ticker_analysis IS
    'Denormalized per-(ticker, date) view powering HORIZON''s analyze_ticker tool. JOINs market_daily + macro_daily + market_regimes + market_sentiment by date. Query: SELECT * FROM v_ticker_analysis WHERE ticker=$1 ORDER BY date DESC LIMIT 30.';


-- ────────────────────────────────────────────────────────────────────────
-- Role grants — bhn_trader (RW), agent_reader + grafana_reader (RO)
-- Roles are created in trading-schema.sql; this file only grants on the new tables.
-- ────────────────────────────────────────────────────────────────────────

-- bhn_trader: full read + insert + update on all new tables
-- (No DELETE — pattern_library uses active=FALSE for soft retire; others append-only.)
GRANT SELECT, INSERT, UPDATE ON market_daily        TO bhn_trader;
GRANT SELECT, INSERT, UPDATE ON macro_daily         TO bhn_trader;
GRANT SELECT, INSERT, UPDATE ON market_regimes      TO bhn_trader;
GRANT SELECT, INSERT, UPDATE ON market_sentiment    TO bhn_trader;
GRANT SELECT, INSERT, UPDATE ON market_events       TO bhn_trader;
GRANT SELECT, INSERT, UPDATE ON pattern_library     TO bhn_trader;
GRANT SELECT, INSERT, UPDATE ON investment_signals  TO bhn_trader;
GRANT SELECT, INSERT, UPDATE ON operator_config     TO bhn_trader;

-- Sequence grants — required for SERIAL/BIGSERIAL INSERTs to succeed
GRANT USAGE, SELECT ON SEQUENCE market_daily_id_seq             TO bhn_trader;
GRANT USAGE, SELECT ON SEQUENCE macro_daily_id_seq              TO bhn_trader;
GRANT USAGE, SELECT ON SEQUENCE market_regimes_id_seq           TO bhn_trader;
GRANT USAGE, SELECT ON SEQUENCE market_sentiment_id_seq         TO bhn_trader;
GRANT USAGE, SELECT ON SEQUENCE market_events_id_seq            TO bhn_trader;
GRANT USAGE, SELECT ON SEQUENCE pattern_library_id_seq          TO bhn_trader;
GRANT USAGE, SELECT ON SEQUENCE investment_signals_id_seq       TO bhn_trader;
-- operator_config uses CHECK(id=1), no sequence.

-- agent_reader: HORIZON's read-only role — gets SELECT on all new tables + view
GRANT SELECT ON market_daily        TO agent_reader;
GRANT SELECT ON macro_daily         TO agent_reader;
GRANT SELECT ON market_regimes      TO agent_reader;
GRANT SELECT ON market_sentiment    TO agent_reader;
GRANT SELECT ON market_events       TO agent_reader;
GRANT SELECT ON pattern_library     TO agent_reader;
GRANT SELECT ON investment_signals  TO agent_reader;
GRANT SELECT ON operator_config     TO agent_reader;
GRANT SELECT ON v_ticker_analysis   TO agent_reader;

-- grafana_reader: dashboard panels — SELECT on all new tables + view
GRANT SELECT ON market_daily        TO grafana_reader;
GRANT SELECT ON macro_daily         TO grafana_reader;
GRANT SELECT ON market_regimes      TO grafana_reader;
GRANT SELECT ON market_sentiment    TO grafana_reader;
GRANT SELECT ON market_events       TO grafana_reader;
GRANT SELECT ON pattern_library     TO grafana_reader;
GRANT SELECT ON investment_signals  TO grafana_reader;
GRANT SELECT ON operator_config     TO grafana_reader;
GRANT SELECT ON v_ticker_analysis   TO grafana_reader;


COMMIT;

-- ────────────────────────────────────────────────────────────────────────
-- Post-deploy operator action items
-- ────────────────────────────────────────────────────────────────────────
-- 1. Verify role grants from LA:
--      sudo -u postgres psql -d eventhorizon \
--        -c "\dp market_daily"
--    Should show bhn_trader=arw, agent_reader=r, grafana_reader=r.
--
-- 2. Add FRED_API_KEY to /etc/bhn-trading/env (free key at fred.stlouisfed.org/docs/api/api_key.html).
--    FMP_API_KEY should already be present (used by strat_2_value and strat_8_sector_rotation).
--
-- 3. Add SMTP credentials to /etc/bhn-trading/env for the morning brief:
--      SMTP_HOST=...
--      SMTP_PORT=587
--      SMTP_USER=...
--      SMTP_PASSWORD=...
--      SMTP_FROM=horizon@eventhorizonvpn.com
--      SMTP_TO=hayden.harper92@proton.me
--
-- 4. First-run sequence (after env is set up):
--      python3 scripts/horizon/market_data_collector.py --backfill
--      python3 scripts/horizon/macro_collector.py --backfill
--      python3 scripts/horizon/sentiment_collector.py
--      python3 scripts/horizon/regime_classifier.py
--      python3 scripts/horizon/events_calendar.py
--    Then enable systemd timers in scripts/horizon/systemd-units/.
--
-- 5. Pattern detector remains scaffold until market_daily has >= 63 rows per
--    ticker (~3 months). Self-gates and exits cleanly until then.
--
-- 6. Morning brief is operator-triggered (no daily systemd timer). Trigger paths:
--      - SMS to HORIZON: "BRIEF" or "MORNING BRIEF" → HORIZON SMS handler
--        invokes morning_brief_generator.py via webhook
--      - n8n manual webhook (operator clicks "Execute Workflow" in n8n UI)
--      - Grafana alertmanager P2+ overnight → webhook fires 7am brief
--        (only if operator_config.brief_auto_on_incident = TRUE)
--      - SMS "BRIEF DAILY 8AM" → HORIZON writes operator_config.brief_schedule
--        and n8n cron workflow polls it; SMS "BRIEF CANCEL" clears it
--    Default state on fresh deploy: brief_schedule=NULL, no automatic brief.
