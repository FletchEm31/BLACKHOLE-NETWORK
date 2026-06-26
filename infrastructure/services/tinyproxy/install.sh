#!/bin/bash
# BHN tinyproxy installer — run on Hillsboro after bootstrap completes
# and the WG tunnel to LA is up (i.e. `ping <BHN_WG_LA_IP>` succeeds).
#
# Installs tinyproxy from apt, drops the BHN config, restarts the service,
# and opens UFW for the BHN tunnel network only. tinyproxy MUST NOT be
# reachable from the public internet — verify Listen <BHN_WG_HIL_IP> is in the
# config before this script runs.
#
# Usage:
#   sudo bash install.sh
#
# Idempotent — safe to re-run if config changes.

set -euo pipefail

CONF_SRC="$(dirname "$(readlink -f "$0")")/tinyproxy.conf"
CONF_DST=/etc/tinyproxy/tinyproxy.conf

[[ $EUID -ne 0 ]] && { echo "Must run as root" >&2; exit 1; }
[[ -r "$CONF_SRC" ]] || { echo "Missing $CONF_SRC" >&2; exit 1; }

# Sanity check — config must bind to tunnel IP, not 0.0.0.0
grep -qE '^Listen[[:space:]]+10\.8\.0\.6$' "$CONF_SRC" \
  || { echo "Refusing to install: $CONF_SRC does not bind to <BHN_WG_HIL_IP>" >&2; exit 1; }

echo "[tinyproxy] Installing"
DEBIAN_FRONTEND=noninteractive apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tinyproxy

echo "[tinyproxy] Deploying config"
install -m 0644 -o root -g root "$CONF_SRC" "$CONF_DST"

echo "[tinyproxy] UFW: allow 8888/tcp from BHN tunnel only"
ufw allow from 10.8.0.0/24 to <BHN_WG_HIL_IP> port 8888 proto tcp comment 'tinyproxy from BHN' >/dev/null

echo "[tinyproxy] Restarting service"
systemctl enable tinyproxy >/dev/null
systemctl restart tinyproxy

echo "[tinyproxy] Verifying listen socket"
ss -lntp | grep -E ':8888\s' | grep -q '10\.8\.0\.6' \
  || { echo "tinyproxy not bound to <BHN_WG_HIL_IP>:8888 — check logs: journalctl -u tinyproxy" >&2; exit 1; }

echo "[tinyproxy] OK — listening on <BHN_WG_HIL_IP>:8888, accepting only from 10.8.0.0/24"
echo "[tinyproxy] On LA, set: export https_proxy=http://<BHN_WG_HIL_IP>:8888"
echo "[tinyproxy] Verify from LA: curl -x http://<BHN_WG_HIL_IP>:8888 https://api.ipify.org"
echo "[tinyproxy]   (should return <BHN_HIL_PUBLIC_IP> — Hillsboro's public IP)"
