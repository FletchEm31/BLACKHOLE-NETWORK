#!/bin/bash
# BHN LA egress lockdown — deploy config files + restart affected services.
#
# Run on LA as root, from the staged copy of this directory (e.g.
# /opt/bhn-la-egress-lockdown/). Idempotent — re-run is safe.
#
# Modes:
#   (default)    deploy: copy files into place, daemon-reload, restart services
#   --uninstall  remove the deployed files, daemon-reload, restart services
#
# Files affected:
#   /etc/environment                                                       (append/remove BHN block)
#   /etc/apt/apt.conf.d/95bhn-proxy.conf                                   (copy/delete)
#   /etc/systemd/system/n8n.service.d/proxy.conf                           (copy/delete)
#   /etc/systemd/system/grafana-server.service.d/proxy.conf                (copy/delete)
#
# Restarts:  n8n, grafana-server  (only if their systemd drop-ins changed)

set -euo pipefail

SRC_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MODE="${1:-deploy}"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${CYAN}[LA-LOCKDOWN]${NC} $*"; }
ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*" >&2; }
err()  { echo -e "${RED}[✗]${NC} $*" >&2; exit "${2:-1}"; }

[[ $EUID -ne 0 ]] && err "Must run as root"

ENV_MARK_BEGIN="# === BHN egress proxy (managed by la-egress-lockdown/deploy.sh) ==="
ENV_MARK_END="# === end BHN egress proxy ==="

restart_n8n=0
restart_grafana=0

case "$MODE" in
  deploy)
    # 1. /etc/environment
    log "Updating /etc/environment"
    if grep -qF "$ENV_MARK_BEGIN" /etc/environment 2>/dev/null; then
      ok "/etc/environment already has BHN block — skipping"
    else
      {
        echo ""
        echo "$ENV_MARK_BEGIN"
        cat "$SRC_DIR/environment.snippet"
        echo "$ENV_MARK_END"
      } >> /etc/environment
      ok "Appended BHN proxy block to /etc/environment"
    fi

    # 2. apt config
    log "Installing apt proxy config"
    install -m 0644 -o root -g root "$SRC_DIR/apt.conf.d/95bhn-proxy.conf" /etc/apt/apt.conf.d/95bhn-proxy.conf
    ok "/etc/apt/apt.conf.d/95bhn-proxy.conf installed"

    # 3. n8n systemd drop-in
    log "Installing n8n systemd drop-in"
    mkdir -p /etc/systemd/system/n8n.service.d
    if ! cmp -s "$SRC_DIR/systemd/n8n.service.d/proxy.conf" /etc/systemd/system/n8n.service.d/proxy.conf 2>/dev/null; then
      install -m 0644 -o root -g root "$SRC_DIR/systemd/n8n.service.d/proxy.conf" /etc/systemd/system/n8n.service.d/proxy.conf
      ok "n8n proxy drop-in updated"
      restart_n8n=1
    else
      ok "n8n proxy drop-in already current — no restart"
    fi

    # 4. Grafana systemd drop-in
    log "Installing grafana-server systemd drop-in"
    mkdir -p /etc/systemd/system/grafana-server.service.d
    if ! cmp -s "$SRC_DIR/systemd/grafana-server.service.d/proxy.conf" /etc/systemd/system/grafana-server.service.d/proxy.conf 2>/dev/null; then
      install -m 0644 -o root -g root "$SRC_DIR/systemd/grafana-server.service.d/proxy.conf" /etc/systemd/system/grafana-server.service.d/proxy.conf
      ok "grafana-server proxy drop-in updated"
      restart_grafana=1
    else
      ok "grafana-server proxy drop-in already current — no restart"
    fi

    # 5. Reload + restart
    if [[ $restart_n8n -eq 1 || $restart_grafana -eq 1 ]]; then
      log "systemctl daemon-reload"
      systemctl daemon-reload
      [[ $restart_n8n -eq 1 ]]     && { systemctl restart n8n;            ok "n8n restarted"; }
      [[ $restart_grafana -eq 1 ]] && { systemctl restart grafana-server; ok "grafana-server restarted"; }
    fi

    log "Deploy complete. Verify with the README's deploy-order step 3 checklist."
    ;;

  --uninstall)
    log "Removing BHN proxy configuration from LA"

    # 1. /etc/environment
    if grep -qF "$ENV_MARK_BEGIN" /etc/environment 2>/dev/null; then
      # Strip the marker block (sed-D-via-temp-file pattern)
      sed -i.bak "/^${ENV_MARK_BEGIN//\//\\/}$/,/^${ENV_MARK_END//\//\\/}$/d" /etc/environment
      ok "BHN block removed from /etc/environment (backup at .bak)"
    fi

    # 2. apt
    rm -f /etc/apt/apt.conf.d/95bhn-proxy.conf && ok "apt config removed"

    # 3. systemd drop-ins
    rm -f /etc/systemd/system/n8n.service.d/proxy.conf            && restart_n8n=1
    rm -f /etc/systemd/system/grafana-server.service.d/proxy.conf && restart_grafana=1
    rmdir --ignore-fail-on-non-empty /etc/systemd/system/n8n.service.d /etc/systemd/system/grafana-server.service.d 2>/dev/null || true
    systemctl daemon-reload
    [[ $restart_n8n -eq 1 ]]     && { systemctl restart n8n;            ok "n8n restarted (no proxy)"; }
    [[ $restart_grafana -eq 1 ]] && { systemctl restart grafana-server; ok "grafana-server restarted (no proxy)"; }

    log "Uninstall complete. UFW rules are NOT touched — run ufw-rewrite.sh restore-direct-egress to revert those."
    ;;

  *)
    cat <<USAGE
Usage: $0 [--uninstall]

  (default)    deploy: copy proxy config files into place + restart n8n/grafana
  --uninstall  remove the deployed files + restart (does NOT touch UFW)
USAGE
    exit 1
    ;;
esac
