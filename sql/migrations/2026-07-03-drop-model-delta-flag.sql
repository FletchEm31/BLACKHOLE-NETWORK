-- 2026-07-03-drop-model-delta-flag.sql
--
-- Drop weather_gold_contract_ledger.model_delta_flag — an overloaded column
-- with two incompatible writers, neither of which is needed anymore.
--
-- History (confirmed via git log):
--   - Column originated 2026-06-25/26 with weather_gold_contract_ledger,
--     written by weather_edge_calculator.py: 'AGREE'/'DIVERGE'/'NO_GFS'
--     based on abs(nws_tmax_f - gfs_tmax_f) >= 2.0 (NWS-vs-GFS divergence).
--   - 2026-06-30, commit f4620da ("deploy CP1-CP4 pipeline"): cp4_kelly_sizer.py
--     started ALSO writing this same column: 'DIVERGE'/'CONVERGE' based on
--     abs(predicted_tmax_f - nws_forecast_f) >= 1.5 (model-vs-NWS divergence —
--     a different comparison entirely). weather_edge_calculator.py's timer
--     (bhn-weather-edge-calculator.timer) was stopped that same night as part
--     of the cutover, orphaning its half of the column's meaning.
--
-- Why drop rather than pick one meaning:
--   - CP4's meaning (model-vs-NWS) is fully redundant with model_confidence,
--     which is already written by the same INSERT from the same model_delta_f
--     value, at finer granularity (HIGH/MEDIUM/LOW vs DIVERGE/CONVERGE), and
--     is the field actually read by Metabase (CLEAN_QUERIES.sql).
--   - weather_edge_calculator.py's meaning (NWS-vs-GFS) is redundant with the
--     ledger's own numeric ensemble_spread column (abs(nws_tmax_f - om_tmax_f)),
--     and its only writer has been intentionally offline since 2026-06-30.
--   - Grepped every Metabase query, Grafana dashboard, and n8n workflow:
--     zero readers reference model_delta_flag directly.
--   - Confirmed live: the column currently holds a mix of both vocabularies
--     across different rows (stale 'AGREE' on rows CP4 no longer revisits,
--     fresh 'CONVERGE'/'DIVERGE' on rows CP4 is actively re-signaling) —
--     actively misleading to anyone who reads it as one consistent signal.
--
-- Companion code changes (apply together, not included in this SQL file):
--   - scripts/weather/cp4_kelly_sizer.py: remove model_delta_flag from
--     _LEDGER_UPSERT (INSERT column list, VALUES, ON CONFLICT SET) and from
--     the results dict in run_cp4_kelly().
--   - scripts/trading/weather_edge_calculator.py: remove model_delta_flag
--     param from _upsert_calibrated_probabilities() and its call site —
--     cosmetic only, since this script's timer is already stopped, but keeps
--     the table's own schema/writer consistent if it's ever revived.
--
-- Run on LA (after code changes are deployed, so no writer targets a column
-- that no longer exists):
--   psql -U postgres eventhorizon -f sql/migrations/2026-07-03-drop-model-delta-flag.sql

\set ON_ERROR_STOP on

BEGIN;

-- 1. Drop the column from the ledger.
ALTER TABLE weather_gold_contract_ledger
    DROP COLUMN IF EXISTS model_delta_flag;

