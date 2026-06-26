-- 2026-06-26-contract-ledger-threshold-fix.sql
--
-- Fixes two bugs in refresh_contract_ledger():
--
-- Bug 1 (resolution): T-contracts (bucket_floor = bucket_cap) were never settling YES.
--   The original CASE used "temp >= floor AND temp < cap". When floor = cap (e.g. T86 = 86/86),
--   that condition is always FALSE regardless of actual temperature.
--   Fix: detect floor = cap as a threshold contract (cumulative >=) and use temp >= floor only.
--
-- Bug 2 (discovered at same time): the mip fringe filter was added to the edge calculator
--   (Python, separate commit) but the SQL function itself has no such filter — it still stores
--   whatever the edge sheet says. The ledger stores reality; the Python fix handles future signals.
--
-- Apply on LA:
--   sudo -u postgres psql -d eventhorizon -f /opt/bhn/sql/migrations/2026-06-26-contract-ledger-threshold-fix.sql
--
-- Then re-run the bootstrap to fix all historical resolution outcomes:
--   sudo -u postgres psql -d eventhorizon -c "SELECT refresh_contract_ledger(NULL);"
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
        -- Determine bucket settlement outcome for each contract.
        -- T-contracts (threshold) have bucket_floor = bucket_cap (e.g. T86 = floor=86, cap=86).
        -- They represent cumulative "high >= X" contracts, so resolution = temp >= floor.
        -- Range contracts have floor < cap; resolution = floor <= temp < cap.
        SELECT
            le.contract_ticker,
            CASE
                WHEN a.final_tmax_f IS NOT NULL THEN
                    CASE
                        -- Threshold contract (T-type): floor = cap → cumulative >= floor
                        WHEN le.bucket_floor IS NOT NULL
                         AND le.bucket_cap  IS NOT NULL
                         AND le.bucket_floor = le.bucket_cap
                            THEN a.final_tmax_f >= le.bucket_floor
                        -- Range bucket: floor < cap → floor ≤ temp < cap
                        WHEN le.bucket_floor IS NOT NULL AND le.bucket_cap IS NOT NULL
                            THEN a.final_tmax_f >= le.bucket_floor
                             AND a.final_tmax_f <  le.bucket_cap
                        -- Open-ended above: floor only
                        WHEN le.bucket_floor IS NOT NULL
                            THEN a.final_tmax_f >= le.bucket_floor
                        -- Open-ended below: cap only
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

        -- outcome_edge_realized: direction-aware actual(0/1) - entry_price
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

        -- paper_pnl — Kalshi binary P&L (matches weather_settlement_reconciliation.py):
        -- BET_YES correct: stake * (1-p)/p  | BET_YES wrong: -stake
        -- BET_NO  correct: stake * p/(1-p)  | BET_NO  wrong: -stake
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

        -- paper_pnl_pct = paper_pnl / stake_usd
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
        contract_resolved_yes   = EXCLUDED.contract_resolved_yes,
        bhn_correct             = EXCLUDED.bhn_correct,
        bhn_predicted_correctly = EXCLUDED.bhn_predicted_correctly,
        outcome_edge_realized   = EXCLUDED.outcome_edge_realized,
        paper_pnl               = EXCLUDED.paper_pnl,
        paper_pnl_pct           = EXCLUDED.paper_pnl_pct,
        ledger_updated_at       = NOW();

    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN v_rows;
END;
$$;

-- Re-run bootstrap to fix historical T-contract resolution outcomes
SELECT refresh_contract_ledger(NULL);
