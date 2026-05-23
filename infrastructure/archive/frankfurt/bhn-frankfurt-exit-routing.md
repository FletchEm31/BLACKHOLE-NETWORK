# Frankfurt Exit Routing — Phase 1

How VPN client traffic on the "full" profile gets routed via Frankfurt's public IP instead of LA's. Phase 1: routing only (DNS still exits via LA — Phase 2 work).

**Status:** designed + scripts committed 2026-05-12; deploy is operator-side on LA.

---

## Why

Today operator's "full" WireGuard profile (`AllowedIPs = 0.0.0.0/0`) routes all client traffic through LA. LA's kernel routes the unmasqued inner packets out its own enp1s0 → exits as LA's US public IP (149.28.91.100).

Target: route those packets out via wg1 to Frankfurt, which masquerades them out enp1s0 → exits as Frankfurt's German public IP (192.248.187.208).

| Goal | Why |
|------|-----|
| Jurisdictional isolation | Operator's web browsing exits a non-US IP (DE, outside 5 Eyes; some 9-Eyes concerns but vastly preferable to US for personal browsing privacy) |
| Use the FRA bandwidth | Already paying ~$12/mo for FRA + 2TB/month allowance; currently idle |
| Reduce LA's exposure | LA hosts BHN's brain (PG, n8n, HORIZON). Less reason for LA's egress to be tied to operator's browsing patterns |

## Architecture — packet flow

### Today (Phase 0 state)

```
op-PC (10.8.0.4)
  │  AllowedIPs=0.0.0.0/0 → encapsulates ALL outbound, sends to LA:51820
  ▼
LA wg0 (10.8.0.1)
  │  decapsulates; inner packet has src=10.8.0.4 dst=<arbitrary internet IP>
  │  kernel routes via DEFAULT route → enp1s0
  │  MASQUERADE (SNAT) → src becomes 149.28.91.100
  ▼
public internet — exits LA's US IP
```

### Phase 1 target

```
op-PC (10.8.0.4)
  │  AllowedIPs=0.0.0.0/0 (no client config change)
  ▼
LA wg0 (10.8.0.1)
  │  PREROUTING mangle marks packet (fwmark 0x100) for "came in via wg0"
  │  ip rule: marked packets → routing table 100
  │  table 100 default → 10.9.0.2 dev wg1
  ▼
LA wg1 (10.9.0.1, encapsulated by wg again)
  ▼
Frankfurt wg1 (10.9.0.2)
  │  decapsulates; inner packet has src=10.8.0.4 dst=<internet>
  │  forwards via enp1s0 (already wg1→enp1s0 FORWARD allowed per FRA's current UFW state)
  │  MASQUERADE (SNAT) → src becomes 192.248.187.208
  ▼
public internet — exits Frankfurt's DE IP
```

Return path is the inverse: packet from internet → Frankfurt enp1s0 (matches conntrack from MASQUERADE) → wg1 → LA wg1 (10.9.0.1) → LA routes to 10.8.0.4 via wg0 → op-PC.

## Behavior matrix

| Source | Traffic type | Routes via | Notes |
|--------|--------------|-----------|-------|
| op-PC "admin" profile | Internal (10.8.0.0/24, 10.9.0.0/24) | LA hub, then wg-internal | unchanged — split tunneling keeps internet on local ISP |
| op-PC "full" profile | Internet (0.0.0.0/0) | LA → wg1 → Frankfurt | **changes after Phase 1 deploy** |
| op-phone (similar profiles) | Same as PC | Same | |
| NJ (10.8.0.5) | Internal BHN only (AllowedIPs=10.8.0.0/24 on its peer entry for LA) | LA, no internet routing | unchanged — NJ's trading API calls go from NJ's own eth0, not via tunnel |
| LA's own outbound | apt updates, n8n→Anthropic, dnscrypt-proxy upstream | enp1s0 direct | unchanged — LA-originated traffic isn't `-i wg0`, so doesn't get marked |

## What gets installed on LA

Three things, all idempotent:

1. **iptables mangle rule** — mark fwmark 0x100 on PREROUTING -i wg0
2. **ip rule** — fwmark 0x100 lookup table 100 priority 100
3. **ip routes in table 100** — internal nets stay local, default via Frankfurt

Plus FORWARD chain allows for marked wg0↔wg1 traffic (existing UFW state may already cover this; script verifies).

## Persistence across reboot

The mangle rule + ip rule + ip routes are kernel state, not config files. They evaporate on reboot. Two persistence mechanisms used together:

1. **wg-quick PostUp/PostDown hooks** — wg-quick brings up wg0 on boot, runs the hook script that re-applies the policy routing
2. **Idempotent script** — re-runnable any time without breaking existing state (defends against partial-apply scenarios)

Adds these lines to `/etc/wireguard/wg0.conf` `[Interface]` section:
```ini
PostUp = /etc/wireguard/bhn-frankfurt-exit.sh up
PostDown = /etc/wireguard/bhn-frankfurt-exit.sh down
```

The `up` mode applies routing; `down` mode removes it. wg-quick handles invocation on `systemctl start/stop/restart wg-quick@wg0`.

## What needs to be true on Frankfurt

Frankfurt already has (per current STATUS):
- ✅ `net.ipv4.ip_forward = 1`
- ✅ iptables FORWARD `wg1 → enp1s0` ACCEPT
- ⚠ Verify: iptables NAT `MASQUERADE` covers source `10.8.0.0/24` (currently might be only `10.9.0.0/24`)

The setup script's pre-flight verifies the MASQUERADE rule. If missing, prints the exact iptables command for operator to add.

## Deploy steps (operator-side)

### Step 1 — Verify Frankfurt's NAT covers 10.8.0.0/24

