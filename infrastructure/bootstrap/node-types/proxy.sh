#!/bin/bash
# infrastructure/bootstrap/node-types/proxy.sh
#
# Sourced by bhn-node-bootstrap.sh after modules. Defines:
#   node_type_install
#
# Proxy composition: minimal Shadowsocks-only DPI-resistant entry point.
# WG peer connection is for mgmt only (not peer transit). NAT MASQUERADE
# for SS-forwarded traffic is set up by the FORWARD rule in
# policies/proxy-network-policy.conf during phase 3.
#
# No PG, no Grafana, no n8n, no Suricata — keep this lightweight and fast.

node_type_install() {
  log "Composing proxy-type install"

  setup_dnscrypt
  setup_wireguard_peer "$WG_INTERFACE" 51821 "$HUB_IP" "$HUB_PUBKEY" "$HUB_WG_PORT" "$TUNNEL_IP"
  setup_shadowsocks "$SS_PORT" "$SS_PASSWORD"
  setup_crowdsec

  ok "Proxy node services up — Shadowsocks entry on ${SS_PORT}/tcp+udp"
}
