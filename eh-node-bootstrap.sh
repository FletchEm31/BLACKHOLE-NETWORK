#!/bin/bash
# ╔══════════════════════════════════════════════════════════╗
# ║         EVENTHORIZON — NODE BOOTSTRAP SCRIPT v2         ║
# ║   Auto-configures any new VPS into the EH network       ║
# ╚══════════════════════════════════════════════════════════╝
# Usage: bash eh-node-bootstrap.sh <NODE_NAME> <NODE_IP> <WG_INTERFACE>
# Example: bash eh-node-bootstrap.sh EH-NYC-US2 123.456.789.0 wg2

set -e

NODE_NAME=${1:-"EH-NODE"}
NODE_IP=${2:-""}
WG_INTERFACE=${3:-"wg1"}

LA_IP="149.28.91.100"
LA_PUBKEY="TOYnFt18v4NynEN91o6zkmV5hsvHBLJTb8qL7GG/KAo="
LA_WG_PORT="51820"
SS_PASSWORD="EventHorizon2026"
SS_PORT="8388"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${CYAN}[EH]${NC} $1"; }
ok()   { echo -e "${GREEN}[✓]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }

[[ $EUID -ne 0 ]] && err "Must run as root"
[[ -z "$NODE_IP" ]] && err "Usage: bash eh-node-bootstrap.sh <NODE_NAME> <NODE_IP> <WG_INTERFACE>"

NET_IFACE=$(ip route | grep default | awk '{print $5}' | head -1)

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║       EVENTHORIZON NODE BOOTSTRAP v2         ║"
echo "║  Node: $NODE_NAME"
echo "║  IP:   $NODE_IP"
echo "║  WG:   $WG_INTERFACE"
echo "║  NIC:  $NET_IFACE"
echo "╚══════════════════════════════════════════════╝"
echo ""

log "Updating system..."
apt update -qq && apt upgrade -y -qq
ok "System updated"

log "Installing WireGuard..."
apt install -y wireguard wireguard-tools -qq
ok "WireGuard installed"

log "Generating WireGuard keys..."
mkdir -p /etc/wireguard && chmod 700 /etc/wireguard
wg genkey | tee /etc/wireguard/private.key | wg pubkey > /etc/wireguard/public.key
chmod 600 /etc/wireguard/private.key
NODE_PRIVKEY=$(cat /etc/wireguard/private.key)
NODE_PUBKEY=$(cat /etc/wireguard/public.key)
ok "Keys generated"

WG_NUM=$(echo $WG_INTERFACE | tr -dc '0-9'); WG_NUM=${WG_NUM:-1}
TUNNEL_IP="10.$((8 + WG_NUM)).0.2"

cat > /etc/wireguard/${WG_INTERFACE}.conf << EOF
[Interface]
PrivateKey = ${NODE_PRIVKEY}
Address = ${TUNNEL_IP}/30
ListenPort = 51821

[Peer]
PublicKey = ${LA_PUBKEY}
Endpoint = ${LA_IP}:${LA_WG_PORT}
AllowedIPs = 10.8.0.0/24
PersistentKeepalive = 25
EOF
chmod 600 /etc/wireguard/${WG_INTERFACE}.conf
ok "WireGuard config written — Tunnel IP: $TUNNEL_IP"

log "Installing Shadowsocks..."
apt install -y shadowsocks-libev -qq
cat > /etc/shadowsocks-libev/config.json << EOF
{"server":"0.0.0.0","server_port":${SS_PORT},"password":"${SS_PASSWORD}","timeout":300,"method":"chacha20-ietf-poly1305","fast_open":true,"mode":"tcp_and_udp"}
EOF
ok "Shadowsocks configured"

log "Installing dnscrypt-proxy..."
apt install -y dnscrypt-proxy -qq
sed -i "s/server_names = \['cloudflare'\]/server_names = ['cloudflare', 'quad9-dnscrypt-ip4-filter-pri', 'mullvad', 'nextdns']/" /etc/dnscrypt-proxy/dnscrypt-proxy.toml
sed -i "s/ListenStream=127.0.*/ListenStream=0.0.0.0:53/" /lib/systemd/system/dnscrypt-proxy.socket
sed -i "s/ListenDatagram=127.0.*/ListenDatagram=0.0.0.0:53/" /lib/systemd/system/dnscrypt-proxy.socket
systemctl disable systemd-resolved > /dev/null 2>&1 || true
systemctl stop systemd-resolved > /dev/null 2>&1 || true
systemctl daemon-reload
systemctl restart dnscrypt-proxy.socket
systemctl restart dnscrypt-proxy
ok "Encrypted DNS configured"

log "Enabling IP forwarding..."
echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf && sysctl -p -q
ok "IP forwarding enabled"

log "Configuring firewall..."
apt install -y ufw -qq
ufw --force reset > /dev/null
ufw allow 22/tcp > /dev/null
ufw allow from ${LA_IP} to any port 51821 proto udp > /dev/null
ufw allow from ${LA_IP} to any port ${SS_PORT} > /dev/null
ufw --force enable > /dev/null
ok "Firewall configured"

log "Setting up NAT masquerade..."
iptables -t nat -A POSTROUTING -o ${NET_IFACE} -j MASQUERADE
apt install -y iptables-persistent -qq
netfilter-persistent save > /dev/null
ok "NAT masquerade enabled on $NET_IFACE"

log "Starting services..."
systemctl enable wg-quick@${WG_INTERFACE} > /dev/null
wg-quick up ${WG_INTERFACE}
systemctl enable shadowsocks-libev > /dev/null && systemctl start shadowsocks-libev
ok "All services running"

hostnamectl set-hostname ${NODE_NAME}

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║              BOOTSTRAP COMPLETE ✓                        ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Node: ${NODE_NAME} | IP: ${NODE_IP} | Tunnel: ${TUNNEL_IP}/30"
echo "║  WireGuard ✓ | Shadowsocks ✓ | Encrypted DNS ✓"
echo "║  NAT on: ${NET_IFACE}"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  PUBLIC KEY: ${NODE_PUBKEY}"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  RUN ON LA:"
echo "║  wg set wg0 peer ${NODE_PUBKEY} endpoint ${NODE_IP}:51821 allowed-ips ${TUNNEL_IP}/32 persistent-keepalive 25"
echo "║  wg-quick save wg0"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
