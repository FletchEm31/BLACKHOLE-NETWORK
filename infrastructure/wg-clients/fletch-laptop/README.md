# FLETCH-LAPTOP — WireGuard peer onboarding

Adds the operator's laptop as a new WG peer on LA's `wg0`. Mirrors FLETCH-DESKTOP and FLETCH-PHONE's two-profile pattern: one keypair, two `.conf` files differing only in `AllowedIPs` (and `DNS` on the full-tunnel profile).

## Tunnel IP assignment

Recommended: **`10.8.0.7`** (next free slot above current peers).

Current allocations on LA's `wg0/24`:
- `.1` LA hub
- `.2` FLETCH-PHONE
- `.3` (free — historical skip; can reassign)
- `.4` FLETCH-DESKTOP
- `.5` BHN-VPS-NEWJERSEY-US2
- `.6` BHN-HILLSBORO-US3
- `.7` ← **FLETCH-LAPTOP** (proposed)

Alternative: use `.3` if you prefer to fill the gap and keep personal devices in `.2–.4`. Both work; convention isn't strict. The rest of this doc uses `10.8.0.7`.

## Step 1 — Generate the laptop's keypair (on the LAPTOP)

The private key MUST stay on the laptop. Don't paste it back to LA or this doc.

### Windows (WireGuard for Windows GUI)
1. Open WireGuard for Windows → **Add Tunnel → Add empty tunnel…**
2. The GUI auto-generates a keypair. **Copy the `[Interface] PublicKey`** field — that's what LA needs. The matching `PrivateKey` field stays in the tunnel config (DPAPI-encrypted on disk).
3. Don't save yet — you'll paste the rest of the config in Step 4.

### Linux / macOS (CLI)
```bash
# Pick a config path
sudo mkdir -p /etc/wireguard && sudo chmod 700 /etc/wireguard

# Generate the keypair
umask 077
wg genkey | sudo tee /etc/wireguard/fletch-laptop.privkey | wg pubkey | sudo tee /etc/wireguard/fletch-laptop.pubkey
sudo chmod 600 /etc/wireguard/fletch-laptop.privkey

# Show the pubkey — paste this into Step 2's wg-set on LA
sudo cat /etc/wireguard/fletch-laptop.pubkey
```

## Step 2 — Generate PSK on LA + register the peer

This whole block runs on LA over the existing SSH alias.

```bash
ssh la   # or: ssh -J root@frankfurt root@10.8.0.1

# === On LA, as root ===
LAPTOP_PUBKEY='paste-the-pubkey-from-Step-1-here'
LAPTOP_TUNNEL_IP='10.8.0.7'
PSK=$(wg genpsk)

# Apply on LA — adds the peer to wg0 with PSK
echo "$PSK" | wg set wg0 peer "$LAPTOP_PUBKEY" \
    preshared-key /dev/stdin \
    allowed-ips "$LAPTOP_TUNNEL_IP/32"
wg-quick save wg0 >/dev/null

# Verify
wg show wg0 | awk "/^peer: $LAPTOP_PUBKEY/,/^$/" | head -6

# Output PSK ONCE for password manager (this is the only time it appears
# in clear text). Save as EH-WG-FLETCH-LAPTOP-PSK.
echo
echo "==================================================================="
echo "  PSK for EH-WG-FLETCH-LAPTOP-PSK — save to password manager NOW"
echo "==================================================================="
echo "  $PSK"
echo "==================================================================="
unset PSK
```

**Don't proceed to Step 3 until the PSK is saved.** Without it in the PM, you can't reproduce the laptop's tunnel config if WireGuard for Windows ever loses its DPAPI store.

## Step 3 — Update STATUS.md WG peer registry + Secrets inventory

Add a row to `STATUS.md` "WireGuard peer registry" table:

