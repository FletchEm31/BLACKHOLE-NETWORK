#!/bin/bash
# ╔══════════════════════════════════════════════════════════╗
# ║  BHN — Archive+delete old rows from                       ║
# ║  weather_silver_market_conformed.                          ║
# ║                                                            ║
# ║  Unlike weather_bronze_kalshi_market_snapshots, this table ║
# ║  is NOT partitioned: it carries a partial unique index     ║
# ║  (one is_latest_snapshot=TRUE row per market_ticker) that  ║
# ║  native time-partitioning would stop enforcing across      ║
# ║  partition boundaries. Instead: archive rows that are both ║
# ║  (a) not the current "latest" row for their ticker and     ║
# ║  (b) older than the retention cutoff, to cold storage,     ║
# ║  then delete them from the hot table. Archived rows are    ║
# ║  NOT live-queryable without a restore (accepted trade-off, ║
# ║  confirmed with operator 2026-07-07).                      ║
# ╚══════════════════════════════════════════════════════════╝
#
# Deploy:
#   scp scripts/bhn-weather-silver-market-archive.sh root@<LA>:/usr/local/sbin/
#   ssh root@<LA> 'chmod 0755 /usr/local/sbin/bhn-weather-silver-market-archive.sh'
#
# Usage:
#   bhn-weather-silver-market-archive.sh install              One-time: create audit log table
#   bhn-weather-silver-market-archive.sh archive [days]        Detached COPY of eligible rows to
#                                                              cold tier (default cutoff: 90 days)
#   bhn-weather-silver-market-archive.sh status                Poll progress (non-blocking)
#   bhn-weather-silver-market-archive.sh delete --yes-i-have-signoff
#                                                              Deletes exactly the rows captured
#                                                              in the most recent archive run
#                                                              (matched by id list, not by
#                                                              re-evaluating the WHERE clause —
#                                                              avoids a race with newly-arrived rows)

set -u

TABLE="weather_silver_market_conformed"
COLD_TIER="/mnt/eh-hdd-cold"
ARCHIVE_DIR="${COLD_TIER}/pg-table-archive"
PG_DB="eventhorizon"
PG_USER="postgres"
LOGFILE="/var/log/bhn-weather-silver-market-archive.log"

