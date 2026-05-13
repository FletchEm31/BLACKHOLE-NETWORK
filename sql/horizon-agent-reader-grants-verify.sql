-- horizon-agent-reader-grants-verify.sql
-- BHN — verify agent_reader has SELECT on all monitoring tables added in
-- the 2026-05-13 expansion. Idempotent: re-grants where missing.
--
-- Apply on the hub:
--   sudo -u postgres psql -d eventhorizon -f sql/horizon-agent-reader-grants-verify.sql

DO $$
DECLARE
    tbl TEXT;
    monitoring_tables TEXT[] := ARRAY[
        -- 2026-05-13 monitoring expansion
        'wg_peer_stats', 'wg_sessions',
        'tor_relay_stats',
        'node_bandwidth_stats', 'node_resource_stats', 'node_disk_stats',
        'node_patch_status',
        'crowdsec_decisions', 'fail2ban_events',
        'connection_snapshots', 'iptables_stats',
        'container_stats',
        'pg_activity_snapshots', 'pg_query_stats', 'pg_table_stats',
        'n8n_execution_stats',
        'proxy_request_logs', 'dns_query_log',
        'ssh_sessions', 'ssh_commands',
        'node_logs_summary',
        -- Market data
        'prediction_market_data', 'crypto_market_data',
        'macro_indicators', 'analyst_data', 'earnings_data',
        'energy_prices', 'agriculture_prices',
        'corporate_actions', 'alpaca_news', 'options_chain_snapshots',
        'market_bars', 'market_bars_1min', 'market_bars_5min',
        'market_bars_15min', 'market_bars_1hour', 'market_bars_1day',
        'market_ticks', 'order_events'
    ];
BEGIN
    FOREACH tbl IN ARRAY monitoring_tables LOOP
        -- Skip tables that don't exist yet (schema not applied)
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = tbl) THEN
            EXECUTE format('GRANT SELECT ON %I TO agent_reader', tbl);
            RAISE NOTICE 'GRANT SELECT ON % TO agent_reader', tbl;
        ELSE
            RAISE NOTICE 'skipping % (table does not exist)', tbl;
        END IF;
    END LOOP;
END $$;
