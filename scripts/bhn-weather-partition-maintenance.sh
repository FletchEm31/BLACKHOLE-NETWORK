#!/bin/bash
# ╔══════════════════════════════════════════════════════════╗
# ║  BHN — Monthly partition maintenance for                 ║
# ║  weather_bronze_kalshi_market_snapshots.                  ║
# ║  1) Creates the partition for current+2 months so the     ║
# ║     collector never hits a missing-partition insert error.║
# ║  2) Moves the partition that just aged past the retention ║
# ║     cutoff to the eh_cold_ts tablespace.                  ║
# ║  3) Alerts (does not silently ignore) if the DEFAULT      ║
# ║     partition ever has rows.                              ║
# ╚══════════════════════════════════════════════════════════╝
#
# Run via bhn-weather-partition-maintenance.timer (monthly).
# Manual dry run: bhn-weather-partition-maintenance.sh --dry-run

set -u

TABLE="weather_bronze_kalshi_market_snapshots"
PG_DB="eventhorizon"
PG_USER="postgres"
RETENTION_DAYS=90
LOGFILE="/var/log/bhn-weather-partition-maintenance.log"
DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

ts()  { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
log() { echo "$(ts) [bhn-weather-partition-maintenance] $*" | tee -a "$LOGFILE"; }
sql_exec()  { sudo -u "$PG_USER" psql -d "$PG_DB" -v ON_ERROR_STOP=1 -qAtX -c "$1"; }
sql_query() { sudo -u "$PG_USER" psql -d "$PG_DB" -v ON_ERROR_STOP=1 -qAtX -c "$1"; }

sql_exec "
    CREATE TABLE IF NOT EXISTS weather_partition_maintenance_log (
        id              BIGSERIAL PRIMARY KEY,
        started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        finished_at     TIMESTAMPTZ,
        action          TEXT NOT NULL,
        detail          TEXT,
        status          TEXT NOT NULL DEFAULT 'running'
                        CHECK (status IN ('running','success','failed','alert')),
        error           TEXT
    );
" >/dev/null

log_row_open() {
    sql_query "INSERT INTO weather_partition_maintenance_log (action, detail, status)
               VALUES ('$1', '$2', 'running') RETURNING id;"
}
log_row_close() {
    local id=$1 status=$2 detail=$3
    sql_exec "UPDATE weather_partition_maintenance_log SET finished_at=NOW(), status='${status}', detail='${detail}' WHERE id=${id};" >/dev/null
}

# ── 1. Create the partition for current+2 months ──────────────────────
future_month=$(date -u -d "+2 month" +%Y-%m-01)
future_month_end=$(date -u -d "${future_month} +1 month" +%Y-%m-01)
part_suffix=$(date -u -d "${future_month}" +%Y_%m)
part_name="${TABLE}_p${part_suffix}"

id=$(log_row_open "create_future_partition" "${part_name} FOR VALUES FROM ('${future_month}') TO ('${future_month_end}')")
if $DRY_RUN; then
    log "[dry-run] would create partition ${part_name} FOR VALUES FROM ('${future_month}') TO ('${future_month_end}')"
    log_row_close "$id" "success" "dry-run, not executed"
else
    if sql_exec "CREATE TABLE IF NOT EXISTS ${part_name} PARTITION OF ${TABLE} FOR VALUES FROM ('${future_month}') TO ('${future_month_end}');" >/dev/null 2>>"$LOGFILE"; then
        log "created/confirmed partition ${part_name}"
        log_row_close "$id" "success" "created or already existed"
    else
        log "FAILED to create partition ${part_name}"
        log_row_close "$id" "failed" "CREATE TABLE failed, see $LOGFILE"
    fi
fi

# ── 2. Relocate the partition that just aged past RETENTION_DAYS ──────
cutoff_month_start=$(date -u -d "-${RETENTION_DAYS} days" +%Y-%m-01)
cutoff_suffix=$(date -u -d "${cutoff_month_start}" +%Y_%m)
aging_part="${TABLE}_p${cutoff_suffix}"

current_tablespace=$(sql_query "
    SELECT COALESCE(t.spcname, 'pg_default')
    FROM pg_class c LEFT JOIN pg_tablespace t ON t.oid = c.reltablespace
    WHERE c.relname = '${aging_part}';
" 2>/dev/null)

if [[ -z "$current_tablespace" ]]; then
    log "partition ${aging_part} does not exist (nothing to relocate this month)"
elif [[ "$current_tablespace" == "eh_cold_ts" ]]; then
    log "partition ${aging_part} already on eh_cold_ts, nothing to do"
else
    id=$(log_row_open "relocate_to_cold" "${aging_part} -> eh_cold_ts")
    if $DRY_RUN; then
        log "[dry-run] would run: ALTER TABLE ${aging_part} SET TABLESPACE eh_cold_ts;"
        log_row_close "$id" "success" "dry-run, not executed"
    else
        log "relocating ${aging_part} to eh_cold_ts (locks only this partition)"
        if sql_exec "ALTER TABLE ${aging_part} SET TABLESPACE eh_cold_ts;" >/dev/null 2>>"$LOGFILE"; then
            log "relocated ${aging_part} to eh_cold_ts"
            log_row_close "$id" "success" "relocated"
        else
            log "FAILED to relocate ${aging_part}"
            log_row_close "$id" "failed" "ALTER TABLE SET TABLESPACE failed, see $LOGFILE"
        fi
    fi
fi

# ── 3. Alert if the DEFAULT partition ever has rows ────────────────────
default_rows=$(sql_query "SELECT COUNT(*) FROM ${TABLE}_default;" 2>/dev/null || echo "0")
if [[ "$default_rows" != "0" ]]; then
    id=$(log_row_open "default_partition_check" "DEFAULT partition has ${default_rows} rows -- clock skew or bad data")
    log_row_close "$id" "alert" "${default_rows} rows in ${TABLE}_default -- investigate immediately"
    log "ALERT: ${TABLE}_default has ${default_rows} rows -- this should never happen. Investigate."
else
    log "DEFAULT partition check OK (0 rows)"
fi

log "=== maintenance run complete ==="
