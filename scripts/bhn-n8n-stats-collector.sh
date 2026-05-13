#!/bin/bash
# bhn-n8n-stats-collector — ship n8n execution history → LA PG.
#
# Queries n8n's SQLite execution_entity + workflow_entity tables for runs
# newer than the last-shipped id, then INSERTs the deltas. State at
# /var/lib/bhn-n8n-stats/state.json tracks the highwater id.
#
# Reads PG DSN from /root/.bhn-n8n-stats.env:
#   BHN_N8N_STATS_PG_DSN='postgresql://log_shipper:<PW>@10.8.0.1/eventhorizon'
#   BHN_N8N_SQLITE_PATH='/root/.n8n/database.sqlite'   # override if non-default
#
# Cron (LA only):
#   */5 * * * * root /usr/local/sbin/bhn-n8n-stats-collector.sh
#
# Note: if n8n is configured to use PostgreSQL as its DB (DB_TYPE=postgresdb)
# instead of SQLite, this script needs adapting. Current default install
# uses SQLite at ~/.n8n/database.sqlite.

set -euo pipefail

ENV_FILE=/root/.bhn-n8n-stats.env
STATE_DIR=/var/lib/bhn-n8n-stats
STATE_FILE="$STATE_DIR/state.json"

[[ -r "$ENV_FILE" ]] || { echo "bhn-n8n-stats: missing $ENV_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
[[ -n "${BHN_N8N_STATS_PG_DSN:-}" ]] || { echo "bhn-n8n-stats: BHN_N8N_STATS_PG_DSN empty" >&2; exit 1; }
SQLITE_PATH="${BHN_N8N_SQLITE_PATH:-/root/.n8n/database.sqlite}"
[[ -r "$SQLITE_PATH" ]] || { echo "bhn-n8n-stats: $SQLITE_PATH not readable" >&2; exit 3; }

command -v sqlite3 >/dev/null || { echo "bhn-n8n-stats: sqlite3 not installed (apt install sqlite3)" >&2; exit 3; }
command -v jq      >/dev/null || { echo "bhn-n8n-stats: jq not installed" >&2; exit 3; }

mkdir -p "$STATE_DIR"
last_id=0
if [[ -f "$STATE_FILE" ]]; then
  last_id=$(jq -r '.last_id // 0' "$STATE_FILE")
fi

# Pull rows newer than last_id. JOIN workflow_entity for the workflow name.
# Field names valid for n8n >= 1.0 schema. Older versions may differ slightly.
rows=$(sqlite3 -separator $'\x1f' "$SQLITE_PATH" "
  SELECT e.id, e.workflowId, w.name, e.status, e.mode,
         e.startedAt, e.stoppedAt,
         CASE WHEN e.stoppedAt IS NOT NULL THEN (julianday(e.stoppedAt) - julianday(e.startedAt)) * 86400000 ELSE NULL END AS duration_ms,
         COALESCE(json_extract(e.data, '$.resultData.error.message'), '')
  FROM execution_entity e
  LEFT JOIN workflow_entity w ON w.id = e.workflowId
  WHERE e.id > $last_id
  ORDER BY e.id
  LIMIT 5000;
" 2>/dev/null || true)
[[ -z "$rows" ]] && exit 0

esc() { printf '%s' "$1" | sed "s/'/''/g"; }

values=""
new_high=$last_id
while IFS=$'\x1f' read -r exec_id wf_id wf_name status mode started stopped duration err; do
  [[ -z "$exec_id" ]] && continue
  [[ "$exec_id" -gt "$new_high" ]] && new_high="$exec_id"
  dur_lit="NULL"; [[ -n "$duration" && "$duration" != "" ]] && dur_lit=$(printf '%.0f' "$duration")
  started_lit="NULL"; [[ -n "$started" ]] && started_lit="'$(esc "$started")'::timestamptz"
  stopped_lit="NULL"; [[ -n "$stopped" ]] && stopped_lit="'$(esc "$stopped")'::timestamptz"
  values+="($exec_id,'$(esc "$wf_id")','$(esc "$wf_name")','$(esc "$status")','$(esc "$mode")',$started_lit,$stopped_lit,$dur_lit,'$(esc "$err")'),"
done <<< "$rows"
values="${values%,}"
[[ -z "$values" ]] && exit 0

psql "$BHN_N8N_STATS_PG_DSN" -v ON_ERROR_STOP=1 >/dev/null <<SQL \
  || { echo "bhn-n8n-stats: PG insert failed" >&2; exit 2; }
INSERT INTO n8n_execution_stats (n8n_exec_id, workflow_id, workflow_name, status, mode,
                                  started_at, finished_at, duration_ms, error_message)
VALUES $values
ON CONFLICT (n8n_exec_id) DO NOTHING;
SQL

# Persist new highwater
printf '{"last_id": %d}\n' "$new_high" > "$STATE_FILE.tmp"
mv "$STATE_FILE.tmp" "$STATE_FILE"
