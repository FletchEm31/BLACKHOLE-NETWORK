#!/bin/bash
# infrastructure/bootstrap/node-types/scan.sh
#
# Sourced by eh-node-bootstrap.sh after modules. Defines:
#   node_type_install
#
# Scan composition: passive sensor — heavy Suricata IDS + node_exporter +
# minimal WG peer connection back to hub. Findings ship to hub PG over the
# tunnel (operator wires up shippers like vector / promtail / custom pipelines
# post-bootstrap). No public-facing services beyond SSH.

node_type_install() {
  log "Composing scan-type install"

  setup_dnscrypt
  setup_wireguard_peer "$WG_INTERFACE" 51821 "$HUB_IP" "$HUB_PUBKEY" "$HUB_WG_PORT" "$TUNNEL_IP"
  setup_crowdsec
  setup_suricata   # always on for scan nodes — that's the point

  # node_exporter for Prometheus / Grafana scrape from hub
  log "Installing prometheus-node-exporter"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq prometheus-node-exporter
  # Bind to the tunnel IP only — VPN-only metrics scraping
  sed -i "s|^ARGS=.*|ARGS=\"--web.listen-address=${TUNNEL_IP}:9090\"|" \
    /etc/default/prometheus-node-exporter 2>/dev/null || true
  systemctl enable --now prometheus-node-exporter >/dev/null
  ok "node_exporter listening on ${TUNNEL_IP}:9090"

  ok "Scan node services up — wire up shippers to hub PG post-bootstrap"
}
