-- Migration: 2026-06-25-contract-ledger
-- Creates weather_gold_contract_ledger — one row per Kalshi contract, all layers
-- consolidated: model inputs, market data at signal time, weather feature flags,
-- ENSO context, NWS settlement, and computed outcome metrics.
--
-- Also creates refresh_contract_ledger(p_target_date DATE) — call after each
-- settlement reconciliation run to populate/update ledger rows.
--
-- Snapshot first:
--   sudo -u postgres pg_dump eventhorizon > /mnt/eh-nvme-hot/backups/pre-contract-ledger-$(date +%Y%m%d-%H%M).sql
--
-- Deploy:
--   sudo -u postgres psql -d eventhorizon -f sql/migrations/2026-06-25-contract-ledger.sql
--
-- Rollback:
--   DROP TABLE IF EXISTS weather_gold_contract_ledger CASCADE;
--   DROP FUNCTION IF EXISTS refresh_contract_ledger(DATE);

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1) weather_gold_contract_ledger — master contract performance ledger
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS weather_gold_contract_ledger (
    id                      BIGSERIAL       PRIMARY KEY,

    -- ── Contract identity ──────────────────────────────────────────────────
    city                    TEXT            NOT NULL,
    station_code            TEXT            NOT NULL,
    target_date             DATE            NOT NULL,
    contract_side           TEXT            NOT NULL,   -- 'high' or 'low'
    contract_ticker         TEXT            NOT NULL,   -- Kalshi market_ticker
    bucket_floor            NUMERIC,
    bucket_cap              NUMERIC,
    bucket_label            TEXT,

    -- ── Model inputs (latest edge sheet snapshot at signal_generated_at) ──
    nws_forecast_f          NUMERIC,        -- edge_sheet.raw_forecast_f (latest NWS tmax)
    gfs_forecast_f          NUMERIC,        -- edge_sheet.gfs_forecast_f (latest GFS tmax)
    calibrated_prob         NUMERIC,        -- model probability after calibration
    raw_model_prob          NUMERIC,        -- pre-calibration probability
    model_delta_f           NUMERIC,        -- abs(nws - gfs) in °F
    model_confidence        TEXT,           -- HIGH (≤2°F) / MEDIUM (≤4°F) / LOW (>4°F)
    model_delta_flag        TEXT,           -- AGREE / DIVERGE / NO_GFS (calibrated_probs)
    ensemble_spread         NUMERIC,        -- Open-Meteo ensemble stddev across members
    nws_high_prob_pct       NUMERIC,        -- P(bucket) from NBM percentile CDF
    gfs_high_prob_pct       NUMERIC,        -- P(bucket) from GFS ensemble member count

    -- ── Edge / market context at signal time ──────────────────────────────
    market_implied_prob     NUMERIC,        -- Kalshi yes_mid at signal time
    market_yes_mid          NUMERIC,
    edge                    NUMERIC,        -- calibrated_prob - market_implied_prob (direction-aware)
    edge_pct                NUMERIC,        -- edge × 100
    edge_rank               INTEGER,        -- 1 = highest edge across all contracts today

    -- ── Signal decision ───────────────────────────────────────────────────
    recommended_action      TEXT,           -- BET_YES / BET_NO / SKIP
    signal_strength         TEXT,           -- STRONG (|edge|≥0.15) / MODERATE (≥0.08) / WEAK
    stake_fraction          NUMERIC,        -- half-Kelly fraction
    stake_usd               NUMERIC,        -- dollar stake (fraction × bankroll)
    skip_reason             TEXT,           -- reason string when SKIP
    is_active               BOOLEAN,        -- edge_sheet.is_active at signal time
    signal_generated_at     TIMESTAMPTZ,    -- edge_sheet.last_updated

    -- ── Bid/ask at signal time (bronze snapshot closest to signal_generated_at) ──
    yes_bid                 NUMERIC,        -- Kalshi YES bid (cents ÷ 100)
    yes_ask                 NUMERIC,
    no_bid                  NUMERIC,
    no_ask                  NUMERIC,
    open_interest           NUMERIC,
    volume                  NUMERIC,        -- edge_sheet.market_volume
    market_status           TEXT,           -- open / closed / settled
    market_liquidity        TEXT,           -- liquid / thin / illiquid (edge_sheet)

    -- ── Weather feature flags (from NWS hourly, computed by edge calculator) ──
    peak_hour               INTEGER,        -- hour 0-23 with highest daytime temp
    afternoon_storm_flag    BOOLEAN,        -- any hour 12-17 with PoP > 20%
    pre_peak_storm_flag     BOOLEAN,        -- storm in [12, peak_hour) — suppresses max
    cloud_timing_delta      NUMERIC,        -- (max cloud cover hour) - peak_hour
    sea_breeze_flag         BOOLEAN,        -- coastal cities only; NULL for inland

    -- ── ENSO context (most recent enso_index.week_ending ≤ target_date) ──
    enso_phase              TEXT,           -- el_nino / neutral / la_nina / *_strong
    enso_oni_value          NUMERIC,        -- Oceanic Niño Index (°C SST anomaly)

    -- ── Actual settlement ─────────────────────────────────────────────────
    -- Source: weather_silver_actuals_conformed WHERE is_final=TRUE AND actual_source='nws_cli'
    -- Precip: weather_bronze_visual_crossing_actuals (NWS CLI has no precip_in column)
    actual_tmax_f           NUMERIC,        -- final NWS daily high (°F) — settlement truth
    actual_tmin_f           NUMERIC,        -- final NWS daily low (°F)
    actual_precip_in        NUMERIC,        -- VC daily precip (in); NULL until VC backfill
    settled_at              TIMESTAMPTZ,    -- NWS CLI report_issued_at
    settlement_source       TEXT            NOT NULL DEFAULT 'nws_cli',

    -- ── Computed outcomes (NULL until settled) ────────────────────────────
    contract_resolved_yes   BOOLEAN,        -- did actual_tmax_f satisfy the bucket condition?
    bhn_correct             BOOLEAN,        -- recommended action matched outcome
    bhn_predicted_correctly BOOLEAN,        -- calibrated_prob > 0.5 matched outcome (no action req'd)
    outcome_edge_realized   NUMERIC,        -- direction-aware: actual(0/1) - entry_price
    paper_pnl               NUMERIC,        -- $ won/lost on half-Kelly stake (matches recon script)
    paper_pnl_pct           NUMERIC,        -- paper_pnl / stake_usd (fraction; -1.0 = full loss)

    -- ── Metadata ──────────────────────────────────────────────────────────
    ledger_updated_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT wcl_contract_unique UNIQUE (contract_ticker)
);

-- Covering index for accuracy-analysis queries
CREATE INDEX IF NOT EXISTS wcl_action_outcome_idx
    ON weather_gold_contract_ledger (recommended_action, contract_resolved_yes)
    WHERE recommended_action IN ('BET_YES', 'BET_NO');

CREATE INDEX IF NOT EXISTS wcl_target_date_idx
    ON weather_gold_contract_ledger (target_date DESC);

CREATE INDEX IF NOT EXISTS wcl_station_date_idx
    ON weather_gold_contract_ledger (station_code, target_date DESC);

CREATE INDEX IF NOT EXISTS wcl_enso_correct_idx
    ON weather_gold_contract_ledger (enso_phase, bhn_correct)
    WHERE enso_phase IS NOT NULL;

-- Unsettled contracts (for nightly settlement job)
CREATE INDEX IF NOT EXISTS wcl_unsettled_idx
    ON weather_gold_contract_ledger (target_date)
    WHERE contract_resolved_yes IS NULL;

COMMENT ON TABLE  weather_gold_contract_ledger IS
    'Gold-layer master performance ledger. One row per Kalshi weather contract. '
    'Combines edge sheet signal, bid/ask at decision time, weather feature flags, '
    'ENSO context, NWS CLI settlement, and paper P&L. Populated by refresh_contract_ledger().';
COMMENT ON COLUMN weather_gold_contract_ledger.nws_forecast_f IS
    'Latest NWS tmax forecast at signal time (edge_sheet.raw_forecast_f)';
COMMENT ON COLUMN weather_gold_contract_ledger.model_delta_flag IS
    'From weather_gold_calibrated_probabilities.model_delta_flag (not on edge sheet)';
COMMENT ON COLUMN weather_gold_contract_ledger.actual_precip_in IS
    'From weather_bronze_visual_crossing_actuals.precip_in — NWS CLI has no precip column';
COMMENT ON COLUMN weather_gold_contract_ledger.paper_pnl IS
    'Kalshi binary: YES correct = stake*(1-p)/p; YES wrong = -stake; NO correct = stake*p/(1-p); NO wrong = -stake';
COMMENT ON COLUMN weather_gold_contract_ledger.outcome_edge_realized IS
    'BET_YES: outcome(0/1) - market_implied_prob. BET_NO: market_implied_prob - outcome(0/1)';


-- ─────────────────────────────────────────────────────────────────────────────
-- 2) refresh_contract_ledger(p_target_date DATE DEFAULT NULL)
--    Upserts one row per contract_ticker. Call after settlement reconciliation.
--    p_target_date = NULL refreshes all contracts; DATE filters to one day.
--    Returns row count upserted.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION refresh_contract_ledger(
    p_target_date DATE DEFAULT NULL
)
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_rows INTEGER;
BEGIN
    WITH latest_edge AS (
        -- One row per contract: the most recently updated edge sheet entry
        SELECT DISTINCT ON (contract_ticker) *
        FROM weather_gold_daily_edge_sheet
        WHERE (p_target_date IS NULL OR target_date = p_target_date)
        ORDER BY contract_ticker, last_updated DESC
    ),
    actuals AS (
        -- NWS CLI final settlement (is_final=TRUE guarantees official report arrived)
        SELECT station_code, target_date,
               final_tmax_f, final_tmin_f, report_issued_at
        FROM weather_silver_actuals_conformed
        WHERE actual_source = 'nws_cli'
          AND is_final      = TRUE
          AND (p_target_date IS NULL OR target_date = p_target_date)
    ),
    resolved AS (
        -- Determine bucket settlement outcome for each contract
        SELECT
            le.contract_ticker,
            CASE
                WHEN a.final_tmax_f IS NOT NULL THEN
                    CASE
                        WHEN le.bucket_floor IS NOT NULL AND le.bucket_cap IS NOT NULL
                            THEN a.final_tmax_f >= le.bucket_floor
                             AND a.final_tmax_f <  le.bucket_cap
                        WHEN le.bucket_floor IS NOT NULL
                            THEN a.final_tmax_f >= le.bucket_floor
                        WHEN le.bucket_cap IS NOT NULL
                            THEN a.final_tmax_f <  le.bucket_cap
                        ELSE NULL
                    END
                ELSE NULL
            END AS contract_resolved_yes
        FROM latest_edge le
        LEFT JOIN actuals a
            ON a.station_code = le.station_code
           AND a.target_date  = le.target_date
    )
    INSERT INTO weather_gold_contract_ledger (
        city, station_code, target_date, contract_side, contract_ticker,
        bucket_floor, bucket_cap, bucket_label,
        nws_forecast_f, gfs_forecast_f,
        calibrated_prob, raw_model_prob,
        model_delta_f, model_confidence, model_delta_flag,
        ensemble_spread, nws_high_prob_pct, gfs_high_prob_pct,
        market_implied_prob, market_yes_mid,
        edge, edge_pct, edge_rank,
        recommended_action, signal_strength,
        stake_fraction, stake_usd, skip_reason, is_active, signal_generated_at,
        yes_bid, yes_ask, no_bid, no_ask, open_interest,
        volume, market_status, market_liquidity,
        peak_hour, afternoon_storm_flag, pre_peak_storm_flag,
        cloud_timing_delta, sea_breeze_flag,
        enso_phase, enso_oni_value,
        actual_tmax_f, actual_tmin_f, actual_precip_in,
        settled_at, settlement_source,
        contract_resolved_yes,
        bhn_correct, bhn_predicted_correctly,
        outcome_edge_realized, paper_pnl, paper_pnl_pct,
        ledger_updated_at
    )
    SELECT
        -- ── Identity ──────────────────────────────────────────────────────
        le.city,
        le.station_code,
        le.target_date,
        le.contract_side,
        le.contract_ticker,
        le.bucket_floor,
        le.bucket_cap,
        le.bucket_label,

        -- ── Model inputs ──────────────────────────────────────────────────
        le.raw_forecast_f               AS nws_forecast_f,
        le.gfs_forecast_f,
        le.calibrated_prob,
        le.raw_model_prob,
        le.model_delta_f,
        le.model_confidence,
        cp_lat.model_delta_flag,
        le.ensemble_spread,
        le.nws_high_prob_pct,
        le.gfs_high_prob_pct,

        -- ── Market context ────────────────────────────────────────────────
        le.market_implied_prob,
        le.market_yes_mid,
        le.edge,
        le.edge_pct,
        le.edge_rank,

        -- ── Signal decision ───────────────────────────────────────────────
        le.recommended_action,
        CASE
            WHEN ABS(le.edge) >= 0.15 THEN 'STRONG'
            WHEN ABS(le.edge) >= 0.08 THEN 'MODERATE'
            ELSE 'WEAK'
        END                             AS signal_strength,
        le.stake_fraction,
        le.stake_usd,
        le.skip_reason,
        le.is_active,
        le.last_updated                 AS signal_generated_at,

        -- ── Bid/ask at signal time (closest bronze snapshot ≤ signal + 5min) ──
        bkm.yes_bid,
        bkm.yes_ask,
        bkm.no_bid,
        bkm.no_ask,
        bkm.open_interest,
        le.market_volume                AS volume,
        bkm.market_status,
        le.market_liquidity,

        -- ── Weather feature flags ─────────────────────────────────────────
        le.peak_hour,
        le.afternoon_storm_flag,
        le.pre_peak_storm_flag,
        le.cloud_timing_delta,
        le.sea_breeze_flag,

        -- ── ENSO (most recent weekly reading ≤ target_date) ──────────────
        ei.phase                        AS enso_phase,
        ei.oni_value                    AS enso_oni_value,

        -- ── Settlement ────────────────────────────────────────────────────
        a.final_tmax_f                  AS actual_tmax_f,
        a.final_tmin_f                  AS actual_tmin_f,
        vc.precip_in                    AS actual_precip_in,
        a.report_issued_at              AS settled_at,
        'nws_cli'                       AS settlement_source,

        -- ── Resolved outcome ──────────────────────────────────────────────
        r.contract_resolved_yes,

        -- bhn_correct: recommended direction matched outcome
        CASE
            WHEN r.contract_resolved_yes IS NULL THEN NULL
            WHEN le.recommended_action = 'BET_YES' THEN r.contract_resolved_yes
            WHEN le.recommended_action = 'BET_NO'  THEN NOT r.contract_resolved_yes
            ELSE NULL
        END                             AS bhn_correct,

        -- bhn_predicted_correctly: calibrated_prob side matched outcome (no action required)
        CASE
            WHEN r.contract_resolved_yes IS NOT NULL AND le.calibrated_prob IS NOT NULL
                THEN (le.calibrated_prob > 0.5) = r.contract_resolved_yes
            ELSE NULL
        END                             AS bhn_predicted_correctly,

        -- outcome_edge_realized: direction-aware: actual(0/1) - entry_price
        -- BET_YES: outcome - market_implied_prob
        -- BET_NO:  market_implied_prob - outcome (NO perspective)
        CASE
            WHEN r.contract_resolved_yes IS NULL
              OR le.recommended_action = 'SKIP'
              OR le.market_implied_prob IS NULL
                THEN NULL
            WHEN le.recommended_action = 'BET_YES' THEN
                r.contract_resolved_yes::int::numeric - le.market_implied_prob
            WHEN le.recommended_action = 'BET_NO' THEN
                le.market_implied_prob - r.contract_resolved_yes::int::numeric
            ELSE NULL
        END                             AS outcome_edge_realized,

        -- paper_pnl — Kalshi binary P&L formula (matches weather_settlement_reconciliation.py):
        -- Buy n contracts at entry_price; each pays $1 if correct, $0 if wrong.
        -- n = stake_usd / entry_price
        -- BET_YES correct: (1 - entry_price) * n = stake_usd * (1-p)/p
        -- BET_YES wrong:   -stake_usd
        -- BET_NO  correct: stake_usd * p/(1-p)  [NO price = 1-p]
        -- BET_NO  wrong:   -stake_usd
        CASE
            WHEN r.contract_resolved_yes IS NULL
              OR le.recommended_action = 'SKIP'
              OR le.stake_usd IS NULL OR le.stake_usd = 0
              OR le.market_implied_prob IS NULL
                THEN NULL
            WHEN le.recommended_action = 'BET_YES' THEN
                CASE WHEN r.contract_resolved_yes THEN
                    ROUND(le.stake_usd * (1.0 - le.market_implied_prob)
                          / NULLIF(le.market_implied_prob, 0), 4)
                ELSE
                    ROUND(-le.stake_usd, 4)
                END
            WHEN le.recommended_action = 'BET_NO' THEN
                CASE WHEN NOT r.contract_resolved_yes THEN
                    ROUND(le.stake_usd * le.market_implied_prob
                          / NULLIF(1.0 - le.market_implied_prob, 0), 4)
                ELSE
                    ROUND(-le.stake_usd, 4)
                END
            ELSE NULL
        END                             AS paper_pnl,

        -- paper_pnl_pct = paper_pnl / stake_usd (fraction; -1.0 = full loss)
        CASE
            WHEN r.contract_resolved_yes IS NULL
              OR le.recommended_action = 'SKIP'
              OR le.stake_usd IS NULL OR le.stake_usd = 0
              OR le.market_implied_prob IS NULL
                THEN NULL
            WHEN le.recommended_action = 'BET_YES' THEN
                CASE WHEN r.contract_resolved_yes THEN
                    ROUND((1.0 - le.market_implied_prob)
                          / NULLIF(le.market_implied_prob, 0), 6)
                ELSE -1.0
                END
            WHEN le.recommended_action = 'BET_NO' THEN
                CASE WHEN NOT r.contract_resolved_yes THEN
                    ROUND(le.market_implied_prob
                          / NULLIF(1.0 - le.market_implied_prob, 0), 6)
                ELSE -1.0
                END
            ELSE NULL
        END                             AS paper_pnl_pct,

        NOW()                           AS ledger_updated_at

    FROM latest_edge le

    -- model_delta_flag: latest calibrated_prob row for this contract
    LEFT JOIN LATERAL (
        SELECT model_delta_flag
        FROM weather_gold_calibrated_probabilities
        WHERE market_ticker = le.contract_ticker
        ORDER BY created_at DESC
        LIMIT 1
    ) cp_lat ON true

    -- bid/ask: bronze snapshot closest to (but not more than 5 min after) signal time
    LEFT JOIN LATERAL (
        SELECT yes_bid, yes_ask, no_bid, no_ask, open_interest, market_status
        FROM weather_bronze_kalshi_market_snapshots
        WHERE market_ticker = le.contract_ticker
          AND retrieved_at  <= le.last_updated + INTERVAL '5 minutes'
        ORDER BY retrieved_at DESC
        LIMIT 1
    ) bkm ON true

    -- ENSO: most recent weekly reading on or before target_date
    LEFT JOIN LATERAL (
        SELECT phase, oni_value
        FROM enso_index
        WHERE week_ending <= le.target_date
        ORDER BY week_ending DESC
        LIMIT 1
    ) ei ON true

    -- NWS CLI settlement
    LEFT JOIN actuals a
        ON a.station_code = le.station_code
       AND a.target_date  = le.target_date

    -- VC precip (NWS CLI does not carry precipitation totals)
    LEFT JOIN weather_bronze_visual_crossing_actuals vc
        ON vc.station_code = le.station_code
       AND vc.target_date  = le.target_date

    JOIN resolved r ON r.contract_ticker = le.contract_ticker

    ON CONFLICT (contract_ticker) DO UPDATE SET
        city                    = EXCLUDED.city,
        station_code            = EXCLUDED.station_code,
        target_date             = EXCLUDED.target_date,
        contract_side           = EXCLUDED.contract_side,
        bucket_floor            = EXCLUDED.bucket_floor,
        bucket_cap              = EXCLUDED.bucket_cap,
        bucket_label            = EXCLUDED.bucket_label,
        nws_forecast_f          = EXCLUDED.nws_forecast_f,
        gfs_forecast_f          = EXCLUDED.gfs_forecast_f,
        calibrated_prob         = EXCLUDED.calibrated_prob,
        raw_model_prob          = EXCLUDED.raw_model_prob,
        model_delta_f           = EXCLUDED.model_delta_f,
        model_confidence        = EXCLUDED.model_confidence,
        model_delta_flag        = EXCLUDED.model_delta_flag,
        ensemble_spread         = EXCLUDED.ensemble_spread,
        nws_high_prob_pct       = COALESCE(EXCLUDED.nws_high_prob_pct, weather_gold_contract_ledger.nws_high_prob_pct),
        gfs_high_prob_pct       = COALESCE(EXCLUDED.gfs_high_prob_pct, weather_gold_contract_ledger.gfs_high_prob_pct),
        market_implied_prob     = EXCLUDED.market_implied_prob,
        market_yes_mid          = EXCLUDED.market_yes_mid,
        edge                    = EXCLUDED.edge,
        edge_pct                = EXCLUDED.edge_pct,
        edge_rank               = EXCLUDED.edge_rank,
        recommended_action      = EXCLUDED.recommended_action,
        signal_strength         = EXCLUDED.signal_strength,
        stake_fraction          = EXCLUDED.stake_fraction,
        stake_usd               = EXCLUDED.stake_usd,
        skip_reason             = EXCLUDED.skip_reason,
        is_active               = EXCLUDED.is_active,
        signal_generated_at     = EXCLUDED.signal_generated_at,
        yes_bid                 = COALESCE(EXCLUDED.yes_bid,          weather_gold_contract_ledger.yes_bid),
        yes_ask                 = COALESCE(EXCLUDED.yes_ask,          weather_gold_contract_ledger.yes_ask),
        no_bid                  = COALESCE(EXCLUDED.no_bid,           weather_gold_contract_ledger.no_bid),
        no_ask                  = COALESCE(EXCLUDED.no_ask,           weather_gold_contract_ledger.no_ask),
        open_interest           = COALESCE(EXCLUDED.open_interest,    weather_gold_contract_ledger.open_interest),
        volume                  = EXCLUDED.volume,
        market_status           = COALESCE(EXCLUDED.market_status,    weather_gold_contract_ledger.market_status),
        market_liquidity        = EXCLUDED.market_liquidity,
        peak_hour               = COALESCE(EXCLUDED.peak_hour,             weather_gold_contract_ledger.peak_hour),
        afternoon_storm_flag    = COALESCE(EXCLUDED.afternoon_storm_flag,  weather_gold_contract_ledger.afternoon_storm_flag),
        pre_peak_storm_flag     = COALESCE(EXCLUDED.pre_peak_storm_flag,   weather_gold_contract_ledger.pre_peak_storm_flag),
        cloud_timing_delta      = COALESCE(EXCLUDED.cloud_timing_delta,    weather_gold_contract_ledger.cloud_timing_delta),
        sea_breeze_flag         = COALESCE(EXCLUDED.sea_breeze_flag,       weather_gold_contract_ledger.sea_breeze_flag),
        enso_phase              = COALESCE(EXCLUDED.enso_phase,       weather_gold_contract_ledger.enso_phase),
        enso_oni_value          = COALESCE(EXCLUDED.enso_oni_value,   weather_gold_contract_ledger.enso_oni_value),
        actual_tmax_f           = COALESCE(EXCLUDED.actual_tmax_f,    weather_gold_contract_ledger.actual_tmax_f),
        actual_tmin_f           = COALESCE(EXCLUDED.actual_tmin_f,    weather_gold_contract_ledger.actual_tmin_f),
        actual_precip_in        = COALESCE(EXCLUDED.actual_precip_in, weather_gold_contract_ledger.actual_precip_in),
        settled_at              = COALESCE(EXCLUDED.settled_at,        weather_gold_contract_ledger.settled_at),
        settlement_source       = COALESCE(EXCLUDED.settlement_source, weather_gold_contract_ledger.settlement_source),
        contract_resolved_yes   = COALESCE(EXCLUDED.contract_resolved_yes, weather_gold_contract_ledger.contract_resolved_yes),
        bhn_correct             = COALESCE(EXCLUDED.bhn_correct,           weather_gold_contract_ledger.bhn_correct),
        bhn_predicted_correctly = COALESCE(EXCLUDED.bhn_predicted_correctly, weather_gold_contract_ledger.bhn_predicted_correctly),
        outcome_edge_realized   = COALESCE(EXCLUDED.outcome_edge_realized, weather_gold_contract_ledger.outcome_edge_realized),
        paper_pnl               = COALESCE(EXCLUDED.paper_pnl,        weather_gold_contract_ledger.paper_pnl),
        paper_pnl_pct           = COALESCE(EXCLUDED.paper_pnl_pct,    weather_gold_contract_ledger.paper_pnl_pct),
        ledger_updated_at       = NOW();

    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN v_rows;
