#!/bin/bash
# infrastructure/bootstrap/modules/backup.sh
#
# Sourced by eh-node-bootstrap.sh. Provides:
#   setup_backup_pipeline
#
# Hub-only. Installs restic + the eh-backup orchestrator + cron + logrotate.
# Backup target defaults to /mnt/eh-hdd-cold/backup-restic on the cold tier.
# Operator can later swap RESTIC_REPOSITORY in /root/.eh-backup.env to an
# offsite SFTP/B2/S3 backend (procedure documented in BACKUP.md).
#
# The repo password is generated here and printed once at the end of the
# bootstrap summary — operator stores in password manager. Without the
# password, encrypted snapshots are unrecoverable on host loss.

setup_backup_pipeline() {
  log "Setting up backup pipeline (restic + eh-backup)"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq restic sqlite3

  mkdir -p /mnt/eh-hdd-cold/backup-restic /mnt/eh-hdd-cold/backup-staging
  chmod 700 /mnt/eh-hdd-cold/backup-restic /mnt/eh-hdd-cold/backup-staging

  # Generate repo password if not already set
  if [[ ! -f /root/.eh-backup.env ]]; then
    local pw
    pw="$(openssl rand -base64 36 | tr -d "\n=" | cut -c1-44)"
    umask 077
    cat >/root/.eh-backup.env <<EOF
# EH offsite backup config — managed by eh-backup script
# DO NOT COMMIT. Mode 0600 root:root.
RESTIC_REPOSITORY=/mnt/eh-hdd-cold/backup-restic
RESTIC_PASSWORD=${pw}
EOF
    chmod 600 /root/.eh-backup.env

    # Surface for the bootstrap summary
    EH_BACKUP_PASSWORD="$pw"
    export EH_BACKUP_PASSWORD
  fi

  set -a
  # shellcheck disable=SC1091
  source /root/.eh-backup.env
  set +a

  # Initialize repo if empty (restic init is a no-op if it already exists,
  # but errors loudly — guard with a probe)
  if ! restic snapshots --no-cache >/dev/null 2>&1; then
    restic init >/dev/null
    ok "restic repo initialized at $RESTIC_REPOSITORY"
  else
    ok "restic repo already initialized at $RESTIC_REPOSITORY"
  fi

  # Deploy eh-backup orchestrator. Source-of-truth lives in repo at
  # scripts/eh-backup.sh; bootstrap embeds a self-contained copy so a fresh
  # node doesn't need the repo cloned.
  cat >/usr/local/sbin/eh-backup <<'BACKUP_SCRIPT'
#!/bin/bash
# eh-backup — daily encrypted offsite backup for EventHorizon hub
# Source of truth: <repo>/scripts/eh-backup.sh
set -euo pipefail
ENV_FILE=/root/.eh-backup.env
LOCK_FILE=/run/eh-backup.lock
LOG_FILE=/var/log/eh-backup.log
STAGING=/mnt/eh-hdd-cold/backup-staging
PG_DB=eventhorizon
N8N_DIR=/root/.n8n
log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_FILE"; }
die() { log "ERROR: $*"; exit 1; }
[[ -r "$ENV_FILE" ]] || die "$ENV_FILE missing"
set -a; . "$ENV_FILE"; set +a
cmd="${1:-backup}"
case "$cmd" in
  status)        restic snapshots --compact | tail -15; restic stats --mode raw-data 2>/dev/null | grep -E "Total|Snapshot"; exit 0 ;;
  check)         restic check --read-data-subset=10% 2>&1 | tee -a "$LOG_FILE"; exit 0 ;;
  restore-test)  tmp=$(mktemp -d); restic restore latest --target "$tmp" 2>&1 | tee -a "$LOG_FILE"; find "$tmp" -type f -printf '  %p (%s bytes)\n' | tee -a "$LOG_FILE"; log "artifacts at $tmp"; exit 0 ;;
  backup)        ;;
  *)             die "unknown command: $cmd" ;;
esac
exec 9>"$LOCK_FILE"; flock -n 9 || die "another run in progress"
START=$(date +%s); log "=== eh-backup run start ==="
rm -rf "$STAGING"/*; mkdir -p "$STAGING"; chmod 700 "$STAGING"
log "dumping PG globals"
( cd /tmp && sudo -u postgres pg_dumpall --globals-only ) > "$STAGING/pg_globals.sql"
log "dumping PG database: $PG_DB"
( cd /tmp && sudo -u postgres pg_dump -Fc "$PG_DB" ) > "$STAGING/${PG_DB}.dump"
if [[ -f "$N8N_DIR/database.sqlite" ]]; then
  log "snapshotting n8n sqlite"
  sqlite3 "$N8N_DIR/database.sqlite" ".backup '$STAGING/n8n-database.sqlite'"
  tar --zstd \
      --exclude='database.sqlite' --exclude='database.sqlite-shm' \
      --exclude='database.sqlite-wal' --exclude='database.sqlite.bak.*' \
      --exclude='n8nEventLog*.log' \
      -cf "$STAGING/n8n-files.tar.zst" -C /root .n8n
fi
log "restic backup → $RESTIC_REPOSITORY"
restic backup --tag daily --tag "host=$(hostname)" --host eh-hub "$STAGING" 2>&1 | tee -a "$LOG_FILE"
log "applying retention"
restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune --tag daily 2>&1 | tee -a "$LOG_FILE"
rm -rf "$STAGING"/*
log "=== complete in $(($(date +%s) - START))s ==="
BACKUP_SCRIPT
  chmod 750 /usr/local/sbin/eh-backup

  cat >/etc/cron.d/eh-backup <<'EOF'
# /etc/cron.d/eh-backup — managed by eh-node-bootstrap v4
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
30 2 * * * root /usr/local/sbin/eh-backup backup >> /var/log/eh-backup.log 2>&1
30 3 * * 0 root /usr/local/sbin/eh-backup check  >> /var/log/eh-backup.log 2>&1
EOF
  chmod 644 /etc/cron.d/eh-backup

  cat >/etc/logrotate.d/eh-backup <<'EOF'
/var/log/eh-backup.log {
    weekly
    rotate 8
    compress
    delaycompress
    missingok
    notifempty
    create 0640 root adm
}
EOF

  ok "Backup pipeline installed (cron 02:30 UTC daily, weekly check Sun 03:30 UTC)"
}
