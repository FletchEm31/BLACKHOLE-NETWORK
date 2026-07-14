#!/usr/bin/env bash
# bhn-wg1-egress.sh — wg1 alt-egress to Hillsboro OR Helsinki + full-tunnel
# client routing.
#
# Purpose: Bring up wg1 between LA and a chosen egress node, and wire
# policy routing so that traffic from full-tunnel client peers on wg0 is
# forwarded through wg1 (visible egress IP becomes that node's public IP).
#
# Supersedes bhn-wg1-hillsboro.sh (single-target, Hillsboro-only), which
# itself replaced the pre-2026-05-28 bhn-frankfurt-exit.sh. This version
# parameterizes the target so the same table-200/fwmark/CLIENT_IPS
# machinery works against either node without duplicating the script.
#
# Usage:
#   bhn-wg1-egress.sh <hillsboro|helsinki> up
#   bhn-wg1-egress.sh down                        (no target needed)
#   bhn-wg1-egress.sh status

set -euo pipefail

WG1_PRIVKEY="/etc/wireguard/wg1-private.key"
WG1_ADDR="10.10.0.1/30"
WG1_PORT=51822
TABLE=200
FWMARK_TABLE="0x200"
PRIO_FWMARK=200
PRIO_SRCIP=201
# wg0's listen port in hex — sharing this fwmark keeps wg1 underlay packets out
# of table 51820 (which wg-quick wg0 set up for AllowedIPs=0.0.0.0/0 behavior).
FWMARK_WG=51820
STATE_FILE="/etc/wireguard/wg1-current-target"

# Node lookup table: name -> "pubkey|endpoint|pskfile(or empty)"
# Pubkeys are not secret; endpoints are redacted here (see repo convention
# in the retired bhn-wg1-hillsboro.sh). Both peers carry a PSK as of
# 2026-07-14 (Helsinki added 2026-07-13/14, Hillsboro added 2026-07-14,
# closing what had been the last remaining PSK gap in the mesh).
declare -A NODES=(
    [hillsboro]="EwBHwkT4iJXzhJZMvtlo70NOLx+wPv8IXmAGSa89zBg=|<BHN_HIL_PUBLIC_IP>:51821|/etc/wireguard/wg1-hillsboro.psk"
    [helsinki]="uQZyqleD4vx4rjklp+PHo6v4AuvPN4apzKCyq4zzkDg=|<BHN_HEL_PUBLIC_IP>:51821|/etc/wireguard/wg1-helsinki.psk"
)

# Client peer IPs that should egress via wg1 when running full-tunnel profiles.
# Keep in sync with the [Peer] AllowedIPs blocks in /etc/wireguard/wg0.conf.
# Mesh-internal peers (NJ, Hillsboro, Helsinki mesh addrs) are intentionally
# excluded — they don't egress through this hub.
CLIENT_IPS=(<BHN_WG_PEER_IP> <BHN_WG_OPC_IP> <BHN_WG_PEER_IP> <BHN_WG_PEER_IP> <BHN_WG_PEER_IP>)

GREEN='\033[0;32m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'
log() { echo -e "${CYAN}[BHN-WG1]${NC} $*"; }
ok()  { echo -e "${GREEN}[OK]${NC} $*"; }
err() { echo -e "${RED}[ERR]${NC} $*" >&2; exit 1; }

