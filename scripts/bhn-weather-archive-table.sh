#!/bin/bash
# ╔══════════════════════════════════════════════════════════╗
# ║   BHN — WeatherBHN table archive-then-drop procedure     ║
# ║   pg_dump (custom format) → /mnt/eh-hdd-cold/pg-table-   ║
# ║   archive, detached via systemd-run + completion marker,  ║
# ║   restore-verify into a scratch DB, gated DROP.           ║
# ╚══════════════════════════════════════════════════════════╝
#
# Deploy:
#   scp scripts/bhn-weather-archive-table.sh root@<LA>:/usr/local/sbin/
#   ssh root@<LA> 'chmod 0755 /usr/local/sbin/bhn-weather-archive-table.sh'
#
# Usage:
#   bhn-weather-archive-table.sh install                 One-time: create weather_table_archive_log
#   bhn-weather-archive-table.sh archive <table>          Launch detached pg_dump -Fc to cold tier
#   bhn-weather-archive-table.sh status  <table>          Poll the most recent archive run (non-blocking)
#   bhn-weather-archive-table.sh verify  <table>          Detached restore into a scratch DB + row-count check
#   bhn-weather-archive-table.sh drop    <table> --yes-i-have-signoff
#                                                          Actual DROP TABLE. Refuses without the flag.
#
# Never run archive/verify as a bare foreground command — both launch a
# transient systemd unit and return immediately; poll with `status`.

set -u

COLD_TIER="/mnt/eh-hdd-cold"
ARCHIVE_DIR="${COLD_TIER}/pg-table-archive"
PG_DB="eventhorizon"
PG_USER="postgres"
LOGFILE="/var/log/bhn-weather-archive.log"

