#!/bin/bash
# ╔══════════════════════════════════════════════════════════╗
# ║  BHN — Frankfurt MASQUERADE Fix                          ║
# ║  Adds the missing NAT POSTROUTING rule that completes    ║
# ║  the LA→Frankfurt exit-routing return path.              ║
# ╚══════════════════════════════════════════════════════════╝
#
# Lives at: /usr/local/sbin/bhn-frankfurt-masquerade-fix.sh on Frankfurt
# Repo:     scripts/bhn-frankfurt-masquerade-fix.sh
#
# Context — what this fixes:
#   STATUS.md:70 documents the broken "full" profile exit routing —
#   LA hub clients (10.8.0.0/24) reach Frankfurt over the WG tunnel,
#   but Frankfurt's NAT doesn't rewrite the source IP to its own
#   public IP on the way out, so return packets land at 10.8.0.x
#   addresses Frankfurt's upstream can't route to. Result: full-tunnel
#   profile = no internet.
#
# The fix is a single iptables rule:
#   iptables -t nat -A POSTROUTING -s 10.8.0.0/24 -o enp1s0 -j MASQUERADE
# plus persistence via iptables-save > /etc/iptables/rules.v4 so it
# survives reboot.
#
# Modes:
#   status        Show current state (rule present? persistence file ok?)
#   apply         Add the rule + persist (idempotent — safe to re-run)
#   rollback      Remove the rule + update persistence file
#   verify        Apply was successful — sanity-check from Frankfurt's view
#
# After `apply` on Frankfurt, re-run on LA:
#   bash /etc/wireguard/bhn-frankfurt-exit.sh apply
# then verify a full-tunnel client gets Frankfurt's public IP:
#   curl https://api.ipify.org   # should return 192.248.187.208

set -uo pipefail

# ─── Constants ──────────────────────────────────────────────────────────
WG_HUB_SUBNET="10.8.0.0/24"
FRA_PUBLIC_NIC="enp1s0"
FRA_PUBLIC_IP="192.248.187.208"
PERSISTENCE_FILE="/etc/iptables/rules.v4"

# ─── Helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${CYAN}[BHN-FRA-NAT]${NC} $*"; }
ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*" >&2; }
err()  { echo -e "${RED}[✗]${NC} $*" >&2; exit "${2:-1}"; }

[[ $EUID -ne 0 ]] && err "Must run as root"

# ─── Rule check (idempotency) ───────────────────────────────────────────
# iptables -C exits 0 if rule exists, 1 if not. Suppress its noise.
rule_exists() {
  iptables -t nat -C POSTROUTING -s "$WG_HUB_SUBNET" -o "$FRA_PUBLIC_NIC" -j MASQUERADE 2>/dev/null
}

# ─── Modes ──────────────────────────────────────────────────────────────
case "${1:-status}" in
  status)
    log "Current MASQUERADE state on Frankfurt"
    if rule_exists; then
      ok "Rule present in live iptables"
    else
      warn "Rule NOT present in live iptables (this is the bug)"
    fi
    if [[ -f "$PERSISTENCE_FILE" ]]; then
      if grep -qE "POSTROUTING.*${WG_HUB_SUBNET//./\\.}.*MASQUERADE" "$PERSISTENCE_FILE"; then
        ok "Rule present in $PERSISTENCE_FILE (persists across reboot)"
      else
        warn "Rule NOT in $PERSISTENCE_FILE — would be lost on reboot"
      fi
    else
      warn "$PERSISTENCE_FILE missing — install netfilter-persistent or create the file"
    fi
    log "Frankfurt NIC: $FRA_PUBLIC_NIC"
    log "Frankfurt public IP: $FRA_PUBLIC_IP"
    log "Hub subnet being NAT'd: $WG_HUB_SUBNET"
    echo
    log "Full NAT POSTROUTING chain right now:"
    iptables -t nat -L POSTROUTING -n -v --line-numbers
    ;;

  apply)
    log "Applying MASQUERADE fix"
    if rule_exists; then
      ok "Rule already in live iptables — no change"
    else
      iptables -t nat -A POSTROUTING -s "$WG_HUB_SUBNET" -o "$FRA_PUBLIC_NIC" -j MASQUERADE \
        || err "iptables append failed" 2
      ok "Rule added: -s $WG_HUB_SUBNET -o $FRA_PUBLIC_NIC -j MASQUERADE"
    fi

    log "Persisting to $PERSISTENCE_FILE"
    mkdir -p "$(dirname "$PERSISTENCE_FILE")"
    if iptables-save > "$PERSISTENCE_FILE"; then
      ok "iptables-save → $PERSISTENCE_FILE"
    else
      err "iptables-save failed" 3
    fi

    if command -v netfilter-persistent >/dev/null; then
      netfilter-persistent save >/dev/null 2>&1 \
        && ok "netfilter-persistent save (belt-and-suspenders)" \
        || warn "netfilter-persistent save failed (not blocking — file is written)"
    fi

    log "Next step: on LA, run \`bash /etc/wireguard/bhn-frankfurt-exit.sh apply\`"
    log "Then verify from a full-tunnel client: \`curl https://api.ipify.org\` (expect $FRA_PUBLIC_IP)"
    ;;

  rollback)
    log "Rolling back MASQUERADE fix"
    if rule_exists; then
      iptables -t nat -D POSTROUTING -s "$WG_HUB_SUBNET" -o "$FRA_PUBLIC_NIC" -j MASQUERADE \
        && ok "Rule removed from live iptables" \
        || err "iptables delete failed" 2
    else
      warn "Rule not in live iptables — nothing to remove"
    fi
    iptables-save > "$PERSISTENCE_FILE" && ok "Persistence file updated"
    log "Rollback complete. Full-tunnel exit routing will no longer work — clients will see no internet on \`EH-full\` profile."
    ;;

  verify)
    log "Verifying from Frankfurt's perspective"
    log "Step 1: confirm rule is present"
    rule_exists && ok "Rule in live iptables" || { warn "Rule missing"; exit 1; }
    log "Step 2: confirm WG peer for the hub is up"
    if wg show wg0 2>/dev/null | grep -q "endpoint"; then
      ok "wg0 has at least one peer with an endpoint"
    else
      warn "wg0 has no peers / no endpoints — tunnel might be down"
    fi
    log "Step 3: confirm IPv4 forwarding is enabled"
    if [[ "$(cat /proc/sys/net/ipv4/ip_forward)" == "1" ]]; then
      ok "net.ipv4.ip_forward = 1"
    else
      warn "net.ipv4.ip_forward = 0 — packets won't be forwarded even with MASQUERADE"
    fi
    log "All Frankfurt-side conditions for full-tunnel exit met. Apply LA-side next."
    ;;

  *)
    cat <<USAGE
Usage: $0 {status|apply|rollback|verify}

  status    Show whether the MASQUERADE rule is present + persisted
  apply     Add the rule + persist (idempotent)
  rollback  Remove the rule + update persistence file
  verify    Sanity-check Frankfurt-side prerequisites are met
USAGE
    exit 1
    ;;
esac
