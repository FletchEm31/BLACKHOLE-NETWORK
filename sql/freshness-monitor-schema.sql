-- ============================================================================
-- BHN Table Freshness Monitor
-- ============================================================================
--
-- Detects silent data-flow failures (e.g. the 5/13 -> 5/21 security_events
-- gap that went unnoticed for 2 days). Schema-driven so adding a new table
-- to monitor is one INSERT, not a code change.
--
-- Three objects:
--   table_freshness_targets   manifest - one row per table to watch
--   freshness_alerts          alert log - one row per detected staleness event
--   v_table_freshness         computed view - joins targets with MAX(ts_col)
--
-- The checker script (bhn-freshness-check.sh on LA) reads the view, writes
-- alert rows when stale, and updates last_alert_at on the manifest to
-- dedup repeat alerts within a configurable window.
--
-- No SMS wiring yet - alerts go to the freshness_alerts table only this
-- session. Next session: n8n webhook -> HORIZON SMS.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. Manifest: what to monitor + thresholds + silencing controls
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS table_freshness_targets (
    table_name           TEXT PRIMARY KEY,
    timestamp_column     TEXT NOT NULL,
    max_stale_hours      INTEGER NOT NULL CHECK (max_stale_hours > 0),
    expected_cadence     TEXT,                                          -- human note
    producer             TEXT,                                          -- what should be writing
    enabled              BOOLEAN NOT NULL DEFAULT TRUE,
    last_alert_at        TIMESTAMPTZ,                                   -- checker updates this
    alert_dedup_hours    INTEGER NOT NULL DEFAULT 6 CHECK (alert_dedup_hours > 0),
    silence_until        TIMESTAMPTZ,                                   -- operator-set; pause alerts
    notes                TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE table_freshness_targets IS
    'Manifest of tables monitored for data-flow freshness. enabled=false skips. silence_until in the future pauses alerts (operator planned-maintenance escape hatch). last_alert_at + alert_dedup_hours prevents alert spam.';

COMMENT ON COLUMN table_freshness_targets.timestamp_column IS
    'Column name on the target table to MAX() against. Typically the ingest timestamp (fetched_at, measured_at, inserted_at) - that tracks "is the pipeline running", which is what we want to alert on. NOT the data observation date.';

-- ----------------------------------------------------------------------------
-- 2. Alert log
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS freshness_alerts (
    id                   BIGSERIAL PRIMARY KEY,
    table_name           TEXT NOT NULL,
    detected_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hours_stale          NUMERIC(10,2),
    threshold_hours      INTEGER,
    latest_event_at      TIMESTAMPTZ,
    producer             TEXT,
    notes                TEXT
);
CREATE INDEX IF NOT EXISTS freshness_alerts_table_time_idx
    ON freshness_alerts (table_name, detected_at DESC);

COMMENT ON TABLE freshness_alerts IS
    'One row per detected freshness violation. The checker dedups via table_freshness_targets.last_alert_at + alert_dedup_hours, so a stable outage produces one alert row every alert_dedup_hours window, not one per cron tick.';

-- ----------------------------------------------------------------------------
-- 3. Helper function - dynamic SELECT MAX(col) FROM table
--    Catches errors so a missing column or table doesn't break the view for
--    everyone; returns NULL which the view interprets as infinitely stale.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION get_table_latest(p_table TEXT, p_col TEXT)
RETURNS TIMESTAMPTZ AS $$
DECLARE
    result TIMESTAMPTZ;
BEGIN
    EXECUTE format('SELECT MAX(%I)::timestamptz FROM %I', p_col, p_table) INTO result;
    RETURN result;
EXCEPTION WHEN OTHERS THEN
    -- broken target (missing col/table) -> NULL latest -> shows as infinitely stale
    RETURN NULL;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION get_table_latest IS
    'Dynamic helper for the v_table_freshness view. Format-quotes identifiers, swallows errors (returns NULL) so the view is robust to manifest typos.';

-- ----------------------------------------------------------------------------
-- 4. Computed view - one row per target with current staleness
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_table_freshness AS
SELECT
    t.table_name,
    t.timestamp_column,
    t.producer,
    t.expected_cadence,
    t.max_stale_hours,
    get_table_latest(t.table_name, t.timestamp_column) AS latest_event_at,
    ROUND(
        EXTRACT(EPOCH FROM (NOW() - get_table_latest(t.table_name, t.timestamp_column))) / 3600.0,
        2
    )::numeric(10,2) AS hours_stale,
    (
        get_table_latest(t.table_name, t.timestamp_column) IS NULL
        OR EXTRACT(EPOCH FROM (NOW() - get_table_latest(t.table_name, t.timestamp_column))) / 3600.0 > t.max_stale_hours
    ) AS is_stale,
    t.enabled,
    t.last_alert_at,
    t.alert_dedup_hours,
    t.silence_until,
    (t.silence_until IS NOT NULL AND t.silence_until > NOW()) AS is_silenced,
    -- The checker uses this to filter to actionable alerts
    (
        t.enabled
        AND (t.silence_until IS NULL OR t.silence_until <= NOW())
        AND (
            get_table_latest(t.table_name, t.timestamp_column) IS NULL
            OR EXTRACT(EPOCH FROM (NOW() - get_table_latest(t.table_name, t.timestamp_column))) / 3600.0 > t.max_stale_hours
        )
        AND (
            t.last_alert_at IS NULL
            OR t.last_alert_at < NOW() - (t.alert_dedup_hours || ' hours')::interval
        )
    ) AS needs_alert
FROM table_freshness_targets t
ORDER BY t.table_name;

COMMENT ON VIEW v_table_freshness IS
    'Live freshness state. is_stale = true means past threshold. needs_alert = true means stale AND enabled AND not silenced AND past dedup window. The checker filters on needs_alert.';

-- ----------------------------------------------------------------------------
-- 5. Seed - initial monitored tables (timestamp column verified against live DDL 2026-05-23)
-- ----------------------------------------------------------------------------
INSERT INTO table_freshness_targets
    (table_name, timestamp_column, max_stale_hours, expected_cadence, producer, notes)
VALUES
    -- Security telemetry - now flowing via cron
    ('security_events',     'detected_at',  2,   'every 5 min via cron',         'bhn-security-events-collector cron',     'fixed 2026-05-23'),

    -- Per-node infra stats - high cadence
    ('node_resource_stats', 'measured_at',  1,   'every 5 min, all nodes',       'bhn-resource-collector cron (per-node)', NULL),
    ('wg_peer_stats',       'measured_at',  1,   'every 5 min',                  'bhn-wg-stats cron',                       NULL),

    -- Financial - daily cadence (with weekend grace)
    ('market_daily',        'fetched_at',   30,  'daily via systemd timer',      'bhn-market-data.timer',                   NULL),
    ('macro_daily',         'fetched_at',   30,  'daily via systemd timer',      'bhn-macro-data.timer',                    NULL),

    -- API pollers - just deployed today; wider thresholds because gov releases skip weekends
    ('macro_indicators',    'measured_at',  96,  '08:00 + 14:00 ET weekdays',    'bhn-fred-poller cron',                    'FRED skips weekends'),
    ('energy_prices',       'measured_at',  48,  '10:30 ET weekdays',            'bhn-eia-poller cron',                     NULL),
    ('agriculture_prices',  'measured_at',  96,  '08:30 ET weekdays + Fri 15:00','bhn-usda-poller cron',                    'USDA skips weekends'),
    ('earnings_data',       'measured_at',  30,  'daily 06:30',                  'bhn-finnhub-poller cron (NJ)',            NULL),
    ('analyst_data',        'measured_at',  30,  'daily 06:30',                  'bhn-finnhub-poller cron (NJ)',            NULL),
    ('crypto_market_data',  'measured_at',  2,   'every 15 min',                 'bhn-coingecko-poller cron',               'currently empty; will stay alerting until env file populated'),

    -- Pokemon ingest - irregular but should not go fully dark
    ('pop_reports',         'scraped_at',   192, 'weekly Sun 03:00 UTC',         'bhn-cgc-pop-refresh.timer',               'weekly + 1d grace'),
    ('ebay_listings',       'created_at',   2,   'every 30 min via n8n',         'PSA/CGC/BGS/SGC n8n workflows',           NULL)
ON CONFLICT (table_name) DO NOTHING;

-- ----------------------------------------------------------------------------
-- 6. Grants
-- ----------------------------------------------------------------------------
GRANT SELECT ON table_freshness_targets, freshness_alerts, v_table_freshness TO agent_reader, grafana_reader, ehuser;

-- The checker script writes alerts + updates last_alert_at; runs as agent_reader
GRANT INSERT ON freshness_alerts                            TO agent_reader;
GRANT UPDATE (last_alert_at, updated_at) ON table_freshness_targets TO agent_reader;
GRANT USAGE  ON SEQUENCE freshness_alerts_id_seq            TO agent_reader;

-- Operator can flip enabled/silence_until from ehuser session if needed
GRANT INSERT, UPDATE, DELETE ON table_freshness_targets     TO ehuser;
GRANT INSERT ON freshness_alerts                            TO ehuser;
GRANT USAGE  ON SEQUENCE freshness_alerts_id_seq            TO ehuser;

COMMIT;
