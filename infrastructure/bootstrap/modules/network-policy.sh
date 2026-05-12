#!/bin/bash
# infrastructure/bootstrap/modules/network-policy.sh
#
# Sourced by bhn-node-bootstrap.sh. Provides:
#   apply_network_policy <policy_file>
#
# Parses a policy file (policies/<type>-network-policy.conf) and applies it
# via ufw. Phase 3 of the bootstrap.
#
# Policy grammar (case-sensitive keywords, free-form spacing):
#   OUTBOUND_DEFAULT <allow|deny>
#   INBOUND  <port>/<proto>|any [from <cidr>] [on <iface>]
#   OUTBOUND <port>/<proto>|any [to <cidr>]
#   FORWARD  any in <iface> out <iface>
#
# Placeholders substituted at apply time:
#   <HUB_IP>               → $HUB_IP
#   <HUB_TUNNEL_NETWORK>   → 10.8.0.0/24 (hub's wg0 subnet)
#   <WG_INTERFACE>         → $WG_INTERFACE
#   <NET_IFACE>            → $NET_IFACE (default route's NIC)

apply_network_policy() {
  local policy="$1"
  [[ -f "$policy" ]] || err "Policy file not found: $policy"
  log "Applying policy from $(basename "$policy")"

  # Hard reset and rebuild from scratch so this is the source of truth.
  ufw --force reset >/dev/null
  ufw default deny incoming  >/dev/null
  ufw default deny routed    >/dev/null   # FORWARD allows are explicit

  # First pass: scan for OUTBOUND_DEFAULT to set egress posture.
  local outbound_default="allow"
  local raw line
  while IFS= read -r raw; do
    line="${raw%%#*}"
    line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [[ -z "$line" ]] && continue
    if [[ "$line" =~ ^OUTBOUND_DEFAULT[[:space:]]+([a-z]+)$ ]]; then
      outbound_default="${BASH_REMATCH[1]}"
    fi
  done <"$policy"
  ufw default "${outbound_default}" outgoing >/dev/null
  ok "Outbound default: ${outbound_default}"

  # Second pass: apply each rule.
  local applied=0
  while IFS= read -r raw; do
    line="${raw%%#*}"
    line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [[ -z "$line" ]] && continue
    [[ "$line" =~ ^OUTBOUND_DEFAULT ]] && continue

    # Substitute placeholders
    line="${line//<HUB_IP>/${HUB_IP}}"
    line="${line//<HUB_TUNNEL_NETWORK>/10.8.0.0/24}"
    line="${line//<WG_INTERFACE>/${WG_INTERFACE}}"
    line="${line//<NET_IFACE>/${NET_IFACE}}"

    if _apply_one_rule "$line"; then
      applied=$((applied + 1))
    else
      warn "Skipped malformed rule: $line"
    fi
  done <"$policy"

  ufw --force enable >/dev/null
  ufw reload >/dev/null

  # Defense-in-depth: ensure tunnel→SSH iptables rule survives ufw reset
  iptables -C INPUT -s "${TUNNEL_NETWORK}" -p tcp --dport 22 -j ACCEPT 2>/dev/null \
    || iptables -I INPUT 1 -s "${TUNNEL_NETWORK}" -p tcp --dport 22 -j ACCEPT
  netfilter-persistent save >/dev/null 2>&1 || true

  ok "Policy applied: ${applied} rules"
}

# Parse a single (substituted) rule line and translate to a ufw invocation.
# Returns 0 on success, 1 if the line is malformed.
_apply_one_rule() {
  local line="$1"
  read -r -a tok <<<"$line"
  local kind="${tok[0]:-}"

  case "$kind" in
    INBOUND|OUTBOUND) _apply_io_rule "${tok[@]}" ;;
    FORWARD)          _apply_forward_rule "${tok[@]}" ;;
    *) return 1 ;;
  esac
}

_apply_io_rule() {
  local kind="$1" spec="${2:-}"
  shift 2 || return 1
  [[ -z "$spec" ]] && return 1

  local port proto
  if [[ "$spec" == "any" ]]; then
    port="any"; proto="any"
  elif [[ "$spec" =~ ^([0-9]+)/(tcp|udp)$ ]]; then
    port="${BASH_REMATCH[1]}"; proto="${BASH_REMATCH[2]}"
  else
    return 1
  fi

  # Parse optional `from <cidr>`, `to <cidr>`, `on <iface>`
  local from="" to="" on=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      from) from="$2"; shift 2 ;;
      to)   to="$2";   shift 2 ;;
      on)   on="$2";   shift 2 ;;
      *)    shift ;;
    esac
  done

  local cmd="ufw allow"
  [[ "$kind" == "OUTBOUND" ]] && cmd+=" out"

  if [[ -n "$from" ]]; then
    cmd+=" from $from"
    if [[ "$port" != "any" ]]; then
      cmd+=" to any port $port proto $proto"
    fi
  elif [[ -n "$to" ]]; then
    if [[ "$port" == "any" ]]; then
      cmd+=" to $to"
    else
      cmd+=" to $to port $port proto $proto"
    fi
  else
    if [[ "$port" == "any" ]]; then
      return 1   # bare "any" with no qualifier is meaningless
    fi
    cmd+=" ${port}/${proto}"
  fi

  [[ -n "$on" ]] && cmd+=" on $on"

  eval "$cmd" >/dev/null 2>&1
}

# Format: FORWARD any in <iface> out <iface>
_apply_forward_rule() {
  shift   # drop "FORWARD"
  local in_iface="" out_iface=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      in)  in_iface="$2";  shift 2 ;;
      out) out_iface="$2"; shift 2 ;;
      *)   shift ;;
    esac
  done
  [[ -z "$in_iface" || -z "$out_iface" ]] && return 1

  ufw route allow in on "$in_iface" out on "$out_iface" >/dev/null

  # NAT MASQUERADE for the egress NIC (idempotent)
  iptables -t nat -C POSTROUTING -o "$out_iface" -j MASQUERADE 2>/dev/null \
    || iptables -t nat -A POSTROUTING -o "$out_iface" -j MASQUERADE
  netfilter-persistent save >/dev/null 2>&1 || true
  return 0
}