END;
$$;

COMMENT ON FUNCTION refresh_contract_ledger(DATE) IS
    'Upserts weather_gold_contract_ledger. Call from weather_settlement_reconciliation.py '
    'after writing weather_model_accuracy. p_target_date=NULL refreshes all contracts. '
    'Returns number of rows inserted or updated.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 3) Grants
-- ─────────────────────────────────────────────────────────────────────────────

GRANT SELECT ON weather_gold_contract_ledger TO grafana_reader;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ehuser') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON weather_gold_contract_ledger TO ehuser;
        GRANT USAGE, SELECT ON SEQUENCE weather_gold_contract_ledger_id_seq TO ehuser;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'horizon_agent_reader') THEN
        GRANT SELECT ON weather_gold_contract_ledger TO horizon_agent_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_reader') THEN
        GRANT SELECT ON weather_gold_contract_ledger TO agent_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bhn_trader') THEN
        GRANT SELECT, INSERT, UPDATE ON weather_gold_contract_ledger TO bhn_trader;
        GRANT EXECUTE ON FUNCTION refresh_contract_ledger(DATE) TO bhn_trader;
    END IF;
END
$$;

-- Also grant function execute to ehuser for manual refreshes
GRANT EXECUTE ON FUNCTION refresh_contract_ledger(DATE) TO ehuser;

COMMIT;

\echo ''
\echo '=== weather_gold_contract_ledger created. Verify with:'
\echo "SELECT COUNT(*) FROM weather_gold_contract_ledger;"
\echo ''
\echo '=== Bootstrap historical rows (all contracts):'
\echo "SELECT refresh_contract_ledger(NULL);"
\echo ''
\echo '=== Row count after bootstrap:'
\echo "SELECT COUNT(*), COUNT(*) FILTER (WHERE contract_resolved_yes IS NOT NULL) AS settled FROM weather_gold_contract_ledger;"
