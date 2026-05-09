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
  # The systemd unit runs ss-server as User=nobody Group=nogroup. Mode 0600
  # root:root makes the file unreadable by nobody → "Invalid config path"
  # error and service failure (caught during 2026-05-09 Toronto smoke test).
  # 0640 root:nogroup keeps the password unreadable to other non-root users
  # but lets the SS daemon read it.
  chmod 640 /etc/shadowsocks-libev/config.json
  chown root:nogroup /etc/shadowsocks-libev/config.json

  systemctl enable shadowsocks-libev >/dev/null
  systemctl restart shadowsocks-libev
  ok "Shadowsocks active on ${port}/tcp+udp"
}
