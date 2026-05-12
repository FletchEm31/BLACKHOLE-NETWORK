#!/bin/bash
# infrastructure/bootstrap/modules/firewall.sh
#
# Sourced by bhn-node-bootstrap.sh. Provides:
#   setup_firewall_open_window
#
# Phase 1 firewall — admin window for the install. Wide enough that apt,
# certbot, dnscrypt, and any node-type install can run unblocked. Tightened
# in phase 3 by network-policy.sh.

setup_firewall_open_window() {
  log "Configuring open-window firewall (phase 1)"
  # ufw covers itself; netfilter-persistent only needed on Ubuntu ≤22.04
  # (24.04+ declares ufw Breaks: netfilter-persistent — see master notes).
  local extra_pkgs=()
  . /etc/os-release
  [[ "$VERSION_ID" =~ ^(20\.04|22\.04)$ ]] && extra_pkgs+=(netfilter-persistent)
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ufw "${extra_pkgs[@]}"

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
  # Persist only on Ubuntu ≤22.04 (where netfilter-persistent is installable
  # alongside ufw). On 24.04+ this rule won't survive reboot until we migrate
  # to UFW before.rules — captured as known limitation for exit/scan/hub on 24.04.
  command -v netfilter-persistent >/dev/null && netfilter-persistent save >/dev/null 2>&1 || true

  ok "Phase 1 firewall: ingress 22/80/443, egress allow-all (temporary)"
}
