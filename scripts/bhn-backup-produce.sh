#!/usr/bin/env bash
# bhn-backup-produce.sh — stateless backup-artifact producer for the Cryptometer Vault.
#
# Lives on:    LA hub at /usr/local/sbin/bhn-backup-produce.sh   (chmod 0700, owned root)
# Invoked by:  bhn-vault-sync.ps1 on operator PC, over SSH, when the BHN-BLACKBOX
#              Cryptomator vault unlocks (mounts as E:\).
#
# Contract:
#   $1 = artifact_id  (pg-eventhorizon | n8n-workflows | bhn-repo-snapshot)
#   $2 = out_path     (e.g. /tmp/bhn-backup-<uuid>.tmp — caller-chosen, must not exist)
#
#   Produces the artifact at $out_path.
#   Emits sha256($out_path) — and ONLY that — on stdout. All other output to stderr.
#   The caller (vault-sync) is responsible for scp-pulling $out_path and rm'ing it.
#   Server retains NO plaintext-at-rest — the tmpfile lives only between produce and pull.
#
# Design intent — see infrastructure/docs/audit/org-structure-thoughts/vault-backup-design-2026-05-22.md
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
BHN_REPO_PATH="${BHN_REPO_PATH:-/opt/bhn-repo}"     # only used for bhn-repo-snapshot
MIN_FREE_MB="${BHN_BACKUP_MIN_FREE_MB:-500}"
N8N_USER="${BHN_N8N_USER:-n8n}"                     # only used for n8n-workflows

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
  pg-eventhorizon      pg_dump -Fc | zstd  →  out_path  (.dump.zst)
  n8n-workflows        n8n export:workflow → tar.zst →  out_path  (.tar.zst)
  bhn-repo-snapshot    git bundle --all    →  out_path  (.bundle)

Environment overrides:
  BHN_PG_DB              (default: eventhorizon)
  BHN_REPO_PATH          (default: /opt/bhn-repo)
  BHN_N8N_USER           (default: n8n)
  BHN_BACKUP_MIN_FREE_MB (default: 500)
EOF
  exit 2
}

[ -z "$ARTIFACT" ] && usage
[ -z "$OUT" ] && usage

# Refuse to clobber — caller picks a fresh path
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
    echo "[bhn-backup-produce] producing pg-eventhorizon (db=$PG_DB) → $OUT" >&2
    sudo -u postgres pg_dump -Fc "$PG_DB" | zstd -T0 -19 -q > "$OUT" \
      || err "pg_dump | zstd failed (db=$PG_DB)" 6
    ;;

  n8n-workflows)
    need zstd
    need tar
    if ! command -v n8n >/dev/null 2>&1; then
      err "n8n CLI not on PATH — if n8n is dockerized, edit this case to docker exec into the container" 5
    fi
    EXPORT_DIR=$(mktemp -d -t bhn-n8n-export.XXXXXX)
    # Override the cleanup trap to also nuke the tmpdir
    trap 'rm -rf "$EXPORT_DIR"; rm -f "$OUT"' ERR
    echo "[bhn-backup-produce] producing n8n-workflows (user=$N8N_USER) → $OUT" >&2
    sudo -u "$N8N_USER" n8n export:workflow --all --output="$EXPORT_DIR/workflows.json" >&2 \
      || err "n8n export:workflow failed" 6
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
    echo "[bhn-backup-produce] producing bhn-repo-snapshot ($BHN_REPO_PATH) → $OUT" >&2
    git -C "$BHN_REPO_PATH" bundle create "$OUT" --all >&2 \
      || err "git bundle failed" 1
    ;;

  *)
    err "unknown artifact: $ARTIFACT" 2
    ;;
esac

# Sanity: artifact must exist and be non-empty
[ -s "$OUT" ] || err "produced artifact is empty or missing: $OUT" 1

# Emit sha256 (this — and ONLY this — is what the client parses from stdout)
sha256sum "$OUT" | awk '{print $1}'
