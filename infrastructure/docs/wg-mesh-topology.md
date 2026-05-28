# BHN WireGuard mesh topology

As of 2026-05-28. Maintained alongside the node audit. If a peer or
interface is added/removed, update this file.

## Interfaces and subnets

| Iface | Node | Self IP | Listen | Pubkey (truncated) | Purpose |
|-------|------|---------|--------|--------------------|---------|
| `wg0` | LA | 10.8.0.1 | UDP 51820 | `TOYnFt...` | **Primary mesh hub.** All BHN mesh members connect here. |
| `wg0` | NJ | 10.8.0.5 | UDP 51820 | `ylnSJO...` | Mesh client. Single peer (LA). |
| `wg0` | Hillsboro | 10.8.0.6 | UDP 51821 | `EwBHwk...` | Mesh spoke. Hosts tinyproxy egress for LA. |
| `wg1` | FRA | 10.9.0.2 | UDP 51821 | `zkfJNb...` | **Separate `10.9.0.0/24` subnet** for EU-side services. Single peer (LA via wg1 on LA). |
| `wg1` | LA | 10.10.0.1 | UDP 51822 | `V3RenH...` | **Dedicated point-to-point to Hillsboro on 10.10.0.0/30.** See note below. |

The mesh actually carries two subnets:
- `10.8.0.0/24` — primary mesh (LA hub, NJ, Hillsboro)
- `10.9.0.0/24` — EU spur (LA ↔ FRA)
- `10.10.0.0/30` — LA ↔ Hillsboro alternate egress path (see below)

## Peer matrix (who-talks-to-who)

| From | To | Iface | AllowedIPs | PSK | Notes |
|------|----|----|------------|-----|-------|
| LA wg0 | NJ | wg0 | `10.8.0.5/32` | yes (PSK, rotated 2026-05-28) | Was PSK-less before 2026-05-28. PSK added to both sides via `wg syncconf`; backups at `wg0.conf.bak-2026-05-28` on both nodes. |
| LA wg0 | Hillsboro | wg0 | `10.8.0.6/32, 10.8.0.0/24` | yes (PSK) | The catch-all `10.8.0.0/24` means Hillsboro answers for mesh broadcast paths. |
| LA wg0 | FRA (via wg1 on FRA side) | wg0 | `0.0.0.0/0` | yes (PSK) | LA reaches FRA's 10.9.0.0/24 via the FRA peer. The `0.0.0.0/0` here is what makes the SOCKS scrape egress work. |
| LA wg0 | Operator workstation 10.8.0.4 | wg0 | `10.8.0.4/32` | yes (PSK) | High-traffic peer (5.5 GB rx / 38 GB tx). |
| LA wg0 | Operator workstation 10.8.0.2 | wg0 | `10.8.0.2/32` | yes (PSK) | Second operator endpoint (1.67 GB rx / 19.3 GB tx). |
| NJ wg0 | LA | wg0 | `10.8.0.0/24` | yes (PSK, rotated 2026-05-28) | Matching side of the LA↔NJ rotation. |
| Hillsboro wg0 | LA | wg0 | `10.8.0.0/24` | yes (PSK) | Primary mesh return path. |
| Hillsboro wg0 | LA wg1 (point-to-point) | wg0 | `10.10.0.0/30` | **none** | The 10.10.0.0/30 link. Returns keepalive every ~25s. |
| FRA wg1 | LA | wg1 | `10.8.0.0/24` | yes (PSK) | LA reaches FRA, FRA reaches LA's mesh. |

## What is `wg1` on LA?

**Identified 2026-05-28.** This was flagged as a mystery in the
2026-05-28 node audit because it wasn't documented anywhere. It is:

A **dedicated point-to-point WireGuard tunnel between LA and Hillsboro,
parallel to the main `wg0` mesh.**

- LA side: `wg1` interface, key `V3RenHJ/3UQTD1gl3bfqWnAC/iaqXGvVCzogVlDH8GQ=`, listens on `51822`, self IP `10.10.0.1`
- Hillsboro side: a `[Peer]` block in `wg0.conf` (not a separate interface) for pubkey `V3RenH...` with `AllowedIPs = 10.10.0.0/30`
- LA's `wg1.conf` declares the Hillsboro endpoint at `5.78.94.237:51821` (Hillsboro's wg0 listener)
- `AllowedIPs = 0.0.0.0/0` on the LA side — so if a route ever pointed default-via-wg1, ALL LA outbound would tunnel through Hillsboro

### Current state
- Handshake: active (~25s keepalive)
- Traffic: 3 MiB total since last boot (keepalive only — no app traffic)
- Routing table: **wg1 is NOT a default route on LA.** Only the kernel
  link-scope route `10.10.0.0/30 dev wg1` exists. So the interface is
  up, the tunnel is established, but no application traffic uses it.

### Why it exists
Almost certainly **a provisioned-but-dormant alternate egress path** to
complement (or replace) the tinyproxy on `10.8.0.6:8888`. Two options
explain why it's there:

1. **Kernel-level egress backup:** if tinyproxy ever fails, the operator
   could promote `wg1` to default route on LA with one command and route
   ALL outbound (not just HTTP) through Hillsboro's public IP. tinyproxy
   only intercepts HTTP/HTTPS; many tools (Node native fetch, pg-client,
   anything not using `HTTP_PROXY`) bypass it. wg1 would catch all of it.
2. **Future migration target:** if the eBay TLS-fingerprint approach
   ever requires also rotating exit IP, flipping LA's outbound through
   wg1→Hillsboro→internet gives a different egress IP without code
   changes.

### Do not modify

Removing wg1 on LA or the matching `[Peer]` block on Hillsboro would
break this redundancy. Both sides hold legitimate config. Leave alone.

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
| FRA wg1 | `zkfJNbdL9Ptdxv+fxwV2e1q0mbCR5Z/9T80QanSxKA8=` |
| LA wg1 (alt-egress to Hillsboro) | `V3RenHJ/3UQTD1gl3bfqWnAC/iaqXGvVCzogVlDH8GQ=` |
| Operator workstation #1 (10.8.0.4) | `y+ekkxKZsCn9LERiQ3unZxn2zDjsS1yqbz12limv1kA=` |
| Operator workstation #2 (10.8.0.2) | `N9Tg0dOEE7GQgE7lG1FgfI+pGSQoIo9+EmSUucnEAVA=` |
