#!/bin/bash
# eh-backup — daily encrypted offsite backup for EventHorizon LA hub
#
# Backs up:
#   - PostgreSQL globals (roles, passwords) via pg_dumpall --globals-only
#   - PostgreSQL eventhorizon DB via pg_dump -Fc (custom format, compressed)
#   - n8n state: hot copy of /root/.n8n/database.sqlite via sqlite3 .backup
#                + tar of config/nodes/storage (excludes WAL/SHM and event logs)
#
# Storage: restic repo (AES-256 encrypted, dedup, snapshots).
# Default repo path is local cold tier; flip RESTIC_REPOSITORY in
# /root/.eh-backup.env to a sftp:user@host:path target to push offsite.
#
# Retention: keep-daily 7, keep-weekly 4, keep-monthly 6.
#
# Usage:
#   eh-backup            # full backup run (cron)
#   eh-backup check      # restic repo integrity check
#   eh-backup status     # show last 10 snapshots + repo size
#   eh-backup restore-test  # extract latest snapshot to /tmp and verify

set -euo pipefail

ENV_FILE=/root/.eh-backup.env
LOCK_FILE=/run/eh-backup.lock
LOG_FILE=/var/log/eh-backup.log
STAGING=/mnt/eh-hdd-cold/backup-staging
PG_DB=eventhorizon
N8N_DIR=/root/.n8n

log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_FILE"; }
die() { log "ERROR: $*"; exit 1; }

[[ -r "$ENV_FILE" ]] || die "$ENV_FILE missing or unreadable"
set -a; . "$ENV_FILE"; set +a
[[ -n "${RESTIC_REPOSITORY:-}" ]] || die "RESTIC_REPOSITORY unset"
[[ -n "${RESTIC_PASSWORD:-}" ]]   || die "RESTIC_PASSWORD unset"

cmd="${1:-backup}"

case "$cmd" in
  status)
    restic snapshots --compact | tail -15
    echo "---"
    restic stats --mode raw-data 2>/dev/null | grep -E "Total|Snapshot"
    exit 0
    ;;
  check)
    log "restic check starting"
    restic check --read-data-subset=10% 2>&1 | tee -a "$LOG_FILE"
    exit 0
    ;;
  restore-test)
    tmp=$(mktemp -d)
    log "restore test → $tmp"
    restic restore latest --target "$tmp" 2>&1 | tee -a "$LOG_FILE"
    log "restored files:"
    find "$tmp" -type f -printf '  %p (%s bytes)\n' | tee -a "$LOG_FILE"
    log "restore-test artifacts at $tmp (delete manually after inspection)"
    exit 0
    ;;
  backup) ;;
  *) die "unknown command: $cmd" ;;
esac

# Single-instance lock
exec 9>"$LOCK_FILE"
flock -n 9 || die "another eh-backup run is in progress"

START=$(date +%s)
log "=== eh-backup run start ==="

# Clean + recreate staging
rm -rf "$STAGING"/*
mkdir -p "$STAGING"
chmod 700 "$STAGING"

# 1. PostgreSQL globals (roles, passwords)
# cwd=/tmp so the postgres user (no /root access) doesn't warn on chdir
log "dumping PG globals"
( cd /tmp && sudo -u postgres pg_dumpall --globals-only ) > "$STAGING/pg_globals.sql"

# 2. PostgreSQL eventhorizon DB (custom format = compressed + parallel-restorable)
# Stream to stdout so root's shell creates the file (postgres user can't write to staging)
log "dumping PG database: $PG_DB"
( cd /tmp && sudo -u postgres pg_dump -Fc "$PG_DB" ) > "$STAGING/${PG_DB}.dump"

# 3. n8n SQLite hot copy (safe under WAL mode — no n8n downtime)
log "snapshotting n8n sqlite"
sqlite3 "$N8N_DIR/database.sqlite" ".backup '$STAGING/n8n-database.sqlite'"

# 4. n8n config + custom nodes + storage (exclude live WAL/SHM, event logs, .bak files)
log "archiving n8n config/nodes/storage"
tar --zstd \
    --exclude='database.sqlite' \
    --exclude='database.sqlite-shm' \
    --exclude='database.sqlite-wal' \
    --exclude='database.sqlite.bak.*' \
    --exclude='n8nEventLog*.log' \
    -cf "$STAGING/n8n-files.tar.zst" \
    -C /root .n8n

# Sanity: all artifacts present and non-empty
for f in pg_globals.sql "${PG_DB}.dump" n8n-database.sqlite n8n-files.tar.zst; do
  [[ -s "$STAGING/$f" ]] || die "staging artifact missing or empty: $f"
done

# 5. restic backup
# NOTE: no `tee -a "$LOG_FILE"` here — cron already redirects stdout/stderr
# to LOG_FILE via `>>`, and tee'ing on top duplicates every line in the log.
log "restic backup → $RESTIC_REPOSITORY"
restic backup \
  --tag daily \
  --tag "host=$(hostname)" \
  --host eh-la \
  "$STAGING" 2>&1

# 6. Retention prune
log "applying retention (keep-daily 7, keep-weekly 4, keep-monthly 6)"
restic forget \
  --keep-daily 7 \
  --keep-weekly 4 \
  --keep-monthly 6 \
  --prune \
  --tag daily 2>&1

# 7. Wipe staging (artifacts now live in encrypted restic repo)
rm -rf "$STAGING"/*

DUR=$(( $(date +%s) - START ))
log "=== eh-backup run complete in ${DUR}s ==="
