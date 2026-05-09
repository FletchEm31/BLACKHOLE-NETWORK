#!/bin/bash
# infrastructure/bootstrap/modules/firewall.sh
#
# Sourced by eh-node-bootstrap.sh. Provides:
#   setup_firewall_open_window
#
# Phase 1 firewall — admin window for the install. Wide enough that apt,
# certbot, dnscrypt, and any node-type install can run unblocked. Tightened
# in phase 3 by network-policy.sh.

setup_firewall_open_window() {
  log "Configuring open-window firewall (phase 1)"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ufw iptables-persistent

  ufw --force reset >/dev/null
  ufw default deny incoming  >/dev/null
  ufw default allow outgoing >/dev/null
  ufw default allow routed   >/dev/null

  # Minimum admin ingress — enough for the operator to keep an SSH session
  # open during install, and for certbot HTTP-01 challenge if needed.
  ufw allow 22/tcp                    >/dev/null
  ufw allow 80/tcp                    >/dev/null   # HTTP-01 challenge / landing
  ufw allow 443/tcp                   >/dev/null   # public HTTPS
  ufw --force enable                  >/dev/null

  # Defense-in-depth: if UFW gets reset later in install, an iptables ACCEPT
  # at INPUT position 1 keeps SSH reachable from the WG tunnel (once it's up).
  iptables -C INPUT -s "${TUNNEL_NETWORK}" -p tcp --dport 22 -j ACCEPT 2>/dev/null \
    || iptables -I INPUT 1 -s "${TUNNEL_NETWORK}" -p tcp --dport 22 -j ACCEPT
  netfilter-persistent save >/dev/null

  ok "Phase 1 firewall: ingress 22/80/443, egress allow-all (temporary)"
}
