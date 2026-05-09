-- horizon-schema.sql
-- HORIZON Phase 3 schema additions. Foundation tables that downstream modules
-- depend on (cost-cascade, eBay watchlist, trading rules, voice transcripts,
-- market signal capture). Pure schema, no data. Idempotent — safe to re-run.
--
-- Apply on LA hub:
--   sudo -u postgres psql -d eventhorizon -f sql/horizon-schema.sql
--
-- See infrastructure/docs/horizon-roadmap.md for the build context behind each
-- table. Module references (M1-M10) point at sections of that doc.

-- ════════════════════════════════════════════════════════════════════════════
-- ITEM 2 — Memory lanes
-- ════════════════════════════════════════════════════════════════════════════
-- The roadmap defines three logical lanes (conversation / security_event /
-- market_data) all sharing the existing `memories` pgvector table. The
-- existing `memory_type` enum already includes 'conversation'. Adding the
-- two new lane values + tables for the things that don't naturally fit into
-- a single embedding (full call transcripts, raw market signal time series).

ALTER TABLE memories DROP CONSTRAINT IF EXISTS memories_memory_type_check;
ALTER TABLE memories ADD CONSTRAINT memories_memory_type_check
  CHECK (memory_type = ANY (ARRAY[
    'incident'::text,
    'operator_pref'::text,
    'project_context'::text,
    'deployment'::text,
    'observation'::text,
    'conversation'::text,
    'reference'::text,
    'security_event'::text,    -- HORIZON lane: security audit trail (forever)
    'market_data'::text        -- HORIZON lane: eBay + financial signals (1y rolling)
  ]));

