#!/bin/bash
# infrastructure/bootstrap/modules/wireguard.sh
#
# Sourced by bhn-node-bootstrap.sh. Provides:
#   setup_wireguard_install                          (apt + key gen)
#   setup_wireguard_hub <interface> <listen_port>    (server-side)
#   setup_wireguard_peer <interface> <listen_port> <hub_ip> <hub_pubkey> \
#                        <hub_listen_port> <tunnel_ip> <tunnel_cidr_bits>
#
# Notes:
#   - Keys live in /etc/wireguard/{private,public}.key (mode 600/700).
#   - Hub config has no [Peer] entries — peers are added later via `wg set` or
#     by the operator running the registration command printed at end of
#     bootstrap.
#   - The down/up dance after writing config is required: wg-quick up is a
#     no-op if the interface is already up, so without down/up the kernel
#     keeps running the OLD config. (Bit us on FRA's first install.)

setup_wireguard_install() {
  log "Installing WireGuard"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wireguard wireguard-tools
  mkdir -p /etc/wireguard && chmod 700 /etc/wireguard
  if [[ ! -f /etc/wireguard/private.key ]]; then
    (umask 077 && wg genkey | tee /etc/wireguard/private.key | wg pubkey >/etc/wireguard/public.key)
    chmod 600 /etc/wireguard/private.key
    ok "WireGuard keys generated"
  else
    ok "WireGuard keys already present"
  fi
}

setup_wireguard_hub() {
  local iface="$1" listen_port="$2"
  setup_wireguard_install

  local privkey hub_tunnel_ip
  privkey="$(cat /etc/wireguard/private.key)"
  # Hub takes .1 in its tunnel /24
  hub_tunnel_ip="$TUNNEL_IP"

  cat >"/etc/wireguard/${iface}.conf" <<EOF
[Interface]
PrivateKey = ${privkey}
Address    = ${hub_tunnel_ip}/24
ListenPort = ${listen_port}
SaveConfig = true

# Peers added live via:  wg set ${iface} peer <PUBKEY> allowed-ips <TUNNEL_IP>/32 ...
#                        wg-quick save ${iface}
EOF
  chmod 600 "/etc/wireguard/${iface}.conf"

  # Enable IP forwarding
  grep -q "^net.ipv4.ip_forward=1" /etc/sysctl.conf || echo "net.ipv4.ip_forward=1" >>/etc/sysctl.conf
  sysctl -p -q

  systemctl enable "wg-quick@${iface}" >/dev/null
  wg-quick down "${iface}" 2>/dev/null || true
  wg-quick up "${iface}"
  ok "WireGuard hub up on ${iface} (${hub_tunnel_ip}/24, listen ${listen_port})"
}

setup_wireguard_peer() {
  local iface="$1" listen_port="$2" hub_ip="$3" hub_pubkey="$4" \
        hub_listen_port="$5" tunnel_ip="$6"
  setup_wireguard_install

  local privkey
  privkey="$(cat /etc/wireguard/private.key)"

  # Allowed-ips for the hub peer covers both the hub-tunnel /24 (so admin
  # plane reaches every WG peer through the hub) and FRA's /24 if applicable.
  # Default to the hub's tunnel network passed via TUNNEL_NETWORK.
  cat >"/etc/wireguard/${iface}.conf" <<EOF
[Interface]
PrivateKey = ${privkey}
Address    = ${tunnel_ip}/30
ListenPort = ${listen_port}

[Peer]
PublicKey  = ${hub_pubkey}
Endpoint   = ${hub_ip}:${hub_listen_port}
AllowedIPs = 10.8.0.0/24
PersistentKeepalive = 25
EOF
  chmod 600 "/etc/wireguard/${iface}.conf"

  grep -q "^net.ipv4.ip_forward=1" /etc/sysctl.conf || echo "net.ipv4.ip_forward=1" >>/etc/sysctl.conf
  sysctl -p -q

  systemctl enable "wg-quick@${iface}" >/dev/null
  wg-quick down "${iface}" 2>/dev/null || true
  wg-quick up "${iface}"
  ok "WireGuard peer up on ${iface} (${tunnel_ip}/30 → ${hub_ip}:${hub_listen_port})"
}
