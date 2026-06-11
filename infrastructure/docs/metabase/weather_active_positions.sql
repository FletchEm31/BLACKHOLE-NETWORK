-- Active weather bets + P&L
-- Paste as Native Query in Metabase
-- Returns empty rows until strategy goes live (rules.json enabled=true)
-- Pin to BHN FULL SYSTEM HEALTH dashboard

SELECT
    wb.exchange,
    wb.contract_ticker,
    wb.side,
    wb.stake_usd,
    wb.edge_pct,
    wb.model_prob,
    wb.market_prob,
    ROUND(CAST(wb.model_prob - wb.market_prob AS numeric), 4)
                            AS edge_raw,
    wb.status,
    wb.placed_at,
    wb.resolved_at,
    wb.pnl_usd,

    -- Running P&L across all closed bets
    SUM(wb.pnl_usd) FILTER (WHERE wb.status = 'resolved')
        OVER ()         AS total_pnl_usd,

    COUNT(*) FILTER (WHERE wb.status = 'resolved' AND wb.pnl_usd > 0)
        OVER ()         AS wins,

    COUNT(*) FILTER (WHERE wb.status = 'resolved' AND wb.pnl_usd <= 0)
        OVER ()         AS losses

FROM weather_bets wb
ORDER BY wb.placed_at DESC
LIMIT 100;
