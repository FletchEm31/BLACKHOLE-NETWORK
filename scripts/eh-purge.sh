#!/bin/bash
# ╔══════════════════════════════════════════════════════════╗
# ║          EVENTHORIZON — PURGE / ARCHIVE SCRIPT           ║
# ║   Archives /mnt/eh-nvme-hot → /mnt/eh-hdd-cold,          ║
# ║   pg_dumps eventhorizon, VACUUMs PostgreSQL,             ║
# ║   logs every run to the purge_log table.                 ║
# ╚══════════════════════════════════════════════════════════╝
#
# Usage:
#   eh-purge                  Manual run (asks for confirmation)
#   eh-purge --yes            Manual run, no confirmation
#   eh-purge --auto           Cron entrypoint (48h schedule)
#   eh-purge --check-capacity Capacity safety net (runs every 15min;
#                             triggers a purge only if NVMe ≥ 80%)
#   eh-purge --install        One-time setup: copy to /usr/local/sbin,
#                             create purge_log table, write /etc/cron.d/eh-purge
#   eh-purge --status         Show last 10 purge_log rows
#   eh-purge --help           This text
#
# Behavior:
#   pcap, logs:  files older than 48h are tar+gzipped to the cold tier,
#                then the originals are deleted. Live PG data dir at
#                /mnt/eh-nvme-hot/postgres is NEVER touched directly.
#   postgres:    pg_dump of `eventhorizon` → cold tier as .sql.gz
#   vacuum:      VACUUM ANALYZE on `eventhorizon` after a successful dump
#
# Author convention: matches eh-node-bootstrap.sh style.

set -u  # treat unset vars as errors; we handle errors explicitly via trap

# ─── Constants ─────────────────────────────────────────────────
HOT_TIER="/mnt/eh-nvme-hot"
COLD_TIER="/mnt/eh-hdd-cold"
COLD_PCAP="${COLD_TIER}/archives/pcap"
COLD_LOGS="${COLD_TIER}/archives/logs"
COLD_PG="${COLD_TIER}/archives/postgres"

PG_DB="eventhorizon"
PG_USER="postgres"

CAPACITY_THRESHOLD=80         # %
ARCHIVE_AGE_DAYS=2            # find -mtime +2 (i.e. older than 48h)

LOCKFILE="/var/lock/eh-purge.lock"
LOGFILE="/var/log/eh-purge.log"
INSTALL_PATH="/usr/local/sbin/eh-purge"
CRON_PATH="/etc/cron.d/eh-purge"

