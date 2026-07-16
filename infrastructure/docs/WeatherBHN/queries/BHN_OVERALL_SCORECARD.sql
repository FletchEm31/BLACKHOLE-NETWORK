-- WeatherBHN - BHN Overall Scorecard
-- High level BHN signal performance summary. Shows total recommendations,
-- win rate, P&L, and how BHN compares to simply following the market.
-- Headline number: is BHN actually adding value over the market?
-- Populates after settlement reconciler runs nightly at 15:00 UTC.
-- Source: WeatherBHN_Performance_Queries.txt (June 12, 2026) — Query 19
-- (that file removed 2026-07-15 as a stale, unreferenced duplicate)
-- Tab: FORMULA/MODELS — PIN at top
--
-- UPDATED 2026-07-02: weather_model_accuracy retired, replaced by
-- weather_gold_contract_ledger (see project memory
-- weather-gold-edge-sheet-retired). Compat CTE below aliases every old
-- column; market_was_correct, bhn_position_taken, bhn_position_side, and
-- accuracy_score have no direct equivalent and are derived.
--
-- FIXED 2026-07-02: found (not a dashboard bug — a data-provenance issue).
-- 48 BET_NO rows carry an identical flat stake_usd=125.0 (zero variance)
-- instead of genuine Kelly sizing, and 90 BET_YES rows predate the current
-- pipeline's action space entirely (cp4_kelly_sizer.py never writes
-- BET_YES, only BET_NO/SKIP). Both are legacy/pre-CP4 data. Several of the
-- flat-stake rows also had market_implied_prob clamped at the 0.99
-- tick-size boundary, generating $271,191.73 of a then-$343,894.13
-- reported PnL (79%) via a legitimate-but-flat-staked 99x payout
-- multiplier.
--
-- PERMANENT FIX 2026-07-02: Fletch decided to exclude both legacy
-- populations permanently rather than backfill/reconstruct them. Rather
-- than re-deriving the exclusion condition per-query, migration
-- sql/migrations/2026-07-02-ledger-exclude-legacy-rows.sql added an
-- is_legacy_row column to weather_gold_contract_ledger and created
-- weather_gold_contract_ledger_performance, a view with legacy rows
-- already filtered out. This CTE now reads from that view — no ad-hoc
-- WHERE filter needed here or in any future dashboard on this table.
--
-- FIXED 2026-07-02: bhn_win_rate_pct's denominator was COUNT(*) over ALL
-- rows including SKIP (bhn_correct is always NULL for skips, silently
-- diluting the rate — e.g. showing a false 0.0% for a case with zero
-- actual trades, which reads as "model failing" instead of "no data
-- yet"). Denominator is now COUNT(*) FILTER (WHERE bhn_position_taken =
-- true) throughout — trade-only win rate, matching trades_placed above.
--
-- REBUILT 2026-07-15: weather_gold_contract_ledger's settlement columns
-- (contract_resolved_yes, bhn_correct, paper_pnl, settled_at,
-- outcome_edge_realized) were retired/frozen 2026-07-07 — see
-- sql/migrations/2026-07-07-retire-contract-ledger-settlement-columns.sql.
-- No new value since 2026-06-30; this scorecard was silently showing
-- stale/dead data (discovered while investigating a much larger raw/
-- corrected column ambiguity in weather_position_exits the same night).
--
-- The compat CTE now FULL OUTER JOINs weather_gold_contract_ledger_
-- performance (still live/healthy for signal volume, edge, calibrated_prob
-- etc.) against weather_position_exits (the live, corrected settlement
-- source — see sql/migrations/2026-07-15-consolidate-position-exits-view-
-- columns.sql). A plain LEFT JOIN from the ledger is NOT sufficient:
--   1. weather_gold_contract_ledger upserts ON CONFLICT (contract_ticker),
--      so recommended_action reflects the CURRENT re-evaluation of a
--      contract, not the historical decision. Confirmed 67 of 98 real
--      historical positions in weather_position_exits now show SKIP in
--      the live ledger snapshot, because market conditions moved after
--      entry — the trade still genuinely happened. bhn_position_taken is
--      therefore keyed off presence in weather_position_exits, never off
--      recommended_action.
--   2. 9 real, settled 2026-07-01 trades (ids 296/358/422/523/524/525/
--      526/527/529) have NO matching ledger row at all, in either the raw
--      table or the _performance view — a plain LEFT JOIN from the ledger
--      would silently drop them entirely. FULL OUTER JOIN does not.
-- Verified against the independently-confirmed true numbers: trades_placed
-- (98) matches weather_position_exits' row count exactly; total settled
-- PnL (-$758.12) and win/loss split (47/22) match exactly.
--
-- FIXED 2026-07-15: the trailing `WHERE actual_outcome IS NOT NULL` (below,
-- inherited unchanged since 2026-07-02) gated the ENTIRE result set before
-- COUNT(*) ran — since actual_outcome is NULL for every SKIP and every
-- still-open position, signals_skipped was structurally guaranteed to
-- read 0 no matter what, even back when the ledger settlement columns
-- were live. This was never caused by tonight's rebuild; the rebuild just
-- made it visible again once real signal volume was wired back in. Every
-- other aggregate here (win rates, PnL, accuracy_score) already keys off
-- columns that are NULL exactly when inapplicable (bhn_was_correct,
-- pnl_dollar, etc.), so SUM/AVG/COUNT already skip them correctly without
-- an outer WHERE — removed it so total_recommendations/signals_skipped
-- reflect true volume again.
--
-- accuracy_score (outcome_edge_realized) formula recovered from git history
-- (weather_settlement_reconciliation.py / refresh_contract_ledger() era,
-- pre-2026-06-30 freeze). Original BET_NO-branch formula:
--   market_implied_prob(meaning P(YES) at the time) - resolved_YES_as_int
-- Today's live writer (cp4_kelly_sizer.py) defines market_implied_prob as
-- P(NO) (no_ask), not P(YES) — a field semantics drift, not a rename.
-- Substituting P(YES) = 1 - P(NO) and resolved_YES = 1 - resolved_NO into
-- the original formula gives the algebraically identical definition in
-- terms of today's field:
--   resolved_NO_as_int - P(NO)
-- Same NULL conditions as the original (unresolved, no position taken, or
-- market_implied_prob unavailable). Not a new formula — a re-derivation of
-- the same one under the field's current meaning.

