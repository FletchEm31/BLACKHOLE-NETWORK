-- n8n-execution-stats-schema.sql
-- BHN — n8n workflow execution history shipped from n8n's SQLite to LA PG.
-- LA-only (n8n runs on LA). Populated by scripts/bhn-n8n-stats-collector.sh
-- which queries n8n's execution_entity table and ships new rows since
-- the last-shipped id stored in /var/lib/bhn-n8n-stats/state.json.

CREATE TABLE IF NOT EXISTS n8n_execution_stats (
    id              BIGSERIAL PRIMARY KEY,
    measured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    n8n_exec_id     BIGINT NOT NULL,            -- n8n's own execution id
    workflow_id     TEXT,
    workflow_name   TEXT,
    status          TEXT,                        -- 'success' | 'error' | 'canceled' | 'running' | 'crashed' | 'waiting'
    mode            TEXT,                        -- 'manual' | 'trigger' | 'webhook' | 'schedule' | ...
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    duration_ms     BIGINT,
    error_message   TEXT,
    UNIQUE (n8n_exec_id)
);

CREATE INDEX IF NOT EXISTS n8n_es_workflow_idx ON n8n_execution_stats (workflow_id, started_at DESC);
CREATE INDEX IF NOT EXISTS n8n_es_status_idx   ON n8n_execution_stats (status);
CREATE INDEX IF NOT EXISTS n8n_es_started_idx  ON n8n_execution_stats (started_at DESC);

GRANT INSERT ON n8n_execution_stats TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE n8n_execution_stats_id_seq TO log_shipper;
GRANT SELECT ON n8n_execution_stats TO agent_reader, grafana_reader, ehuser;

COMMENT ON TABLE n8n_execution_stats IS
    'n8n workflow execution history, shipped from n8n SQLite to LA PG every 5 min. UNIQUE(n8n_exec_id) for idempotent re-shipping.';