ts()  { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
log() { echo "$(ts) [bhn-weather-archive] $*" | tee -a "$LOGFILE"; }

sql_exec()  { sudo -u "$PG_USER" psql -d "$PG_DB" -v ON_ERROR_STOP=1 -qAtX -c "$1"; }
sql_query() { sudo -u "$PG_USER" psql -d "$PG_DB" -v ON_ERROR_STOP=1 -qAtX -c "$1"; }

require_root() { [[ $EUID -eq 0 ]] || { echo "Must run as root" >&2; exit 1; }; }

do_install() {
    require_root
    mkdir -p "$ARCHIVE_DIR"
    touch "$LOGFILE"; chmod 640 "$LOGFILE"
    sql_exec "
        CREATE TABLE IF NOT EXISTS weather_table_archive_log (
            id                BIGSERIAL PRIMARY KEY,
            table_name        TEXT NOT NULL,
            started_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at       TIMESTAMPTZ,
            row_count_before  BIGINT,
            dump_path         TEXT,
            dump_bytes        BIGINT,
            verify_status     TEXT,
            verify_row_count  BIGINT,
            dropped_at        TIMESTAMPTZ,
            status            TEXT NOT NULL DEFAULT 'running'
                              CHECK (status IN ('running','archived','verified','dropped','failed')),
            notes             TEXT
        );
        CREATE INDEX IF NOT EXISTS wtal_table_started_idx
            ON weather_table_archive_log (table_name, started_at DESC);
    " >/dev/null
    log "weather_table_archive_log ready, archive dir $ARCHIVE_DIR ready"
}

do_archive() {
    require_root
    local table=$1
    [[ -n "$table" ]] || { echo "Usage: $0 archive <table>" >&2; exit 1; }
    mkdir -p "$ARCHIVE_DIR"

    local stamp unit dump_path marker statusfile runner
    stamp=$(date -u +%Y%m%d-%H%M%S)
    echo "$stamp" > "${ARCHIVE_DIR}/${table}.latest-stamp"
    unit="bhn-archive-${table}-${stamp}"
    dump_path="${ARCHIVE_DIR}/${table}_${stamp}.dump"
    marker="${ARCHIVE_DIR}/${table}_${stamp}.done"
    statusfile="${ARCHIVE_DIR}/${table}_${stamp}.status"
    runner="${ARCHIVE_DIR}/${table}_${stamp}.runner.sh"

    local row_count id
    row_count=$(sql_query "SELECT COUNT(*) FROM ${table};") || { echo "table ${table} not found" >&2; exit 1; }
    id=$(sql_query "INSERT INTO weather_table_archive_log (table_name, row_count_before, dump_path, status)
                    VALUES ('${table}', ${row_count}, '${dump_path}', 'running') RETURNING id;")
    log "archive ${table}: row_count_before=${row_count} unit=${unit} log_id=${id}"

    # Write the payload as a standalone script (all values already substituted
    # here, plain literals below) instead of threading escaped variables
    # through systemd-run's -c string — avoids nested-quoting bugs.
    cat > "$runner" <<RUNNER
#!/bin/bash
set -euo pipefail
echo running > "${statusfile}"
sudo -u ${PG_USER} pg_dump -Fc -d ${PG_DB} -t ${table} > "${dump_path}"
sudo -u ${PG_USER} pg_restore --list "${dump_path}" > "${dump_path}.toc" 2>&1
bytes=\$(stat -c%s "${dump_path}")
sudo -u ${PG_USER} psql -d ${PG_DB} -qtAX -c "UPDATE weather_table_archive_log SET finished_at=NOW(), dump_bytes=\${bytes}, verify_status='toc_ok', status='archived' WHERE id=${id};"
echo ok > "${statusfile}"
touch "${marker}"
RUNNER
    chmod +x "$runner"

    systemd-run --unit="$unit" --description="BHN archive: ${table}" \
        --property=Type=oneshot --remain-after-exit --collect \
        /bin/bash "$runner"
    log "detached. poll with: $0 status ${table}"
}

do_status() {
    local table=$1
    [[ -n "$table" ]] || { echo "Usage: $0 status <table>" >&2; exit 1; }
    local stamp
    stamp=$(cat "${ARCHIVE_DIR}/${table}.latest-stamp" 2>/dev/null) || { echo "no run recorded for ${table}"; exit 1; }
    local unit="bhn-archive-${table}-${stamp}"
    local marker="${ARCHIVE_DIR}/${table}_${stamp}.done"
    if [[ -f "$marker" ]]; then echo "DONE — $marker present"; else echo "NOT DONE YET"; fi
    systemctl show "$unit" -p ActiveState,Result,ExecMainStatus 2>/dev/null || true
    cat "${ARCHIVE_DIR}/${table}_${stamp}.status" 2>/dev/null || true
}

do_verify() {
    require_root
    local table=$1
    [[ -n "$table" ]] || { echo "Usage: $0 verify <table>" >&2; exit 1; }
    local stamp
    stamp=$(cat "${ARCHIVE_DIR}/${table}.latest-stamp") || { echo "no archive run found for ${table}" >&2; exit 1; }
    local dump_path="${ARCHIVE_DIR}/${table}_${stamp}.dump"
    local unit="bhn-verify-${table}-${stamp}"
    local marker="${ARCHIVE_DIR}/${table}_${stamp}.verify.done"
    local verify_db="weatherbhn_archive_verify_${table}"
    local runner="${ARCHIVE_DIR}/${table}_${stamp}.verify-runner.sh"

    cat > "$runner" <<RUNNER
#!/bin/bash
set -euo pipefail
sudo -u ${PG_USER} psql -c "DROP DATABASE IF EXISTS ${verify_db};"
sudo -u ${PG_USER} psql -c "CREATE DATABASE ${verify_db};"
sudo -u ${PG_USER} pg_restore -d ${verify_db} "${dump_path}"
n=\$(sudo -u ${PG_USER} psql -d ${verify_db} -qtAX -c "SELECT COUNT(*) FROM ${table};")
before=\$(sudo -u ${PG_USER} psql -d ${PG_DB} -qtAX -c "SELECT row_count_before FROM weather_table_archive_log WHERE table_name='${table}' ORDER BY id DESC LIMIT 1;")
status='mismatch'; [[ "\$n" == "\$before" ]] && status='restore_verified'
sudo -u ${PG_USER} psql -d ${PG_DB} -qtAX -c "UPDATE weather_table_archive_log SET verify_status='\${status}', verify_row_count=\${n} WHERE id = (SELECT id FROM weather_table_archive_log WHERE table_name='${table}' ORDER BY id DESC LIMIT 1);"
sudo -u ${PG_USER} psql -c "DROP DATABASE ${verify_db};"
touch "${marker}"
RUNNER
    chmod +x "$runner"

    systemd-run --unit="$unit" --description="BHN verify restore: ${table}" \
        --property=Type=oneshot --remain-after-exit --collect \
        /bin/bash "$runner"
    log "detached verify launched. poll with: $0 status ${table}"
}

do_drop() {
    require_root
    local table=$1
    local flag=${2:-}
    [[ -n "$table" ]] || { echo "Usage: $0 drop <table> --yes-i-have-signoff" >&2; exit 1; }
    if [[ "$flag" != "--yes-i-have-signoff" ]]; then
        echo "This will DROP TABLE ${table}. Re-run with --yes-i-have-signoff to proceed." >&2
        exit 1
    fi
    sql_exec "DROP TABLE ${table};"
    sql_exec "UPDATE weather_table_archive_log SET dropped_at=NOW(), status='dropped' WHERE id = (SELECT id FROM weather_table_archive_log WHERE table_name='${table}' ORDER BY id DESC LIMIT 1);" >/dev/null
    log "DROPPED ${table}"
}

case "${1:-}" in
    install) do_install ;;
    archive) do_archive "${2:-}" ;;
    status)  do_status  "${2:-}" ;;
    verify)  do_verify  "${2:-}" ;;
    drop)    do_drop    "${2:-}" "${3:-}" ;;
    *) echo "Usage: $0 {install|archive|status|verify|drop} [table] [--yes-i-have-signoff]" >&2; exit 1 ;;
esac
