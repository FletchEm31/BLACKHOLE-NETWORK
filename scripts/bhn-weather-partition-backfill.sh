#!/bin/bash
# ╔══════════════════════════════════════════════════════════╗
# ║  BHN — Backfill weather_bronze_kalshi_market_snapshots    ║
# ║  from the live table into its partitioned _new replacement║
# ║  in batches, detached via systemd-run + completion marker. ║
# ╚══════════════════════════════════════════════════════════╝
#
# Deploy:
#   scp scripts/bhn-weather-partition-backfill.sh root@<LA>:/usr/local/sbin/
#   ssh root@<LA> 'chmod 0755 /usr/local/sbin/bhn-weather-partition-backfill.sh'
#
# Usage:
#   bhn-weather-partition-backfill.sh run        Launch detached backfill (resumable)
#   bhn-weather-partition-backfill.sh status      Poll progress (non-blocking)
#   bhn-weather-partition-backfill.sh catchup     One more incremental pass (run right
#                                                  before cutover to shrink the remaining
#                                                  delta to seconds' worth of rows)
#
# Never run `run`/`catchup` as a bare foreground command — both launch a
# transient systemd unit and return immediately; poll with `status`.
# Safe to re-run `run` — it resumes from the last copied id (tracked in
# weather_partition_backfill_state), it does not restart from zero.

set -u

SRC_TABLE="weather_bronze_kalshi_market_snapshots"
DST_TABLE="weather_bronze_kalshi_market_snapshots_new"
BATCH_SIZE=200000
PG_DB="eventhorizon"
PG_USER="postgres"
STATE_DIR="/mnt/eh-hdd-cold/pg-table-archive"
LOGFILE="/var/log/bhn-weather-partition-backfill.log"

ts()  { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
log() { echo "$(ts) [bhn-weather-partition-backfill] $*" | tee -a "$LOGFILE"; }
require_root() { [[ $EUID -eq 0 ]] || { echo "Must run as root" >&2; exit 1; }; }
sql_query() { sudo -u "$PG_USER" psql -d "$PG_DB" -v ON_ERROR_STOP=1 -qAtX -c "$1"; }

do_run() {
    require_root
    mkdir -p "$STATE_DIR"
    local unit stamp runner
    stamp=$(date -u +%Y%m%d-%H%M%S)
    unit="bhn-weather-partition-backfill-${stamp}"
    runner="${STATE_DIR}/partition-backfill_${stamp}.runner.sh"
    local marker="${STATE_DIR}/partition-backfill_${stamp}.done"
    local statusfile="${STATE_DIR}/partition-backfill.status"
    echo "$stamp" > "${STATE_DIR}/partition-backfill.latest-stamp"

    cat > "$runner" <<RUNNER
#!/bin/bash
set -euo pipefail
echo "running" > "${statusfile}"

sudo -u ${PG_USER} psql -d ${PG_DB} -v ON_ERROR_STOP=1 -c "
    CREATE TABLE IF NOT EXISTS weather_partition_backfill_state (
        src_table   TEXT PRIMARY KEY,
        last_id     BIGINT NOT NULL DEFAULT 0,
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    INSERT INTO weather_partition_backfill_state (src_table, last_id)
        VALUES ('${SRC_TABLE}', 0)
        ON CONFLICT (src_table) DO NOTHING;
"

total_copied=0
while true; do
    last_id=\$(sudo -u ${PG_USER} psql -d ${PG_DB} -qtAX -c \
        "SELECT last_id FROM weather_partition_backfill_state WHERE src_table='${SRC_TABLE}';")
    copied=\$(sudo -u ${PG_USER} psql -d ${PG_DB} -v ON_ERROR_STOP=1 -qtAX -c "
        WITH batch AS (
            SELECT * FROM ${SRC_TABLE}
            WHERE id > \${last_id}
            ORDER BY id
            LIMIT ${BATCH_SIZE}
        ), ins AS (
            INSERT INTO ${DST_TABLE}
            SELECT * FROM batch
            RETURNING id
        )
        SELECT COUNT(*) FROM ins;
    ")
    if [[ "\$copied" -eq 0 ]]; then
        log_line=\$(date -u +'%Y-%m-%dT%H:%M:%SZ')
        echo "\${log_line} no more rows to copy (last_id=\${last_id}), total_copied=\${total_copied}" | tee -a "${LOGFILE}"
        break
    fi
    new_last_id=\$(sudo -u ${PG_USER} psql -d ${PG_DB} -qtAX -c \
        "SELECT MAX(id) FROM ${DST_TABLE} WHERE id > \${last_id};")
    sudo -u ${PG_USER} psql -d ${PG_DB} -v ON_ERROR_STOP=1 -qtAX -c \
        "UPDATE weather_partition_backfill_state SET last_id=\${new_last_id}, updated_at=NOW() WHERE src_table='${SRC_TABLE}';"
    total_copied=\$((total_copied + copied))
    log_line=\$(date -u +'%Y-%m-%dT%H:%M:%SZ')
    echo "\${log_line} copied batch of \${copied} (last_id now \${new_last_id}, total \${total_copied})" | tee -a "${LOGFILE}"
done

echo "done total_copied=\${total_copied}" > "${statusfile}"
touch "${marker}"
RUNNER
    chmod +x "$runner"

    systemd-run --unit="$unit" --description="BHN weather partition backfill" \
        --property=Type=oneshot --remain-after-exit --collect \
        /bin/bash "$runner"
    log "detached backfill launched (unit=$unit). poll with: $0 status"
}

do_status() {
    local statusfile="${STATE_DIR}/partition-backfill.status"
    local stamp
    stamp=$(cat "${STATE_DIR}/partition-backfill.latest-stamp" 2>/dev/null) || { echo "no backfill run recorded"; exit 1; }
    local marker="${STATE_DIR}/partition-backfill_${stamp}.done"
    [[ -f "$marker" ]] && echo "DONE" || echo "IN PROGRESS"
    cat "$statusfile" 2>/dev/null
    sql_query "SELECT last_id, updated_at FROM weather_partition_backfill_state WHERE src_table='${SRC_TABLE}';" 2>/dev/null
    echo "--- row counts ---"
    sql_query "SELECT (SELECT COUNT(*) FROM ${SRC_TABLE}) AS src, (SELECT COUNT(*) FROM ${DST_TABLE}) AS dst;"
}

case "${1:-}" in
    run|catchup) do_run ;;
    status)      do_status ;;
    *) echo "Usage: $0 {run|catchup|status}" >&2; exit 1 ;;
esac
