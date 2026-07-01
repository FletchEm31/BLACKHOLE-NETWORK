# BHN-HELSINKI-EU1 — EU Exit Node

**Commissioned:** 2026-06-27  
**Provider:** Hetzner CX23, Helsinki (EU-NORTH)  
**Public IP:** 46.62.162.87  
**Tunnel IP:** 10.8.0.8 (BHN mesh wg0)  
**Role:** EU exit node + Tor middle relay  
**SSH host key (ED25519):** `SHA256:zFoSi3Qz0EIjDUlaH75AWXJ/GJY257RtQvWDbk3Icho`

---

## Network Topology

```
LA hub (10.8.0.1 / 149.28.91.100)
  └── wg0 mesh
        ├── 10.8.0.6  BHN-HILLSBORO-US3  (primary US exit)
        └── 10.8.0.8  BHN-HELSINKI-EU1   (EU exit, US failover)
```

Helsinki peers to LA on **port 51821/udp** (LA listens on 51820). PersistentKeepalive 25s.

---

## WireGuard

| Field | Value |
|-------|-------|
| Interface | wg0 |
| Address | 10.8.0.8/30 |
| Listen port | 51821 |
| Public key | `uQZyqleD4vx4rjklp+PHo6v4AuvPN4apzKCyq4zzkDg=` |
| LA peer pub key | `TOYnFt18v4NynEN91o6zkmV5hsvHBLJTb8qL7GG/KAo=` |
| Config | `/etc/wireguard/wg0.conf` |
| PresharedKey | Set 2026-07-01 (LA↔Helsinki peer, matching value on both ends) |

---

## tinyproxy

| Field | Value |
|-------|-------|
| Listen | `10.8.0.8:8888` (tunnel only — not public) |
| Allow | `10.8.0.0/24` |
| Outbound bind | `46.62.162.87` (public IP) |
| ConnectPort | `443` only |
| Config | `/etc/tinyproxy/tinyproxy.conf` |
| Service | `tinyproxy.service` (systemd, enabled) |

Test from LA:
```bash
curl -x http://10.8.0.8:8888 https://wttr.in/Helsinki?format=3
# Helsinki: ☀️  +17°C  (verified 2026-06-27)
```

---

## Tor Middle Relay

| Field | Value |
|-------|-------|
| Nickname | **BHNAuroraEU1** |
| Fingerprint | `6AA0F8D730220D992914DB599E6A305DB5384913` |
| ORPort | `9001/tcp` (public) |
| SocksPort | `0` (disabled — relay-only, no proxy) |
| ExitRelay | `0` — never exits traffic |
| Contact | `admin@eventhorizonvpn.com` |
| BandwidthRate | 512 KB/s |
| BandwidthBurst | 1 MB/s |
| AccountingMax | 750 GB/month (resets 1st of month) |
| MaxMemInQueues | 256 MB |
| Deploy path | `/opt/bhn-tor-relay-helsinki/` |
| Data volume | `bhn-tor-relay-helsinki_tor-data` |
| Container | `bhn-tor-relay-helsinki` (Docker, restart: unless-stopped) |

**MyFamily (both relays must declare each other):**

| Relay | Fingerprint |
|-------|-------------|
| BHNHeliosUS3 (Hillsboro) | `CEBFF0886A263D4EA1D6D08A7ED86138F98D10AA` |
| BHNAuroraEU1 (Helsinki) | `6AA0F8D730220D992914DB599E6A305DB5384913` |

Bandwidth caps are intentionally matched to Hillsboro. Raise both nodes together.

---

## UFW Rules

```
ALLOW IN  22/tcp       (SSH, any)
ALLOW IN  51821/udp    from 149.28.91.100  (WireGuard from LA)
ALLOW IN  8388/tcp+udp from 149.28.91.100  (Shadowsocks)
ALLOW IN  9001/tcp     (Tor ORPort, any)
ALLOW IN  8888/tcp     from 10.8.0.0/24    (tinyproxy, tunnel only)

ALLOW OUT 53/udp+tcp   (DNS)
ALLOW OUT 123/udp      (NTP)
ALLOW OUT 443/tcp      (HTTPS)
ALLOW OUT 149.28.91.100:51820/udp  (WireGuard to LA)
ALLOW OUT 10.8.0.0/24  (tunnel)
ALLOW FWD eth0 ↔ wg0
```

Default policy: deny incoming, deny outgoing, deny routed.

Netdata child→parent streaming (`10.8.0.8` → `10.8.0.1:19999`) rides the existing `ALLOW OUT 10.8.0.0/24` tunnel rule — no firewall change was needed. Direct dashboard access to Helsinki's own Netdata UI over the mesh is not exposed (no inbound 19999 rule, unlike Hillsboro).

---

## Running Services

| Service | Notes |
|---------|-------|
| `tinyproxy.service` | Proxy egress, tunnel-only |
| `bhn-tor-relay-helsinki` (Docker) | Tor middle relay |
| `netdata` (Docker) | Metrics, streams to LA parent as child `BHN-HELSINKI-EU1` (configured 2026-07-01, `/etc/netdata/stream.conf` inside container — not on a bind mount, so config lives in the container's writable layer and survives `docker restart` but not `docker rm`) |
| `crowdsec.service` + `crowdsec-firewall-bouncer.service` | IDS/IPS |
| `dnscrypt-proxy.service` | Encrypted DNS |
| `docker.service` | Container runtime |

---

## Useful Commands

```bash
# Check tunnel
wg show
ping 10.8.0.1

# Tor relay status
docker logs bhn-tor-relay-helsinki --tail 30
docker exec bhn-tor-relay-helsinki cat /var/lib/tor/fingerprint

# Rebuild relay after torrc change
cd /opt/bhn-tor-relay-helsinki
docker compose up -d --build

# tinyproxy log
tail -f /var/log/tinyproxy/tinyproxy.log
```
