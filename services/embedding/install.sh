#!/bin/bash
# Idempotent installer for the BHN local embedding service.
# Sets up /opt/eh-embed with a Python venv, installs fastembed + fastapi + uvicorn,
# writes the systemd unit, enables + starts it, and waits for /health.
#
# NOTE: LA-side install paths (/opt/eh-embed, systemd unit name `eh-embed`) keep
# the eh- prefix until coordinated LA migration. Repo-side source filename is
# bhn-embed.service. See project_blackhole_network_rename memory.
#
# Usage:
#   bash install.sh
#
# Pre-reqs: Ubuntu 22.04, Python 3.10+, root or sudo.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[eh-embed] installing prereqs..."
apt update -qq
apt install -y python3-venv python3-pip -qq

echo "[eh-embed] creating /opt/eh-embed..."
mkdir -p /opt/eh-embed/cache
cp "${SCRIPT_DIR}/service.py" /opt/eh-embed/service.py

echo "[eh-embed] creating venv + installing deps..."
python3 -m venv /opt/eh-embed/venv
/opt/eh-embed/venv/bin/pip install --quiet --upgrade pip
/opt/eh-embed/venv/bin/pip install --quiet fastembed fastapi 'uvicorn[standard]'

echo "[eh-embed] installing systemd unit..."
# Source filename in repo: bhn-embed.service (renamed 2026-05-11)
# Target on LA: /etc/systemd/system/eh-embed.service (LA-side name kept until migration)
cp "${SCRIPT_DIR}/bhn-embed.service" /etc/systemd/system/eh-embed.service
systemctl daemon-reload
systemctl enable --now eh-embed

echo "[eh-embed] waiting for /health (model load takes 5-30s on first run)..."
for i in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:8001/health >/dev/null 2>&1; then
        echo "[eh-embed] ready after ${i}s"
        curl -fsS http://127.0.0.1:8001/health
        echo
        exit 0
    fi
    sleep 2
done

echo "[eh-embed] ERROR: service did not come up within 120s"
journalctl -u eh-embed --no-pager -n 30
exit 1
