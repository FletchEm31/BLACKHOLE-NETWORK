#!/bin/bash
# bhn-la-restore.sh — restore LA's general-internet egress after tonight's
# Frankfurt-exit-routing debugging session.
#
# What this fixes:
#   Tonight we removed the unconditional MASQUERADE PostUp from
#   /etc/wireguard/wg0.conf and replaced it with a fwmark-conditional one
#   while experimenting with policy routing. The unconditional rule is what
#   makes LA's wg0 mesh able to reach the public internet via LA's own NIC.
#   With it gone, LA hub can't NAT outbound for itself OR for full-tunnel
#   peers. Result: LA hub apparently up but no internet egress.
#
# What this does (idempotent — safe to re-run):
#   1. Backup current /etc/wireguard/wg0.conf
#   2. If the unconditional MASQUERADE PostUp/PostDown lines aren't present,
#      insert them after the [Interface] block (before first [Peer])
#   3. Remove the fwmark-conditional MASQUERADE if present
#   4. Remove the FORWARD wg0→enp1s0 ACCEPT rule if present (it was an
#      experiment; the implicit FORWARD chain already handles wg0 via the
#      `iptables -A FORWARD -i wg0 -j ACCEPT` and `-o wg0 -j ACCEPT` PostUps
#      that we kept)
#   5. wg-quick down + up wg0 (BRIEF disconnect for all peers — ~5s)
#   6. Verify LA can curl https://api.ipify.org
#   7. wg show wg0 — confirm peers reconnected
#
# IMPORTANT: When wg-quick down runs, LA's SSH-over-wg path drops too. If
# you're SSH'd into LA via the tunnel (not via public 22/tcp), this will
# disconnect you. Recommended: run from a local console OR from a direct
# SSH to LA's public IP (149.28.91.100:22).
#
# Modes:
#   restore  — apply the fix (default if no arg)
#   status   — show current PostUp/PostDown + relevant iptables rules
#   undo     — restore the backup created by the last 'restore' run

set -uo pipefail

WG_CONF=/etc/wireguard/wg0.conf
PUBLIC_NIC=enp1s0
LATEST_BACKUP_LINK=/etc/wireguard/wg0.conf.bhn-la-restore-latest

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${CYAN}[bhn-la-restore]${NC} $*"; }
ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*" >&2; }
err()  { echo -e "${RED}[✗]${NC} $*" >&2; exit "${2:-1}"; }

[[ $EUID -ne 0 ]] && err "Must run as root"
[[ -r "$WG_CONF" ]] || err "Missing $WG_CONF" 2

MODE="${1:-restore}"

case "$MODE" in
status)
  log "wg0.conf PostUp/PostDown lines:"
  grep -E '^(PostUp|PostDown)' "$WG_CONF" || warn "no PostUp/PostDown lines found"
  echo
  log "NAT POSTROUTING chain (looking for MASQUERADE on $PUBLIC_NIC):"
  iptables -t nat -L POSTROUTING -n -v --line-numbers | head -20
  echo
  log "FORWARD chain (looking for wg0 → $PUBLIC_NIC):"
  iptables -L FORWARD -n -v --line-numbers | head -20
  exit 0
  ;;

undo)
  if [[ ! -L "$LATEST_BACKUP_LINK" ]]; then
    err "no backup link at $LATEST_BACKUP_LINK — nothing to undo" 3
  fi
  backup=$(readlink -f "$LATEST_BACKUP_LINK")
  log "Restoring $WG_CONF from $backup"
  cp -a "$backup" "$WG_CONF"
  log "Restarting wg0"
  wg-quick down wg0 || true
  wg-quick up wg0
  ok "Undo complete. Last backup preserved at $backup"
  exit 0
  ;;

restore)
  ;;

*)
  cat <<USAGE
Usage: $0 {restore|status|undo}
  restore  apply the fix (default)
  status   show current state without changing anything
  undo     restore the most recent backup created by 'restore'
USAGE
  exit 1
  ;;
esac

# ─── 1. Backup ────────────────────────────────────────────────────────────
ts=$(date +%Y%m%d-%H%M%S)
backup="$WG_CONF.bak.bhn-la-restore.$ts"
cp -a "$WG_CONF" "$backup"
ln -sfn "$backup" "$LATEST_BACKUP_LINK"
ok "Backup: $backup"

