#!/bin/bash
# ╔══════════════════════════════════════════════════════════╗
# ║  BHN — Cutover weather_bronze_kalshi_market_snapshots to  ║
# ║  its partitioned replacement. GATED — do not run `swap`   ║
# ║  without explicit operator sign-off; it stops the live    ║
# ║  collector timer for the duration of the rename-swap.     ║
# ╚══════════════════════════════════════════════════════════╝
#
# Run bhn-weather-partition-backfill.sh run/status to completion FIRST.
# Then:
#   bhn-weather-partition-cutover.sh precheck   Read-only: row-count diff, backfill freshness
#   bhn-weather-partition-cutover.sh swap        THE gated step — stops the collector timer,
#                                                final catch-up copy, builds indexes, verifies
#                                                counts match, atomic rename-swap, restarts timer
#   bhn-weather-partition-cutover.sh rollback   Only if swap already ran and something broke:
#                                                renames back to the pre-cutover table

set -u

SRC_TABLE="weather_bronze_kalshi_market_snapshots"
DST_TABLE="weather_bronze_kalshi_market_snapshots_new"
OLD_TABLE="weather_bronze_kalshi_market_snapshots_old"
PG_DB="eventhorizon"
PG_USER="postgres"
TIMER="bhn-weather-collector.timer"
SERVICE="bhn-weather-collector.service"
LOGFILE="/var/log/bhn-weather-partition-cutover.log"

ts()  { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
log() { echo "$(ts) [bhn-weather-partition-cutover] $*" | tee -a "$LOGFILE"; }
require_root() { [[ $EUID -eq 0 ]] || { echo "Must run as root" >&2; exit 1; }; }
sql_exec()  { sudo -u "$PG_USER" psql -d "$PG_DB" -v ON_ERROR_STOP=1 -qAtX -c "$1"; }
sql_query() { sudo -u "$PG_USER" psql -d "$PG_DB" -v ON_ERROR_STOP=1 -qAtX -c "$1"; }

do_precheck() {
    echo "--- row counts ---"
    sql_query "SELECT (SELECT COUNT(*) FROM ${SRC_TABLE}) AS src, (SELECT COUNT(*) FROM ${DST_TABLE}) AS dst;"
    echo "--- backfill state ---"
    sql_query "SELECT * FROM weather_partition_backfill_state WHERE src_table='${SRC_TABLE}';"
    echo "--- collector timer status ---"
    systemctl status "$TIMER" --no-pager | head -5
}

do_swap() {
    require_root
    log "=== cutover start ==="

    log "stopping ${TIMER}"
    systemctl stop "$TIMER"

    log "waiting for any in-flight ${SERVICE} run to finish"
    while systemctl is-active --quiet "$SERVICE"; do sleep 2; done

    log "final catch-up copy"
    local last_id copied
    last_id=$(sql_query "SELECT last_id FROM weather_partition_backfill_state WHERE src_table='${SRC_TABLE}';")
    copied=$(sql_query "
        WITH batch AS (
            SELECT * FROM ${SRC_TABLE} WHERE id > ${last_id} ORDER BY id
        ), ins AS (
            INSERT INTO ${DST_TABLE} SELECT * FROM batch RETURNING id
        )
        SELECT COUNT(*) FROM ins;
    ")
    log "final catch-up copied ${copied} rows"

    local src_count dst_count
    src_count=$(sql_query "SELECT COUNT(*) FROM ${SRC_TABLE};")
    dst_count=$(sql_query "SELECT COUNT(*) FROM ${DST_TABLE};")
    log "post-catchup counts: src=${src_count} dst=${dst_count}"
    if [[ "$src_count" != "$dst_count" ]]; then
        log "ABORT: row counts do not match (src=${src_count} dst=${dst_count}). Restarting timer, NOT swapping."
        systemctl start "$TIMER"
        exit 1
    fi

    log "building indexes on ${DST_TABLE} (propagates to all partitions)"
    sql_exec "CREATE INDEX IF NOT EXISTS brkm_ticker_time_idx_new ON ${DST_TABLE} (market_ticker, retrieved_at DESC);"
    sql_exec "CREATE INDEX IF NOT EXISTS brkm_station_date_idx_new ON ${DST_TABLE} (station_code, target_date, retrieved_at DESC) WHERE station_code IS NOT NULL;"
    sql_exec "CREATE INDEX IF NOT EXISTS brkm_series_date_idx_new ON ${DST_TABLE} (series_ticker, target_date) WHERE series_ticker IS NOT NULL;"

    local invalid
    invalid=$(sql_query "SELECT COUNT(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relname LIKE 'brkm_%_new%' AND i.indisvalid = false;")
    if [[ "$invalid" != "0" ]]; then
        log "ABORT: ${invalid} invalid index(es) on ${DST_TABLE}. Restarting timer, NOT swapping."
        systemctl start "$TIMER"
        exit 1
    fi

    log "atomic rename-swap"
    sql_exec "BEGIN; ALTER TABLE ${SRC_TABLE} RENAME TO ${OLD_TABLE}; ALTER TABLE ${DST_TABLE} RENAME TO ${SRC_TABLE}; COMMIT;"

    log "starting ${TIMER}"
    systemctl start "$TIMER"

    log "running one manual dry-run cycle to confirm the swapped-in table accepts writes"
    su - root -c "cd /opt/bhn/trading && python3 weather_data_collector.py --source kalshi_markets --dry-run" || log "WARNING: dry-run invocation failed, check manually"

    log "=== cutover complete. ${OLD_TABLE} retained for 7-day rollback window, not dropped. ==="
}

do_rollback() {
    require_root
    log "ROLLBACK: renaming ${SRC_TABLE} -> weather_bronze_kalshi_market_snapshots_failed, ${OLD_TABLE} -> ${SRC_TABLE}"
    systemctl stop "$TIMER"
    sql_exec "BEGIN; ALTER TABLE ${SRC_TABLE} RENAME TO weather_bronze_kalshi_market_snapshots_failed; ALTER TABLE ${OLD_TABLE} RENAME TO ${SRC_TABLE}; COMMIT;"
    systemctl start "$TIMER"
    log "rollback complete"
}

case "${1:-}" in
    precheck) do_precheck ;;
    swap)     do_swap ;;
    rollback) do_rollback ;;
    *) echo "Usage: $0 {precheck|swap|rollback}" >&2; exit 1 ;;
esac
