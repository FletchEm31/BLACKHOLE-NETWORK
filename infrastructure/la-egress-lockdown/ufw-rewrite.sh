#!/bin/bash
# BHN LA egress lockdown — UFW rewrite.
#
# Two modes, applied separately so the operator can verify between phases:
#   add-proxy-route          (additive, no risk) — adds `allow out to 10.8.0.6 port 8888 proto tcp`
#                            so LA can reach the tinyproxy endpoint.
#   lockdown                 (destructive) — removes direct egress for 443/tcp, 587/tcp, 80/tcp.
#                            DO NOT RUN until the proxy path is verified end-to-end (see README).
#   restore-direct-egress    rollback for `lockdown` — re-adds the direct 443/587/80 rules.
#   status                   show current state, identify whether each rule is present.
#
# Rules KEPT in all modes:
#   53/udp+tcp (DNS — local dnscrypt-proxy on 127.0.0.1 uses 443 outbound DoH;
#              the 443 is what's removed in lockdown, but dnscrypt continues using
#              the proxy via NO_PROXY misses — see README's "What's NOT proxied")
#   123/udp (NTP — UDP, can't proxy)
#   51820/51821/udp to known peer endpoints (WG underlay)
#   10.8.0.0/24, 10.9.0.0/24 (intra-mesh)
#
# Lives at: /opt/bhn-la-egress-lockdown/ufw-rewrite.sh on LA
# Repo:     infrastructure/la-egress-lockdown/ufw-rewrite.sh

set -uo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${CYAN}[LA-LOCKDOWN-UFW]${NC} $*"; }
ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*" >&2; }
err()  { echo -e "${RED}[✗]${NC} $*" >&2; exit "${2:-1}"; }

[[ $EUID -ne 0 ]] && err "Must run as root"

HSB_TUNNEL_IP="10.8.0.6"
PROXY_PORT="8888"

# Idempotent rule helpers
rule_present() {
  ufw status | grep -qE "$1"
}

add_rule() {
  local rule_spec="$1" desc="$2" match_pattern="$3"
  if rule_present "$match_pattern"; then
    ok "Already present: $desc"
  else
    eval "ufw $rule_spec" >/dev/null && ok "Added: $desc" || err "Failed to add: $desc"
  fi
}

del_rule() {
  local rule_spec="$1" desc="$2" match_pattern="$3"
  if rule_present "$match_pattern"; then
    eval "ufw delete $rule_spec" >/dev/null && ok "Removed: $desc" || warn "Failed to remove: $desc"
  else
    ok "Already absent: $desc"
  fi
}

case "${1:-status}" in
  add-proxy-route)
    log "Adding outbound rule for Hillsboro tinyproxy (additive)"
    add_rule \
      "allow out to $HSB_TUNNEL_IP port $PROXY_PORT proto tcp comment 'la-egress-proxy via hillsboro'" \
      "out to $HSB_TUNNEL_IP:$PROXY_PORT/tcp" \
      "$HSB_TUNNEL_IP $PROXY_PORT/tcp.*ALLOW OUT"
    log "Done. Direct 443/tcp / 587/tcp / 80/tcp still allowed — proxy is now ONE OF the egress paths."
    ;;

  lockdown)
    log "Removing direct egress rules for 443/tcp, 587/tcp, 80/tcp"
    log "PRECONDITION: tinyproxy verified end-to-end. If unsure, run \`$0 status\` and abort if anything looks off."

    # Sanity check — proxy rule MUST exist or we'll cut LA off entirely
    if ! rule_present "$HSB_TUNNEL_IP $PROXY_PORT/tcp.*ALLOW OUT"; then
      err "Proxy egress rule not present — run \`$0 add-proxy-route\` first" 2
    fi

    # Sanity check — confirm tinyproxy is reachable RIGHT NOW
    if ! curl -fsS --max-time 5 -x "http://$HSB_TUNNEL_IP:$PROXY_PORT" https://api.ipify.org >/dev/null 2>&1; then
      err "Proxy reachability check failed (curl -x http://$HSB_TUNNEL_IP:$PROXY_PORT https://api.ipify.org). Refusing to lock down." 3
    fi
    ok "Proxy reachability verified (LA → tinyproxy → internet works)"

    del_rule "allow out 443/tcp"  "direct 443/tcp egress" "443/tcp.*ALLOW OUT.*Anywhere"
    del_rule "allow out 587/tcp"  "direct 587/tcp egress" "587/tcp.*ALLOW OUT.*Anywhere"
    del_rule "allow out 80/tcp"   "direct 80/tcp  egress" "80/tcp.*ALLOW OUT.*Anywhere"

    log "Lockdown applied. LA's outbound HTTP/HTTPS now requires the proxy. DNS/NTP/WG/intra-mesh unchanged."
    log "Verify: curl https://api.ipify.org → should return 5.78.94.237 (via env vars routed through proxy)"
    log "       curl --noproxy '*' https://api.ipify.org → should TIME OUT (direct egress gone)"
    ;;

  restore-direct-egress)
    log "Restoring direct egress rules (rollback)"
    add_rule "allow out 443/tcp comment 'egress-https'"           "direct 443/tcp" "443/tcp.*ALLOW OUT.*Anywhere"
    add_rule "allow out 587/tcp comment 'egress-smtp-submission'" "direct 587/tcp" "587/tcp.*ALLOW OUT.*Anywhere"
    add_rule "allow out 80/tcp  comment 'egress-http'"            "direct 80/tcp"  "80/tcp.*ALLOW OUT.*Anywhere"
    log "Direct egress restored. The proxy egress rule (to $HSB_TUNNEL_IP:$PROXY_PORT) was NOT removed — it's safe to leave."
    ;;

  status)
    log "Current LA UFW egress state (relevant lines only)"
    echo
    ufw status verbose | grep -E "(ALLOW OUT|Default: .* outgoing)" | head -30
    echo
    log "Lockdown state checks:"
    rule_present "$HSB_TUNNEL_IP $PROXY_PORT/tcp.*ALLOW OUT"   && ok "Proxy egress rule PRESENT (good)"          || warn "Proxy egress rule MISSING (run add-proxy-route)"
    rule_present "443/tcp.*ALLOW OUT.*Anywhere"                 && warn "Direct 443/tcp PRESENT (lockdown not yet applied)" || ok "Direct 443/tcp REMOVED (locked down)"
    rule_present "587/tcp.*ALLOW OUT.*Anywhere"                 && warn "Direct 587/tcp PRESENT (lockdown not yet applied)" || ok "Direct 587/tcp REMOVED (locked down)"
    rule_present "80/tcp.*ALLOW OUT.*Anywhere"                  && warn "Direct 80/tcp  PRESENT (lockdown not yet applied)" || ok "Direct 80/tcp  REMOVED (locked down)"
    ;;

  *)
    cat <<USAGE
Usage: $0 {add-proxy-route|lockdown|restore-direct-egress|status}

  add-proxy-route        Add outbound 8888/tcp to 10.8.0.6 (Hillsboro tinyproxy). Additive, safe.
  lockdown               Remove direct 443/587/80 egress. Cuts non-proxied calls. Verifies
                         proxy reachability before applying.
  restore-direct-egress  Re-add direct 443/587/80 rules (rollback).
  status                 Show egress state.
USAGE
    exit 1
    ;;
esac