WITH weather_model_accuracy AS (
    SELECT
        COALESCE(g.contract_ticker, pe.contract_ticker)        AS contract_id,
        COALESCE(g.contract_ticker, pe.contract_ticker)        AS contract_title,
        COALESCE(g.city, CASE pe.station_code
            WHEN 'KDEN' THEN 'Denver' WHEN 'KLAX' THEN 'Los Angeles' WHEN 'KMIA' THEN 'Miami'
            ELSE pe.station_code END)                          AS region,
        COALESCE(g.contract_side, 'high')                      AS variable,
        COALESCE(g.calibrated_prob, pe.model_prob_no_cents / 100.0)
                                                                AS bhn_predicted_probability,
        COALESCE(g.market_implied_prob, COALESCE(pe.entry_no_ask_cents, pe.no_ask_cents) / 100.0)
                                                                AS market_implied_probability,
        COALESCE(g.edge, pe.edge_cents / 100.0)                AS edge,
        (pe.contract_ticker IS NOT NULL)                       AS bhn_position_taken,
        COALESCE(g.stake_usd, pe.corrected_stake_usd_recommended, pe.stake_usd_recommended)
                                                                AS bhn_position_value,
        CASE WHEN pe.contract_ticker IS NOT NULL THEN 'no' END AS bhn_position_side,
        COALESCE(pe.corrected_actual_outcome, pe.actual_outcome)
                                                                AS actual_outcome,
        (COALESCE(pe.corrected_actual_outcome, pe.actual_outcome) = 'NO_WIN')
                                                                AS bhn_was_correct,
        (COALESCE(g.market_implied_prob, COALESCE(pe.entry_no_ask_cents, pe.no_ask_cents) / 100.0) >= 0.5)
            = (COALESCE(pe.corrected_actual_outcome, pe.actual_outcome) = 'NO_WIN')
                                                                AS market_was_correct,
        COALESCE(pe.corrected_realized_pnl_usd, pe.realized_pnl_usd)
                                                                AS pnl_dollar,
        CASE
            WHEN pe.contract_ticker IS NULL
              OR COALESCE(pe.corrected_actual_outcome, pe.actual_outcome) IS NULL
              OR COALESCE(g.market_implied_prob, COALESCE(pe.entry_no_ask_cents, pe.no_ask_cents) / 100.0) IS NULL
                THEN NULL
            ELSE
                (CASE WHEN COALESCE(pe.corrected_actual_outcome, pe.actual_outcome) = 'NO_WIN' THEN 1 ELSE 0 END)
                - COALESCE(g.market_implied_prob, COALESCE(pe.entry_no_ask_cents, pe.no_ask_cents) / 100.0)
        END                                                    AS accuracy_score,
        pe.scored_at                                           AS resolved_at,
        COALESCE(g.signal_generated_at, pe.decision_timestamp) AS created_at
    FROM weather_gold_contract_ledger_performance g
    FULL OUTER JOIN weather_position_exits pe ON pe.contract_ticker = g.contract_ticker
)
SELECT
    -- Volume
    COUNT(*)                                           AS total_recommendations,
    COUNT(*) FILTER (WHERE bhn_position_taken = true) AS trades_placed,
    COUNT(*) FILTER (WHERE bhn_position_taken = false) AS signals_skipped,

    -- BHN accuracy
    COUNT(*) FILTER (WHERE bhn_was_correct = true)    AS bhn_correct,
    COUNT(*) FILTER (WHERE bhn_was_correct = false)   AS bhn_wrong,
    ROUND(
        COUNT(*) FILTER (WHERE bhn_was_correct = true)::numeric
        / NULLIF(COUNT(*) FILTER (WHERE bhn_position_taken = true), 0) * 100, 1
    )                                                  AS bhn_win_rate_pct,

    -- Market accuracy
    COUNT(*) FILTER (WHERE market_was_correct = true)  AS market_correct,
    ROUND(
        COUNT(*) FILTER (WHERE market_was_correct = true)::numeric
        / NULLIF(COUNT(*) FILTER (WHERE bhn_position_taken = true), 0) * 100, 1
    )                                                  AS market_win_rate_pct,

    -- BHN vs Market edge
    ROUND(
        (
            COUNT(*) FILTER (WHERE bhn_was_correct = true)::numeric
            / NULLIF(COUNT(*) FILTER (WHERE bhn_position_taken = true), 0)
            -
            COUNT(*) FILTER (WHERE market_was_correct = true)::numeric
            / NULLIF(COUNT(*) FILTER (WHERE bhn_position_taken = true), 0)
        ) * 100, 1
    )                                                  AS bhn_vs_market_edge_pct,

    -- P&L
    ROUND(SUM(pnl_dollar), 2)                          AS total_pnl,
    ROUND(AVG(pnl_dollar), 2)                          AS avg_pnl_per_trade,
    ROUND(SUM(pnl_dollar) FILTER (WHERE pnl_dollar > 0), 2) AS total_wins,
    ROUND(SUM(pnl_dollar) FILTER (WHERE pnl_dollar < 0), 2) AS total_losses,

    -- Edge stats
    ROUND(AVG(edge * 100), 1)                          AS avg_edge_pct,
    ROUND(AVG(accuracy_score), 3)                      AS avg_accuracy_score,

    -- Date range
    MIN(resolved_at) AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'
                                                       AS first_reconciled_time_pt,
    MAX(resolved_at) AT TIME ZONE 'UTC' AT TIME ZONE 'America/Los_Angeles'
                                                       AS latest_reconciled_time_pt,
    COUNT(DISTINCT DATE(resolved_at))                  AS trading_days,

    -- Transparency count for the card label — permanently excluded via
    -- weather_gold_contract_ledger_performance, see FIXED note above
    (SELECT COUNT(*) FROM weather_gold_contract_ledger
       WHERE is_legacy_row AND contract_resolved_yes IS NOT NULL)
                                                       AS legacy_rows_excluded

FROM weather_model_accuracy;
