#!/bin/bash
# ╔══════════════════════════════════════════════════════════╗
# ║    BLACKHOLE NETWORK (BHN) — NODE BOOTSTRAP SCRIPT v4    ║
# ║   Three-phase orchestrator: open → install → lockdown    ║
# ╚══════════════════════════════════════════════════════════╝
#
# Usage:
#   bash bhn-node-bootstrap.sh <NAME> <IP> <WG_INTERFACE> <TYPE> <REGION>
#
# Example:
#   bash bhn-node-bootstrap.sh BHN-VPS-NYC-US2 1.2.3.4 wg2 exit US-EAST
#
# TYPE ∈ {hub, exit, scan, proxy}
#
# Optional environment variables:
#   ATTACH_NVME=/dev/vdb           Encrypt + mount as hot tier (hub only)
#   ATTACH_HDD=/dev/vdc            Encrypt + mount as cold tier (hub only)
#   ADMIN_PUBKEYS_FILE=<path>      File of SSH pubkeys, one per line
#   INSTALL_SURICATA=1             Force-enable Suricata IDS (auto-on for hub+scan)
#   EH_BOOTSTRAP_PG_DSN=<dsn>      DSN for hub PG to register this node
#                                  e.g. postgresql://bootstrap_writer:PASS@<BHN_WG_LA_IP>/eventhorizon
#                                  If unset, registration SQL is staged at
#                                  /root/eh-node-register.sql for manual apply.
#   EH_NTFY_URL=<url>              ntfy endpoint to notify on completion
#                                  e.g. https://ntfy.sh/eh-bootstrap-<topic>
#                                  If unset, notification is skipped.
#   HUB_IP=<ip>                    Override hub IP (default: LA hub)
#   HUB_PUBKEY=<key>               Override hub WG pubkey
#   TUNNEL_IP_OVERRIDE=<ip>        Override derived TUNNEL_IP. Use when joining
#                                  the hub's flat /24 instead of getting your own
#                                  (e.g. NJ at <BHN_WG_NJ_IP>, Hillsboro at <BHN_WG_HIL_IP>
#                                  both on LA's wg0 alongside personal peers).
#
# Exit codes:
#   0  bootstrap completed (lockdown applied)
#   1  pre-flight or arg error
#   2  phase 1 (open window) failed
#   3  phase 2 (type install) failed — node may be in partial state
#   4  phase 3 (lockdown) failed — node may still be in open-window state

set -euo pipefail

BOOTSTRAP_VERSION=4
BOOTSTRAP_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

# ─── Helpers (used by all modules and node-type scripts) ───────
RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${CYAN}[BHN]${NC} $*"; }
ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*" >&2; exit "${2:-1}"; }
phase(){ echo -e "\n${CYAN}╔═══ PHASE $1: $2 ═══╗${NC}"; }

# ─── Args ──────────────────────────────────────────────────────
NODE_NAME="${1:-}"
NODE_IP="${2:-}"
WG_INTERFACE="${3:-}"
NODE_TYPE="${4:-}"
NODE_REGION="${5:-}"

usage() {
  cat <<EOF
Usage: bash bhn-node-bootstrap.sh <NAME> <IP> <WG_INTERFACE> <TYPE> <REGION>

  NAME          e.g. BHN-VPS-NYC-US2
  IP            public IPv4 of the node
  WG_INTERFACE  e.g. wg2 (hub uses wg0 by convention)
  TYPE          one of: hub | exit | scan | proxy
  REGION        free-form, e.g. US-EAST | EU-CENTRAL | APAC-SE
EOF
  exit 1
}

[[ $EUID -ne 0 ]] && err "Must run as root"
for a in NODE_NAME NODE_IP WG_INTERFACE NODE_TYPE NODE_REGION; do
  [[ -z "${!a}" ]] && usage
done

case "$NODE_TYPE" in
  hub|exit|scan|proxy) ;;
  *) err "TYPE must be one of: hub, exit, scan, proxy (got '$NODE_TYPE')" ;;
