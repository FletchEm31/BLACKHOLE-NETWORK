#!/bin/bash
# ╔══════════════════════════════════════════════════════════╗
# ║       EVENTHORIZON — NODE BOOTSTRAP SCRIPT v3            ║
# ║   Auto-configures any new VPS into the EH network        ║
# ║   Includes all security hardening from production        ║
# ╚══════════════════════════════════════════════════════════╝
#
# Usage: bash eh-node-bootstrap.sh <NODE_NAME> <NODE_IP> <WG_INTERFACE>
# Example: bash eh-node-bootstrap.sh EH-NYC-US2 123.456.789.0 wg2
#
# Optional environment variables:
#   ATTACH_NVME=/dev/vdb       Attach + LUKS encrypt + mount this device as hot tier
#   ATTACH_HDD=/dev/vdc        Attach + LUKS encrypt + mount this device as cold tier
#   ADMIN_PUBKEYS_FILE=/path   File containing SSH pubkeys to authorize (one per line)
#   SKIP_FWKNOP=1              Don't install/configure fwknop (default for v3)
#   INSTALL_POSTGRES=1         Install PostgreSQL (typically only on hub nodes)
#
# CHANGES FROM v2:
#   + Optional block storage attach with LUKS2 encryption
#   + SSH hardening: PermitRootLogin = prohibit-password (key-only)
#   + SSH host keys regenerated (safe for snapshot-based deployments)
#   + Pre-loaded admin SSH keys for immediate access
#   + Fail2ban auto-whitelists VPN tunnel range
#   + iptables ACCEPT rule for VPN→SSH at position 1
#   + Skips fwknop entirely (was causing silent SSH drops)
#   + Optional PostgreSQL install for hub nodes

set -e

# ─── Parameters ────────────────────────────────────────────────
NODE_NAME=${1:-"EH-NODE"}
NODE_IP=${2:-""}
WG_INTERFACE=${3:-"wg1"}

# ─── Constants (LA hub) ────────────────────────────────────────
LA_IP="149.28.91.100"
LA_PUBKEY="TOYnFt18v4NynEN91o6zkmV5hsvHBLJTb8qL7GG/KAo="
LA_WG_PORT="51820"
SS_PASSWORD="EventHorizon2026"
SS_PORT="8388"

# ─── Colors ────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${CYAN}[EH]${NC} $1"; }
ok()   { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }

# ─── Pre-flight checks ─────────────────────────────────────────
[[ $EUID -ne 0 ]] && err "Must run as root"
[[ -z "$NODE_IP" ]] && err "Usage: bash eh-node-bootstrap.sh <NODE_NAME> <NODE_IP> <WG_INTERFACE>"

NET_IFACE=$(ip route | grep default | awk '{print $5}' | head -1)
WG_NUM=$(echo $WG_INTERFACE | tr -dc '0-9'); WG_NUM=${WG_NUM:-1}
TUNNEL_IP="10.$((8 + WG_NUM)).0.2"
TUNNEL_NETWORK="10.$((8 + WG_NUM)).0.0/24"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║       EVENTHORIZON NODE BOOTSTRAP v3         ║"
echo "║  Node:    $NODE_NAME"
echo "║  IP:      $NODE_IP"
echo "║  WG:      $WG_INTERFACE"
echo "║  Tunnel:  $TUNNEL_IP/30"
echo "║  NIC:     $NET_IFACE"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ─── 1. System update ──────────────────────────────────────────
log "Updating system..."
apt update -qq && apt upgrade -y -qq
apt install -y curl wget gnupg2 ca-certificates lsb-release software-properties-common -qq
ok "System updated"

# ─── 2. Optional: Block storage encryption + mount ─────────────
if [[ -n "$ATTACH_NVME" ]] && [[ -b "$ATTACH_NVME" ]]; then
    log "Setting up encrypted NVMe at $ATTACH_NVME..."
    apt install -y cryptsetup xfsprogs -qq

    # Generate LUKS keyfile
    NVME_KEYFILE="/root/.luks-eh-nvme"
    if [[ ! -f "$NVME_KEYFILE" ]]; then
        dd if=/dev/urandom of="$NVME_KEYFILE" bs=512 count=8 2>/dev/null
        chmod 600 "$NVME_KEYFILE"
    fi

    # Format only if not already LUKS
    if ! cryptsetup isLuks "$ATTACH_NVME" 2>/dev/null; then
        warn "Formatting $ATTACH_NVME with LUKS2 (this destroys existing data)"
        cryptsetup luksFormat --type luks2 --batch-mode --key-file="$NVME_KEYFILE" "$ATTACH_NVME"
    fi

    # Open + format XFS + mount
    cryptsetup open "$ATTACH_NVME" eh-nvme --key-file="$NVME_KEYFILE" 2>/dev/null || true
    blkid /dev/mapper/eh-nvme | grep -q xfs || mkfs.xfs -f /dev/mapper/eh-nvme
    mkdir -p /mnt/eh-nvme-hot
    mountpoint -q /mnt/eh-nvme-hot || mount -o noatime,nodiratime /dev/mapper/eh-nvme /mnt/eh-nvme-hot

    # Persistence
    NVME_UUID=$(blkid -s UUID -o value "$ATTACH_NVME")
    grep -q "eh-nvme" /etc/crypttab || echo "eh-nvme UUID=${NVME_UUID} ${NVME_KEYFILE} luks" >> /etc/crypttab
    grep -q "eh-nvme-hot" /etc/fstab || echo "/dev/mapper/eh-nvme /mnt/eh-nvme-hot xfs defaults,noatime,nodiratime,nofail 0 0" >> /etc/fstab

    # Standard subdirectories
    mkdir -p /mnt/eh-nvme-hot/{postgres,pcap,logs,grafana}
    ok "NVMe encrypted hot tier mounted at /mnt/eh-nvme-hot"
