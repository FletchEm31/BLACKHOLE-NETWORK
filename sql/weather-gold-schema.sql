-- BHN Strategy 9 — Gold Layer Schema
-- Model-ready features and final trading outputs.
-- Populated by weather_edge_calculator.py (runs every 5 minutes).
--
-- Apply via: sql/migrations/2026-06-11-weather-bsg-tables.sql

-- ─────────────────────────────────────────────────────────────────────────────
-- 1) weather_gold_city_day_features
--    Model-ready feature set per (city, date, contract_side).
--    One row per (station, date, side) — updated each calculator run.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_gold_city_day_features (
    id                      BIGSERIAL   PRIMARY KEY,
    city                    TEXT        NOT NULL,
    station_code            TEXT        NOT NULL,
    target_date             DATE        NOT NULL,
    contract_side           TEXT        NOT NULL,   -- 'high' or 'low'

    -- Latest forecasts
    latest_nws_tmax_f       NUMERIC,
    latest_nws_tmin_f       NUMERIC,
    latest_gfs_tmax_f       NUMERIC,
    latest_gfs_tmin_f       NUMERIC,

    -- Disagreement features
    forecast_spread         NUMERIC,    -- abs(nws_tmax - gfs_tmax)
    dewpoint_spread         NUMERIC,
    humidity_spread         NUMERIC,
    cloud_cover_change      NUMERIC,
    wind_shift_flag         BOOLEAN,

    -- Timing
    lead_time_hours         INTEGER,
    season                  TEXT,

    -- Historical bias (populated once silver_forecast_error has 7+ days)
    historical_bias_7d      NUMERIC,    -- avg forecast_error last 7 days
    historical_bias_30d     NUMERIC,    -- avg forecast_error last 30 days
    historical_mae_30d      NUMERIC,    -- mean absolute error last 30 days

    -- Market
    market_yes_mid          NUMERIC,
    market_implied_prob     NUMERIC,

    -- Label (NULL until settlement arrives)
    target_label            NUMERIC,    -- actual_tmax_f or actual_tmin_f

    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT gold_features_unique
        UNIQUE (station_code, target_date, contract_side)
);

CREATE INDEX IF NOT EXISTS gfeat_station_date_idx
    ON weather_gold_city_day_features (station_code, target_date DESC, contract_side);


-- ─────────────────────────────────────────────────────────────────────────────
-- 2) weather_gold_calibrated_probabilities
--    Per-bucket model probability + edge.
--    Written by edge calculator on each run — historical rows preserved.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_gold_calibrated_probabilities (
    id                      BIGSERIAL   PRIMARY KEY,
    city                    TEXT        NOT NULL,
    station_code            TEXT        NOT NULL,
    target_date             DATE        NOT NULL,
    contract_side           TEXT        NOT NULL,
    market_ticker           TEXT        NOT NULL,
    bucket_floor            NUMERIC,
    bucket_cap              NUMERIC,
    bucket_label            TEXT,

    raw_model_prob          NUMERIC,    -- before calibration
    calibrated_prob         NUMERIC,    -- after isotonic/platt (passthrough until 30 days)
    market_implied_prob     NUMERIC,    -- kalshi yes_mid
    edge                    NUMERIC,    -- calibrated_prob - market_implied_prob
    edge_rank               INTEGER,    -- 1 = best edge across all contracts today

    trade_flag              TEXT,       -- 'BET_YES','BET_NO','SKIP'
    confidence              TEXT,       -- 'HIGH','MEDIUM','LOW'
    model_delta_flag        TEXT,       -- 'AGREE' (delta<2°F), 'DIVERGE'

    calibrator_version      TEXT        DEFAULT 'v0_passthrough',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS gcprob_station_date_edge_idx
    ON weather_gold_calibrated_probabilities (station_code, target_date, contract_side, edge DESC);

CREATE INDEX IF NOT EXISTS gcprob_trade_date_idx
    ON weather_gold_calibrated_probabilities (trade_flag, target_date)
    WHERE trade_flag != 'SKIP';


-- ─────────────────────────────────────────────────────────────────────────────
-- 3) weather_gold_daily_edge_sheet
--    THE MAIN TRADING VIEW — one active row per (contract_ticker, sheet_date).
--    ON CONFLICT (contract_ticker, sheet_date) DO UPDATE on every calculator run.
--    sheet_date = CURRENT_DATE when the edge calculator writes the row.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_gold_daily_edge_sheet (
    id                      BIGSERIAL   PRIMARY KEY,
    city                    TEXT        NOT NULL,
    station_code            TEXT        NOT NULL,
    target_date             DATE        NOT NULL,
    contract_side           TEXT        NOT NULL,
    contract_ticker         TEXT        NOT NULL,
    bucket_floor            NUMERIC,
    bucket_cap              NUMERIC,
    bucket_label            TEXT,
    sheet_date              DATE        NOT NULL DEFAULT CURRENT_DATE,

    -- BHN forecast inputs
    raw_forecast_f          NUMERIC,    -- latest NWS tmax_f
    gfs_forecast_f          NUMERIC,    -- latest GFS tmax_f (NULL if no data yet)
    model_delta_f           NUMERIC,    -- abs(nws - gfs)
    model_confidence        TEXT,       -- 'HIGH' delta<2, 'MEDIUM' delta<4, 'LOW' else

    -- Model output
    calibrated_prob         NUMERIC,
    raw_model_prob          NUMERIC,

    -- Market
    market_implied_prob     NUMERIC,
    market_yes_mid          NUMERIC,
    market_volume           NUMERIC,
    market_liquidity        TEXT,       -- 'liquid','thin','illiquid'

    -- Edge
    edge                    NUMERIC,    -- calibrated_prob - market_implied_prob
    edge_pct                NUMERIC,    -- edge * 100
    edge_rank               INTEGER,    -- 1 = highest edge today

    -- Decision
    recommended_action      TEXT,       -- 'BET_YES','BET_NO','SKIP'
    stake_fraction          NUMERIC,    -- half-Kelly fraction
    stake_usd               NUMERIC,    -- stake_fraction * KALSHI_BANKROLL
    skip_reason             TEXT,       -- why SKIP if applicable

    -- Metadata
    last_updated            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    calibrator_version      TEXT        DEFAULT 'v0_passthrough',
    is_active               BOOLEAN     NOT NULL DEFAULT TRUE,

    CONSTRAINT gold_edge_sheet_unique
        UNIQUE (contract_ticker, sheet_date)
);

