-- BHN Trading Dashboard
-- Pinned DBeaver tab: "BHN Trading Dashboard"
--
-- Open positions appear first, sorted by hours remaining (most urgent to settle).
-- Settled positions follow, most recently scored at top.
-- Refresh manually or enable DBeaver auto-refresh (Query → Auto-refresh).
--
-- Requires: migration 2026-06-30-weather-trading-dashboard-views.sql applied.

SELECT
    position_status,
    station_code,
    target_date,
    bucket_label,
    -- Pricing
    entry_no_ask_cents,
    current_no_ask_cents,
    ask_drift_cents,
    -- Edge
    entry_edge_cents,
    current_edge_cents,
    -- Sizing
    contracts_recommended,
    stake_usd,
    -- Settlement timing (NULL for settled rows)
    round(hours_to_settle::numeric, 1)                             AS hours_left,
    -- Model signal
    predicted_tmax_f,
    model_prob_no_cents,
    -- Hypothetical / actual P&L
    hypothetical_win_usd,
    hypothetical_loss_usd,
    actual_tmax_f,
    actual_outcome,
    realized_pnl_usd,
    -- Timestamps
    first_captured_at,
    last_updated_at,
    scored_at
FROM weather_paper_pnl_dashboard
ORDER BY
    CASE position_status WHEN 'OPEN' THEN 0 ELSE 1 END,
    hours_to_settle ASC NULLS LAST,
    scored_at DESC NULLS LAST,
    target_date,
    station_code;

-- ─────────────────────────────────────────────────────────────────────────────
-- P&L Summary (run separately or as a second DBeaver tab)
-- ─────────────────────────────────────────────────────────────────────────────
-- SELECT
--     COUNT(*)               FILTER (WHERE position_status = 'OPEN')          AS open_positions,
--     COUNT(*)               FILTER (WHERE position_status = 'SETTLED_WIN')   AS wins,
--     COUNT(*)               FILTER (WHERE position_status = 'SETTLED_LOSS')  AS losses,
--     ROUND(
--         COUNT(*) FILTER (WHERE position_status = 'SETTLED_WIN')::numeric
--         / NULLIF(COUNT(*) FILTER (WHERE position_status IN ('SETTLED_WIN','SETTLED_LOSS')), 0) * 100,
--     1)                                                                       AS win_pct,
--     COALESCE(SUM(realized_pnl_usd)  FILTER (WHERE realized_pnl_usd IS NOT NULL), 0) AS total_realized_pnl,
--     COALESCE(SUM(stake_usd)         FILTER (WHERE position_status = 'OPEN'), 0)      AS total_at_risk_usd,
--     COALESCE(SUM(hypothetical_win_usd)  FILTER (WHERE position_status = 'OPEN'), 0)  AS total_upside_usd,
--     COALESCE(SUM(hypothetical_loss_usd) FILTER (WHERE position_status = 'OPEN'), 0)  AS total_downside_usd
-- FROM weather_paper_pnl_dashboard;