-- Full call transcripts. The `transcript` column holds the raw STT output
-- (Whisper). HORIZON also writes a derived `summary` after the call, plus
-- creates a memories row with memory_type='conversation' linking back here
-- via related_memory_id. Raw audio is NOT persisted (deleted immediately
-- after STT per recording posture in horizon-roadmap.md).
CREATE TABLE IF NOT EXISTS call_transcripts (
    id                  BIGSERIAL PRIMARY KEY,
    call_id             TEXT UNIQUE NOT NULL,        -- Twilio call SID
    direction           TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    started_at          TIMESTAMPTZ NOT NULL,
    ended_at            TIMESTAMPTZ,
    duration_seconds    INT,
    other_party         TEXT,                        -- callee/caller E.164 number
    other_party_label   TEXT,                        -- 'friend' | 'family' | 'business' | 'unknown'
    recording_phase     TEXT NOT NULL DEFAULT 'production'
                        CHECK (recording_phase IN ('friend_family', 'business_test', 'production')),
    transcript          TEXT NOT NULL,
    summary             TEXT,                        -- HORIZON-generated post-call summary
    purpose             TEXT,                        -- why the call was placed
    outcome             TEXT,                        -- result classification
    related_memory_id   BIGINT REFERENCES memories(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS call_transcripts_started_idx       ON call_transcripts (started_at DESC);
CREATE INDEX IF NOT EXISTS call_transcripts_other_party_idx   ON call_transcripts (other_party);
CREATE INDEX IF NOT EXISTS call_transcripts_phase_idx         ON call_transcripts (recording_phase);
COMMENT ON TABLE call_transcripts IS
  'M7 outbound + future inbound voice calls. Raw audio NEVER stored. Retention: 90d hot / 1y cold / purge.';

-- Time-series market signal capture. Different shape from `memories` —
-- this is dense, numerical, queried with time-range scans, not semantic
-- similarity. Feeds M2 morning briefing, M3 evening summary, M4 intraday
-- alerts, M6 trading rules engine.
CREATE TABLE IF NOT EXISTS market_signals (
    id           BIGSERIAL PRIMARY KEY,
    captured_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source       TEXT NOT NULL,                      -- 'fmp' | 'alpaca' | 'ebay' | 'newsapi' | 'openweathermap'
    symbol       TEXT,                               -- ticker, card composite key, etc. NULL for non-symbol signals
    signal_type  TEXT NOT NULL,                      -- 'price' | 'volume' | 'rsi' | 'sentiment' | 'comp_avg_90d' | 'listing_seen' | etc.
    value        DECIMAL,                            -- numeric value if applicable
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb, -- source-specific extras
    expires_at   TIMESTAMPTZ                         -- NULL = retain per type rule; populated explicitly for ephemeral signals
);
CREATE INDEX IF NOT EXISTS market_signals_captured_idx       ON market_signals (captured_at DESC);
CREATE INDEX IF NOT EXISTS market_signals_symbol_time_idx    ON market_signals (symbol, captured_at DESC);
CREATE INDEX IF NOT EXISTS market_signals_source_type_idx    ON market_signals (source, signal_type);
CREATE INDEX IF NOT EXISTS market_signals_expires_idx        ON market_signals (expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS market_signals_metadata_idx       ON market_signals USING GIN (metadata);
COMMENT ON TABLE market_signals IS
  'Time-series market data feed (FMP, Alpaca, eBay comps, NewsAPI, weather). Hot 90d / cold 1y / purge.';

-- ════════════════════════════════════════════════════════════════════════════
-- ITEM 4 — eBay watchlist + trading rules
-- ════════════════════════════════════════════════════════════════════════════

-- eBay watchlist. Operator-defined target cards. M5 polls eBay Browse API
-- against rows where active=TRUE, computes alert against rolling 90d comp
-- average from market_signals.
CREATE TABLE IF NOT EXISTS ebay_watchlist (
    id                          SERIAL PRIMARY KEY,
    set_name                    TEXT NOT NULL,                  -- e.g. 'Team Rocket 1st Edition'
    card_name                   TEXT,                           -- specific card; NULL = whole set
    grader                      TEXT CHECK (grader IS NULL OR grader IN ('PSA','CGC','SGC','BGS')),
    grade_value                 DECIMAL(3,1),                   -- 10.0, 9.5, 9.0, ...
    cgc_blue_label              BOOLEAN DEFAULT FALSE,           -- CGC Blue Label flag (highest tier)
    max_price_pct_of_90d_avg    INT,                            -- alert if listing < avg * pct/100 (e.g. 80 = "20%+ below comp avg")
    max_price_absolute          DECIMAL(10,2),                   -- hard cap in USD; both filters apply if both set
    active                      BOOLEAN NOT NULL DEFAULT TRUE,
    notes                       TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ebay_watchlist_active_idx     ON ebay_watchlist (active) WHERE active = TRUE;
CREATE INDEX IF NOT EXISTS ebay_watchlist_set_card_idx   ON ebay_watchlist (set_name, card_name);
COMMENT ON TABLE ebay_watchlist IS
  'M5 watchlist for eBay card monitoring. Operator-edited via n8n UI or admin workflow. NEVER hardcode targets in scripts.';

-- Trading rules engine. M6 evaluates active rules each polling cycle,
-- triggers confirmation flow when threshold crossed.
CREATE TABLE IF NOT EXISTS trading_rules (
    id                  SERIAL PRIMARY KEY,
    symbol              TEXT NOT NULL,                          -- ticker (AAPL, SPY, BTC-USD, etc.)
    direction           TEXT NOT NULL CHECK (direction IN ('long','short','hedge')),
    trigger_type        TEXT NOT NULL,                          -- 'rsi_below', 'rsi_above', 'pct_drop', 'pct_gain', 'price_below', 'price_above', 'ma_cross', etc.
    threshold           DECIMAL,                                -- value the trigger compares against
    timeframe           TEXT,                                   -- '1d', '5min', '1h', etc. for momentum/lookback rules
    action              TEXT NOT NULL,                          -- 'buy 5', 'sell 100%', 'sell_pct 50', etc. Free-text v1; structured later.
    cooldown_minutes    INT NOT NULL DEFAULT 60,                -- min time between triggers for same rule
    last_triggered_at   TIMESTAMPTZ,
    paper_or_live       TEXT NOT NULL DEFAULT 'paper'
                        CHECK (paper_or_live IN ('paper','live')),
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS trading_rules_active_idx   ON trading_rules (active) WHERE active = TRUE;
CREATE INDEX IF NOT EXISTS trading_rules_symbol_idx   ON trading_rules (symbol);
COMMENT ON TABLE trading_rules IS
  'M6 trading rules. paper_or_live=live ONLY after explicit STATUS.md PROMOTE TO LIVE entry per rule.';

-- ════════════════════════════════════════════════════════════════════════════
-- ITEM 7 — Q&A cache (cost-cascade layer 3)
-- ════════════════════════════════════════════════════════════════════════════

-- Cached question→answer pairs. Layer 3 of the cost cascade (after PG keyword
-- and pgvector similarity, before the Haiku classifier). Hash-based exact
-- lookup for fast reuse + embedding for semantic-similarity matching of
-- paraphrased questions.
--
-- expires_at lets us mark answers as stale-by-time (weather, market price
-- queries) while letting durable answers (operator preferences, architecture
-- explanations) live forever.
CREATE TABLE IF NOT EXISTS qa_cache (
    id              BIGSERIAL PRIMARY KEY,
    question        TEXT NOT NULL,
    question_hash   TEXT NOT NULL UNIQUE,                       -- SHA-256 of normalized question
    answer          TEXT NOT NULL,
    embedding       vector(384),                                -- for semantic match against paraphrased questions
    model_used      TEXT,                                       -- 'haiku' | 'sonnet' | 'manual' | 'pg_lookup'
    confidence      SMALLINT,                                   -- 1-10, how sure HORIZON is in this cached answer
    hit_count       INT NOT NULL DEFAULT 0,
    expires_at      TIMESTAMPTZ,                                -- NULL = never expires
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_hit_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS qa_cache_hash_idx       ON qa_cache (question_hash);
CREATE INDEX IF NOT EXISTS qa_cache_embedding_idx  ON qa_cache USING hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64');
CREATE INDEX IF NOT EXISTS qa_cache_expires_idx    ON qa_cache (expires_at) WHERE expires_at IS NOT NULL;
COMMENT ON TABLE qa_cache IS
  'Cost-cascade layer 3. Free lookup before Haiku classifier. Operator can also pre-populate with manual entries (model_used="manual") for FAQ-style content.';

-- ════════════════════════════════════════════════════════════════════════════
-- Permissions
-- ════════════════════════════════════════════════════════════════════════════

-- agent_reader (used by HORIZON's query_db tool) — SELECT on all new tables
GRANT SELECT ON call_transcripts, market_signals, ebay_watchlist, trading_rules, qa_cache TO agent_reader;

-- n8n_user (used by HORIZON workflows for writes) — INSERT/UPDATE on operational tables
GRANT INSERT, UPDATE ON call_transcripts, market_signals, qa_cache TO n8n_user;
GRANT USAGE, SELECT ON SEQUENCE call_transcripts_id_seq, market_signals_id_seq, qa_cache_id_seq TO n8n_user;

-- ebay_watchlist + trading_rules are operator-edited config; n8n_user gets
-- read-only and full edits go through a small admin path (operator-confirmed
-- via SMS) rather than auto-mutated by workflows.
GRANT SELECT ON ebay_watchlist, trading_rules TO n8n_user;
GRANT INSERT, UPDATE ON ebay_watchlist, trading_rules TO n8n_user;  -- v1: trust workflow; v2 will lock down via separate role
GRANT USAGE, SELECT ON SEQUENCE ebay_watchlist_id_seq, trading_rules_id_seq TO n8n_user;

-- grafana_reader — SELECT on everything for dashboards
GRANT SELECT ON call_transcripts, market_signals, ebay_watchlist, trading_rules, qa_cache TO grafana_reader;
