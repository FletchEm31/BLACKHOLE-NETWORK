-- strategy-5-weather-schema.sql
-- BHN Strategy 5 weather-arbitrage tables. Per project_strategy_5_weather_arbitrage
-- memory: Strategy 5 has two parallel signal sources — macro-event ETFs (Polymarket/
-- Kalshi political/policy contracts → sector ETFs) and weather contracts
-- (Polymarket/Kalshi weather markets vs BHN's own weather_snapshots predictions).
--
-- These 3 tables capture the weather-arbitrage side. The macro-event side reuses
-- the framework's paper_trades / signals_log / strategy_performance tables
-- without needing schema additions.
--
-- Apply on LA hub AFTER trading-schema.sql:
--   sudo -u postgres psql -d eventhorizon -f sql/strategy-5-weather-schema.sql
--
-- Pre-req: trading-schema.sql applied (provides bhn_trader role + GRANT pattern).

\set ON_ERROR_STOP on

BEGIN;

-- ────────────────────────────────────────────────────────────────────────
-- 1. weather_forecasts — BHN's own model predictions
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_forecasts (
    id                  BIGSERIAL PRIMARY KEY,
    predicted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    target_date         DATE NOT NULL,
    region              TEXT NOT NULL,            -- e.g. 'NYC', 'LAX', 'MIA', 'tropical-atlantic'
    variable            TEXT NOT NULL,            -- 'precipitation_pct' | 'hurricane_track' | 'el_nino_phase' | etc.
    predicted_value     NUMERIC,                  -- raw forecast value (mm, mph, °F, etc.)
    predicted_probability NUMERIC,                -- 0-1 probability of a contract condition being true
    confidence          NUMERIC,                  -- 0-1 model confidence (uncertainty quantification)
    source_model        TEXT NOT NULL,            -- 'openweathermap' | 'noaa-gfs' | 'ecmwf' | 'bhn-ensemble'
    raw_payload         JSONB
);

CREATE INDEX IF NOT EXISTS weather_forecasts_target_idx
    ON weather_forecasts (target_date, region, variable);
CREATE INDEX IF NOT EXISTS weather_forecasts_predicted_idx
    ON weather_forecasts (predicted_at DESC);
CREATE INDEX IF NOT EXISTS weather_forecasts_region_var_idx
    ON weather_forecasts (region, variable, predicted_at DESC);

COMMENT ON TABLE  weather_forecasts IS
    'BHN-side weather model predictions. Sourced from weather_snapshots (OpenWeatherMap) initially; future: NOAA GFS, ECMWF, BHN-ensemble blends. Used for arbitrage vs Kalshi/Polymarket contract odds.';
COMMENT ON COLUMN weather_forecasts.predicted_probability IS
    'Probability the binary contract resolves YES. e.g. "NYC > 1 inch rain on May 15" → 0.65. Direct comparable to market implied probability for edge calc.';


-- ────────────────────────────────────────────────────────────────────────
-- 2. weather_contract_prices — Kalshi/Polymarket implied probabilities
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_contract_prices (
    id                  BIGSERIAL PRIMARY KEY,
    captured_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    exchange            TEXT NOT NULL CHECK (exchange IN ('kalshi', 'polymarket')),
    contract_id         TEXT NOT NULL,            -- exchange's own contract identifier
    contract_title      TEXT NOT NULL,
    implied_probability NUMERIC NOT NULL CHECK (implied_probability >= 0 AND implied_probability <= 1),
    yes_price           NUMERIC,                  -- cents on Kalshi (0-100); dollars on Polymarket (0-1)
    no_price            NUMERIC,
    volume_24h          NUMERIC,                  -- liquidity check
    open_interest       NUMERIC,
    resolution_date     DATE,                     -- when the contract settles
    region              TEXT,                     -- mapped from contract title — for joining w/ weather_forecasts
    variable            TEXT,                     -- same
    raw_payload         JSONB
);

CREATE INDEX IF NOT EXISTS weather_contract_prices_contract_time_idx
    ON weather_contract_prices (contract_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS weather_contract_prices_region_var_idx
    ON weather_contract_prices (region, variable, captured_at DESC)
    WHERE region IS NOT NULL AND variable IS NOT NULL;
CREATE INDEX IF NOT EXISTS weather_contract_prices_resolution_idx
    ON weather_contract_prices (resolution_date, captured_at DESC);

COMMENT ON TABLE  weather_contract_prices IS
    'Time-series of weather-contract prices from Kalshi + Polymarket. Polled every 10min during market hours; correlated against weather_forecasts for edge calc. region + variable are extracted from contract title via parser to enable JOIN to BHN forecasts.';
COMMENT ON COLUMN weather_contract_prices.implied_probability IS
    'Market-implied prob the contract resolves YES. Polymarket: yes_price ∈ [0,1] directly. Kalshi: yes_price ∈ [0,100] cents, divide by 100.';


-- ────────────────────────────────────────────────────────────────────────
-- 3. weather_model_accuracy — outcome tracking for self-improvement loop
-- ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_model_accuracy (
    id                          BIGSERIAL PRIMARY KEY,
    contract_id                 TEXT NOT NULL,
    contract_title              TEXT,
    region                      TEXT,
    variable                    TEXT,
    bhn_predicted_probability   NUMERIC NOT NULL,
    market_implied_probability  NUMERIC NOT NULL,
    edge                        NUMERIC NOT NULL,      -- bhn_predicted - market_implied (signed pp)
    bhn_position_taken          BOOLEAN NOT NULL DEFAULT FALSE,
    bhn_position_value          NUMERIC,
    bhn_position_side           TEXT CHECK (bhn_position_side IN ('yes', 'no')),
    actual_outcome              BOOLEAN,               -- TRUE = resolved YES; FALSE = resolved NO; NULL = unresolved
    bhn_was_correct             BOOLEAN,
    market_was_correct          BOOLEAN,
    pnl_dollar                  NUMERIC,               -- realized P&L if position taken
    accuracy_score              NUMERIC,               -- Brier-style: (predicted_prob - outcome)²; lower = better
    resolved_at                 TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS weather_model_accuracy_resolved_idx
    ON weather_model_accuracy (resolved_at DESC) WHERE resolved_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS weather_model_accuracy_region_var_idx
    ON weather_model_accuracy (region, variable, resolved_at DESC);
CREATE INDEX IF NOT EXISTS weather_model_accuracy_unresolved_idx
    ON weather_model_accuracy (resolved_at, created_at DESC) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS weather_model_accuracy_edge_idx
    ON weather_model_accuracy (ABS(edge) DESC);

COMMENT ON TABLE  weather_model_accuracy IS
    'Outcome ledger for the self-improvement loop. One row created on signal evaluation (bhn_position_taken may be false if read-only-mode or edge below threshold); resolved_at + actual_outcome filled in when contract settles. Weekly HORIZON workflow reads this to identify high-edge regions/variables and proposes threshold tweaks via the rules-mutator confirmation flow.';
COMMENT ON COLUMN weather_model_accuracy.edge IS
    'BHN predicted probability minus market implied. Positive = BHN thinks YES is undervalued. Negative = BHN thinks NO is undervalued (would short YES or buy NO).';
COMMENT ON COLUMN weather_model_accuracy.accuracy_score IS
    'Brier score component: (predicted_prob - outcome_as_0or1)². Smaller is better. Avg over windowed rows = model calibration quality.';


-- ────────────────────────────────────────────────────────────────────────
-- GRANTs — bhn_trader writes, agent_reader + grafana_reader read
-- ────────────────────────────────────────────────────────────────────────
GRANT SELECT, INSERT         ON weather_forecasts         TO bhn_trader;
GRANT USAGE, SELECT          ON SEQUENCE weather_forecasts_id_seq         TO bhn_trader;
GRANT SELECT, INSERT         ON weather_contract_prices   TO bhn_trader;
GRANT USAGE, SELECT          ON SEQUENCE weather_contract_prices_id_seq   TO bhn_trader;
GRANT SELECT, INSERT, UPDATE ON weather_model_accuracy    TO bhn_trader;
GRANT USAGE, SELECT          ON SEQUENCE weather_model_accuracy_id_seq    TO bhn_trader;

GRANT SELECT ON weather_forecasts, weather_contract_prices, weather_model_accuracy TO agent_reader;
GRANT SELECT ON weather_forecasts, weather_contract_prices, weather_model_accuracy TO grafana_reader;


COMMIT;
