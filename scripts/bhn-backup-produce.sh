#!/usr/bin/env bash
# bhn-backup-produce.sh -- stateless backup-artifact producer for the Cryptometer Vault.
#
# Lives on:    LA hub at /usr/local/sbin/bhn-backup-produce.sh   (chmod 0700, owned root)
# Invoked by:  bhn-vault-sync.ps1 on operator PC, over SSH, when the BHN-BLACKBOX
#              Cryptomator vault unlocks (mounts as E:\).
#
# Contract:
#   $1 = artifact_id  (pg-eventhorizon | n8n-workflows | bhn-repo-snapshot)
#   $2 = out_path     (e.g. /tmp/bhn-backup-<uuid>.tmp -- caller-chosen, must not exist)
#
#   Produces the artifact at $out_path.
#   Emits sha256($out_path) -- and ONLY that -- on stdout. All other output to stderr.
#   The caller (vault-sync) is responsible for scp-pulling $out_path and rm'ing it.
#   Server retains NO plaintext-at-rest -- the tmpfile lives only between produce and pull.
#
# Design intent -- see infrastructure/docs/audit/org-structure-thoughts/vault-backup-design-2026-05-22.md
#
# Exit codes:
#   0  success (sha256 on stdout)
#   1  generic failure (see stderr)
#   2  unknown artifact ID / missing args
#   3  output path already exists (refusing to clobber)
#   4  insufficient disk space
#   5  required tool not found on PATH
#   6  source (DB / repo / n8n) missing or unhealthy

set -euo pipefail

ARTIFACT="${1:-}"
OUT="${2:-}"

# === Configuration (override via env) ===
PG_DB="${BHN_PG_DB:-eventhorizon}"
BHN_REPO_PATH="${BHN_REPO_PATH:-/opt/bhn-repo}"       # only used for bhn-repo-snapshot
MIN_FREE_MB="${BHN_BACKUP_MIN_FREE_MB:-500}"
N8N_USER="${BHN_N8N_USER:-n8n}"                       # only used for native n8n
N8N_CONTAINER="${BHN_N8N_CONTAINER:-n8n}"             # docker container name; auto-detected

# === Helpers ===
err() {
  local msg="$1" code="${2:-1}"
  echo "[bhn-backup-produce] ERROR: $msg" >&2
  exit "$code"
}

need() {
  command -v "$1" >/dev/null 2>&1 || err "required tool not on PATH: $1" 5
}

usage() {
  cat >&2 <<EOF
Usage: $0 <artifact_id> <out_path>

Artifact IDs:
  pg-eventhorizon      pg_dump -Fc | zstd  ->  out_path  (.dump.zst)
  n8n-workflows        n8n export:workflow -> tar.zst ->  out_path  (.tar.zst)
  bhn-repo-snapshot    git bundle --all    ->  out_path  (.bundle)
  matrix-synapse       tar zstd of homeserver.db + media/ -> out_path (.tar.zst)

Environment overrides:
  BHN_PG_DB              (default: eventhorizon)
  BHN_REPO_PATH          (default: /opt/bhn-repo)
  BHN_N8N_USER           (default: n8n)         -- native CLI path only
  BHN_N8N_CONTAINER      (default: n8n)         -- docker container name; preferred if docker present
  BHN_BACKUP_MIN_FREE_MB (default: 500)
EOF
  exit 2
}

[ -z "$ARTIFACT" ] && usage
[ -z "$OUT" ] && usage

# Refuse to clobber -- caller picks a fresh path
[ -e "$OUT" ] && err "output path already exists: $OUT" 3

# Disk space check on the directory that will hold $OUT
OUT_DIR="$(dirname "$OUT")"
[ -d "$OUT_DIR" ] || err "output directory does not exist: $OUT_DIR" 6
FREE_MB=$(df -P -BM "$OUT_DIR" | awk 'NR==2 {sub("M","",$4); print $4+0}')
if [ "$FREE_MB" -lt "$MIN_FREE_MB" ]; then
  err "insufficient free space on $OUT_DIR (${FREE_MB}MB < required ${MIN_FREE_MB}MB)" 4
fi

# Clean up partial output on any error
trap 'rm -f "$OUT"' ERR