esac

# Derived state
NET_IFACE="$(ip route | awk '/^default/{print $5; exit}')"
WG_NUM="$(echo "$WG_INTERFACE" | tr -dc '0-9')"; WG_NUM="${WG_NUM:-1}"
TUNNEL_NETWORK="10.$((8 + WG_NUM)).0.0/24"
if [[ "$NODE_TYPE" == "hub" ]]; then
  TUNNEL_IP="10.$((8 + WG_NUM)).0.1"
else
  TUNNEL_IP="10.$((8 + WG_NUM)).0.2"
fi
TUNNEL_IP="${TUNNEL_IP_OVERRIDE:-$TUNNEL_IP}"
BOOTSTRAP_TS="$(date -u +%FT%TZ)"

# Hub identity (defaults — overridable for new hubs / migrations)
HUB_IP="${HUB_IP:-<BHN_LA_PUBLIC_IP>}"
HUB_PUBKEY="${HUB_PUBKEY:-<BHN_WG_LA_PUBKEY>}"
HUB_WG_PORT="${HUB_WG_PORT:-51820}"

# Service constants
SS_PORT=8388
SS_PASSWORD="${SS_PASSWORD:-$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 32)}"

POLICY_FILE="${BOOTSTRAP_DIR}/policies/${NODE_TYPE}-network-policy.conf"
[[ -f "$POLICY_FILE" ]] || err "Policy file missing: $POLICY_FILE"

# Decide if Suricata should be on (hub+scan get it by default)
case "$NODE_TYPE" in
  hub|scan) INSTALL_SURICATA="${INSTALL_SURICATA:-1}" ;;
  *)        INSTALL_SURICATA="${INSTALL_SURICATA:-0}" ;;
esac

# ─── Banner ────────────────────────────────────────────────────
cat <<EOF

╔══════════════════════════════════════════════╗
║  BLACKHOLE NETWORK NODE BOOTSTRAP v${BOOTSTRAP_VERSION}     ║
╠══════════════════════════════════════════════╣
║  Node:    ${NODE_NAME}
║  Type:    ${NODE_TYPE}
║  Region:  ${NODE_REGION}
║  IP:      ${NODE_IP}
║  WG:      ${WG_INTERFACE} (tunnel ${TUNNEL_IP})
║  NIC:     ${NET_IFACE}
║  Hub:     ${HUB_IP}
║  Started: ${BOOTSTRAP_TS}
╚══════════════════════════════════════════════╝
EOF

# ─── Source modules (libraries — define functions, no side effects) ─
for m in ssh-hardening storage wireguard shadowsocks dnscrypt firewall \
         network-policy crowdsec suricata backup; do
  src="${BOOTSTRAP_DIR}/modules/${m}.sh"
  [[ -f "$src" ]] || err "Missing module: $src"
  # shellcheck disable=SC1090
  source "$src"
done

# ─── Source the node-type composer ──────────────────────────────
NODE_TYPE_SCRIPT="${BOOTSTRAP_DIR}/node-types/${NODE_TYPE}.sh"
[[ -f "$NODE_TYPE_SCRIPT" ]] || err "Missing node-type script: $NODE_TYPE_SCRIPT"
# shellcheck disable=SC1090
source "$NODE_TYPE_SCRIPT"

declare -F node_type_install >/dev/null \
  || err "node-types/${NODE_TYPE}.sh did not define node_type_install()"

# ════════════════════════════════════════════════════════════════
# PHASE 1: OPEN WINDOW
# ════════════════════════════════════════════════════════════════
phase 1 "OPEN WINDOW (admin access for install)"
trap 'err "Phase 1 failed at line $LINENO" 2' ERR