# ─── Colors / logging ──────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
ts()   { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
log()  { echo -e "$(ts) ${CYAN}[EH-PURGE]${NC} $1"; }
ok()   { echo -e "$(ts) ${GREEN}[✓]${NC} $1"; }
warn() { echo -e "$(ts) ${YELLOW}[!]${NC} $1"; }
fail() { echo -e "$(ts) ${RED}[✗]${NC} $1" >&2; }

# ─── State (populated during a purge run) ──────────────────────
PURGE_ID=""
TRIGGER=""
PCAP_FILES=0; PCAP_BYTES=0
LOGS_FILES=0; LOGS_BYTES=0
PG_DUMP_BYTES=0
VACUUM_OK=false
NVME_PCT_BEFORE=0
NVME_PCT_AFTER=0
ERROR_MSG=""

# ─── Helpers ───────────────────────────────────────────────────

require_root() {
    [[ $EUID -eq 0 ]] || { fail "Must run as root"; exit 1; }
}

nvme_pct() {
    # Used % for the NVMe hot tier mountpoint, no trailing % sign.
    df --output=pcent "$HOT_TIER" 2>/dev/null | tail -1 | tr -d ' %' || echo 0
}

sql_exec() {
    # Runs a single statement; suppresses the psql banner.
    sudo -u "$PG_USER" psql -d "$PG_DB" -v ON_ERROR_STOP=1 -qAtX -c "$1"
}

sql_query() {
    # Returns a single scalar (one row, one column).
    sudo -u "$PG_USER" psql -d "$PG_DB" -v ON_ERROR_STOP=1 -qAtX -c "$1"
}

pg_quote() {
    # Escape single quotes for embedding into a SQL string literal.
    printf %s "$1" | sed "s/'/''/g"
}

# ─── Archive helper ────────────────────────────────────────────
# Archives files in $src older than ARCHIVE_AGE_DAYS into $dst_dir as a
# single .tar.gz, verifies the archive, then deletes the originals.
# Sets $count_var and $bytes_var on success.
archive_tier() {
    local src=$1
    local label=$2
    local dst_dir=$3
    declare -n _count_ref=$4
    declare -n _bytes_ref=$5

    if [[ ! -d "$src" ]]; then
        warn "$src does not exist; skipping $label"
        return 0
    fi

    mkdir -p "$dst_dir"

    local filelist
    filelist=$(mktemp)

    # Build the list of stale files. -mtime +N means modified > N*24h ago.
    find "$src" -type f -mtime "+${ARCHIVE_AGE_DAYS}" -print > "$filelist" 2>/dev/null || true

    if [[ ! -s "$filelist" ]]; then
        log "No $label files older than ${ARCHIVE_AGE_DAYS}d"
        rm -f "$filelist"
        return 0
    fi

    local count bytes
    count=$(wc -l < "$filelist")
    bytes=$(xargs -a "$filelist" -d '\n' du -bc 2>/dev/null | tail -1 | awk '{print $1}')
    bytes=${bytes:-0}

    local stamp archive
    stamp=$(date -u +%Y-%m-%d_%H-%M-%S)
    archive="${dst_dir}/${label}_${stamp}.tar.gz"

    log "Archiving ${count} ${label} files (${bytes} bytes) → ${archive}"

    if ! tar -czf "$archive" --files-from="$filelist" 2>>"$LOGFILE"; then
        fail "tar failed for $label"
        rm -f "$archive" "$filelist"
        return 1
    fi

    # Verify the archive is readable end-to-end before deleting the originals.
    if ! tar -tzf "$archive" >/dev/null 2>&1; then
        fail "archive verification failed: $archive"
        rm -f "$archive" "$filelist"
        return 1
    fi

    xargs -a "$filelist" -d '\n' rm -f
    rm -f "$filelist"

    _count_ref=$count
    _bytes_ref=$bytes
    ok "Archived ${label}: ${count} files, ${bytes} bytes"
    return 0
}

# ─── PostgreSQL dump + vacuum ──────────────────────────────────
pg_dump_and_vacuum() {
    mkdir -p "$COLD_PG"
    local stamp dump
    stamp=$(date -u +%Y-%m-%d_%H-%M-%S)
    dump="${COLD_PG}/${PG_DB}_${stamp}.sql.gz"

    log "pg_dump $PG_DB → $dump"
    if ! sudo -u "$PG_USER" pg_dump --format=plain "$PG_DB" 2>>"$LOGFILE" | gzip -c > "$dump"; then
        fail "pg_dump failed"
        rm -f "$dump"
        return 1
    fi

    PG_DUMP_BYTES=$(stat -c%s "$dump" 2>/dev/null || echo 0)
    ok "pg_dump complete (${PG_DUMP_BYTES} bytes)"

    log "VACUUM ANALYZE on $PG_DB"
    if sql_exec "VACUUM (ANALYZE);" >/dev/null 2>>"$LOGFILE"; then
        VACUUM_OK=true
        ok "VACUUM ANALYZE complete"
    else
        fail "VACUUM failed"
        return 1
    fi

    return 0
}

# ─── purge_log row lifecycle ───────────────────────────────────
purge_log_open() {
    local trig=$1
    PURGE_ID=$(sql_query "INSERT INTO purge_log (trigger, nvme_pct_before, status)
                          VALUES ('${trig}', ${NVME_PCT_BEFORE}, 'running')
                          RETURNING id;") || PURGE_ID=""
    if [[ -n "$PURGE_ID" ]]; then
        log "purge_log row id=${PURGE_ID} opened (trigger=${trig})"
    else
        warn "Could not open purge_log row — continuing without DB tracking"
    fi
}

purge_log_close() {
    local status=$1
    [[ -z "$PURGE_ID" ]] && return 0
    local err_sql="NULL"
    if [[ -n "$ERROR_MSG" ]]; then
        err_sql="'$(pg_quote "$ERROR_MSG")'"
    fi
    sql_exec "UPDATE purge_log SET
                finished_at      = NOW(),
                nvme_pct_after   = ${NVME_PCT_AFTER},
                pcap_files       = ${PCAP_FILES},
                pcap_bytes       = ${PCAP_BYTES},
                logs_files       = ${LOGS_FILES},
                logs_bytes       = ${LOGS_BYTES},
                pg_dump_bytes    = ${PG_DUMP_BYTES},
                vacuum_ok        = ${VACUUM_OK},
                status           = '${status}',
                error            = ${err_sql}
              WHERE id = ${PURGE_ID};" >/dev/null 2>>"$LOGFILE" || \
        warn "Failed to close purge_log row id=${PURGE_ID}"
}

# ─── Main purge ────────────────────────────────────────────────
do_purge() {
    TRIGGER=$1

    require_root

    # Single-flight guard so the 48h cron and the 15min safety-net cron
    # can't stomp on each other.
    exec 200>"$LOCKFILE"
    if ! flock -n 200; then
        warn "Another eh-purge is already running; exiting"
        exit 0
    fi

    log "═══ eh-purge start (trigger=${TRIGGER}) ═══"
    NVME_PCT_BEFORE=$(nvme_pct)
    log "NVMe usage before: ${NVME_PCT_BEFORE}%"

    purge_log_open "$TRIGGER"

    local failed=false

    if ! archive_tier "${HOT_TIER}/pcap" "pcap" "$COLD_PCAP" PCAP_FILES PCAP_BYTES; then
        ERROR_MSG="${ERROR_MSG}pcap archive failed; "
        failed=true
    fi

    if ! archive_tier "${HOT_TIER}/logs" "logs" "$COLD_LOGS" LOGS_FILES LOGS_BYTES; then
        ERROR_MSG="${ERROR_MSG}logs archive failed; "
        failed=true
    fi

    if ! pg_dump_and_vacuum; then
        ERROR_MSG="${ERROR_MSG}postgres dump/vacuum failed; "
        failed=true
    fi

    NVME_PCT_AFTER=$(nvme_pct)
    log "NVMe usage after:  ${NVME_PCT_AFTER}%"

    if $failed; then
        purge_log_close "failed"
        fail "═══ eh-purge finished with errors ═══"
        exit 1
    else
        purge_log_close "success"
        ok "═══ eh-purge complete ═══"
    fi
}

# ─── Capacity check ────────────────────────────────────────────
do_check_capacity() {
    require_root
    local pct
    pct=$(nvme_pct)
    if [[ "$pct" -ge "$CAPACITY_THRESHOLD" ]]; then
        warn "NVMe at ${pct}% (≥ ${CAPACITY_THRESHOLD}%) — triggering capacity purge"
        do_purge "capacity"
    fi
    # Below threshold: silent exit so we don't spam the log every 15 min.
}

# ─── Status ────────────────────────────────────────────────────
do_status() {
    sudo -u "$PG_USER" psql -d "$PG_DB" -c "
        SELECT id, started_at, trigger, status,
               nvme_pct_before AS bef, nvme_pct_after AS aft,
               pcap_files AS pcap, logs_files AS logs,
               pg_size_pretty(pg_dump_bytes) AS dump,
               vacuum_ok AS vac
        FROM purge_log
        ORDER BY started_at DESC
        LIMIT 10;"
}

# ─── Install ───────────────────────────────────────────────────
do_install() {
    require_root

    # 1. Copy this script to /usr/local/sbin/eh-purge
    local self
    self=$(readlink -f "$0")
    if [[ "$self" != "$INSTALL_PATH" ]]; then
        log "Installing to $INSTALL_PATH"
        cp "$self" "$INSTALL_PATH"
        chmod +x "$INSTALL_PATH"
        ok "Installed"
    else
        ok "Already at $INSTALL_PATH"
    fi

    # 2. Ensure the cold-tier subdirs exist
    mkdir -p "$COLD_PCAP" "$COLD_LOGS" "$COLD_PG"
    ok "Cold-tier directories ensured"

    # 3. Ensure the log file exists with sane perms
    touch "$LOGFILE"
    chmod 640 "$LOGFILE"

    # 4. Create the purge_log table
    log "Creating purge_log table in '$PG_DB' (idempotent)"
    sql_exec "
        CREATE TABLE IF NOT EXISTS purge_log (
            id              BIGSERIAL PRIMARY KEY,
            started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at     TIMESTAMPTZ,
            trigger         TEXT NOT NULL CHECK (trigger IN ('cron','manual','capacity')),
            nvme_pct_before INT,
            nvme_pct_after  INT,
            pcap_files      INT    DEFAULT 0,
            pcap_bytes      BIGINT DEFAULT 0,
            logs_files      INT    DEFAULT 0,
            logs_bytes      BIGINT DEFAULT 0,
            pg_dump_bytes   BIGINT DEFAULT 0,
            vacuum_ok       BOOLEAN DEFAULT FALSE,
            status          TEXT NOT NULL DEFAULT 'running'
                            CHECK (status IN ('running','success','failed')),
            error           TEXT
        );
        CREATE INDEX IF NOT EXISTS purge_log_started_at_idx
            ON purge_log (started_at DESC);
    " >/dev/null
    ok "purge_log table ready"

    # 5. Write the cron file
    log "Writing $CRON_PATH"
    cat > "$CRON_PATH" << EOF
# /etc/cron.d/eh-purge — managed by 'eh-purge --install'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin

# Full purge every 48 hours at 03:00 UTC
0 3 */2 * * root ${INSTALL_PATH} --auto >> ${LOGFILE} 2>&1

# 80% capacity safety net, every 15 minutes
*/15 * * * * root ${INSTALL_PATH} --check-capacity >> ${LOGFILE} 2>&1
EOF
    chmod 644 "$CRON_PATH"
    ok "Cron schedule installed"

    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║  eh-purge installed.                                     ║"
    echo "║                                                          ║"
    echo "║  Manual run:        eh-purge                             ║"
    echo "║  Tail log:          tail -f ${LOGFILE}"
    echo "║  Last 10 runs:      eh-purge --status                    ║"
    echo "║  Schedule:          ${CRON_PATH}"
    echo "╚══════════════════════════════════════════════════════════╝"
}

# ─── Manual confirmation ───────────────────────────────────────
do_manual() {
    local pct
    pct=$(nvme_pct)
    echo "About to run eh-purge manually."
    echo "  Hot tier:  ${HOT_TIER} (${pct}% used)"
    echo "  Cold tier: ${COLD_TIER}"
    echo "  Files older than ${ARCHIVE_AGE_DAYS}d in pcap/ and logs/ will be archived and deleted."
    echo "  pg_dump of '${PG_DB}' will be written, then VACUUM ANALYZE."
    read -r -p "Proceed? [y/N] " ans
    case "$ans" in
        y|Y|yes|YES) do_purge "manual" ;;
        *) echo "Aborted."; exit 0 ;;
    esac
}

usage() {
    sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
}

# ─── Dispatch ──────────────────────────────────────────────────
case "${1:-}" in
    --auto)            do_purge "cron" ;;
    --check-capacity)  do_check_capacity ;;
    --install)         do_install ;;
    --status)          do_status ;;
    --yes|-y)          do_purge "manual" ;;
    --help|-h)         usage ;;
    "")                do_manual ;;
    *)                 fail "Unknown option: $1"; usage; exit 1 ;;
esac
