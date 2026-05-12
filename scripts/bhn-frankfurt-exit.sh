#!/bin/bash
# ╔══════════════════════════════════════════════════════════╗
# ║  BHN — Frankfurt Exit Routing (Phase 1)                  ║
# ║  Policy-route wg0 client traffic out via Frankfurt       ║
# ║  Multi-mode: dry-run / apply / confirm / rollback /      ║
# ║              status / up / down / install-persistence    ║
# ╚══════════════════════════════════════════════════════════╝
#
# Lives at: /etc/wireguard/bhn-frankfurt-exit.sh on LA
# Repo:     scripts/bhn-frankfurt-exit.sh
#
# Modes:
#   dry-run               Print every change without applying
#   apply                 Apply changes + schedule 5-min auto-rollback
#   confirm               Cancel auto-rollback (call after verifying traffic)
#   rollback              Manually revert all changes
#   status                Show current state of routing
#   up                    Apply changes silently (called from wg0 PostUp hook)
#   down                  Revert changes silently (called from wg0 PostDown hook)
#   install-persistence   Patch wg0.conf to call this script on PostUp/PostDown
#
# After a successful apply + confirm, run install-persistence to make the
# changes survive reboot.

set -uo pipefail

# ─── Constants ──────────────────────────────────────────────────────────
FRA_TUNNEL_IP="10.9.0.2"
LA_PUBLIC_IP="149.28.91.100"
WG_HUB_SUBNET="10.8.0.0/24"
WG_FRA_SUBNET="10.9.0.0/24"
FWMARK="0x100"
TABLE_ID=100
RULE_PRIO=100

ROLLBACK_PID_FILE="/var/run/bhn-fra-rollback.pid"
CONFIRMED_FLAG="/var/lib/bhn/fra-routing-confirmed"
WG_CONFIG="/etc/wireguard/wg0.conf"
SELF_PATH="/etc/wireguard/bhn-frankfurt-exit.sh"

# ─── Colors ─────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${CYAN}[BHN-FRA-EXIT]${NC} $*"; }
ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*" >&2; }
err()  { echo -e "${RED}[✗]${NC} $*" >&2; exit "${2:-1}"; }

[[ $EUID -ne 0 ]] && err "Must run as root"

# ─── Helpers ────────────────────────────────────────────────────────────
have_rule_mangle() {
  iptables -t mangle -C PREROUTING -i wg0 -j MARK --set-mark "$FWMARK" 2>/dev/null
}
have_ip_rule() {
  ip rule show | grep -q "fwmark $FWMARK lookup $TABLE_ID"
}
have_route() {
  local route="$1"
  ip route show table "$TABLE_ID" | grep -q "$route"
}
have_forward_wg0_wg1() {
  iptables -C FORWARD -i wg0 -o wg1 -j ACCEPT 2>/dev/null
}
have_forward_wg1_wg0() {
  iptables -C FORWARD -i wg1 -o wg0 -j ACCEPT 2>/dev/null
}

apply_changes() {
  local dry="${1:-}"
  local exec=""
  [[ "$dry" == "dry-run" ]] && exec="echo [DRY-RUN]"

  # 1. mangle PREROUTING mark
  if have_rule_mangle && [[ "$dry" != "dry-run" ]]; then
    log "mangle rule already present — skipping"
  else
    $exec iptables -t mangle -A PREROUTING -i wg0 -j MARK --set-mark "$FWMARK"
    [[ "$dry" != "dry-run" ]] && ok "mangle: mark wg0-inbound with $FWMARK"
  fi

  # 2. ip rule for marked packets → table 100
  if have_ip_rule && [[ "$dry" != "dry-run" ]]; then
    log "ip rule already present — skipping"
  else
    $exec ip rule add fwmark "$FWMARK" lookup "$TABLE_ID" priority "$RULE_PRIO"
    [[ "$dry" != "dry-run" ]] && ok "ip rule: fwmark $FWMARK → table $TABLE_ID"
  fi

  # 3. Routes in table 100
  declare -a routes=(
    "$WG_HUB_SUBNET dev wg0"
    "$WG_FRA_SUBNET dev wg1"
    "${LA_PUBLIC_IP}/32 dev enp1s0"
    "default via $FRA_TUNNEL_IP dev wg1"
  )
  for r in "${routes[@]}"; do
    if have_route "$r" && [[ "$dry" != "dry-run" ]]; then
      log "route already present: $r — skipping"
    else
      $exec ip route add $r table "$TABLE_ID"
      [[ "$dry" != "dry-run" ]] && ok "route added: $r (table $TABLE_ID)"
    fi
  done

  # 4. FORWARD chain allow for wg0↔wg1
  if have_forward_wg0_wg1 && [[ "$dry" != "dry-run" ]]; then
    log "FORWARD wg0→wg1 already present — skipping"
  else
    $exec iptables -I FORWARD 1 -i wg0 -o wg1 -j ACCEPT
    [[ "$dry" != "dry-run" ]] && ok "FORWARD: wg0 → wg1 ACCEPT (top of chain)"
  fi
  if have_forward_wg1_wg0 && [[ "$dry" != "dry-run" ]]; then
    log "FORWARD wg1→wg0 already present — skipping"
  else
    $exec iptables -I FORWARD 2 -i wg1 -o wg0 -j ACCEPT
    [[ "$dry" != "dry-run" ]] && ok "FORWARD: wg1 → wg0 ACCEPT (for return path)"
  fi
}