fi

if [[ -n "$ATTACH_HDD" ]] && [[ -b "$ATTACH_HDD" ]]; then
    log "Setting up encrypted HDD at $ATTACH_HDD..."

    HDD_KEYFILE="/root/.luks-eh-hdd"
    if [[ ! -f "$HDD_KEYFILE" ]]; then
        dd if=/dev/urandom of="$HDD_KEYFILE" bs=512 count=8 2>/dev/null
        chmod 600 "$HDD_KEYFILE"
    fi

    if ! cryptsetup isLuks "$ATTACH_HDD" 2>/dev/null; then
        warn "Formatting $ATTACH_HDD with LUKS2 (this destroys existing data)"
        cryptsetup luksFormat --type luks2 --batch-mode --key-file="$HDD_KEYFILE" "$ATTACH_HDD"
    fi

    cryptsetup open "$ATTACH_HDD" eh-hdd --key-file="$HDD_KEYFILE" 2>/dev/null || true
    blkid /dev/mapper/eh-hdd | grep -q xfs || mkfs.xfs -f /dev/mapper/eh-hdd
    mkdir -p /mnt/eh-hdd-cold
    mountpoint -q /mnt/eh-hdd-cold || mount -o noatime,nodiratime /dev/mapper/eh-hdd /mnt/eh-hdd-cold

    HDD_UUID=$(blkid -s UUID -o value "$ATTACH_HDD")
    grep -q "eh-hdd" /etc/crypttab || echo "eh-hdd UUID=${HDD_UUID} ${HDD_KEYFILE} luks" >> /etc/crypttab
    grep -q "eh-hdd-cold" /etc/fstab || echo "/dev/mapper/eh-hdd /mnt/eh-hdd-cold xfs defaults,noatime,nodiratime,nofail 0 0" >> /etc/fstab

    mkdir -p /mnt/eh-hdd-cold/{archives/{postgres,pcap,logs},snapshots,reports}
    ok "HDD encrypted cold tier mounted at /mnt/eh-hdd-cold"
fi

# ─── 3. WireGuard ──────────────────────────────────────────────
log "Installing WireGuard..."
apt install -y wireguard wireguard-tools -qq
ok "WireGuard installed"

log "Generating WireGuard keys..."
mkdir -p /etc/wireguard && chmod 700 /etc/wireguard
if [[ ! -f /etc/wireguard/private.key ]]; then
    wg genkey | tee /etc/wireguard/private.key | wg pubkey > /etc/wireguard/public.key
    chmod 600 /etc/wireguard/private.key
fi
NODE_PRIVKEY=$(cat /etc/wireguard/private.key)
NODE_PUBKEY=$(cat /etc/wireguard/public.key)
ok "Keys generated"

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
ok "WireGuard config written"

# ─── 4. Shadowsocks ────────────────────────────────────────────
log "Installing Shadowsocks..."
apt install -y shadowsocks-libev -qq
cat > /etc/shadowsocks-libev/config.json << EOF
{"server":"0.0.0.0","server_port":${SS_PORT},"password":"${SS_PASSWORD}","timeout":300,"method":"chacha20-ietf-poly1305","fast_open":true,"mode":"tcp_and_udp"}
EOF
ok "Shadowsocks configured"

