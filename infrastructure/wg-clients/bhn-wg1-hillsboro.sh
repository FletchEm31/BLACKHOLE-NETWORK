#!/usr/bin/env bash
# bhn-wg1-hillsboro.sh — wg1 alt-egress to Hillsboro + full-tunnel client routing
#
# Purpose: Bring up wg1 between LA and Hillsboro AND wire policy routing so
# that traffic from full-tunnel client peers on wg0 is forwarded through wg1
# (egress IP becomes Hillsboro <BHN_HIL_PUBLIC_IP>).
#
# Replaces the pre-2026-05-28 bhn-frankfurt-exit.sh which routed full-tunnel
# clients via Frankfurt (decommissioned 2026-05-28).
#
# Usage: bash bhn-wg1-hillsboro.sh [up|down|status]

set -euo pipefail

WG1_PRIVKEY="/etc/wireguard/wg1-private.key"
WG1_PUBKEY_HIL="EwBHwkT4iJXzhJZMvtlo70NOLx+wPv8IXmAGSa89zBg="
HIL_ENDPOINT="<BHN_HIL_PUBLIC_IP>:51821"
WG1_ADDR="10.10.0.1/30"
WG1_PORT=51822
TABLE=200
FWMARK_TABLE="0x200"
PRIO_FWMARK=200
PRIO_SRCIP=201
# wg0's listen port in hex — sharing this fwmark keeps wg1 underlay packets out
# of table 51820 (which wg-quick wg0 set up for AllowedIPs=0.0.0.0/0 behavior).
FWMARK_WG=51820

# Client peer IPs that should egress via wg1 when running full-tunnel profiles.
# Keep in sync with the [Peer] AllowedIPs blocks in /etc/wireguard/wg0.conf.
# Mesh-internal peers (NJ <BHN_WG_NJ_IP>, Hillsboro <BHN_WG_HIL_IP>) are intentionally
# excluded — they don't egress through this hub.
CLIENT_IPS=(<BHN_WG_PEER_IP> <BHN_WG_OPC_IP> <BHN_WG_PEER_IP> <BHN_WG_PEER_IP> <BHN_WG_PEER_IP>)

GREEN='\033[0;32m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'
log() { echo -e "${CYAN}[BHN-WG1]${NC} $*"; }
ok()  { echo -e "${GREEN}[OK]${NC} $*"; }
err() { echo -e "${RED}[ERR]${NC} $*" >&2; exit 1; }

up() {
    log "Bringing up wg1 (Hillsboro egress + client forward)..."

    # Tear down any prior incarnation.
    if ip link show wg1 &>/dev/null; then ip link delete wg1 || true; fi
    while ip rule show | grep -q "lookup $TABLE"; do ip rule del lookup $TABLE 2>/dev/null || break; done
    ip route flush table $TABLE 2>/dev/null || true

    # Interface.
    ip link add wg1 type wireguard
    wg set wg1 listen-port $WG1_PORT private-key $WG1_PRIVKEY fwmark $FWMARK_WG
    wg set wg1 peer $WG1_PUBKEY_HIL         allowed-ips 0.0.0.0/0         endpoint $HIL_ENDPOINT         persistent-keepalive 25
    ip addr add $WG1_ADDR dev wg1
    ip link set wg1 up

    # Routing table 200.
    ip route add default       dev wg1 table $TABLE
    ip route add 10.8.0.0/24   dev wg0 table $TABLE
    ip route add 10.10.0.0/30  dev wg1 table $TABLE

    # Source-IP rules: full-tunnel clients + wg1 self → table 200.
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
    iptables -t mangle -C FORWARD -o wg1 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null ||         iptables -t mangle -A FORWARD -o wg1 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

    ok "wg1 up; waiting 3s for handshake"
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
    while ip rule show | grep -q "lookup $TABLE"; do ip rule del lookup $TABLE 2>/dev/null || break; done
    ip route flush table $TABLE 2>/dev/null || true
    ip link delete wg1 2>/dev/null || true
    ok "wg1 down"
}

status() {
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
    up)     up ;;
    down)   down ;;
    status) status ;;
    *)      echo "Usage: $0 [up|down|status]"; exit 1 ;;
esac
