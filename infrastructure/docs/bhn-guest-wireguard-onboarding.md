# BHN Guest WireGuard Onboarding

Guide for adding a new guest peer to the BHN mesh and connecting to MatrixBHN.

---

## Overview

Guest peers get IPs in the `<BHN_WG_GUEST_IP>+` range on the LA wg0 hub.  
They receive a **split-tunnel config** by default:

- **DNS → through WireGuard** — all DNS queries go to AdGuard Home on LA
  (<BHN_WG_LA_IP>) for ad blocking, tracker blocking, and `.bhn.local` resolution
- **All other traffic → direct ISP** — browsing, streaming, Netflix, etc.
  bypasses the tunnel entirely and uses the guest's own internet connection

This gives guests AdGuard DNS protection and access to BHN mesh services
(MatrixBHN, Grafana, etc.) without routing their full internet traffic through LA.

---

## Step 1 — Add the peer on LA

Run on LA as root. Replace `PEER_PUBKEY`, `PEER_PSK`, and `PEER_IP`:

```bash
# Generate a key pair on the guest's device first, then:
wg set wg0 peer <PEER_PUBKEY> \
  preshared-key <(echo "<PEER_PSK>") \
  allowed-ips 10.8.0.XX/32

# Persist to wg0.conf
wg showconf wg0 > /etc/wireguard/wg0.conf
```

Assign IPs sequentially: `<BHN_WG_GUEST_IP>`, `<BHN_WG_GUEST_IP>`, etc.  
Current operator endpoints: `.4` (workstation), `.2` (phone). Guests start at `.10`.

---

## Step 2 — Guest WireGuard config (split-tunnel template)

Send this to the guest. Fill in `<LA_PUBLIC_IP>`, `<GUEST_PRIVATE_KEY>`,
`<LA_WG0_PUBKEY>`, `<PSK>`, and `<ASSIGNED_IP>`:

```ini
[Interface]
PrivateKey = <GUEST_PRIVATE_KEY>
Address    = <ASSIGNED_IP>/32
DNS        = <BHN_WG_LA_IP>

# Split tunnel: only BHN mesh subnet + DNS go through WireGuard.
# All other traffic (Netflix, browsing) uses your normal ISP connection.

[Peer]
PublicKey    = <BHN_WG_LA_PUBKEY>
PresharedKey = <PSK>
Endpoint     = <BHN_LA_PUBLIC_IP>:51820
AllowedIPs   = 10.8.0.0/24
PersistentKeepalive = 25
```

**AllowedIPs = `10.8.0.0/24`** is the split-tunnel key — only mesh traffic
(and DNS via `DNS = <BHN_WG_LA_IP>`) routes through the tunnel.

For full-tunnel (all traffic through BHN/Hillsboro egress), change to:
```
AllowedIPs = 0.0.0.0/0, ::/0
```

---

## Step 3 — Connect to MatrixBHN

Once WireGuard is active:

1. Download Element: **https://element.io/download**
2. Open Element → **Sign in** → tap **Edit** next to the homeserver URL
3. Enter homeserver: **`http://<BHN_WG_LA_IP>:8008`**
   (or `http://chat.bhn.local:8008` — resolves via AdGuard once WG is active)
4. Log in with the credentials provided

---

## Step 4 — Verify connectivity

From the guest device with WireGuard active:

```bash
# Ping the LA hub
ping <BHN_WG_LA_IP>

# Check MatrixBHN API (should return Matrix version JSON)
curl http://<BHN_WG_LA_IP>:8008/_matrix/client/versions

# Confirm DNS is routing through AdGuard
nslookup chat.bhn.local    # should return <BHN_WG_LA_IP>
nslookup google.com        # should still resolve (via AdGuard → upstream)
```

---

## Peer IP assignment log

| IP | User | Device | Added |
|----|------|--------|-------|
| <BHN_WG_OPC_IP> | Operator | Workstation | 2026-05-12 |
| <BHN_WG_PEER_IP> | Operator | Phone | 2026-05-12 |
| *(<BHN_WG_GUEST_IP>+ reserved for guests)* | | | |

Update this table when adding new peers.

---

## Remove a peer

```bash
wg set wg0 peer <PEER_PUBKEY> remove
wg showconf wg0 > /etc/wireguard/wg0.conf
```
