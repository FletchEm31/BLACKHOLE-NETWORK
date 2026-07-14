# BHN WireGuard mesh topology

As of 2026-05-28 (FRA decommissioned). Maintained alongside the node
audit. If a peer or interface is added/removed, update this file.

## Interfaces and subnets

| Iface | Node | Self IP | Listen | Pubkey (truncated) | Purpose |
|-------|------|---------|--------|--------------------|---------|
| `wg0` | LA | <BHN_WG_LA_IP> | UDP 51820 | `TOYnFt...` | **Primary mesh hub.** All BHN mesh members connect here. |
| `wg0` | NJ | <BHN_WG_NJ_IP> | UDP 51820 | `ylnSJO...` | Mesh client. Single peer (LA). |
| `wg0` | Hillsboro | <BHN_WG_HIL_IP> | UDP 51821 | `EwBHwk...` | Mesh spoke. Hosts tinyproxy egress for LA. |
| `wg1` | LA | 10.10.0.1 | UDP 51822 | `V3RenH...` | **Dedicated point-to-point to Hillsboro on 10.10.0.0/30.** See note below. |

Active subnets:
- `10.8.0.0/24` — primary mesh (LA hub, NJ, Hillsboro)
- `10.10.0.0/30` — LA ↔ Hillsboro alternate egress path (see below)

Retired:
- `10.9.0.0/24` — EU spur (LA ↔ FRA). FRA decommissioned 2026-05-28; subnet no longer routed.

## Peer matrix (who-talks-to-who)

| From | To | Iface | AllowedIPs | PSK | Notes |
|------|----|----|------------|-----|-------|
| LA wg0 | NJ | wg0 | `<BHN_WG_NJ_IP>/32` | yes (PSK, rotated 2026-05-28) | Was PSK-less before 2026-05-28. PSK added to both sides via `wg syncconf`; backups at `wg0.conf.bak-2026-05-28` on both nodes. |
| LA wg0 | Hillsboro | wg0 | `<BHN_WG_HIL_IP>/32, 10.8.0.0/24` | yes (PSK) | The catch-all `10.8.0.0/24` means Hillsboro answers for mesh broadcast paths. |
| LA wg0 | Operator workstation <BHN_WG_OPC_IP> | wg0 | `<BHN_WG_OPC_IP>/32` | yes (PSK) | High-traffic peer (5.5 GB rx / 38 GB tx). |
| LA wg0 | Operator workstation <BHN_WG_PEER_IP> | wg0 | `<BHN_WG_PEER_IP>/32` | yes (PSK) | Second operator endpoint (1.67 GB rx / 19.3 GB tx). |
| NJ wg0 | LA | wg0 | `10.8.0.0/24` | yes (PSK, rotated 2026-05-28) | Matching side of the LA↔NJ rotation. |
| Hillsboro wg0 | LA | wg0 | `10.8.0.0/24` | yes (PSK) | Primary mesh return path. |
| Hillsboro wg0 | LA wg1 (point-to-point) | wg0 | `10.10.0.0/30` | **none** | The 10.10.0.0/30 link. Returns keepalive every ~25s. Pre-existing gap, not yet closed. |
| Helsinki wg0 | LA wg1 (point-to-point) | wg0 | `10.10.0.0/30` | yes (PSK, added 2026-07-14) | The alt-egress 10.10.0.0/30 link, symmetric with Hillsboro's but with a PSK. |
| ~~LA wg0 → FRA~~ | ~~FRA (via wg1 on FRA side)~~ | ~~wg0~~ | ~~`0.0.0.0/0`~~ | — | **Retired 2026-05-28.** FRA peer block removed from LA `wg0.conf`. Used to carry the SOCKS scrape egress; replaced by `curl_cffi` impersonation from LA's own IP. |
| ~~FRA wg1 → LA~~ | — | — | — | — | **Retired 2026-05-28** — FRA server destroyed. |

## `wg1` on LA — full-tunnel client egress, switchable Hillsboro/Helsinki

**Activated 2026-05-28 (late)** as a Hillsboro-only tunnel, replacing the
Frankfurt-based full-tunnel egress retired earlier that day. **Generalized
2026-07-14** into a switchable target (Hillsboro or Helsinki), so the
egress node can be flipped without touching any client's WireGuard config.

A **dedicated point-to-point WireGuard tunnel between LA and whichever node
is currently selected**, parallel to the main `wg0` mesh, used to forward
full-tunnel client traffic through that node's public IP.