revert_changes() {
  # Remove iptables/ip-rule/ip-route changes. Safe to run repeatedly.
  iptables -t mangle -D PREROUTING -i wg0 -j MARK --set-mark "$FWMARK" 2>/dev/null && ok "mangle rule removed"
  ip rule del fwmark "$FWMARK" lookup "$TABLE_ID" 2>/dev/null && ok "ip rule removed"
  ip route flush table "$TABLE_ID" 2>/dev/null && ok "routing table $TABLE_ID flushed"
  iptables -D FORWARD -i wg0 -o wg1 -j ACCEPT 2>/dev/null && ok "FORWARD wg0→wg1 removed"
  iptables -D FORWARD -i wg1 -o wg0 -j ACCEPT 2>/dev/null && ok "FORWARD wg1→wg0 removed"
}

preflight() {
  # Verify we're on LA + wg0/wg1 are up + FRA reachable
  log "Pre-flight checks..."

  ip link show wg0 >/dev/null 2>&1 || err "wg0 interface not present"
  ip link show wg1 >/dev/null 2>&1 || err "wg1 interface not present"

  # Quick FRA handshake check (last 5 min)
  local last_hs
  last_hs=$(wg show wg1 latest-handshakes 2>/dev/null | awk 'NR==1 {print $2}')
  local now=$(date +%s)
  if [[ -z "$last_hs" || $((now - last_hs)) -gt 300 ]]; then
    err "Frankfurt wg1 handshake stale (>5 min). Refusing to apply — fix tunnel first."
  fi
  ok "wg1 handshake fresh ($(( (now - last_hs) ))s ago)"

  # Verify FRA reachable on the tunnel
  if ! timeout 3 ping -I wg1 -c 2 "$FRA_TUNNEL_IP" >/dev/null 2>&1; then
    err "Cannot ping $FRA_TUNNEL_IP via wg1 — tunnel up but unhealthy"
  fi
  ok "FRA reachable via tunnel"

  # Reminder about FRA-side MASQUERADE
  warn "Reminder: confirm Frankfurt has MASQUERADE rule for ${WG_HUB_SUBNET} source."
  warn "Run on FRA: iptables -t nat -S POSTROUTING | grep MASQUERADE"
  warn "If missing: iptables -t nat -A POSTROUTING -s ${WG_HUB_SUBNET} -o enp1s0 -j MASQUERADE && iptables-save > /etc/iptables/rules.v4"
}

