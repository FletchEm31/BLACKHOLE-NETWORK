-- Active weather bets + P&L
-- Paste as Native Query in Metabase
-- Returns empty rows until strategy goes live (rules.json enabled=true)
-- Pin to BHN FULL SYSTEM HEALTH dashboard

SELECT
    wb.id,
    wb.exchange,
    pc.contract_id                              AS contract_ticker,
    pc.title,
    pc.station_code,
    pc.resolution_date,
    wb.side,
    wb.stake_usd,
    wb.edge_pct,
    wb.model_probability,
    wb.entry_price,
    ROUND(CAST(wb.model_probability - wb.entry_price AS numeric), 4)
                                                AS edge_at_entry,
    wb.status,
    wb.placed_at,
    wb.exit_at,
    wb.pnl_usd,

    -- Running totals across all resolved bets
    SUM(wb.pnl_usd) FILTER (WHERE wb.status IN ('won','lost'))
        OVER ()                                 AS total_pnl_usd,

    COUNT(*) FILTER (WHERE wb.status = 'won')
        OVER ()                                 AS wins,

    COUNT(*) FILTER (WHERE wb.status = 'lost')
        OVER ()                                 AS losses

FROM weather_bets wb
JOIN prediction_contracts pc ON pc.id = wb.contract_id
ORDER BY wb.placed_at DESC
LIMIT 100;
