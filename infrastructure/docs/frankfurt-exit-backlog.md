# Frankfurt exit routing — backlog (deprioritized 2026-05-13)

## Current state

LA's `EH-admin` profile (split-tunnel, mesh-only) works fine.
LA's `EH-full` profile (full-tunnel to Frankfurt exit) does **not** work — clients on the full-tunnel profile lose internet access entirely. Frankfurt's IP doesn't appear in `curl https://api.ipify.org` from those clients; instead, the request times out.

Per STATUS.md:70, the original diagnosis was that **Frankfurt is missing a NAT MASQUERADE rule** for `10.8.0.0/24` source on its `enp1s0`. The rule should be:

```
iptables -t nat -A POSTROUTING -s 10.8.0.0/24 -o enp1s0 -j MASQUERADE
```

`scripts/bhn-frankfurt-masquerade-fix.sh` was written to apply this. As of the start of tonight's session, this had **not been applied** on FRA. Whether it's been applied since this doc was written: check `STATUS.md` or run the script's `status` mode on FRA.

## What was attempted tonight (2026-05-13)

Debugging tried (in order):

1. **Removed the unconditional `MASQUERADE -o enp1s0` PostUp from `/etc/wireguard/wg0.conf` on LA** so that egress NAT could be made conditional on a fwmark.
2. **Added a fwmark-conditional MASQUERADE**: `iptables -t nat -A POSTROUTING -o enp1s0 -m mark ! --mark 0x100 -j MASQUERADE`. Theory: WG-quick + the PostUp script `/etc/wireguard/bhn-frankfurt-exit.sh up` sets fwmark `0x100` on packets destined for the FRA tunnel; the inverse-fwmark MASQUERADE NATs everything else direct.
3. **Added `iptables -A FORWARD -i wg0 -o enp1s0 -j ACCEPT`** to permit forwarding from wg0 to the public NIC. This is technically redundant with the existing `PostUp = iptables -A FORWARD -i wg0 -j ACCEPT` (which is direction-less and broader) but was added as an experiment.
4. **Tested full-tunnel client connectivity** — still broken.

After debugging, LA's general internet egress was broken because step 1 left LA with no unconditional MASQUERADE, AND fwmark `0x100` isn't being set on traffic from LA itself (only on tunnel-forwarded traffic when bhn-frankfurt-exit.sh is configured correctly). Result: LA hub can't NAT its own outbound, AND can't NAT the full-tunnel peers' outbound either.

**Tonight's restoration**: `scripts/bhn-la-restore.sh` reverses steps 1, 2, and 3 to bring LA's general egress back online. It does NOT re-attempt the Frankfurt exit routing.

## Known-good baseline (pre-tonight)

Before tonight, LA's working config had:

```
[Interface]
...
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT
PostUp = iptables -A FORWARD -o wg0 -j ACCEPT
PostUp = iptables -t nat -A POSTROUTING -o enp1s0 -j MASQUERADE     # ← removed during debugging; restore script puts back
PostUp = /etc/wireguard/bhn-frankfurt-exit.sh up                    # ← this is the FRA routing apply
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT
PostDown = iptables -D FORWARD -o wg0 -j ACCEPT
PostDown = iptables -t nat -D POSTROUTING -o enp1s0 -j MASQUERADE   # ← removed; restored
PostDown = /etc/wireguard/bhn-frankfurt-exit.sh down
```

With the unconditional `-o enp1s0 -j MASQUERADE`, **all** wg0-traversing traffic gets NAT'd out LA's NIC — including full-tunnel traffic that's supposed to be re-routed to FRA. So even when this was "working," full-tunnel traffic was almost certainly egressing LA's IP instead of Frankfurt's. The bug pre-exists tonight's debugging.

## What we know about the actual fix

The exit routing should look like:

1. **Full-tunnel client** sets `AllowedIPs = 0.0.0.0/0` → packets go to LA's wg0
2. **LA wg0** receives the packet, kernel routing decides next hop
3. `/etc/wireguard/bhn-frankfurt-exit.sh up` should install a **policy route** like:
   - `ip rule add fwmark 0x100 table 100 priority 100`
   - `ip route add default via 10.9.0.2 dev wg0 table 100`