log "System update + base utilities"
DEBIAN_FRONTEND=noninteractive apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get -o Dpkg::Options::="--force-confold" upgrade -y -qq
BASE_PKGS=(curl wget gnupg2 ca-certificates lsb-release software-properties-common ufw jq postgresql-client)
# Ubuntu 24.04+ declares `ufw : Breaks: netfilter-persistent` AND
# `ufw : Breaks: iptables-persistent`. UFW now persists its own rules natively;
# the only thing we used netfilter-persistent for was persisting custom non-UFW
# iptables rules (NAT MASQUERADE + VPN→SSH ACCEPT). On 24.04+ those need a
# different home (UFW before.rules) — TODO: refactor for full exit/scan/hub
# portability on 24.04. For 22.04 nodes, install netfilter-persistent so
# existing behaviour is preserved.
. /etc/os-release
if [[ "$VERSION_ID" =~ ^(20\.04|22\.04)$ ]]; then
  BASE_PKGS+=(netfilter-persistent)
fi
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${BASE_PKGS[@]}"
ok "Base packages installed (Ubuntu $VERSION_ID)"

setup_ssh_hardening
setup_firewall_open_window   # ingress: 22, 80, 443; egress: allow-all (temporary)
ok "Phase 1 complete — admin window open, install can proceed"

# ════════════════════════════════════════════════════════════════
# PHASE 2: INSTALL BY TYPE
# ════════════════════════════════════════════════════════════════
phase 2 "INSTALL (type=${NODE_TYPE})"
trap 'err "Phase 2 failed at line $LINENO — node may be in partial state" 3' ERR

node_type_install

# Generate /etc/eh-node-info.conf (consumed by ops scripts + Grafana labels)
log "Writing /etc/eh-node-info.conf"
NODE_PUBKEY="$(cat /etc/wireguard/public.key 2>/dev/null || echo '')"
cat >/etc/eh-node-info.conf <<EOF
# Auto-generated by bhn-node-bootstrap.sh v${BOOTSTRAP_VERSION} — DO NOT EDIT
NODE_NAME=${NODE_NAME}
NODE_TYPE=${NODE_TYPE}
NODE_REGION=${NODE_REGION}
NODE_PUBLIC_IP=${NODE_IP}
NODE_TUNNEL_IP=${TUNNEL_IP}
WG_INTERFACE=${WG_INTERFACE}
WG_PUBKEY=${NODE_PUBKEY}
HUB_IP=${HUB_IP}
HUB_PUBKEY=${HUB_PUBKEY}
BOOTSTRAP_VERSION=${BOOTSTRAP_VERSION}
BOOTSTRAP_TIMESTAMP=${BOOTSTRAP_TS}
EOF
chmod 644 /etc/eh-node-info.conf
ok "/etc/eh-node-info.conf written"

hostnamectl set-hostname "${NODE_NAME}"
ok "Phase 2 complete — services installed, node-info written"

# ════════════════════════════════════════════════════════════════
# PHASE 3: AUTO LOCKDOWN
# ════════════════════════════════════════════════════════════════
phase 3 "LOCKDOWN (apply network policy)"
trap 'err "Phase 3 failed at line $LINENO — node still in open-window state" 4' ERR

apply_network_policy "$POLICY_FILE"
ok "Network policy applied from $(basename "$POLICY_FILE")"