- LA side: `wg1` interface, key `V3RenHJ/3UQTD1gl3bfqWnAC/iaqXGvVCzogVlDH8GQ=`, listens on `51822`, self IP `10.10.0.1/30`, `fwmark 0xca6c` (same as wg0 — keeps wg1's underlay packets out of table `51820`). The interface is fully torn down and rebuilt on every switch — the peer key/endpoint are the only things that change.
- Hillsboro side: a `[Peer]` block in `wg0.conf` for pubkey `V3RenH...` with `AllowedIPs = 10.10.0.0/30`. No PSK (pre-existing gap). Endpoint learned dynamically. Return route `10.10.0.0/30 dev wg0` added 2026-05-28.
- Helsinki side: a `[Peer]` block in `wg0.conf` for the same LA wg1 pubkey, `AllowedIPs = 10.10.0.0/30`, **with a PSK** (added 2026-07-14, stored at `/etc/wireguard/wg1-la.psk` on Helsinki and `/etc/wireguard/wg1-helsinki.psk` on LA). UFW egress rule `ALLOW OUT 149.28.91.100 51822/udp` added to permit the wg1 handshake reply.
- LA's wg1 peer endpoint: `<BHN_HIL_PUBLIC_IP>:51821` or `<BHN_HEL_PUBLIC_IP>:51821` depending on target (both nodes listen on the same wg0 port; demultiplexed by handshake key).

### How traffic actually moves

Lifecycle is driven by `/etc/wireguard/bhn-wg1-egress.sh` (repo copy:
`infrastructure/wg-clients/bhn-wg1-egress.sh`), invoked from wg0's
`PostUp` (`bhn-wg1-egress.sh hillsboro up` by default) so wg1 comes up
whenever wg0 comes up. Supersedes the old single-target
`bhn-wg1-hillsboro.sh` (retired 2026-07-14, kept as `.bak` on LA for
reference).

Usage: `bhn-wg1-egress.sh <hillsboro|helsinki> up`, `bhn-wg1-egress.sh
down`, `bhn-wg1-egress.sh status` (prints the current target from
`/etc/wireguard/wg1-current-target`, plus live interface/routing/iptables
state).

The script wires (same machinery regardless of target):
1. `wg1` interface up with the selected node's peer (pubkey/endpoint from an internal lookup table, PSK file if the target has one).
2. Routing table `200`: `default dev wg1`, `10.8.0.0/24 dev wg0`, `10.10.0.0/30 dev wg1`.
3. `ip rule from {<BHN_WG_PEER_IP>, <BHN_WG_OPC_IP>, <BHN_WG_PEER_IP>, <BHN_WG_PEER_IP>, <BHN_WG_PEER_IP>, 10.10.0.0/30} lookup 200 priority 201`. Mesh peers (NJ `<BHN_WG_NJ_IP>`, Hillsboro `<BHN_WG_HIL_IP>`, Helsinki `<BHN_WG_HEL_IP>`, LA itself `<BHN_WG_LA_IP>`) are intentionally NOT in this list — they keep their existing egress.
4. `iptables FORWARD wg0↔wg1 ACCEPT`, `OUTPUT wg1 ACCEPT`, `nat POSTROUTING -o wg1 MASQUERADE` (SNATs to `10.10.0.1` so the target's `AllowedIPs = 10.10.0.0/30` peer matches), `mangle FORWARD -o wg1 TCPMSS --clamp-mss-to-pmtu`.

On the target node: the existing `iptables nat POSTROUTING -o eth0 MASQUERADE` SNATs again to that node's public IP. UFW covers the forward path (`FWD eth0<->wg0`) and now, on both nodes, an explicit egress allow for UDP `51822` back to LA (needed for the wg1 handshake reply).

**Switching is disruptive for ~3-5 seconds and breaks in-flight TCP
connections.** `up()` fully tears down table 200 before rebuilding it
against the new target — there's a brief window with no route at all, and
any already-open TCP connection that was NAT'd through the old node's
public IP breaks when the visible source IP changes (same as changing
networks mid-connection). New connections after the switch work cleanly.

**Known-fixed bug (2026-07-14):** `up()`'s teardown originally didn't clear
the docker-bridge exemption `ip rule` (item 3 above, `to 172.16.0.0/12
lookup main priority 100`) from a prior run. Re-running `up()` while `wg1`
was already up (i.e. switching targets) hit `RTNETLINK answers: File
exists` on the re-add and aborted under `set -e`, leaving table 200 with
**zero** rules — this took down a live full-tunnel session during initial
testing. Fixed by clearing that rule in the teardown block too; validated
with two clean round-trip switches (Hillsboro→Helsinki→Hillsboro) with real
client traffic confirmed flowing after each.

### Verifying it works

From LA: `curl --interface wg1 --noproxy '*' -k -s https://1.1.1.1/cdn-cgi/trace | grep ip=` → returns the current target's public IP. From a client running a full-tunnel profile: `curl https://1.1.1.1/cdn-cgi/trace` should likewise show that IP. `bhn-wg1-egress.sh status` gives a full readout without needing an external check.

### Adding a new full-tunnel client peer

When provisioning a new wg0 client that should egress via wg1 (whichever target is active):
1. Add the peer block in `/etc/wireguard/wg0.conf` (assign next free `10.8.0.X/32`).
2. Add `10.8.0.X` to the `CLIENT_IPS` array in `bhn-wg1-egress.sh`.
3. Re-run `bash bhn-wg1-egress.sh <current-target> up` (or restart wg0 if convenient) — `status` shows the current target if unsure.

### Adding a third egress node

1. Provision the node's own tunnel: main mesh peer (with PSK) plus a second `[Peer]` block on its `wg0` for LA's wg1 pubkey (`AllowedIPs = 10.10.0.0/30`, PSK recommended), matching Helsinki's setup above.
2. Add the matching UFW egress rule (`ALLOW OUT 149.28.91.100 51822/udp`).
3. Add an entry to the `NODES` lookup table in `bhn-wg1-egress.sh` (pubkey, endpoint, PSK file path).
4. No other script changes needed — table 200/fwmark/CLIENT_IPS machinery is already target-agnostic.
5. **Verify the `10.10.0.0/30 dev wg0` route exists on the new node before trusting it as an egress target** — see the gotcha below. Don't skip this; it doesn't show up as a WireGuard error, only as return traffic silently vanishing.

#### ⚠ `wg set` vs `wg-quick` — the route-creation gotcha (cost real debugging time 2026-07-14)

`wg-quick up` (i.e. the `wg-quick@wg0` systemd service) automatically adds
a kernel route for every `[Peer]`'s `AllowedIPs` block **at interface-start
time**, by reading the static `wg0.conf` file. `wg set` (the imperative,
live-reconfiguration command) does **not** — it only updates WireGuard's
own peer table, never the kernel routing table.

If you add a new peer to an *already-running* `wg0` via `wg set ... peer
... allowed-ips 10.10.0.0/30` (which is exactly what provisioning a new
egress node live requires — you don't want to bounce `wg0` and drop the
main mesh peer to add a second peer), the peer works fine for anything
`wg0` itself originates or terminates, but **no route to that
`AllowedIPs` subnet gets created**. This is easy to miss because nothing
errors: the WireGuard handshake succeeds, `wg show` looks correct, and
outbound-only checks (ping, a SOCKS5 test, `wg show ... latest-handshakes`)
all pass. The failure only shows up as forwarded traffic whose *return*
leg needs that route — which is exactly wg1's use case (NAT'd traffic
comes back from the internet addressed to `10.10.0.1`, and without the
route it falls through to the node's default route out its own WAN
interface instead of back through `wg0` to LA). Symptom: outbound
half of a proxied connection works, the reply never arrives — timeouts,
or a browser-side 502 from whatever's waiting on the response.

This is exactly what happened when Helsinki's wg1 peer was added live on
2026-07-14: peer/handshake/PSK were all correct, but `10.10.0.0/30 dev
wg0` never got created, and it took a live-traffic failure plus a direct
`ip route show` diff against Hillsboro (which has the route, added when
its peer was provisioned via a full `wg-quick` cycle) to find it.

**Fix/workaround, in order of preference:**
- After `wg set`-ing a new peer with an `AllowedIPs` subnet that needs to
  be routable (not just reachable as a WireGuard endpoint), immediately
  run `ip route add <AllowedIPs> dev wg0` by hand.
- Confirm it'll survive a restart: since the peer is also written to
  `wg0.conf` (not just live-`wg set`), a future `systemctl restart
  wg-quick@wg0` (or reboot) will recreate the route automatically via
  `wg-quick`'s normal startup behavior — verified 2026-07-14 by restarting
  `wg-quick@wg0` on Helsinki and confirming the route reappeared. No
  `PostUp` line is needed for this specifically, *as long as* the peer
  block is actually persisted to the conf file, not just live-`wg set`.
- Verification command: `ip route show | grep <AllowedIPs>` — should show
  `dev wg0`. If it's absent, don't trust that peer's traffic to route
  correctly, even if `wg show` looks perfect.

### Do not modify by hand

All ends of the tunnel hold legitimate config that the script depends on.
Edit the script (and the repo copy) rather than poking at runtime state.

## PSK gaps (work queued)

- ~~**LA ↔ NJ on `wg0`:** no PSK on either side.~~ ✅ Rotated 2026-05-28.
- **LA wg1 ↔ Hillsboro `V3RenH` peer:** no PSK. Could be added in a
  separate session — operator decision.

## Pubkey reference (full)

For grep-ability when comparing audit dumps:

| Owner | Pubkey |
|-------|--------|
| LA wg0 | `<BHN_WG_LA_PUBKEY>` |
| NJ wg0 | `ylnSJOqwkqrNZwt/saJdqoMG7j3l35hoUk+zejru1Sk=` |
| Hillsboro wg0 | `EwBHwkT4iJXzhJZMvtlo70NOLx+wPv8IXmAGSa89zBg=` |
| ~~FRA wg1~~ | ~~`zkfJNbdL9Ptdxv+fxwV2e1q0mbCR5Z/9T80QanSxKA8=`~~ — retired 2026-05-28 |
| LA wg1 (alt-egress to Hillsboro) | `V3RenHJ/3UQTD1gl3bfqWnAC/iaqXGvVCzogVlDH8GQ=` |
| Operator workstation #1 (<BHN_WG_OPC_IP>) | `<BHN_WG_OPC_PUBKEY>` |
| Operator workstation #2 (<BHN_WG_PEER_IP>) | `<BHN_WG_PHONE_PUBKEY>` |
