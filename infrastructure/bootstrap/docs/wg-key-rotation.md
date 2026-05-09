# EventHorizon — WireGuard Key Rotation Runbook

When to run this: any time a peer's private key or pre-shared key is exposed (chat logs, leaked backups, lost device, etc.), or as periodic hygiene.

## Critical lesson learned (2026-05-09)

**Never run destructive `wg` commands while SSHed to the hub through the very peer you're modifying.**

WireGuard's `allowed-ips` are unique per interface. The moment you `wg set wg0 peer NEW allowed-ips 10.8.0.X/32`, the kernel **moves** that IP from the existing peer to the new one — even before any explicit `remove`. So your "additive" step is implicitly destructive: the old peer's tunnel breaks immediately, your SSH session gets reset, and any subsequent commands (the heredoc that prints the new private key, the explicit `remove`, the `wg-quick save`) execute on the server but their output never makes it back to you. The new private key ends up in a now-dead bash process and is **lost forever**.

The fix: run all rotation commands via a path that does **not** depend on the peer being rotated. For client-peer rotations, use direct public-IP SSH to the hub (`ssh root@<hub-public-ip>`) instead of the tunnel-IP path (`ssh root@10.8.0.1`).

## Pre-flight

- [ ] Have the hub's public WireGuard pubkey on hand (run `wg show <iface> public-key` if unsure)
- [ ] Confirm hub's public IP allows inbound `22/tcp` from anywhere (LA does — see `policies/hub-network-policy.conf`)
- [ ] Identify the OLD peer pubkey to remove
- [ ] Decide the new peer's `allowed-ips` (typically the same slot as the old peer)

## Procedure (client peer rotation)

Run from your local workstation, **via public-IP SSH** to the hub:

```bash
ssh root@<HUB_PUBLIC_IP> 'set -e

OLD_PUB="<old-peer-pubkey>"
HUB_PUB="<hub-pubkey>"
HUB_ENDPOINT="<hub-public-ip>:51820"

# Generate fresh material on the hub (never leaves this command)
PRIV=$(wg genkey)
PUB=$(echo "$PRIV" | wg pubkey)
PSK=$(wg genpsk)

# Stage PSK for `wg set` (file handle, not env var, so it doesnt show in /proc)
PSKF=$(mktemp); chmod 600 "$PSKF"; echo "$PSK" > "$PSKF"

# Atomic-ish: remove OLD first, then add NEW with the same allowed-ips
wg set wg0 peer "$OLD_PUB" remove
wg set wg0 peer "$PUB" preshared-key "$PSKF" allowed-ips <NEW_ALLOWED_IPS>

# Persist + clean
wg-quick save wg0
shred -u "$PSKF"

# Print the client config — this output reaches you because SSH is via public IP,
# not via the now-dead tunnel
echo ""
echo "[Interface]"
echo "PrivateKey = $PRIV"
echo "Address = <client-tunnel-ip>/24"
echo ""
echo "[Peer]"
echo "PublicKey = $HUB_PUB"
echo "PresharedKey = $PSK"
echo "AllowedIPs = <client-allowed-ips>"
echo "Endpoint = $HUB_ENDPOINT"
echo "PersistentKeepalive = 25"
'
```

Why remove OLD before adding NEW: if `OLD` and `NEW` would share an `allowed-ip`, doing `add NEW` first triggers the implicit move described above. Doing `remove OLD` first means the IP is unowned for a few microseconds, then explicitly assigned to `NEW` — same end-state, no race.

## Client-side update

Paste the printed `[Interface]` + `[Peer]` block into the WireGuard client (Windows GUI: Edit selected tunnel → replace contents → Save). Tunnel auto-reconnects within 2-3 seconds. Verify handshake on the hub:

```bash
ssh root@<HUB_PUBLIC_IP> 'wg show wg0 | grep -A4 "<NEW_PUB>"'
```

`latest handshake` should be within the last 30 seconds.

## Verification (privacy-side)

If the rotated profile is full-tunnel:

- **DNS** — visit `https://dnsleaktest.com`. Resolvers should be the dnscrypt upstreams (Cloudflare, Quad9, Mullvad, AdGuard, NextDNS, Anexia/Digitale Gesellschaft). If you see your local ISP, add `DNS = 10.8.0.1` under `[Interface]`.
- **IPv4 egress** — `curl -s https://ifconfig.me/ip` should return the hub's public IP.
- **IPv6** — `curl -6 -s https://ifconfig.me/ip` should error out (kill-switch blocks v6 since `::/0` isn't in `AllowedIPs`). If it returns an IPv6 from your ISP, IPv6 is leaking — add `::/0` to `AllowedIPs`.

## Why generate on the hub, not the client?

The trade-off:

- **Client-side gen** is more secure — private key never leaves the device. Best practice.
- **Hub-side gen** is faster and works for clients that don't have `wg` tooling (e.g., raw Windows boxes without admin/CLI). Private key transits the SSH session once and lands in chat history if you used a tool that logs it.

For Windows operator workstations without `wg` in PATH (and with DPAPI-encrypted configs that resist programmatic editing), hub-side gen is the pragmatic choice. **Rotate again** if the hub-side material ends up logged anywhere it shouldn't.

If client-side gen is feasible, the WireGuard for Windows GUI has a regenerate button (curved arrow) next to the `PrivateKey` field in the Edit tunnel view — click it, copy the new pubkey, send only the **public** key to whoever maintains the hub registration.

## Two-profile pattern (split + full)

If a workstation needs both admin access (low-burden, default) and full-tunnel privacy (on-demand for untrusted networks), use **two WireGuard profiles with the same keypair, different `AllowedIPs`**:

| Profile | `AllowedIPs` | `DNS` | When to use |
|---------|--------------|-------|-------------|
| `EH-admin` (split) | `10.8.0.0/24, 10.9.0.0/24` | (optional) | Default — admin work, dev, browsing via local ISP |
| `EH-full` (full)   | `0.0.0.0/0`                | `10.8.0.1` | Coffee shops, hotels, paranoid mode |

Both profiles register **once** on the hub. The hub doesn't see the `AllowedIPs` difference — that's a client-side routing decision. Toggle between profiles in the WireGuard GUI; only one is active at a time.
