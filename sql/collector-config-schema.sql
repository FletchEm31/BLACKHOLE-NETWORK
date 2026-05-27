-- collector-config-schema.sql
-- BHN — per-source collector configuration for Data Health dashboard.
-- Stores expected cadence, grace period, and manual maintenance override.
-- The Grafana freshness query LEFT JOINs this table to compute status:
--   MAINTENANCE (yellow) = status_override = 'maintenance'
--   PENDING (blue/grey)  = status_override = 'pending'  (schema exists, collector never deployed)
--   ACTIVE (green)       = hours_since_last_record <= cadence_hours + grace_hours
--   OUTAGE (red)         = everything else
--
-- To mark a source under maintenance:
--   UPDATE collector_config SET status_override = 'maintenance', override_note = 'rebuilding collector', updated_at = NOW() WHERE source_name = 'EIA Energy Prices';
--
-- To clear maintenance/pending:
--   UPDATE collector_config SET status_override = NULL, override_note = NULL, updated_at = NOW() WHERE source_name = 'EIA Energy Prices';
--
-- Apply on LA hub:
--   sudo -u postgres psql -d eventhorizon -f sql/collector-config-schema.sql

CREATE TABLE IF NOT EXISTS collector_config (
    source_name      TEXT PRIMARY KEY,
    table_name       TEXT NOT NULL,
    domain           TEXT NOT NULL,
    cadence_hours    NUMERIC NOT NULL,     -- expected update frequency in hours
    grace_hours      NUMERIC NOT NULL DEFAULT 6,  -- grace window before OUTAGE fires
    status_override  TEXT CHECK (status_override IS NULL OR status_override IN ('maintenance', 'pending')),
    override_note    TEXT,                 -- human note: why it's in maintenance/pending
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Idempotent constraint refresh — CREATE TABLE IF NOT EXISTS skips column/check
-- changes on an existing table, so drop+add the named check so re-applying this
-- script on LA actually picks up the 'pending' value.
ALTER TABLE collector_config DROP CONSTRAINT IF EXISTS collector_config_status_override_check;
ALTER TABLE collector_config ADD  CONSTRAINT collector_config_status_override_check
    CHECK (status_override IS NULL OR status_override IN ('maintenance', 'pending'));

GRANT SELECT ON collector_config TO grafana_reader, agent_reader, ehuser;
GRANT INSERT, UPDATE ON collector_config TO ehuser;

COMMENT ON TABLE collector_config IS
    'Per-collector cadence config + manual maintenance override for the Data Health dashboard. status_override = maintenance → yellow; NULL → auto-compute from staleness.';

-- ─────────────────────────────────────────────────────────────────────────────
-- SEED — one row per source in the freshness table
-- cadence_hours: realistic expected update frequency
-- grace_hours:   tolerance before flipping to OUTAGE (operator spec: 6h)
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO collector_config (source_name, table_name, domain, cadence_hours, grace_hours, status_override, override_note) VALUES

-- MARKET
('Alpaca ETF (market_daily)',   'market_daily',     'Market',      48,    6,    NULL, NULL),  -- weekday only; 48h covers weekend
('Regime Classifier',           'market_regimes',   'Market',      48,    6,    NULL, NULL),
('Sentiment Collector',         'market_sentiment', 'Market',      48,    6,    NULL, NULL),
('Finnhub Earnings',            'earnings_data',    'Market',      720,   24,   NULL, NULL),  -- monthly-ish
('Finnhub Analysts',            'analyst_data',     'Market',      720,   24,   NULL, NULL),

-- MACRO
('FRED (macro_daily)',          'macro_daily',      'Macro',       24,    6,    NULL, NULL),

-- ENERGY / AGRICULTURE
('EIA Energy Prices',           'energy_prices',    'Energy',      168,   12,   NULL, NULL),  -- weekly
('USDA Agriculture',            'agriculture_prices','Agriculture', 720,   24,   NULL, NULL),  -- monthly

-- WEATHER / NEWS
('OpenWeather',                 'weather_snapshots','Weather',     1,     6,    NULL, NULL),
('NewsAPI',                     'news_articles',    'News',        0.5,   6,    NULL, NULL),

-- SECURITY (continuous — grace is the threshold)
('Suricata/CrowdSec',           'security_events',  'Security',    0,     2,    NULL, NULL),
('fail2ban',                    'fail2ban_events',  'Security',    0,     24,   NULL, NULL),
('CrowdSec',                    'crowdsec_decisions','Security',   0,     24,   NULL, NULL),

-- TRADING (market hours only — 48h covers weekend)
('Alpaca Paper Trades',         'paper_trades',     'Trading',     48,    6,    NULL, NULL),
('Strategy Signals',            'signals_log',      'Trading',     48,    6,    NULL, NULL),
('Reconciliation Daemon',       'reconciliation_heartbeat','Trading',0.083,1,   NULL, NULL),  -- 5min

-- COLLECTIBLES
('CGC Pop Scraper',             'pop_reports',      'Collectibles',168,   24,   NULL, NULL),  -- weekly
('eBay Sold Comps',             'sold_listings',    'Collectibles',720,   48,   NULL, NULL),  -- manual
('eBay Active Listings',        'ebay_listings',    'Collectibles',720,   48,   NULL, NULL),  -- manual

-- AI
('HORIZON Memory',              'memories',         'AI',          168,   48,   NULL, NULL),
('HORIZON Tokens',              'agent_token_log',  'AI',          168,   48,   NULL, NULL),

-- INFRASTRUCTURE
('WireGuard Stats',             'wg_peer_stats',        'Infrastructure', 0.083, 1, NULL, NULL),
('Node Resources',              'node_resource_stats',  'Infrastructure', 0.083, 1, NULL, NULL),
('Docker Stats',                'container_stats',      'Infrastructure', 0.083, 1, NULL, NULL),
('n8n Executions',              'n8n_execution_stats',  'Infrastructure', 0.083, 1, NULL, NULL),
('Iptables Counters',           'iptables_stats',       'Infrastructure', 0.083, 1, NULL, NULL),
('DNS Query Log',               'dns_query_log',        'Infrastructure', 0.083, 1, NULL, NULL),
('Node Bandwidth (vnstat)',     'node_bandwidth_stats', 'Infrastructure', 0.25,  1, NULL, NULL),
('Conntrack Snapshots',         'connection_snapshots', 'Infrastructure', 0.083, 1, NULL, NULL),
('PG Activity Snapshots',       'pg_activity_snapshots','Infrastructure', 0.083, 1, NULL, NULL),
('Tinyproxy Requests',          'proxy_request_logs',   'Infrastructure', 0.083, 1, NULL, NULL),
('Tor Relay Stats',             'tor_relay_stats',      'Infrastructure', 0.083, 1, NULL, NULL)

ON CONFLICT (source_name) DO UPDATE SET
    cadence_hours   = EXCLUDED.cadence_hours,
    grace_hours     = EXCLUDED.grace_hours,
    updated_at      = NOW();
-- Note: status_override and override_note are NOT updated on conflict
-- so manual maintenance flags survive a re-apply of this script.
-- The two UPDATE blocks below fill override_note (auto-fill only when NULL)
-- and apply the PENDING flag for never-deployed sources.


-- ─────────────────────────────────────────────────────────────────────────────
-- AUTO-FILL override_note — one-line current-state description per source.
-- Only writes when override_note IS NULL so any manual note set by an operator
-- (e.g. mid-incident annotation) is preserved across re-applies. Sources that
-- get status_override='pending' below intentionally get their note from the
-- next block, not here.
-- Source: 2026-05-27 collection audit (BHN-COLLECTION-ISSUES-2026-05-27).
-- ─────────────────────────────────────────────────────────────────────────────
UPDATE collector_config
   SET override_note = CASE source_name
       -- MARKET / MACRO
       WHEN 'Alpaca ETF (market_daily)' THEN 'Active — weekday equity close pulls'
       WHEN 'Regime Classifier'         THEN 'Active — daily regime classification'
       WHEN 'Sentiment Collector'       THEN 'Active — daily sentiment aggregation'
       WHEN 'Finnhub Earnings'          THEN 'Stale 10–19 days — Finnhub fetcher cluster unhealthy'
       WHEN 'Finnhub Analysts'          THEN 'Stale 10–19 days — Finnhub fetcher cluster unhealthy'
       WHEN 'FRED (macro_daily)'        THEN 'Active — daily FRED macro pulls'
       -- ENERGY / AGRICULTURE
       WHEN 'EIA Energy Prices'         THEN 'Stale 12+ days — weekly EIA fetcher stalled, API debugging pending'
       WHEN 'USDA Agriculture'          THEN 'Dead since January 2026 — USDA API endpoint change suspected'
       -- WEATHER / NEWS
       WHEN 'OpenWeather'               THEN 'Active — hourly weather snapshots'
       WHEN 'NewsAPI'                   THEN 'Active — 30-minute news pulls'
       -- SECURITY
       WHEN 'Suricata/CrowdSec'         THEN 'Stopped 2026-05-24 19:25 UTC — Suricata daemon healthy, collector script died'
       -- TRADING
       WHEN 'Alpaca Paper Trades'       THEN 'Active — paper trades sync'
       WHEN 'Strategy Signals'          THEN 'Active — strategy signal log'
       WHEN 'Reconciliation Daemon'     THEN 'Active — 5-min reconciliation heartbeat'
       -- COLLECTIBLES
       WHEN 'CGC Pop Scraper'           THEN 'Active — weekly CGC pop report scrape (LA cron)'
       WHEN 'eBay Sold Comps'           THEN 'Data quality issue — large NULL skeleton-row chunks, scraper inserting empty records'
       WHEN 'eBay Active Listings'      THEN 'Data quality issue — large NULL skeleton-row chunks, scraper inserting empty records'
       -- AI
       WHEN 'HORIZON Memory'            THEN 'HORIZON workflow stale — workflow not re-imported after 2026-05-23 session'
       ELSE override_note
   END,
   updated_at = NOW()
 WHERE override_note IS NULL
   AND source_name IN (
       'Alpaca ETF (market_daily)','Regime Classifier','Sentiment Collector',
       'Finnhub Earnings','Finnhub Analysts','FRED (macro_daily)',
       'EIA Energy Prices','USDA Agriculture',
       'OpenWeather','NewsAPI',
       'Suricata/CrowdSec',
       'Alpaca Paper Trades','Strategy Signals','Reconciliation Daemon',
       'CGC Pop Scraper','eBay Sold Comps','eBay Active Listings',
       'HORIZON Memory'
   );


-- ─────────────────────────────────────────────────────────────────────────────
-- PENDING — schema exists but collector was never deployed (no cron, no rows).
-- Applies to never-deployed infrastructure collectors and broken-upstream
-- sources that have never written data. Only flips rows that have no override
-- already (so 'maintenance' wins over 'pending' on conflict). Each source gets
-- a specific note so the dashboard tooltip explains *why* it's pending.
-- Source: 2026-05-27 collection audit (BHN-COLLECTION-ISSUES-2026-05-27).
-- ─────────────────────────────────────────────────────────────────────────────
UPDATE collector_config
   SET status_override = 'pending',
       override_note   = CASE source_name
           WHEN 'n8n Executions'          THEN 'Schema exists — no SQLite→PG bridge wired (1.18 GB n8n SQLite unsynced)'
           WHEN 'CrowdSec'                THEN 'Schema exists — collector not wired; no rows ever written despite CrowdSec running'
           WHEN 'WireGuard Stats'         THEN 'Schema exists — LA-only cron never deployed'
           WHEN 'Node Resources'          THEN 'Schema exists — cron never deployed on any of 4 nodes'
           WHEN 'Docker Stats'            THEN 'Schema exists — LA-only cron never deployed'
           WHEN 'fail2ban'                THEN 'Schema exists — cron never deployed on any of 4 nodes'
           WHEN 'HORIZON Tokens'          THEN 'HORIZON workflow stale — re-import required before token logging resumes'
           WHEN 'Iptables Counters'       THEN 'Schema exists — cron never deployed on any of 4 nodes'
           WHEN 'DNS Query Log'           THEN 'Schema exists — cron never deployed on any of 4 nodes'
           WHEN 'Node Bandwidth (vnstat)' THEN 'Schema exists — vnstat cron never deployed on any of 4 nodes'
           WHEN 'Conntrack Snapshots'     THEN 'Schema exists — conntrack cron never deployed; current rows have zero metric columns since 2026-05-24'
           WHEN 'PG Activity Snapshots'   THEN 'Schema exists — LA-only cron never deployed'
           WHEN 'Tinyproxy Requests'      THEN 'Schema exists — Hillsboro-only; Hillsboro SSH wedged, deferred'
           WHEN 'Tor Relay Stats'         THEN 'Schema exists — cron never deployed on Frankfurt + Hillsboro'
       END,
       updated_at      = NOW()
 WHERE status_override IS NULL
   AND source_name IN (
       'n8n Executions','CrowdSec','WireGuard Stats','Node Resources',
       'Docker Stats','fail2ban','HORIZON Tokens',
       'Iptables Counters','DNS Query Log','Node Bandwidth (vnstat)',
       'Conntrack Snapshots','PG Activity Snapshots',
       'Tinyproxy Requests','Tor Relay Stats'
   );