case "$ARTIFACT" in
  pg-eventhorizon)
    need pg_dump
    need zstd
    echo "[bhn-backup-produce] producing pg-eventhorizon (db=$PG_DB) -> $OUT" >&2
    sudo -u postgres pg_dump -Fc "$PG_DB" | zstd -T0 -19 -q > "$OUT" \
      || err "pg_dump | zstd failed (db=$PG_DB)" 6
    ;;

  n8n-workflows)
    need zstd
    need tar
    EXPORT_DIR=$(mktemp -d -t bhn-n8n-export.XXXXXX)
    # Override the cleanup trap to also nuke the tmpdir
    trap 'rm -rf "$EXPORT_DIR"; rm -f "$OUT"' ERR

    # Detect Docker n8n container first; fall back to native /usr/bin/n8n.
    # In practice on LA the n8n service is a systemd-managed Docker container
    # (image n8nio/n8n:latest), so the host's native /usr/bin/n8n CLI cannot
    # see the live workflow DB -- it would export an empty set.
    if command -v docker >/dev/null 2>&1 && docker inspect "$N8N_CONTAINER" >/dev/null 2>&1; then
      echo "[bhn-backup-produce] producing n8n-workflows (docker container=$N8N_CONTAINER) -> $OUT" >&2
      docker exec "$N8N_CONTAINER" n8n export:workflow --all --output=/tmp/bhn-workflows.json >&2 \
        || err "docker exec $N8N_CONTAINER n8n export:workflow failed" 6
      docker cp "$N8N_CONTAINER:/tmp/bhn-workflows.json" "$EXPORT_DIR/workflows.json" \
        || err "docker cp from $N8N_CONTAINER failed" 1
      docker exec "$N8N_CONTAINER" rm -f /tmp/bhn-workflows.json 2>/dev/null || true
    elif command -v n8n >/dev/null 2>&1; then
      echo "[bhn-backup-produce] producing n8n-workflows (native user=$N8N_USER) -> $OUT" >&2
      sudo -u "$N8N_USER" n8n export:workflow --all --output="$EXPORT_DIR/workflows.json" >&2 \
        || err "native n8n export:workflow failed" 6
    else
      err "n8n not available -- neither docker container '$N8N_CONTAINER' nor native n8n CLI" 5
    fi

    tar -C "$EXPORT_DIR" -cf - workflows.json | zstd -T0 -19 -q > "$OUT" \
      || err "tar | zstd failed" 1
    rm -rf "$EXPORT_DIR"
    # Restore the simpler cleanup trap
    trap 'rm -f "$OUT"' ERR
    ;;

  bhn-repo-snapshot)
    need git
    [ -d "$BHN_REPO_PATH/.git" ] \
      || err "bhn repo not found at $BHN_REPO_PATH (set BHN_REPO_PATH env to override)" 6
    echo "[bhn-backup-produce] producing bhn-repo-snapshot ($BHN_REPO_PATH) -> $OUT" >&2
    git -C "$BHN_REPO_PATH" bundle create "$OUT" --all >&2 \
      || err "git bundle failed" 1
    ;;

  matrix-synapse)
    need tar
    need zstd
    MATRIX_DB="/mnt/eh-nvme-hot/matrix-synapse/homeserver.db"
    MATRIX_MEDIA="/mnt/eh-nvme-hot/matrix-synapse/media"
    [ -f "$MATRIX_DB" ]    || err "matrix-synapse DB not found: $MATRIX_DB" 6
    [ -d "$MATRIX_MEDIA" ] || err "matrix-synapse media dir not found: $MATRIX_MEDIA" 6
    echo "[bhn-backup-produce] producing matrix-synapse (db + media) -> $OUT" >&2
    tar -C /mnt/eh-nvme-hot/matrix-synapse \
        --exclude='./media/url_cache' \
        -cf - homeserver.db media \
      | zstd -T0 -19 -q > "$OUT" \
      || err "tar | zstd failed for matrix-synapse" 1
    ;;

  *)
    err "unknown artifact: $ARTIFACT" 2
    ;;
esac

# Sanity: artifact must exist and be non-empty
[ -s "$OUT" ] || err "produced artifact is empty or missing: $OUT" 1

# Emit sha256 (this -- and ONLY this -- is what the client parses from stdout)
sha256sum "$OUT" | awk '{print $1}'
