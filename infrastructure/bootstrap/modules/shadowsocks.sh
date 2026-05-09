#!/bin/bash
# infrastructure/bootstrap/modules/shadowsocks.sh
#
# Sourced by eh-node-bootstrap.sh. Provides:
#   setup_shadowsocks <port> <password>
#
# Installs shadowsocks-libev with chacha20-ietf-poly1305 + FastOpen + UDP relay.

setup_shadowsocks() {
  local port="$1" password="$2"
  log "Installing Shadowsocks (port ${port})"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq shadowsocks-libev

  cat >/etc/shadowsocks-libev/config.json <<EOF
{
    "server": "0.0.0.0",
    "server_port": ${port},
    "password": "${password}",
    "timeout": 300,
    "method": "chacha20-ietf-poly1305",
    "fast_open": true,
    "mode": "tcp_and_udp"
}
EOF
  chmod 600 /etc/shadowsocks-libev/config.json

  systemctl enable shadowsocks-libev >/dev/null
  systemctl restart shadowsocks-libev
  ok "Shadowsocks active on ${port}/tcp+udp"
}