# ─── 2. Add MASQUERADE PostUp/PostDown if missing ────────────────────────
# We look for an existing line that contains POSTROUTING + MASQUERADE in a
# PostUp context. If found, skip; otherwise insert AFTER the last PostUp
# and BEFORE the first [Peer] section.
already_has=$(grep -cE '^PostUp[[:space:]]*=[[:space:]]*iptables[[:space:]]+-t[[:space:]]+nat[[:space:]]+-A[[:space:]]+POSTROUTING[[:space:]]+-o[[:space:]]+'$PUBLIC_NIC'[[:space:]]+-j[[:space:]]+MASQUERADE' "$WG_CONF" || true)

if [[ "$already_has" -gt 0 ]]; then
  ok "Unconditional MASQUERADE PostUp already present — wg0.conf unchanged"
else
  log "Adding MASQUERADE PostUp/PostDown before first [Peer]"
  awk -v nic="$PUBLIC_NIC" '
    BEGIN { added = 0 }
    /^\[Peer\]/ && !added {
      print "PostUp = iptables -t nat -A POSTROUTING -o " nic " -j MASQUERADE"
      print "PostDown = iptables -t nat -D POSTROUTING -o " nic " -j MASQUERADE"
      print ""
      added = 1
    }
    { print }
    END {
      if (!added) {
        # No [Peer] section — append at end of file
        print "PostUp = iptables -t nat -A POSTROUTING -o " nic " -j MASQUERADE"
        print "PostDown = iptables -t nat -D POSTROUTING -o " nic " -j MASQUERADE"
      }
    }
  ' "$backup" > "$WG_CONF"
  ok "Added MASQUERADE PostUp/PostDown to $WG_CONF"
fi

# Quick syntax sanity: wg-quick refuses to up a config that has unparseable
# directives. Use `wg-quick strip` to validate without applying.
if ! wg-quick strip wg0 >/dev/null 2>&1; then
  warn "wg-quick strip failed — wg0.conf may be malformed. Restoring backup."
  cp -a "$backup" "$WG_CONF"
  err "Aborted: wg0.conf failed validation; original restored" 4
fi
ok "wg0.conf passes wg-quick strip validation"

# ─── 3. Remove fwmark-conditional MASQUERADE if it exists in live state ──
# `iptables -D` returns non-zero if the rule isn't there — that's the "clean
# already" signal. Suppress its stderr and discard the return code.
log "Looking for fwmark-conditional MASQUERADE to clean up"
if iptables -t nat -C POSTROUTING -o $PUBLIC_NIC -m mark ! --mark 0x100 -j MASQUERADE 2>/dev/null; then
  iptables -t nat -D POSTROUTING -o $PUBLIC_NIC -m mark ! --mark 0x100 -j MASQUERADE
  ok "Removed fwmark-conditional MASQUERADE"
else
  ok "fwmark-conditional MASQUERADE not present (already clean)"
fi

# ─── 4. Remove FORWARD wg0 → enp1s0 ACCEPT if present ────────────────────
log "Looking for FORWARD wg0 → $PUBLIC_NIC ACCEPT to clean up"
if iptables -C FORWARD -i wg0 -o $PUBLIC_NIC -j ACCEPT 2>/dev/null; then
  iptables -D FORWARD -i wg0 -o $PUBLIC_NIC -j ACCEPT
  ok "Removed FORWARD wg0 → $PUBLIC_NIC ACCEPT"
else
  ok "FORWARD wg0 → $PUBLIC_NIC not present (already clean)"
fi

# ─── 5. Restart wg0 ──────────────────────────────────────────────────────
log "Restarting wg0 (peers will reconnect within ~5 seconds)"
wg-quick down wg0 || warn "wg-quick down returned non-zero (continuing)"
sleep 1
if ! wg-quick up wg0; then
  warn "wg-quick up FAILED — restoring backup and retrying"
  cp -a "$backup" "$WG_CONF"
  wg-quick up wg0 || err "wg-quick up failed even with original backup — manual intervention required" 5
  err "Original wg0.conf restored after wg-quick up failure on patched config" 5
fi
ok "wg0 back up"

# ─── 6. Verify LA can reach internet ─────────────────────────────────────
log "Verifying LA can reach the internet (curl https://api.ipify.org)"
sleep 2
public_ip=$(curl -fsS --max-time 10 https://api.ipify.org 2>/dev/null || echo "")
if [[ -n "$public_ip" ]]; then
  ok "LA public IP: $public_ip"
else
  warn "curl to api.ipify.org failed — check UFW egress rules + DNS resolution"
fi

# ─── 7. Verify WG peers ──────────────────────────────────────────────────
log "wg show wg0 — peer status:"
wg show wg0

echo
ok "Restore complete. If anything looks wrong, run: $0 undo"