up() {
    local target="$1"
    [[ -n "${NODES[$target]:-}" ]] || err "Unknown target '$target'. Valid: ${!NODES[*]}"

    IFS='|' read -r pubkey endpoint pskfile <<< "${NODES[$target]}"

    log "Bringing up wg1 ($target egress + client forward)..."

    # Tear down any prior incarnation.
    if ip link show wg1 &>/dev/null; then ip link delete wg1 || true; fi
    while ip rule show | grep -q "lookup $TABLE"; do ip rule del lookup $TABLE 2>/dev/null || break; done
    ip route flush table $TABLE 2>/dev/null || true
    # Also clear the docker-bridge exemption rule (added below) — without
    # this, re-running up() while wg1 is already up (i.e. switching targets)
    # fails with "RTNETLINK answers: File exists" on the re-add and aborts
    # under set -e, leaving table 200 with NO rules at all. Found + fixed
    # 2026-07-14 after exactly this took down full-tunnel mid-switch-test.
    ip rule del to 172.16.0.0/12 lookup main priority 100 2>/dev/null || true

    # Interface.
    ip link add wg1 type wireguard
    wg set wg1 listen-port $WG1_PORT private-key $WG1_PRIVKEY fwmark $FWMARK_WG
    if [[ -n "$pskfile" ]]; then
        wg set wg1 peer "$pubkey" allowed-ips 0.0.0.0/0 endpoint "$endpoint" persistent-keepalive 25 preshared-key "$pskfile"
    else
        wg set wg1 peer "$pubkey" allowed-ips 0.0.0.0/0 endpoint "$endpoint" persistent-keepalive 25
    fi
    ip addr add $WG1_ADDR dev wg1
    ip link set wg1 up

    # Routing table 200.
    ip route add default       dev wg1 table $TABLE
    ip route add 10.8.0.0/24   dev wg0 table $TABLE
    ip route add 10.10.0.0/30  dev wg1 table $TABLE

    # Docker bridge exemption: without this, DNAT'd traffic to any
    # docker-published service (e.g. Homarr) gets swallowed by the
    # source-IP rules below and shipped out wg1 instead of being delivered
    # locally to docker0/br-*. Must outrank both the fwmark and source-IP
    # rules (lower priority number = higher precedence). Found + fixed
    # 2026-07-01.
    ip rule add to 172.16.0.0/12 lookup main priority 100

    # Source-IP rules: full-tunnel clients + wg1 self -> table 200.
    # fwmark 0x200 rule kept for ad-hoc marking (legacy interface).
    ip rule add fwmark $FWMARK_TABLE lookup $TABLE priority $PRIO_FWMARK
    for cip in "${CLIENT_IPS[@]}"; do
        ip rule add from $cip lookup $TABLE priority $PRIO_SRCIP
    done
    ip rule add from 10.10.0.0/30 lookup $TABLE priority $PRIO_SRCIP

    # iptables wiring.
    iptables -C FORWARD -i wg0 -o wg1 -j ACCEPT 2>/dev/null || iptables -I FORWARD -i wg0 -o wg1 -j ACCEPT
    iptables -C FORWARD -i wg1 -o wg0 -j ACCEPT 2>/dev/null || iptables -I FORWARD -i wg1 -o wg0 -j ACCEPT
    iptables -C OUTPUT  -o wg1 -j ACCEPT          2>/dev/null || iptables -I OUTPUT  -o wg1 -j ACCEPT
    iptables -t nat -C POSTROUTING -o wg1 -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o wg1 -j MASQUERADE
    # MSS clamp so forwarded TCP sessions account for wg1 MTU.
    iptables -t mangle -C FORWARD -o wg1 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || \
        iptables -t mangle -A FORWARD -o wg1 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

    echo "$target" > "$STATE_FILE"

    ok "wg1 up, egressing via $target; waiting 3s for handshake"
    sleep 3
    wg show wg1 | grep -E 'handshake|transfer' || true
}

down() {
    log "Bringing down wg1..."
    # iptables.
    iptables -D FORWARD -i wg0 -o wg1 -j ACCEPT 2>/dev/null || true
    iptables -D FORWARD -i wg1 -o wg0 -j ACCEPT 2>/dev/null || true
    iptables -D OUTPUT  -o wg1 -j ACCEPT 2>/dev/null || true
    iptables -t nat -D POSTROUTING -o wg1 -j MASQUERADE 2>/dev/null || true
    iptables -t mangle -D FORWARD -o wg1 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || true
    # rules + table + iface.
    ip rule del to 172.16.0.0/12 lookup main priority 100 2>/dev/null || true
    while ip rule show | grep -q "lookup $TABLE"; do ip rule del lookup $TABLE 2>/dev/null || break; done
    ip route flush table $TABLE 2>/dev/null || true
    ip link delete wg1 2>/dev/null || true
    rm -f "$STATE_FILE"
    ok "wg1 down"
}

status() {
    echo "=== current target ==="
    cat "$STATE_FILE" 2>/dev/null || echo "(none — wg1 not up via this script)"
    echo
    echo "=== wg1 interface ==="
    wg show wg1 2>/dev/null || echo "wg1 not running"
    echo
    echo "=== table $TABLE ==="
    ip route show table $TABLE 2>/dev/null || echo "(empty)"
    echo
    echo "=== ip rules (filtered) ==="
    ip rule show | grep -E "$TABLE|$FWMARK_TABLE" || echo "(none)"
    echo
    echo "=== iptables FORWARD wg1 ==="
    iptables -L FORWARD -n -v | grep wg1 || echo "(none)"
    echo "=== iptables nat POSTROUTING wg1 ==="
    iptables -t nat -L POSTROUTING -n -v | grep wg1 || echo "(none)"
}

case "${1:-}" in
    status) status ;;
    down)   down ;;
    hillsboro|helsinki)
        [[ "${2:-}" == "up" ]] || err "Usage: $0 <hillsboro|helsinki> up"
        up "$1"
        ;;
    *)      echo "Usage: $0 <hillsboro|helsinki> up, or: $0 down, or: $0 status"; exit 1 ;;
esac
