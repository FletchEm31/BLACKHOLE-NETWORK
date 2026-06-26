#!/bin/bash
# bhn-pg-stats-collector — snapshot PG workload stats on LA → LA PG (self-write).
#
# Snapshots three views in one cron run:
#   1. pg_stat_database row for `eventhorizon`
#   2. Top 50 queries from pg_stat_statements by total_exec_time
#   3. Per-table pg_stat_user_tables + pg_total_relation_size
#
# Reads PG DSN from /root/.bhn-pg-stats.env (mode 0600):
#   BHN_PG_STATS_PG_DSN='postgresql://log_shipper:<PW>@<BHN_WG_LA_IP>/eventhorizon'
#
# Prereq: CREATE EXTENSION pg_stat_statements; (see schema file header)
#
# Cron (LA only):
#   */5 * * * * root /usr/local/sbin/bhn-pg-stats-collector.sh

set -euo pipefail

ENV_FILE=/root/.bhn-pg-stats.env
[[ -r "$ENV_FILE" ]] || { echo "bhn-pg-stats: missing $ENV_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
[[ -n "${BHN_PG_STATS_PG_DSN:-}" ]] || { echo "bhn-pg-stats: BHN_PG_STATS_PG_DSN empty" >&2; exit 1; }

# All three snapshots in one psql session — one transaction, atomic.
psql "$BHN_PG_STATS_PG_DSN" -v ON_ERROR_STOP=1 >/dev/null <<'SQL' \
  || { echo "bhn-pg-stats: PG insert failed" >&2; exit 2; }
BEGIN;

-- 1. db activity
INSERT INTO pg_activity_snapshots (db_name, numbackends, xact_commit, xact_rollback,
                                    blks_read, blks_hit, tup_returned, tup_fetched,
                                    tup_inserted, tup_updated, tup_deleted, deadlocks,
                                    temp_files, temp_bytes)
SELECT datname, numbackends, xact_commit, xact_rollback,
       blks_read, blks_hit, tup_returned, tup_fetched,
       tup_inserted, tup_updated, tup_deleted, deadlocks,
       temp_files, temp_bytes
FROM pg_stat_database
WHERE datname = current_database();

-- 2. top queries (only if pg_stat_statements is installed)
INSERT INTO pg_query_stats (db_name, role_name, queryid, query_text,
                            calls, total_exec_ms, mean_exec_ms, rows_returned,
                            shared_blks_hit, shared_blks_read)
SELECT current_database(),
       (SELECT rolname FROM pg_roles WHERE oid = pss.userid),
       pss.queryid,
       LEFT(pss.query, 500),
       pss.calls,
       pss.total_exec_time,
       pss.mean_exec_time,
       pss.rows,
       pss.shared_blks_hit,
       pss.shared_blks_read
FROM pg_stat_statements pss
WHERE pss.dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
ORDER BY pss.total_exec_time DESC
LIMIT 50;

-- 3. per-table stats
INSERT INTO pg_table_stats (schema_name, table_name, n_live_tup, n_dead_tup,
                            total_bytes, last_vacuum, last_autovacuum, last_analyze)
SELECT schemaname, relname,
       n_live_tup, n_dead_tup,
       pg_total_relation_size(relid),
       last_vacuum, last_autovacuum, last_analyze
FROM pg_stat_user_tables;

COMMIT;
SQL
