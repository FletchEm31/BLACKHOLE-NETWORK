#!/bin/bash
# infrastructure/bootstrap/node-types/exit.sh
#
# Sourced by eh-node-bootstrap.sh after modules. Defines:
#   node_type_install
#
# Exit composition: WG peer to hub + Shadowsocks + dnscrypt-proxy + CrowdSec
# + (optional) Suricata. NAT MASQUERADE for peer transit is set up by the
# FORWARD rule in policies/exit-network-policy.conf during phase 3.
#
# No PostgreSQL, no Grafana, no n8n — exit nodes are pure egress points.

node_type_install() {
  log "Composing exit-type install"

  setup_dnscrypt
  setup_wireguard_peer "$WG_INTERFACE" 51821 "$HUB_IP" "$HUB_PUBKEY" "$HUB_WG_PORT" "$TUNNEL_IP"
  setup_shadowsocks "$SS_PORT" "$SS_PASSWORD"
  setup_crowdsec
  [[ "$INSTALL_SURICATA" == "1" ]] && setup_suricata

  ok "Exit node services up — peer registration command will be printed in summary"
}