CREATE INDEX IF NOT EXISTS ges_target_action_edge_idx
    ON weather_gold_daily_edge_sheet (target_date, recommended_action, edge DESC);

CREATE INDEX IF NOT EXISTS ges_station_date_idx
    ON weather_gold_daily_edge_sheet (station_code, target_date);

CREATE INDEX IF NOT EXISTS ges_sheet_date_idx
    ON weather_gold_daily_edge_sheet (sheet_date DESC, recommended_action);

-- Permissions
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'horizon_agent_reader') THEN
        GRANT SELECT ON weather_gold_city_day_features TO horizon_agent_reader;
        GRANT SELECT ON weather_gold_calibrated_probabilities TO horizon_agent_reader;
        GRANT SELECT ON weather_gold_daily_edge_sheet TO horizon_agent_reader;
    END IF;
END $$;


-- ─────────────────────────────────────────────────────────────────────────────
-- 4) weather_gold_contract_ledger
--    CP4 Kelly-sizer signals — one row per Kalshi contract ticker.
--    Signal cols upserted every ~5 min by core_trading_orchestrator.py.
--    Settlement cols written by bhn-weather-settlement-recon (15:00 UTC daily);
--    ON CONFLICT DO UPDATE never touches settlement cols.
--    Currently DRY_RUN=true — no Kalshi orders placed.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_gold_contract_ledger (
    id                      BIGSERIAL       PRIMARY KEY,

    -- Identity
    city                    TEXT            NOT NULL,           -- 'Denver' | 'Los Angeles' | 'Miami'
    station_code            TEXT            NOT NULL,           -- KDEN | KLAX | KMIA
    target_date             DATE            NOT NULL,
    contract_side           TEXT            NOT NULL DEFAULT 'high',  -- tmax only; NO-side strategy
    contract_ticker         TEXT            NOT NULL,           -- e.g. KXHIGHLAX-26JUN29-69-70

    -- Bucket geometry
    bucket_floor            NUMERIC,                            -- NULL for open-ended tail buckets
    bucket_cap              NUMERIC,
    bucket_label            TEXT,                               -- e.g. '69-70', 'T66', 'T73'

    -- Forecast inputs captured at signal time
    nws_forecast_f          NUMERIC,                            -- raw NWS tmax (°F)
    gfs_forecast_f          NUMERIC,                            -- raw Open-Meteo GFS tmax (°F)

    -- Model probabilities (P(NO) in decimal 0–1)
    calibrated_prob         NUMERIC,
    raw_model_prob          NUMERIC,

    -- Model vs NWS divergence
    model_delta_f           NUMERIC,                            -- predicted_tmax_f − nws_forecast_f
    model_confidence        TEXT,                               -- 'HIGH' | 'MEDIUM' | 'LOW'
    model_delta_flag        TEXT,                               -- 'DIVERGE' (≥1.5°F) | 'CONVERGE'
    ensemble_spread         NUMERIC,                            -- abs(nws − gfs); NULL if one missing

    -- Market prices — ALWAYS read from DB, never derived (no_ask ≠ 1 − yes_ask)
    market_implied_prob     NUMERIC,                            -- no_ask (market's P(NO))
    market_yes_mid          NUMERIC,                            -- midpoint(yes_bid, 1 − no_ask)
    yes_bid                 NUMERIC,
    yes_ask                 NUMERIC,
    no_bid                  NUMERIC,
    no_ask                  NUMERIC,
    market_liquidity        TEXT,                               -- 'ILLIQUID' (default until volume available)

    -- Edge
    edge                    NUMERIC,                            -- model_prob_no − no_ask (decimal)
    edge_pct                NUMERIC,                            -- edge / no_ask
    edge_rank               INTEGER,                            -- 1 = best qualifying bucket by edge

    -- Decision
    recommended_action      TEXT,                               -- 'BET_NO' | 'SKIP'
    signal_strength         TEXT,                               -- 'STRONG' (≥15¢) | 'MODERATE' (≥10¢) | 'WEAK'
    stake_fraction          NUMERIC,                            -- half-Kelly, capped at BANKROLL_CAP_PCT (10%)
    stake_usd               NUMERIC,
    skip_reason             TEXT,                               -- 'INVALID_PRICE' | 'EDGE_TOO_LOW' | NULL
    is_active               BOOLEAN         NOT NULL DEFAULT TRUE,
    signal_generated_at     TIMESTAMPTZ,
    ledger_updated_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- ── Settlement actuals ──────────────────────────────────────────────────
    -- Written by bhn-weather-settlement-recon only.
    -- The ON CONFLICT DO UPDATE in core_trading_orchestrator.py deliberately
    -- excludes these columns so signal refreshes never clobber settled data.
    actual_tmax_f           NUMERIC,                            -- NULL until contract settles
    settled_at              TIMESTAMPTZ,                        -- UTC timestamp of Kalshi settlement
    contract_resolved_yes   BOOLEAN,                            -- TRUE if YES side won
    paper_pnl               NUMERIC,                            -- DRY_RUN P&L in USD (NULL until settled)

    CONSTRAINT weather_gold_contract_ledger_ticker_uq UNIQUE (contract_ticker)
);

CREATE INDEX IF NOT EXISTS ledger_station_date_idx
    ON weather_gold_contract_ledger (station_code, target_date DESC);

CREATE INDEX IF NOT EXISTS ledger_action_date_idx
    ON weather_gold_contract_ledger (recommended_action, target_date)
    WHERE recommended_action = 'BET_NO';

CREATE INDEX IF NOT EXISTS ledger_unsettled_idx
    ON weather_gold_contract_ledger (target_date)
    WHERE settled_at IS NULL AND is_active = TRUE;

GRANT SELECT ON weather_gold_contract_ledger TO grafana_reader;
GRANT SELECT, INSERT, UPDATE ON weather_gold_contract_ledger TO bhn_trader;
GRANT SELECT, INSERT, UPDATE ON weather_gold_contract_ledger TO ehuser;
GRANT USAGE, SELECT ON SEQUENCE weather_gold_contract_ledger_id_seq TO bhn_trader, ehuser;

-- ── is_legacy_row + performance view ────────────────────────────────────
-- Added by sql/migrations/2026-07-02-ledger-exclude-legacy-rows.sql.
-- Permanently flags/excludes two pre-CP4-pipeline populations that
-- distorted the Metabase Scorecard/Edge Tier dashboards: BET_YES rows
-- (the current pipeline architecturally never produces BET_YES, only
-- BET_NO/SKIP) and BET_NO rows carrying the flat $125 pre-Kelly stake
-- from the 2026-06-25/26 migration backfill. Decision: exclude
-- permanently, do not backfill/reconstruct. New rows default to FALSE and
-- should stay FALSE under the current pipeline. All performance/scorecard
-- dashboards should read from weather_gold_contract_ledger_performance,
-- not this base table directly.
--
-- ALTER TABLE weather_gold_contract_ledger
--     ADD COLUMN IF NOT EXISTS is_legacy_row BOOLEAN NOT NULL DEFAULT FALSE;
--
-- CREATE OR REPLACE VIEW weather_gold_contract_ledger_performance AS
-- SELECT * FROM weather_gold_contract_ledger WHERE is_legacy_row = FALSE;
--
-- (Kept here as documentation only — the migration file is the source of
-- truth for actually applying this. Already applied live on LA 2026-07-02.)
