# BHN-HILLSBORO-US3 — US-WEST Exit Node

**Provider:** Hetzner, Hillsboro US-WEST  
**Public IP:** 5.78.94.237  
**Tunnel IP:** 10.8.0.6 (BHN mesh wg0)  
**Role:** Primary US exit node — tinyproxy egress for LA, Tor middle relay, Shadowsocks endpoint, full-tunnel egress for LA outbound traffic

---

## Network Topology

```
LA hub (10.8.0.1 / 149.28.91.100)
  ├── wg0 mesh peer → 10.8.0.6  BHN-HILLSBORO-US3  (BHN mesh + proxy)
  └── wg1 full-tunnel → 10.10.0.1/10.10.0.2        (LA outbound egress via Hillsboro)
```

Hillsboro carries two distinct WireGuard roles:
- **wg0 mesh peer** — BHN tunnel mesh, tinyproxy, Tor SOCKS
- **wg1 egress peer** — LA's default outbound route (all LA internet traffic exits via Hillsboro's public IP)

---

## WireGuard

| Field | Value |
|-------|-------|
| Interface | wg0 |
| Listen port | 51821 |
| Public key | `EwBHwkT4iJXzhJZMvtlo70NOLx+wPv8IXmAGSa89zBg=` |

**Peers:**

| Peer | Key | Endpoint | AllowedIPs | Role |
|------|-----|----------|------------|------|
| LA wg0 | `TOYnFt18v4NynEN91o6zkmV5hsvHBLJTb8qL7GG/KAo=` | 149.28.91.100:51820 | 10.8.0.0/24 | BHN mesh |
| LA wg1 | `V3RenHJ/3UQTD1gl3bfqWnAC/iaqXGvVCzogVlDH8GQ=` | 149.28.91.100:51822 | 10.10.0.0/30 | Full-tunnel egress |

---

## tinyproxy

| Field | Value |
|-------|-------|
| Listen | `10.8.0.6:8888` (tunnel only — not public) |
| Allow | `10.8.0.0/24` |
| Outbound bind | `5.78.94.237` (public IP) |
| ConnectPort | `443` only |
| Config | `/etc/tinyproxy/tinyproxy.conf` |
| Service | `tinyproxy.service` (systemd, enabled) |

Primary proxy egress for LA's outbound HTTPS workload (Anthropic, market data polling, etc.).

---

## Tor Middle Relay

| Field | Value |
|-------|-------|
| Nickname | **BHNHeliosUS3** |
| Fingerprint | `CEBFF0886A263D4EA1D6D08A7ED86138F98D10AA` |
| ORPort | `9001/tcp` (public) |
| SocksPort | `10.8.0.6:9050` (tunnel-only — available for BHN-internal privacy routing) |
| ExitRelay | `0` — never exits traffic |
| Contact | `admin@eventhorizonvpn.com` |
| BandwidthRate | 512 KB/s |
| BandwidthBurst | 1 MB/s |
| AccountingMax | 750 GB/month (resets 1st of month) |
| MaxMemInQueues | 256 MB |
| Deploy path | `/opt/bhn/infrastructure/services/tor-relay-hillsboro/` |
| Data volume | `bhn-tor-relay_tor-data` (via Docker) |
| Container | `bhn-tor-relay` (Docker, restart: unless-stopped) |

**MyFamily (both relays must declare each other):**

| Relay | Fingerprint |
|-------|-------------|
| BHNHeliosUS3 (Hillsboro) | `CEBFF0886A263D4EA1D6D08A7ED86138F98D10AA` |
| BHNAuroraEU1 (Helsinki) | `6AA0F8D730220D992914DB599E6A305DB5384913` |

---

## Shadowsocks

Port `8388/tcp+udp`, inbound from LA only (UFW-restricted to 149.28.91.100).

---

## UFW Rules

```
ALLOW IN  22/tcp              (SSH, any)
ALLOW IN  51821/udp           from 149.28.91.100  (WireGuard wg0 from LA)
ALLOW IN  8388/tcp+udp        from 149.28.91.100  (Shadowsocks from LA)
ALLOW IN  10.8.0.6 8888/tcp   from 10.8.0.0/24    (tinyproxy, tunnel-bound)
ALLOW IN  9001/tcp            (Tor ORPort, any)
ALLOW IN  19999/tcp           from 10.8.0.0/24    (Netdata, mesh-only)
ALLOW IN  19999/tcp           from 10.9.0.0/24    (Netdata, mesh-only)
DENY  IN  53 on eth0                               (block public DNS)

ALLOW OUT 53/udp+tcp          (DNS)
ALLOW OUT 123/udp             (NTP)
ALLOW OUT 443/tcp             (HTTPS)
ALLOW OUT 149.28.91.100:51820/udp  (WireGuard wg0 to LA)
ALLOW OUT 149.28.91.100:51822/udp  (WireGuard wg1 reply to LA)
ALLOW OUT 10.8.0.0/24         (tunnel)
ALLOW FWD eth0 ↔ wg0
```

Default policy: deny incoming, deny outgoing, deny routed.

---

## Running Services

| Service | Notes |
|---------|-------|
| `tinyproxy.service` | Proxy egress, tunnel-only |
| `bhn-tor-relay` (Docker) | Tor middle relay + SOCKS on tunnel |
| `fail2ban.service` | SSH brute-force protection |
| `crowdsec.service` + `crowdsec-firewall-bouncer.service` | IDS/IPS |
| `netdata.service` | Metrics, mesh-accessible at 10.8.0.6:19999 |
| `dnscrypt-proxy.service` | Encrypted DNS |
| `docker.service` | Container runtime |

---

## Useful Commands

```bash
# Check tunnel
wg show
ping 10.8.0.1

# Tor relay status
docker logs bhn-tor-relay --tail 30
docker exec bhn-tor-relay cat /var/lib/tor/fingerprint

# Rebuild relay after torrc change
cd /opt/bhn/infrastructure/services/tor-relay-hillsboro
docker compose up -d --build

# tinyproxy log
tail -f /var/log/tinyproxy/tinyproxy.log

# Netdata (from within BHN mesh)
http://10.8.0.6:19999
```