```
| **FLETCH-LAPTOP** | operator laptop | `<LAPTOP_PUBKEY>` | `10.8.0.7/32` | `<dynamic — laptop's home/cafe NAT>` | `FLETCH-LAPTOP-SPLIT` + `FLETCH-LAPTOP-FULL` (same PSK + privkey across both, only `AllowedIPs` + `DNS` differ) |
```

Add to the "🟠 WireGuard pre-shared keys" Secrets inventory table:

```
| `EH-WG-FLETCH-LAPTOP-PSK` | 32-byte symmetric secret | `/etc/wireguard/wg0.conf` (LA, peer `<first 8 chars>…`) | Generated <YYYY-MM-DD> |
```

And to the "🔴 WireGuard private keys" table:

```
| `EH-WG-FLETCH-LAPTOP-Privkey` | WG ed25519 privkey | DPAPI-encrypted in WireGuard for Windows (or /etc/wireguard/fletch-laptop.privkey on Linux/Mac) | Both `FLETCH-LAPTOP-SPLIT` and `FLETCH-LAPTOP-FULL` profiles use same key |
```

Commit the STATUS update.

## Step 4 — Build the laptop's two profile configs

Use `laptop-wg.conf.template` in this directory. Replace the four placeholders:
- `<LAPTOP_PRIVKEY>` — from Step 1 (Windows: in the GUI; Linux/Mac: `sudo cat /etc/wireguard/fletch-laptop.privkey`)
- `<PSK>` — the value from Step 2's banner (paste from PM, then delete from clipboard)
- `<LAPTOP_TUNNEL_IP>` — `10.8.0.7` (or whatever was used in Step 2)
- (no other placeholders — LA hub pubkey + endpoint are baked into the template)

Two profile variants:

### FLETCH-LAPTOP-SPLIT — admin/mesh access only
- `AllowedIPs = 10.8.0.0/24, 10.9.0.0/24` (mesh-only: WG admin, Grafana, n8n, PG)
- No `DNS` override — laptop keeps its local DNS for everything else
- Use this profile for daily work; laptop's traffic to the public internet still exits via the laptop's local ISP

### FLETCH-LAPTOP-FULL — full tunnel through Frankfurt exit
- `AllowedIPs = 0.0.0.0/0, ::/0`
- `DNS = 10.8.0.1` (forces lookups through LA's dnscrypt-proxy)
- Use this profile when you want all laptop traffic to exit at Frankfurt's IP
- ⚠️ Currently broken — see `STATUS.md:70` "Exit-routing for operator 'full' profile" until the Frankfurt MASQUERADE fix is applied

Save both into WireGuard for Windows (or `/etc/wireguard/*.conf` on Linux/Mac) under those exact names. Same privkey + PSK in both; only `AllowedIPs` (+ `DNS` on full) differs.

## Step 5 — UFW on LA — no changes needed

FLETCH-LAPTOP is a *client* peer. LA never initiates traffic to it — laptop initiates to LA via the WG underlay (UDP 51820, already `ALLOW IN Anywhere`). No new outbound rules needed (unlike server peers FRA/NJ/Hillsboro).

## Step 6 — Verify

```bash
# On the laptop, activate FLETCH-LAPTOP-SPLIT, then:
ping -c 3 10.8.0.1            # LA hub
ping -c 3 10.8.0.5            # NJ
ping -c 3 10.9.0.2            # Frankfurt
curl http://10.8.0.1:3000     # Grafana login page

# From LA:
ssh la 'wg show wg0 | awk "/^peer: <LAPTOP_PUBKEY>/,/^$/"'
# Should show: latest handshake within last 2 min, transfer > 0
```

If all four succeed, FLETCH-LAPTOP is fully onboarded.

## Rollback

If the laptop tunnel needs to be removed (lost device, key compromise, etc.):

```bash
# On LA
LAPTOP_PUBKEY='<the-pubkey>'
wg set wg0 peer "$LAPTOP_PUBKEY" remove
wg-quick save wg0
```

Delete the PSK + privkey entries from password manager. Update STATUS.md to mark the peer as decommissioned.
