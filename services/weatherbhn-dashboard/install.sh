#!/bin/bash
# WeatherBHN Trading Dashboard — install/update script.
# Run on LA (BHN-LOSANGELES-US1) as root.
#
# Prereqs (one-time, manual):
#   1. sql/weatherbhn-dashboard-journal-schema.sql applied
#      (creates weatherbhn_dashboard role + journal table + read grants)
#   2. ALTER ROLE weatherbhn_dashboard WITH PASSWORD '...';
#      (set a real password — not committed to git)
#   3. /etc/bhn-trading/weatherbhn-dashboard.env created with:
#        DATABASE_URL=postgresql://weatherbhn_dashboard:<password>@/eventhorizon?host=/var/run/postgresql
#      (root-only readable, chmod 600)
#
set -euo pipefail

DEPLOY_DIR=/opt/bhn-weatherbhn-dashboard
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$DEPLOY_DIR"
rsync -a --delete \
    --exclude 'venv' \
    "$SRC_DIR/app/" "$DEPLOY_DIR/app/"
rsync -a --delete "$SRC_DIR/static/" "$DEPLOY_DIR/static/"
cp "$SRC_DIR/requirements.txt" "$DEPLOY_DIR/requirements.txt"

if [ ! -d "$DEPLOY_DIR/venv" ]; then
    python3 -m venv "$DEPLOY_DIR/venv"
fi
"$DEPLOY_DIR/venv/bin/pip" install --quiet --upgrade pip
"$DEPLOY_DIR/venv/bin/pip" install --quiet -r "$DEPLOY_DIR/requirements.txt"

cp "$SRC_DIR/weatherbhn-dashboard.service" /etc/systemd/system/weatherbhn-dashboard.service
systemctl daemon-reload
systemctl enable weatherbhn-dashboard.service
systemctl restart weatherbhn-dashboard.service

sleep 2
systemctl --no-pager status weatherbhn-dashboard.service
echo
echo "Verify: curl --noproxy '*' -sI http://10.8.0.1:8098/ | head -1"
echo "Verify API: curl --noproxy '*' -s 'http://10.8.0.1:8098/api/config'"