ts()  { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
log() { echo "$(ts) [bhn-silver-market-archive] $*" | tee -a "$LOGFILE"; }
require_root() { [[ $EUID -eq 0 ]] || { echo "Must run as root" >&2; exit 1; }; }
sql_exec()  { sudo -u "$PG_USER" psql -d "$PG_DB" -v ON_ERROR_STOP=1 -qAtX -c "$1"; }
sql_query() { sudo -u "$PG_USER" psql -d "$PG_DB" -v ON_ERROR_STOP=1 -qAtX -c "$1"; }

do_install() {
    require_root
    mkdir -p "$ARCHIVE_DIR"
    touch "$LOGFILE"; chmod 640 "$LOGFILE"
    sql_exec "
        CREATE TABLE IF NOT EXISTS weather_silver_market_archive_log (
            id                BIGSERIAL PRIMARY KEY,
            started_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at       TIMESTAMPTZ,
            cutoff_days       INT NOT NULL,
            row_count         BIGINT,
            id_list_path      TEXT,
            dump_path         TEXT,
            dump_bytes        BIGINT,
            deleted_at        TIMESTAMPTZ,
            status            TEXT NOT NULL DEFAULT 'running'
                              CHECK (status IN ('running','archived','deleted','failed')),
            notes             TEXT
        );
    " >/dev/null
    log "weather_silver_market_archive_log ready"
}

do_archive() {
    require_root
    local cutoff_days=${1:-90}
    mkdir -p "$ARCHIVE_DIR"
    local stamp unit dump_path id_list_path marker statusfile runner
    stamp=$(date -u +%Y%m%d-%H%M%S)
    echo "$stamp" > "${ARCHIVE_DIR}/silver-market-archive.latest-stamp"
    unit="bhn-silver-market-archive-${stamp}"
    dump_path="${ARCHIVE_DIR}/weather_silver_market_conformed_${stamp}.csv.gz"
    id_list_path="${ARCHIVE_DIR}/weather_silver_market_conformed_${stamp}.ids.txt"
    marker="${ARCHIVE_DIR}/silver-market-archive_${stamp}.done"
    statusfile="${ARCHIVE_DIR}/silver-market-archive_${stamp}.status"
    runner="${ARCHIVE_DIR}/silver-market-archive_${stamp}.runner.sh"

    local row_count id
    row_count=$(sql_query "SELECT COUNT(*) FROM ${TABLE} WHERE is_latest_snapshot = FALSE AND created_at < NOW() - INTERVAL '${cutoff_days} days';")
    id=$(sql_query "INSERT INTO weather_silver_market_archive_log (cutoff_days, row_count, id_list_path, dump_path, status)
                    VALUES (${cutoff_days}, ${row_count}, '${id_list_path}', '${dump_path}', 'running') RETURNING id;")
    log "archive: cutoff_days=${cutoff_days} eligible_rows=${row_count} unit=${unit} log_id=${id}"

    cat > "$runner" <<RUNNER
#!/bin/bash
set -euo pipefail
echo running > "${statusfile}"

# Capture the exact id list up front so delete matches these rows only,
# not a re-evaluation of the WHERE clause (avoids racing newly-arrived rows).
sudo -u ${PG_USER} psql -d ${PG_DB} -qtAX -c \
    "SELECT id FROM ${TABLE} WHERE is_latest_snapshot = FALSE AND created_at < NOW() - INTERVAL '${cutoff_days} days' ORDER BY id;" \
    | sed '/^\s*$/d' > "${id_list_path}"

# Piped through this (root-owned) shell rather than psql's TO PROGRAM --
# TO PROGRAM would run gzip as the postgres OS user, which can't write to
# this root-owned archive directory.
sudo -u ${PG_USER} psql -d ${PG_DB} -c \
    "\\copy (SELECT m.* FROM ${TABLE} m JOIN (SELECT unnest(string_to_array(pg_read_file('${id_list_path}'), E'\\n'))::bigint AS id) ids ON ids.id = m.id) TO STDOUT WITH CSV HEADER" \
    | gzip > "${dump_path}"

bytes=\$(stat -c%s "${dump_path}")
copied_rows=\$(wc -l < "${id_list_path}")
sudo -u ${PG_USER} psql -d ${PG_DB} -qtAX -c \
    "UPDATE weather_silver_market_archive_log SET finished_at=NOW(), dump_bytes=\${bytes}, status='archived', notes='captured \${copied_rows} ids' WHERE id=${id};"
echo ok > "${statusfile}"
touch "${marker}"
RUNNER
    chmod +x "$runner"

    systemd-run --unit="$unit" --description="BHN silver-market archive" \
        --property=Type=oneshot --remain-after-exit --collect \
        /bin/bash "$runner"
    log "detached. poll with: $0 status"
}

do_status() {
    local stamp
    stamp=$(cat "${ARCHIVE_DIR}/silver-market-archive.latest-stamp" 2>/dev/null) || { echo "no run recorded"; exit 1; }
    local marker="${ARCHIVE_DIR}/silver-market-archive_${stamp}.done"
    [[ -f "$marker" ]] && echo "DONE" || echo "NOT DONE YET"
    cat "${ARCHIVE_DIR}/silver-market-archive_${stamp}.status" 2>/dev/null
    sql_query "SELECT id, row_count, dump_bytes, status, notes FROM weather_silver_market_archive_log ORDER BY id DESC LIMIT 1;"
}

do_delete() {
    require_root
    local flag=${1:-}
    if [[ "$flag" != "--yes-i-have-signoff" ]]; then
        echo "This will DELETE the archived rows from ${TABLE}. Re-run with --yes-i-have-signoff to proceed." >&2
        exit 1
    fi
    local stamp id_list_path row_id
    stamp=$(cat "${ARCHIVE_DIR}/silver-market-archive.latest-stamp") || { echo "no archive run found" >&2; exit 1; }
    id_list_path="${ARCHIVE_DIR}/weather_silver_market_conformed_${stamp}.ids.txt"
    row_id=$(sql_query "SELECT id FROM weather_silver_market_archive_log WHERE dump_path LIKE '%${stamp}%' ORDER BY id DESC LIMIT 1;")

    local deleted
    deleted=$(sql_query "DELETE FROM ${TABLE} m USING (SELECT unnest(string_to_array(pg_read_file('${id_list_path}'), E'\n'))::bigint AS id) ids WHERE m.id = ids.id RETURNING 1;" | wc -l)
    sql_exec "UPDATE weather_silver_market_archive_log SET deleted_at=NOW(), status='deleted' WHERE id=${row_id};" >/dev/null
    log "DELETED ${deleted} rows from ${TABLE} (archive log id=${row_id})"
}

case "${1:-}" in
    install) do_install ;;
    archive) do_archive "${2:-90}" ;;
    status)  do_status ;;
    delete)  do_delete "${2:-}" ;;
    *) echo "Usage: $0 {install|archive [days]|status|delete --yes-i-have-signoff}" >&2; exit 1 ;;
esac