4. **Marker for "this is a full-tunnel packet"**: a netfilter rule that marks packets matching some criterion with fwmark `0x100`. *This is the missing piece in the production config — the script may install the policy route table but not the marking rule, OR may install both but be matching the wrong packets.*
5. **Then** packets with fwmark `0x100` go into table 100, which routes them via the FRA tunnel.
6. **At FRA**, the inbound packet on `wg0` (FRA-side) needs to be FORWARDed to `enp1s0` and SNAT'd via the Frankfurt MASQUERADE rule from `bhn-frankfurt-masquerade-fix.sh`.

The combination of (4) being unverified + (6) being unapplied is why full-tunnel doesn't work, even before tonight's debugging.

## What NOT to try again

- **Removing the unconditional MASQUERADE PostUp without a working fwmark replacement.** That's what we did tonight and it broke general LA egress. Step 1 from the attempted-tonight list.
- **Adding `iptables -A FORWARD -i wg0 -o enp1s0 -j ACCEPT`.** This is already covered by the broader `-i wg0 -j ACCEPT` PostUp; adding a more-specific duplicate doesn't help and pollutes the chain.

## What to try next (fresh session)

In rough priority order:

1. **Apply `bhn-frankfurt-masquerade-fix.sh apply` on FRA first.** This is the FRA-side `-s 10.8.0.0/24 -o enp1s0 -j MASQUERADE` rule. Without this, even if LA-side policy routing is perfect, FRA can't NAT the inbound 10.8.0.x packet to its own IP, so the return path fails. This is the load-bearing piece per STATUS.md:70.
2. **Then test with the current `bhn-frankfurt-exit.sh up` PostUp left in place.** It may already work once FRA-side MASQUERADE lands.
3. **If still broken, instrument `bhn-frankfurt-exit.sh`:** add `set -x` + log every `ip rule` / `ip route` / `iptables` call to a file. Run apply; capture the file. Verify each step actually took effect with corresponding `ip rule list`, `ip route show table 100`, `iptables -t mangle -L -n -v`.
4. **Manual fwmark experiment** — instead of trusting bhn-frankfurt-exit.sh, manually:
   - Add an OUTPUT or PREROUTING rule on LA that marks packets matching the full-tunnel client's tunnel IP (e.g. `10.8.0.4` for FLETCH-DESKTOP) with fwmark `0x100`.
   - Verify with `iptables -t mangle -L -n -v` — the rule should have a non-zero packet counter when client makes traffic.
   - Then policy route fwmark `0x100` → table 100 → `default via 10.9.0.2`.
5. **Verify packet path with tcpdump** on both ends: `tcpdump -i wg0 -n 'host <client-IP>'` on LA + `tcpdump -i enp1s0 -n 'host <target>'` on FRA. The packet should appear on FRA's wg0 (inbound from LA), then get NAT'd and reappear on enp1s0 outbound with source = `192.248.187.208`.

## Files involved

- `/etc/wireguard/wg0.conf` (LA) — interface + peer config, PostUp/PostDown hooks
- `/etc/wireguard/bhn-frankfurt-exit.sh` (LA) — the policy-routing apply/rollback script
- `scripts/bhn-frankfurt-exit.sh` (repo) — same script's repo copy (v3-era)
- `scripts/bhn-frankfurt-masquerade-fix.sh` (repo, not deployed) — the FRA-side MASQUERADE installer
- `scripts/bhn-la-restore.sh` (repo, ready) — tonight's emergency restore

## Status

**Deprioritized.** Operator's call: focus on other monitoring + trading work; revisit FRA exit routing when there's a clean window to apply the FRA-side fix and instrument the LA-side script with verbose logging. Until then, `EH-admin` (split-tunnel) works fine for daily operator use; `EH-full` is broken and clients should be told to switch back to `EH-admin` if they had `EH-full` active.