```bash
ssh frankfurt 'iptables -t nat -S POSTROUTING | grep MASQUERADE'
# Expected: a rule like -A POSTROUTING -o enp1s0 -j MASQUERADE
# Or specifically: -A POSTROUTING -s 10.8.0.0/24 -o enp1s0 -j MASQUERADE
# If the rule is scoped to 10.9.0.0/24 only, add:
ssh frankfurt 'iptables -t nat -A POSTROUTING -s 10.8.0.0/24 -o enp1s0 -j MASQUERADE && iptables-save > /etc/iptables/rules.v4'
```

### Step 2 — Deploy the script on LA

```bash
# From operator's PC (or LA-cloned repo):
scp scripts/bhn-frankfurt-exit.sh root@10.8.0.1:/etc/wireguard/bhn-frankfurt-exit.sh
ssh root@10.8.0.1 'chmod 750 /etc/wireguard/bhn-frankfurt-exit.sh'
```

### Step 3 — Dry-run check

```bash
ssh root@10.8.0.1 '/etc/wireguard/bhn-frankfurt-exit.sh dry-run'
# Should print every iptables/ip-rule/ip-route command that WOULD be applied
# Verify no surprises before next step
```

### Step 4 — Apply (with 5-min auto-rollback)

```bash
ssh root@10.8.0.1 '/etc/wireguard/bhn-frankfurt-exit.sh apply'
# Output ends with: "Auto-rollback scheduled for 5 minutes from now. Run confirm if traffic verifies."
```

### Step 5 — Test from operator's PC on "full" profile

```bash
# Switch WG client to "full" profile on operator's PC
# Then:
curl https://api.ipify.org
# Expected output BEFORE Phase 1: 149.28.91.100 (LA's IP)
# Expected output AFTER  Phase 1: 192.248.187.208 (Frankfurt's IP)

# Also verify reach is intact:
curl https://www.google.com > /dev/null && echo "✓ google reachable" || echo "✗ NO GOOGLE — rollback"
ping -c 3 1.1.1.1
```

### Step 6 — Confirm (cancels rollback) or rollback

```bash
# If verification passed:
ssh root@10.8.0.1 '/etc/wireguard/bhn-frankfurt-exit.sh confirm'

# If traffic broken — manually:
ssh root@10.8.0.1 '/etc/wireguard/bhn-frankfurt-exit.sh rollback'

# Or do nothing for 5 min — auto-rollback fires
```

### Step 7 — Install reboot persistence (only after Step 6 confirm)

```bash
ssh root@10.8.0.1 '/etc/wireguard/bhn-frankfurt-exit.sh install-persistence'
# This patches /etc/wireguard/wg0.conf to add PostUp/PostDown hooks
# Routing now survives reboot
```

### Step 8 — Reboot survival test (optional but recommended)

```bash
ssh root@10.8.0.1 'systemctl restart wg-quick@wg0'
# Wait 5 seconds, then from operator's PC re-test:
curl https://api.ipify.org
# Should STILL show Frankfurt's IP
```

## Risks + mitigations

| Risk | Mitigation in script |
|------|---------------------|
| Lockout from bad rule | 5-min auto-rollback timer; pre-flight verifies wg1 handshake before applying |
| iptables-persistent overwrite | Script saves changes to `/etc/iptables/rules.v4` via `iptables-save` |
| wg0.conf comments stripped by save | Script edits the file directly via `awk`/`sed`, doesn't use `wg-quick save` |
| Re-apply breaks existing rules | All `iptables -A`/`ip rule add` ops check first; idempotent |
| Frankfurt's MASQUERADE doesn't cover 10.8.0.0/24 | Pre-flight Step 1 above; script flags it explicitly |
| LA-side wg0/wg1 interface goes down mid-apply | Script aborts; rollback timer still fires |

## Known limitations of Phase 1

| Limitation | Phase 2 fix |
|-----------|-------------|
| DNS queries from "full" profile resolve via LA's dnscrypt-proxy (DNS query exits LA's enp1s0, not Frankfurt's). DNS leak from a privacy-purist perspective. | Phase 2: rebind Frankfurt's dnscrypt-proxy to listen on `10.9.0.2:53`, update operator's "full" profile to `DNS = 10.9.0.2`. Both queries + browsing then exit DE. |
| ICMP from LA tools (mtr, traceroute) to internet still exits LA | Acceptable — LA-originated traffic intentionally untouched |
| MTU pessimization (double-encapsulation) may slow some sites slightly | WG default MTU 1420 + Frankfurt MASQUERADE generally handles. If issues: lower client MTU to 1280. |
| Latency penalty: PC → LA → FRA → internet adds ~140ms vs LA-direct | Acceptable for privacy/jurisdiction trade; web browsing tolerates +140ms |

## Phase 2 — DNS via Frankfurt (separate session)

Not in this commit. When ready:

1. SSH to Frankfurt, edit dnscrypt-proxy config:
   - `listen_addresses = ['10.9.0.2:53']`
   - Restart dnscrypt-proxy
2. UFW: allow inbound 53/udp+tcp on wg1 to 10.9.0.2
3. Update operator's WG "full" client profile: `DNS = 10.9.0.2`
4. Reconnect on operator's PC, verify via dnsleaktest.com → should show Frankfurt's resolvers

---

## Files in this Phase 1 commit

| File | Purpose |
|------|---------|
| `scripts/bhn-frankfurt-exit.sh` | Multi-mode script (dry-run / apply / confirm / rollback / status / up / down / install-persistence) |
| `infrastructure/docs/bhn-frankfurt-exit-routing.md` | This doc |
| `STATUS.md` Frankfurt section | Updated to reflect exit-routing now active (after operator deploys) |
| `BHN-INFRASTRUCTURE.txt` | Frankfurt role line updated |
