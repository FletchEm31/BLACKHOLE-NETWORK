# BHN WireGuard mesh topology

As of 2026-05-28 (FRA decommissioned). Maintained alongside the node
audit. If a peer or interface is added/removed, update this file.

## Interfaces and subnets

| Iface | Node | Self IP | Listen | Pubkey (truncated) | Purpose |
|-------|------|---------|--------|--------------------|---------|
| `wg0` | LA | 10.8.0.1 | UDP 51820 | `TOYnFt...` | **Primary mesh hub.** All BHN mesh members connect here. |
| `wg0` | NJ | 10.8.0.5 | UDP 51820 | `ylnSJO...` | Mesh client. Single peer (LA). |
| `wg0` | Hillsboro | 10.8.0.6 | UDP 51821 | `EwBHwk...` | Mesh spoke. Hosts tinyproxy egress for LA. |
| `wg1` | LA | 10.10.0.1 | UDP 51822 | `V3RenH...` | **Dedicated point-to-point to Hillsboro on 10.10.0.0/30.** See note below. |

Active subnets:
- `10.8.0.0/24` — primary mesh (LA hub, NJ, Hillsboro)
- `10.10.0.0/30` — LA ↔ Hillsboro alternate egress path (see below)

Retired:
- `10.9.0.0/24` — EU spur (LA ↔ FRA). FRA decommissioned 2026-05-28; subnet no longer routed.

## Peer matrix (who-talks-to-who)

| From | To | Iface | AllowedIPs | PSK | Notes |
|------|----|----|------------|-----|-------|
| LA wg0 | NJ | wg0 | `10.8.0.5/32` | yes (PSK, rotated 2026-05-28) | Was PSK-less before 2026-05-28. PSK added to both sides via `wg syncconf`; backups at `wg0.conf.bak-2026-05-28` on both nodes. |
| LA wg0 | Hillsboro | wg0 | `10.8.0.6/32, 10.8.0.0/24` | yes (PSK) | The catch-all `10.8.0.0/24` means Hillsboro answers for mesh broadcast paths. |
| LA wg0 | Operator workstation 10.8.0.4 | wg0 | `10.8.0.4/32` | yes (PSK) | High-traffic peer (5.5 GB rx / 38 GB tx). |
| LA wg0 | Operator workstation 10.8.0.2 | wg0 | `10.8.0.2/32` | yes (PSK) | Second operator endpoint (1.67 GB rx / 19.3 GB tx). |
| NJ wg0 | LA | wg0 | `10.8.0.0/24` | yes (PSK, rotated 2026-05-28) | Matching side of the LA↔NJ rotation. |
| Hillsboro wg0 | LA | wg0 | `10.8.0.0/24` | yes (PSK) | Primary mesh return path. |
| Hillsboro wg0 | LA wg1 (point-to-point) | wg0 | `10.10.0.0/30` | **none** | The 10.10.0.0/30 link. Returns keepalive every ~25s. |
| ~~LA wg0 → FRA~~ | ~~FRA (via wg1 on FRA side)~~ | ~~wg0~~ | ~~`0.0.0.0/0`~~ | — | **Retired 2026-05-28.** FRA peer block removed from LA `wg0.conf`. Used to carry the SOCKS scrape egress; replaced by `curl_cffi` impersonation from LA's own IP. |
| ~~FRA wg1 → LA~~ | — | — | — | — | **Retired 2026-05-28** — FRA server destroyed. |

## `wg1` on LA — full-tunnel client egress to Hillsboro

**Activated 2026-05-28 (late).** Replaced the Frankfurt-based full-tunnel
egress retired earlier that day. Prior to activation, `wg1` had been
provisioned-but-dormant since May.

A **dedicated point-to-point WireGuard tunnel between LA and Hillsboro,
parallel to the main `wg0` mesh**, used to forward full-tunnel client
traffic through Hillsboro's public IP `5.78.94.237`.

- LA side: `wg1` interface, key `V3RenHJ/3UQTD1gl3bfqWnAC/iaqXGvVCzogVlDH8GQ=`, listens on `51822`, self IP `10.10.0.1/30`, `fwmark 0xca6c` (same as wg0 — keeps wg1's underlay packets out of table `51820`).
- Hillsboro side: a `[Peer]` block in `wg0.conf` for pubkey `V3RenH...` with `AllowedIPs = 10.10.0.0/30`. Endpoint learned dynamically. Return route `10.10.0.0/30 dev wg0` added 2026-05-28.
- LA's wg1 peer endpoint: `5.78.94.237:51821` (Hillsboro's wg0 listener — same port as the wg0 peer; demultiplexed by handshake key).