show_status() {
  log "Status of Frankfurt-exit routing:"
  echo
  echo "=== iptables mangle PREROUTING (looking for MARK on wg0) ==="
  iptables -t mangle -nvL PREROUTING --line-numbers | grep -E "wg0|MARK|^$|^Chain" | head -10

  echo
  echo "=== ip rule (looking for fwmark $FWMARK) ==="
  ip rule show

  echo
  echo "=== routing table $TABLE_ID ==="
  ip route show table "$TABLE_ID" 2>/dev/null || echo "(table empty or doesn't exist)"

  echo
  echo "=== iptables FORWARD (wg0/wg1) ==="
  iptables -nvL FORWARD --line-numbers | grep -E "wg0|wg1|^Chain|^num" | head -10

  echo
  echo "=== wg1 handshake ==="
  wg show wg1 | grep -E "latest handshake|transfer" | head -2

  echo
  echo "=== rollback state ==="
  if [[ -f "$ROLLBACK_PID_FILE" ]]; then
    local pid=$(cat "$ROLLBACK_PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
      warn "Auto-rollback pending — PID $pid. Run \`$0 confirm\` to cancel."
    fi
  fi
  if [[ -f "$CONFIRMED_FLAG" ]]; then
    ok "Routing confirmed at $(cat "$CONFIRMED_FLAG")"
  fi
}

schedule_rollback() {
  mkdir -p /var/lib/bhn
  rm -f "$CONFIRMED_FLAG"

  # Spawn background watchdog: if no confirm file in 5 min, revert
  nohup bash -c "
    sleep 300
    if [[ ! -f $CONFIRMED_FLAG ]]; then
      logger -t bhn-fra-exit '5-min rollback fired — no confirm received'
      $SELF_PATH rollback 2>&1 | logger -t bhn-fra-exit
    fi
  " >/dev/null 2>&1 &
  echo $! > "$ROLLBACK_PID_FILE"
  ok "Auto-rollback scheduled in 5 min (watchdog PID $(cat "$ROLLBACK_PID_FILE"))"
  warn "Run \`$0 confirm\` after verifying traffic exits via Frankfurt IP."
  warn "Verify with: curl https://api.ipify.org  (from operator PC on 'full' profile)"
  warn "Expected output: 192.248.187.208 (Frankfurt's IP)"
}

cancel_rollback() {
  if [[ -f "$ROLLBACK_PID_FILE" ]]; then
    local pid=$(cat "$ROLLBACK_PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null
      ok "Rollback watchdog killed (was PID $pid)"
    fi
    rm -f "$ROLLBACK_PID_FILE"
  fi
  echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC')" > "$CONFIRMED_FLAG"
  ok "Routing CONFIRMED. Will survive operator session ends."
}

install_persistence() {
  # Patch /etc/wireguard/wg0.conf to add PostUp/PostDown hooks under [Interface]
  if ! [[ -f "$WG_CONFIG" ]]; then
    err "$WG_CONFIG not found"
  fi

  # Check if hooks already present
  if grep -qE "^PostUp\s*=.*bhn-frankfurt-exit" "$WG_CONFIG" && \
     grep -qE "^PostDown\s*=.*bhn-frankfurt-exit" "$WG_CONFIG"; then
    ok "PostUp/PostDown hooks already present in $WG_CONFIG"
    return 0
  fi

  # Backup
  cp "$WG_CONFIG" "${WG_CONFIG}.bak-$(date +%s)"
  ok "Backup created: ${WG_CONFIG}.bak-$(date +%s)"

  # Inject hooks under [Interface] block, before any [Peer]
  awk -v hooks="PostUp = $SELF_PATH up\nPostDown = $SELF_PATH down" '
    BEGIN { injected = 0 }
    /^\[Peer\]/ && !injected {
      print hooks
      injected = 1
    }
    /^\[Interface\]/ { in_interface = 1; print; next }
    { print }
  ' "$WG_CONFIG" > "${WG_CONFIG}.tmp" && mv "${WG_CONFIG}.tmp" "$WG_CONFIG"

  chmod 600 "$WG_CONFIG"
  ok "PostUp/PostDown hooks added to $WG_CONFIG"
  warn "Hooks take effect on next wg-quick down/up of wg0 (or reboot)."
  warn "wg-quick down/up wg0 momentarily drops all peer connections — do during low-activity window OR rely on next reboot."
}

# ─── Mode dispatch ──────────────────────────────────────────────────────
case "${1:-}" in
  dry-run)
    log "DRY RUN — no changes will be applied"
    apply_changes "dry-run"
    ;;
  apply)
    preflight
    apply_changes
    schedule_rollback
    ;;
  confirm)
    cancel_rollback
    ;;
  rollback)
    log "Reverting Frankfurt-exit routing..."
    revert_changes
    rm -f "$ROLLBACK_PID_FILE" "$CONFIRMED_FLAG"
    ok "Rollback complete"
    ;;
  status)
    show_status
    ;;
  up)
    # Quiet apply — called from wg-quick PostUp hook
    apply_changes >/dev/null 2>&1 || true
    ;;
  down)
    # Quiet revert — called from wg-quick PostDown hook
    revert_changes >/dev/null 2>&1 || true
    ;;
  install-persistence)
    install_persistence
    ;;
  ""|help|-h|--help)
    grep -E '^#' "$0" | head -25 | sed 's/^# \?//'
    ;;
  *)
    err "Unknown mode: $1. Run with no args for help."
    ;;
esac