# Register in hub PG nodes table (best-effort — stages SQL if direct fails)
register_node_in_hub() {
  local sql
  sql=$(cat <<SQL
INSERT INTO nodes (name, type, region, public_ip, tunnel_ip, wg_interface,
                   wg_pubkey, bootstrap_version, bootstrap_completed_at, status)
VALUES ('${NODE_NAME}', '${NODE_TYPE}', '${NODE_REGION}', '${NODE_IP}',
        '${TUNNEL_IP}', '${WG_INTERFACE}', '${NODE_PUBKEY}',
        '${BOOTSTRAP_VERSION}', NOW(), 'online')
ON CONFLICT (name) DO UPDATE SET
  type=EXCLUDED.type, region=EXCLUDED.region, public_ip=EXCLUDED.public_ip,
  tunnel_ip=EXCLUDED.tunnel_ip, wg_interface=EXCLUDED.wg_interface,
  wg_pubkey=EXCLUDED.wg_pubkey, bootstrap_version=EXCLUDED.bootstrap_version,
  bootstrap_completed_at=NOW(), status='online', updated_at=NOW();
SQL
)
  if [[ -n "${EH_BOOTSTRAP_PG_DSN:-}" ]]; then
    log "Registering node in hub PG"
    if echo "$sql" | psql "${EH_BOOTSTRAP_PG_DSN}" -v ON_ERROR_STOP=1 >/dev/null 2>&1; then
      ok "Registered in hub PG nodes table"
      return 0
    fi
    warn "PG registration failed — staging SQL for manual apply"
  fi
  echo "$sql" >/root/eh-node-register.sql
  chmod 600 /root/eh-node-register.sql
  warn "Node registration SQL staged at /root/eh-node-register.sql"
  warn "On hub: psql -d eventhorizon -f /root/eh-node-register.sql"
}
register_node_in_hub

# ntfy push notification
notify_ntfy() {
  [[ -z "${EH_NTFY_URL:-}" ]] && return 0
  local body
  body=$(cat <<MSG
${NODE_NAME} (${NODE_TYPE}) online
Region: ${NODE_REGION}
Public:  ${NODE_IP}
Tunnel:  ${TUNNEL_IP}
WG pub:  ${NODE_PUBKEY:0:24}…
Started: ${BOOTSTRAP_TS}
MSG
)
  curl -sS -m 8 \
    -H "Title: EH bootstrap: ${NODE_NAME} online" \
    -H "Tags: white_check_mark,satellite_antenna" \
    -H "Priority: default" \
    -d "$body" "${EH_NTFY_URL}" >/dev/null \
    && ok "ntfy notification sent" \
    || warn "ntfy POST failed (non-fatal)"
}
notify_ntfy

trap - ERR

# ─── Final summary ─────────────────────────────────────────────
cat <<EOF

╔══════════════════════════════════════════════════════════╗
║              BOOTSTRAP v${BOOTSTRAP_VERSION} COMPLETE ✓                     ║
╠══════════════════════════════════════════════════════════╣
║  Node:     ${NODE_NAME} (${NODE_TYPE}) — ${NODE_REGION}
║  IP:       ${NODE_IP}
║  Tunnel:   ${TUNNEL_IP}
║  Policy:   $(basename "$POLICY_FILE")
║  Suricata: $([[ "$INSTALL_SURICATA" == "1" ]] && echo "✓" || echo "—")
╠══════════════════════════════════════════════════════════╣
EOF
[[ "$NODE_TYPE" != "hub" ]] && cat <<EOF
║  PUBLIC KEY (give to hub):
║  ${NODE_PUBKEY}
╠══════════════════════════════════════════════════════════╣
║  RUN ON HUB to register this peer:
║  wg set wg0 peer ${NODE_PUBKEY} \\
║    endpoint ${NODE_IP}:51821 \\
║    allowed-ips ${TUNNEL_IP}/32 \\
║    persistent-keepalive 25
║  wg-quick save wg0
╠══════════════════════════════════════════════════════════╣
║  RUN ON HUB to allow LA-initiated traffic to this peer:
║  (Hub's outbound default is deny — without these rules,
║   ping/SSH/proxy calls from hub to ${TUNNEL_IP} silently drop
║   at the OUTPUT chain. The WG handshake still works via
║   conntrack from the peer's keepalive, which masks the issue.)
║  ufw allow out to ${NODE_IP} port 51821 proto udp comment 'wg0 hub->${NODE_NAME} underlay'
║  ufw allow out to ${TUNNEL_IP} comment 'egress-${NODE_NAME}-via-tunnel'
╠══════════════════════════════════════════════════════════╣
EOF
cat <<EOF
║  SHADOWSOCKS PASSWORD (save to password manager):
║  ${SS_PASSWORD}
╚══════════════════════════════════════════════════════════╝
EOF