# ─── 5. Encrypted DNS ──────────────────────────────────────────
log "Installing dnscrypt-proxy..."
apt install -y dnscrypt-proxy -qq
sed -i "s/server_names = \['cloudflare'\]/server_names = ['cloudflare', 'quad9-dnscrypt-ip4-filter-pri', 'mullvad', 'nextdns']/" /etc/dnscrypt-proxy/dnscrypt-proxy.toml || true
sed -i "s/ListenStream=127.0.*/ListenStream=0.0.0.0:53/" /lib/systemd/system/dnscrypt-proxy.socket
sed -i "s/ListenDatagram=127.0.*/ListenDatagram=0.0.0.0:53/" /lib/systemd/system/dnscrypt-proxy.socket
systemctl disable systemd-resolved > /dev/null 2>&1 || true
systemctl stop systemd-resolved > /dev/null 2>&1 || true
systemctl daemon-reload
systemctl restart dnscrypt-proxy.socket
systemctl restart dnscrypt-proxy

# Point /etc/resolv.conf at the local dnscrypt-proxy and lock it so resolvconf
# can't restore an invalid `nameserver 0.0.0.0` (which Node.js / nodemailer
# query directly and time out on, even though dig falls back to 127.0.0.1).
chattr -i /etc/resolv.conf 2>/dev/null || true
cat > /etc/resolv.conf << 'RESOLVCONF'
# Locked. dnscrypt-proxy on 127.0.0.1 — fans out to Cloudflare/Quad9/Mullvad/NextDNS.
nameserver 127.0.0.1
options edns0 timeout:2 attempts:2
RESOLVCONF
chattr +i /etc/resolv.conf

ok "Encrypted DNS configured"

# ─── 6. IP forwarding ──────────────────────────────────────────
log "Enabling IP forwarding..."
grep -q "^net.ipv4.ip_forward=1" /etc/sysctl.conf || echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
sysctl -p -q
ok "IP forwarding enabled"

# ─── 7. SSH hardening (NEW in v3) ──────────────────────────────
log "Hardening SSH..."

# Regenerate host keys (critical for snapshot-deployed nodes)
rm -f /etc/ssh/ssh_host_*
dpkg-reconfigure openssh-server > /dev/null 2>&1
ok "SSH host keys regenerated"

# Set PermitRootLogin to key-only
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config

