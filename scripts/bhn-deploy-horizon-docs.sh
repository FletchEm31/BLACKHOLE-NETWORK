#!/bin/bash
# bhn-deploy-horizon-docs.sh
#
# Sync HORIZON-safe docs (infrastructure/docs/BHN HORIZON/ and BHN TEMPLATES/)
# into /opt/bhn/horizon-docs/ on LA. HORIZON reads from horizon-docs/ ONLY —
# never has access to the parent infrastructure/docs/ directory.
#
# Idempotent: safe to re-run after `git pull`. Uses rsync --delete so files
# removed from the repo also disappear from horizon-docs/.
#
# Run from LA. Assumes the BLACKHOLE-NETWORK repo is checked out at /opt/bhn
# (i.e. /opt/bhn/.git exists). If not, clone it first:
#   git clone https://github.com/FletchEm31/BLACKHOLE-NETWORK.git /opt/bhn
#
# Suggested wiring (operator):
#   - cron / systemd timer: every 30 min, runs this script
#   - or call manually after any infrastructure/docs/ change
#
# Exit codes:
#   0  success
#   1  repo not present at /opt/bhn or git pull failed
#   2  rsync failed

set -euo pipefail

REPO=/opt/bhn
DEST=/opt/bhn/horizon-docs

if [ ! -d "$REPO/.git" ]; then
    echo "ERROR: $REPO is not a git repo. Clone first:" >&2
    echo "  git clone https://github.com/FletchEm31/BLACKHOLE-NETWORK.git $REPO" >&2
    exit 1
fi

echo "=== git pull (--ff-only to refuse divergent histories) ==="
cd "$REPO"
git pull --ff-only

mkdir -p "$DEST/templates"

echo "=== rsync BHN HORIZON/ -> $DEST/ ==="
rsync -av --delete \
    "$REPO/infrastructure/docs/BHN HORIZON/" \
    "$DEST/" || { echo "ERROR: rsync BHN HORIZON failed" >&2; exit 2; }

echo "=== rsync BHN TEMPLATES/ -> $DEST/templates/ ==="
rsync -av --delete \
    "$REPO/infrastructure/docs/BHN TEMPLATES/" \
    "$DEST/templates/" || { echo "ERROR: rsync BHN TEMPLATES failed" >&2; exit 2; }

# Restrictive perms so only the deploy user + HORIZON role group can read.
# Operator may need to chgrp horizon-docs to the agent_reader group depending
# on how HORIZON is run (n8n service user, etc.).
chmod -R 0750 "$DEST" 2>/dev/null || true

echo "=== sync complete ==="
echo "Files in $DEST:"
find "$DEST" -type f | sort