-- 2. Recreate refresh_contract_ledger() without the cp_lat LATERAL join
--    (which existed solely to source model_delta_flag from the now-frozen
--    weather_gold_calibrated_probabilities table) and without model_delta_flag
--    in the INSERT/SELECT/ON CONFLICT lists.
CREATE OR REPLACE FUNCTION public.refresh_contract_ledger(p_target_date date DEFAULT NULL::date)
 RETURNS integer
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_rows INTEGER;
BEGIN
    WITH latest_edge AS (
        SELECT DISTINCT ON (contract_ticker) *
        FROM weather_gold_daily_edge_sheet
        WHERE (p_target_date IS NULL OR target_date = p_target_date)
        ORDER BY contract_ticker, last_updated DESC
    ),
    actuals AS (
        SELECT station_code, target_date,
               final_tmax_f, final_tmin_f, report_issued_at
        FROM weather_silver_actuals_conformed
        WHERE actual_source = 'nws_cli'
          AND is_final      = TRUE
          AND (p_target_date IS NULL OR target_date = p_target_date)
    ),
    resolved AS (
        SELECT
            le.contract_ticker,
            CASE
                WHEN a.final_tmax_f IS NOT NULL THEN
                    CASE
                        WHEN le.bucket_floor IS NOT NULL
                         AND le.bucket_cap  IS NOT NULL
                         AND le.bucket_floor = le.bucket_cap
                            THEN a.final_tmax_f >= le.bucket_floor
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
        model_delta_f, model_confidence,
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
        le.city,
        le.station_code,
        le.target_date,
        le.contract_side,
        le.contract_ticker,
        le.bucket_floor,
        le.bucket_cap,
        le.bucket_label,

        le.raw_forecast_f               AS nws_forecast_f,
        le.gfs_forecast_f,
        le.calibrated_prob,
        le.raw_model_prob,
        le.model_delta_f,
        le.model_confidence,
        le.ensemble_spread,
        le.nws_high_prob_pct,
        le.gfs_high_prob_pct,

        le.market_implied_prob,
        le.market_yes_mid,
        le.edge,
        le.edge_pct,
        le.edge_rank,

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

        bkm.yes_bid,
        bkm.yes_ask,
        bkm.no_bid,
        bkm.no_ask,
        bkm.open_interest,
        le.market_volume                AS volume,
        bkm.market_status,
        le.market_liquidity,

        le.peak_hour,
        le.afternoon_storm_flag,
        le.pre_peak_storm_flag,
        le.cloud_timing_delta,
        le.sea_breeze_flag,

        ei.phase                        AS enso_phase,
        ei.oni_value                    AS enso_oni_value,

        a.final_tmax_f                  AS actual_tmax_f,
        a.final_tmin_f                  AS actual_tmin_f,
        vc.precip_in                    AS actual_precip_in,
        a.report_issued_at              AS settled_at,
        'nws_cli'                       AS settlement_source,

        r.contract_resolved_yes,

        CASE
            WHEN r.contract_resolved_yes IS NULL THEN NULL
            WHEN le.recommended_action = 'BET_YES' THEN r.contract_resolved_yes
            WHEN le.recommended_action = 'BET_NO'  THEN NOT r.contract_resolved_yes
            ELSE NULL
        END                             AS bhn_correct,

        CASE
            WHEN r.contract_resolved_yes IS NOT NULL AND le.calibrated_prob IS NOT NULL
                THEN (le.calibrated_prob > 0.5) = r.contract_resolved_yes
            ELSE NULL
        END                             AS bhn_predicted_correctly,

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

    -- bid/ask: bronze snapshot closest to (but not more than 5 min after) signal time
    LEFT JOIN LATERAL (
        SELECT yes_bid, yes_ask, no_bid, no_ask, open_interest, market_status
        FROM weather_bronze_kalshi_market_snapshots
        WHERE market_ticker = le.contract_ticker
          AND retrieved_at  <= le.last_updated + INTERVAL '5 minutes'
        ORDER BY retrieved_at DESC
        LIMIT 1
    ) bkm ON true

    LEFT JOIN LATERAL (
        SELECT phase, oni_value
        FROM enso_index
        WHERE week_ending <= le.target_date
        ORDER BY week_ending DESC
        LIMIT 1
    ) ei ON true

    LEFT JOIN actuals a
        ON a.station_code = le.station_code
       AND a.target_date  = le.target_date

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
$function$;

\echo 'model_delta_flag dropped; refresh_contract_ledger() no longer joins weather_gold_calibrated_probabilities.'

COMMIT;