# Disable password auth in any conf.d overrides
for f in /etc/ssh/sshd_config.d/*.conf; do
    [[ -f "$f" ]] && sed -i 's/^PasswordAuthentication yes/PasswordAuthentication no/' "$f"
done
ok "SSH hardened: root via key only, passwords disabled"

# Pre-load admin SSH keys
mkdir -p /root/.ssh && chmod 700 /root/.ssh
touch /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys

if [[ -n "$ADMIN_PUBKEYS_FILE" ]] && [[ -f "$ADMIN_PUBKEYS_FILE" ]]; then
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        grep -qF "$line" /root/.ssh/authorized_keys || echo "$line" >> /root/.ssh/authorized_keys
    done < "$ADMIN_PUBKEYS_FILE"
    ok "Admin SSH keys loaded from $ADMIN_PUBKEYS_FILE"
else
    # Hardcoded fallback - the operator's primary key
    FLETCH_KEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKi16AATgU6NMKiTPBi4XVRt/BZr1/jrEPG0F6qPTecw fletch-desktop"
    grep -qF "$FLETCH_KEY" /root/.ssh/authorized_keys || echo "$FLETCH_KEY" >> /root/.ssh/authorized_keys
    ok "Default admin SSH key loaded (fletch-desktop)"
fi

systemctl restart sshd
ok "SSH service restarted with new config"

# ─── 8. Firewall (UFW + iptables) ──────────────────────────────
log "Configuring firewall..."
apt install -y ufw iptables-persistent -qq

ufw --force reset > /dev/null
ufw allow 22/tcp > /dev/null
ufw allow from ${LA_IP} to any port 51821 proto udp > /dev/null
ufw allow from ${LA_IP} to any port ${SS_PORT} > /dev/null

# Allow VPN tunnel admin access on common admin ports
ufw allow from ${TUNNEL_NETWORK} to any port 22 proto tcp > /dev/null
ufw allow from ${TUNNEL_NETWORK} to any port 53 proto udp > /dev/null  # dnscrypt-proxy
ufw allow from ${TUNNEL_NETWORK} to any port 53 proto tcp > /dev/null  # dnscrypt-proxy (TCP fallback)
ufw allow from ${TUNNEL_NETWORK} to any port 3000 proto tcp > /dev/null  # Grafana
ufw allow from ${TUNNEL_NETWORK} to any port 5432 proto tcp > /dev/null  # PostgreSQL

ufw --force enable > /dev/null

# Allow forwarded traffic from the WG tunnel out to the public NIC. Without
# this, UFW's default FORWARD policy (DROP) silently drops legitimate VPN
# egress — most browsing survives via conntrack ESTABLISHED tracking but
# every new connection's first SYN gets logged-and-dropped, polluting the
# event stream with thousands of bogus "ufw_block" entries from the
# operator's own tunnel IP.
ufw route allow in on ${WG_INTERFACE} out on ${NET_IFACE} > /dev/null

ok "UFW firewall configured"

# Insert iptables ACCEPT rule for VPN→SSH (defense in depth, position 1)
iptables -C INPUT -s ${TUNNEL_NETWORK} -p tcp --dport 22 -j ACCEPT 2>/dev/null || \
    iptables -I INPUT 1 -s ${TUNNEL_NETWORK} -p tcp --dport 22 -j ACCEPT
ok "iptables: VPN tunnel SSH access guaranteed"

# ─── 9. NAT masquerade ─────────────────────────────────────────
log "Setting up NAT masquerade..."
iptables -t nat -C POSTROUTING -o ${NET_IFACE} -j MASQUERADE 2>/dev/null || \
    iptables -t nat -A POSTROUTING -o ${NET_IFACE} -j MASQUERADE
netfilter-persistent save > /dev/null
ok "NAT masquerade enabled on $NET_IFACE"

# ─── 10. Fail2ban with VPN whitelist ───────────────────────────
log "Configuring Fail2ban..."
apt install -y fail2ban -qq

# Ensure jail.local exists with VPN whitelist
cat > /etc/fail2ban/jail.local << EOF
[DEFAULT]
ignoreip = 127.0.0.1/8 ::1 ${TUNNEL_NETWORK} 10.8.0.0/24
bantime = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true
port = 22
filter = sshd
logpath = /var/log/auth.log
maxretry = 3
EOF

systemctl restart fail2ban
ok "Fail2ban configured with VPN whitelist"

# ─── 11. Optional: PostgreSQL (hub-only) ───────────────────────
if [[ "$INSTALL_POSTGRES" == "1" ]]; then
    log "Installing PostgreSQL..."
    apt install -y postgresql postgresql-contrib -qq

    # If NVMe is mounted, move data dir there
    if mountpoint -q /mnt/eh-nvme-hot; then
        log "Moving PostgreSQL data dir to encrypted NVMe..."
        systemctl stop postgresql
        rsync -aHAX /var/lib/postgresql/14/main/ /mnt/eh-nvme-hot/postgres/ 2>/dev/null || true
        chown -R postgres:postgres /mnt/eh-nvme-hot/postgres
        chmod 700 /mnt/eh-nvme-hot/postgres
        [[ -d /var/lib/postgresql/14/main ]] && mv /var/lib/postgresql/14/main /var/lib/postgresql/14/main.OLD
        sed -i "s|^data_directory.*|data_directory = '/mnt/eh-nvme-hot/postgres'|" /etc/postgresql/14/main/postgresql.conf
        systemctl start postgresql
        ok "PostgreSQL on encrypted NVMe"
    else
        ok "PostgreSQL installed (default location)"
    fi
fi

# ─── 12. Start services ────────────────────────────────────────
log "Starting services..."
systemctl enable wg-quick@${WG_INTERFACE} > /dev/null
# Restart cleanly: if the interface is already up from a prior install/bootstrap,
# `wg-quick up` is a no-op and the kernel keeps running the OLD config — which can
# leave the disk config and the live config diverged. Take it down then up so the
# fresh /etc/wireguard/${WG_INTERFACE}.conf actually loads.
wg-quick down ${WG_INTERFACE} 2>/dev/null || true
wg-quick up ${WG_INTERFACE} 2>/dev/null || true
systemctl enable shadowsocks-libev > /dev/null && systemctl start shadowsocks-libev
ok "All services running"

# ─── 13. Hostname ──────────────────────────────────────────────
hostnamectl set-hostname ${NODE_NAME}

# ─── Summary ───────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║              BOOTSTRAP v3 COMPLETE ✓                     ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Node:     ${NODE_NAME}"
echo "║  IP:       ${NODE_IP}"
echo "║  Tunnel:   ${TUNNEL_IP}/30"
echo "║  Storage:  $([ -d /mnt/eh-nvme-hot ] && echo "NVMe encrypted ✓" || echo "no NVMe")"
echo "║            $([ -d /mnt/eh-hdd-cold ] && echo "HDD encrypted ✓" || echo "no HDD")"
echo "║  Services: WireGuard ✓ Shadowsocks ✓ Encrypted DNS ✓"
echo "║            Fail2ban ✓ UFW ✓ NAT ✓"
echo "║  SSH:      Key-only root login, passwords disabled"
echo "║  NIC:      ${NET_IFACE}"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  PUBLIC KEY (give to LA hub):"
echo "║  ${NODE_PUBKEY}"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  RUN ON LA HUB to register this peer:"
echo "║  wg set wg0 peer ${NODE_PUBKEY} \\"
echo "║    endpoint ${NODE_IP}:51821 \\"
echo "║    allowed-ips ${TUNNEL_IP}/32 \\"
echo "║    persistent-keepalive 25"
echo "║  wg-quick save wg0"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
