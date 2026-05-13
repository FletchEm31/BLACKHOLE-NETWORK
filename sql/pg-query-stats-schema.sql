-- pg-query-stats-schema.sql
-- BHN — PostgreSQL workload telemetry (LA hub).
-- Three tables: db-level activity, top queries, per-table stats.
--
-- Prerequisite: pg_stat_statements extension. Apply on LA:
--   sudo -u postgres psql -d eventhorizon -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"
-- And in postgresql.conf:
--   shared_preload_libraries = 'pg_stat_statements'  # plus restart

CREATE TABLE IF NOT EXISTS pg_activity_snapshots (
    id                BIGSERIAL PRIMARY KEY,
    measured_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    db_name           TEXT NOT NULL,
    numbackends       INTEGER,
    xact_commit       BIGINT,
    xact_rollback     BIGINT,
    blks_read         BIGINT,
    blks_hit          BIGINT,
    tup_returned      BIGINT,
    tup_fetched       BIGINT,
    tup_inserted      BIGINT,
    tup_updated       BIGINT,
    tup_deleted       BIGINT,
    deadlocks         BIGINT,
    temp_files        BIGINT,
    temp_bytes        BIGINT
);

CREATE TABLE IF NOT EXISTS pg_query_stats (
    id                BIGSERIAL PRIMARY KEY,
    measured_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    db_name           TEXT NOT NULL,
    role_name         TEXT,
    queryid           BIGINT,                -- pg_stat_statements stable id
    query_text        TEXT,                  -- first 500 chars normalized
    calls             BIGINT,
    total_exec_ms     NUMERIC(18,2),
    mean_exec_ms      NUMERIC(18,2),
    rows_returned     BIGINT,
    shared_blks_hit   BIGINT,
    shared_blks_read  BIGINT
);

CREATE TABLE IF NOT EXISTS pg_table_stats (
    id                BIGSERIAL PRIMARY KEY,
    measured_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    schema_name       TEXT NOT NULL,
    table_name        TEXT NOT NULL,
    n_live_tup        BIGINT,
    n_dead_tup        BIGINT,
    total_bytes       BIGINT,                -- pg_total_relation_size
    last_vacuum       TIMESTAMPTZ,
    last_autovacuum   TIMESTAMPTZ,
    last_analyze      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS pgact_time_idx   ON pg_activity_snapshots (measured_at DESC);
CREATE INDEX IF NOT EXISTS pgqs_time_idx    ON pg_query_stats        (measured_at DESC);
CREATE INDEX IF NOT EXISTS pgqs_queryid_idx ON pg_query_stats        (queryid);
CREATE INDEX IF NOT EXISTS pgts_time_idx    ON pg_table_stats        (measured_at DESC);
CREATE INDEX IF NOT EXISTS pgts_table_idx   ON pg_table_stats        (schema_name, table_name, measured_at DESC);

GRANT INSERT ON pg_activity_snapshots, pg_query_stats, pg_table_stats TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE pg_activity_snapshots_id_seq, pg_query_stats_id_seq, pg_table_stats_id_seq TO log_shipper;
GRANT SELECT ON pg_activity_snapshots, pg_query_stats, pg_table_stats TO agent_reader, grafana_reader, ehuser;

COMMENT ON TABLE pg_activity_snapshots IS 'PG db-level workload counters per snapshot. Cumulative — deltas via LAG.';
COMMENT ON TABLE pg_query_stats        IS 'Top-N queries per snapshot from pg_stat_statements (top 50 by total_exec_ms).';
COMMENT ON TABLE pg_table_stats        IS 'Per-table live/dead rows + size + vacuum timestamps for bloat tracking.';
