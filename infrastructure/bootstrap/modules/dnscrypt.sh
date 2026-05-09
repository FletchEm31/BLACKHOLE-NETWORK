#!/bin/bash
# infrastructure/bootstrap/modules/dnscrypt.sh
#
# Sourced by eh-node-bootstrap.sh. Provides:
#   setup_dnscrypt
#
# Installs dnscrypt-proxy with multiple upstream resolvers (Cloudflare, Quad9,
# Mullvad, NextDNS, Digitale Gesellschaft, AdGuard). Listens on 0.0.0.0:53,
# replaces systemd-resolved, locks /etc/resolv.conf to 127.0.0.1.
#
# The chattr +i lock on resolv.conf is intentional: resolvconf has been
# observed restoring `nameserver 0.0.0.0` (from dnscrypt-proxy's bind address),
# which Node.js / nodemailer query directly and time out on.

setup_dnscrypt() {
  log "Installing dnscrypt-proxy"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq dnscrypt-proxy

  # Multiple resolvers — load-balanced + fault tolerant
  sed -i \
    "s/server_names = \['cloudflare'\]/server_names = ['cloudflare', 'quad9-dnscrypt-ip4-filter-pri', 'mullvad', 'adguard', 'nextdns', 'digitale-gesellschaft']/" \
    /etc/dnscrypt-proxy/dnscrypt-proxy.toml || true

  # Bind socket to all interfaces (so VPN clients can use it via the tunnel)
  sed -i "s/ListenStream=127.0.*/ListenStream=0.0.0.0:53/" /lib/systemd/system/dnscrypt-proxy.socket
  sed -i "s/ListenDatagram=127.0.*/ListenDatagram=0.0.0.0:53/" /lib/systemd/system/dnscrypt-proxy.socket

  systemctl disable systemd-resolved >/dev/null 2>&1 || true
  systemctl stop systemd-resolved >/dev/null 2>&1 || true
  systemctl daemon-reload
  systemctl restart dnscrypt-proxy.socket
  systemctl restart dnscrypt-proxy

  # Lock /etc/resolv.conf — point at local dnscrypt and prevent resolvconf drift
  chattr -i /etc/resolv.conf 2>/dev/null || true
  cat >/etc/resolv.conf <<'RESOLVCONF'
# Locked. dnscrypt-proxy on 127.0.0.1 — fans out to multiple encrypted resolvers.
nameserver 127.0.0.1
options edns0 timeout:2 attempts:2
RESOLVCONF
  chattr +i /etc/resolv.conf

  ok "dnscrypt-proxy listening on 0.0.0.0:53 (resolv.conf locked to 127.0.0.1)"
}