### How traffic actually moves

Lifecycle is driven by `/etc/wireguard/bhn-wg1-hillsboro.sh` (repo copy:
`infrastructure/wg-clients/bhn-wg1-hillsboro.sh`), invoked from wg0's
`PostUp` so wg1 comes up whenever wg0 comes up.

The script wires:
1. `wg1` interface up with the Hillsboro peer.
2. Routing table `200`: `default dev wg1`, `10.8.0.0/24 dev wg0`, `10.10.0.0/30 dev wg1`.
3. `ip rule from {10.8.0.2, 10.8.0.4, 10.8.0.7, 10.8.0.8, 10.8.0.9, 10.10.0.0/30} lookup 200 priority 201`. Mesh peers (NJ `10.8.0.5`, Hillsboro `10.8.0.6`, LA itself `10.8.0.1`) are intentionally NOT in this list — they keep their existing egress.
4. `iptables FORWARD wg0↔wg1 ACCEPT`, `OUTPUT wg1 ACCEPT`, `nat POSTROUTING -o wg1 MASQUERADE` (SNATs to `10.10.0.1` so Hillsboro's V3RenH `AllowedIPs = 10.10.0.0/30` matches), `mangle FORWARD -o wg1 TCPMSS --clamp-mss-to-pmtu`.

On Hillsboro: the existing `iptables nat POSTROUTING -o eth0 MASQUERADE` SNATs again to `5.78.94.237`. UFW rule 12 (`Anywhere on eth0 ALLOW FWD Anywhere on wg0`) covers the forward path. UFW egress rule on Hillsboro now also allows UDP `51822` back to LA (needed for wg1 handshake replies).

### Verifying it works

From LA: `curl --interface wg1 --noproxy '*' -k -s https://1.1.1.1/cdn-cgi/trace | grep ip=` → returns `ip=5.78.94.237`. From a client running a full-tunnel profile: `curl https://1.1.1.1/cdn-cgi/trace` should likewise show Hillsboro's IP.

### Adding a new full-tunnel client peer

When provisioning a new wg0 client that should egress via Hillsboro:
1. Add the peer block in `/etc/wireguard/wg0.conf` (assign next free `10.8.0.X/32`).
2. Add `10.8.0.X` to the `CLIENT_IPS` array in `bhn-wg1-hillsboro.sh`.
3. Re-run `bash bhn-wg1-hillsboro.sh down && bash bhn-wg1-hillsboro.sh up` (or restart wg0 if convenient).

### Do not modify by hand

Both ends of the tunnel hold legitimate config that the script depends on.
Edit the script (and the repo copy) rather than poking at runtime state.

## PSK gaps (work queued)

- ~~**LA ↔ NJ on `wg0`:** no PSK on either side.~~ ✅ Rotated 2026-05-28.
- **LA wg1 ↔ Hillsboro `V3RenH` peer:** no PSK. Could be added in a
  separate session — operator decision.

## Pubkey reference (full)

For grep-ability when comparing audit dumps:

| Owner | Pubkey |
|-------|--------|
| LA wg0 | `TOYnFt18v4NynEN91o6zkmV5hsvHBLJTb8qL7GG/KAo=` |
| NJ wg0 | `ylnSJOqwkqrNZwt/saJdqoMG7j3l35hoUk+zejru1Sk=` |
| Hillsboro wg0 | `EwBHwkT4iJXzhJZMvtlo70NOLx+wPv8IXmAGSa89zBg=` |
| ~~FRA wg1~~ | ~~`zkfJNbdL9Ptdxv+fxwV2e1q0mbCR5Z/9T80QanSxKA8=`~~ — retired 2026-05-28 |
| LA wg1 (alt-egress to Hillsboro) | `V3RenHJ/3UQTD1gl3bfqWnAC/iaqXGvVCzogVlDH8GQ=` |
| Operator workstation #1 (10.8.0.4) | `y+ekkxKZsCn9LERiQ3unZxn2zDjsS1yqbz12limv1kA=` |
| Operator workstation #2 (10.8.0.2) | `N9Tg0dOEE7GQgE7lG1FgfI+pGSQoIo9+EmSUucnEAVA=` |
