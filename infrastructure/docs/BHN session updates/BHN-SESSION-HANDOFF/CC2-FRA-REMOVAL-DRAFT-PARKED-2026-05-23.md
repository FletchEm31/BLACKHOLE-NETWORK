# FRA Egress Peer Removal — Parked Draft

**Status:** APPROVED, PARKED — DO NOT EXECUTE until Vultr VPS destroyed.
**Drafted:** 2026-05-23 by CC2
**Operator decision:** Option A confirmed (migrate client 10.8.0.8 from FRA → Hillsboro before peer removal). Execution held until operator destroys `EH|VPS-FRANKFURT-EU1` in Vultr (Monday 2026-05-25 earliest — operator locked out of Vultr until then).
**When to run:** After operator confirms Vultr destroy complete. Run the whole sequence (Phases 0–4) in one clean pass.

---

## Pre-execution check (Monday)

Before running Phase 0, verify:

```bash
# FRA box should now be unreachable
ping -c 3 192.248.187.208           # expect: 100% packet loss
ssh root@10.8.0.1 'wg show wg0 | grep -A1 "192.248.187.208"'
# expect: handshake timestamp far in the past (Vultr destroy → tunnel dead)
```

If FRA still pingable, operator hasn't destroyed yet — STOP, ask before proceeding.

---

## Inventory of FRA-related state on LA (captured 2026-05-23)

| Item | Where | Disposition |
|---|---|---|
| FRA peer block (`zkfJNbdL9P…`, AllowedIPs `0.0.0.0/0`, endpoint `192.248.187.208:51821`) | `/etc/wireguard/wg0.conf` | Remove |
| Orphan peer block (`8jYwmEYk…`, no AllowedIPs, no Endpoint, has PSK) | `/etc/wireguard/wg0.conf` | Remove (unrelated to FRA but dead) |
| `PostUp = /etc/wireguard/bhn-frankfurt-exit.sh up` | `/etc/wireguard/wg0.conf` `[Interface]` | Remove (keep MASQUERADE + FORWARD lines) |
| `PostDown = /etc/wireguard/bhn-frankfurt-exit.sh down` | `/etc/wireguard/wg0.conf` `[Interface]` | Remove |
| `ip rule: 100: from all fwmark 0x100 lookup 100` | live runtime | Remove |
| `ip route … table 100` (default via 10.9.0.2, hub subnet, LA public IP) | live runtime | Flush |
| `iptables -t mangle PREROUTING` rule: `-s 10.8.0.8 -j MARK 0x100` | live runtime | **Re-target to 0x200** (Phase 1) |
| `iptables -t mangle PREROUTING` rule: `-s 10.8.0.9 -d 5.78.94.237 -j RETURN` | live runtime | Leave alone |
| `iptables FORWARD: -i wg0 -o wg0 -j ACCEPT` | live runtime | Leave (Hillsboro hairpin still needs it) |
| `/etc/wireguard/bhn-frankfurt-exit.sh` script | LA filesystem | Move to `/etc/wireguard/archive/` |

Client 10.8.0.8 is operator's home-ISP-endpoint client (peer `yYDqTNAf…`, endpoint `68.96.70.83:57735`). Was pushing 48 MiB / 384K packets through FRA. Migrating to Hillsboro (Option A).

---

## Commands — phased, with verification gates

### Phase 0 — Backup wg0.conf
```bash
ssh root@10.8.0.1 'cp /etc/wireguard/wg0.conf /etc/wireguard/wg0.conf.bak.pre-fra-removal.$(date -u +%Y%m%d-%H%M%S) && ls -la /etc/wireguard/wg0.conf.bak.*'
```

### Phase 1 — Migrate client 10.8.0.8 to Hillsboro
```bash
ssh root@10.8.0.1 'iptables -t mangle -L PREROUTING --line-numbers -n -v'
# Confirm rule numbers. If they shifted from the 2026-05-23 capture, adjust accordingly.

ssh root@10.8.0.1 'iptables -t mangle -D PREROUTING -s 10.8.0.8 -i wg0 -j MARK --set-mark 0x100 && \
                   iptables -t mangle -I PREROUTING 2 -s 10.8.0.8 -i wg0 -j MARK --set-mark 0x200 && \
                   iptables -t mangle -L PREROUTING -n -v --line-numbers | head -10'
```
**Verification gate:** ask operator to confirm 10.8.0.8 client still has internet, public IP is now Hetzner not Frankfurt. Don't proceed until confirmed.

### Phase 2 — Edit wg0.conf
```bash
ssh root@10.8.0.1 'python3 - <<'\''PY'\''
import re
p = "/etc/wireguard/wg0.conf"
s = open(p).read()
s = re.sub(r"^(PostUp|PostDown) = /etc/wireguard/bhn-frankfurt-exit\.sh (up|down)\n", "", s, flags=re.M)
s = re.sub(r"\n\[Peer\]\nPublicKey = zkfJNbdL9Ptdxv\+fxwV2e1q0mbCR5Z/9T80QanSxKA8=\n(?:.*\n)+?(?=\n\[Peer\]|\Z)", "\n", s)
s = re.sub(r"\n\[Peer\]\nPublicKey = 8jYwmEYkhXWi1VWCWBhv9MTsLw\+UULkdwGlf5t1KCVA=\n(?:.*\n)+?(?=\n\[Peer\]|\Z)", "\n", s)
open(p + ".new", "w").write(s)
PY
chmod 600 /etc/wireguard/wg0.conf.new && \
diff /etc/wireguard/wg0.conf /etc/wireguard/wg0.conf.new'
```
**Verification gate:** review diff before swap. If correct:
```bash
ssh root@10.8.0.1 'mv /etc/wireguard/wg0.conf.new /etc/wireguard/wg0.conf && \
                   grep -c "^\[Peer\]" /etc/wireguard/wg0.conf'
# Expect: 7 peers (was 9: 9 − FRA − orphan = 7)
```

### Phase 3 — Apply changes to live runtime (no wg-quick restart)
```bash
ssh root@10.8.0.1 'wg set wg0 peer zkfJNbdL9Ptdxv+fxwV2e1q0mbCR5Z/9T80QanSxKA8= remove && \
                   wg set wg0 peer 8jYwmEYkhXWi1VWCWBhv9MTsLw+UULkdwGlf5t1KCVA= remove && \
                   /etc/wireguard/bhn-frankfurt-exit.sh rollback'
```
The helper's `rollback` flushes table 100, removes ip rule fwmark 0x100, removes its own generic mangle MARK (won't touch per-client rules), and removes FORWARD wg0→wg0 from FRA phase if it added it.
```bash
ssh root@10.8.0.1 'wg show wg0 | grep -E "^(peer|interface):" | wc -l; \
                   ip rule show; \
                   ip route show table 100; \
                   iptables -t mangle -L PREROUTING -n -v --line-numbers'
```
**Verification gate:**
- 7 peers in `wg show wg0`
- no `fwmark 0x100` in `ip rule`
- `ip route show table 100` empty
- mangle PREROUTING: 10.8.0.8 line now shows MARK 0x200

### Phase 4 — Archive helper script
```bash
ssh root@10.8.0.1 'mkdir -p /etc/wireguard/archive && \
                   mv /etc/wireguard/bhn-frankfurt-exit.sh /etc/wireguard/archive/ && \
                   ls /etc/wireguard/'
```

### Phase 5 (optional, not in this session) — Persistence test
```bash
ssh root@10.8.0.1 'wg-quick down wg0 && wg-quick up wg0 && wg show wg0 | grep ^peer | wc -l'
```
Drops all mesh peers momentarily — defer to next natural reboot rather than running explicitly.

---

## Rollback (if any phase breaks something)
```bash
ssh root@10.8.0.1 'cp /etc/wireguard/wg0.conf.bak.pre-fra-removal.<TIMESTAMP> /etc/wireguard/wg0.conf && \
                   wg-quick down wg0 && wg-quick up wg0'
```

---

## Out-of-scope for this draft
- Vultr destroy of `EH|VPS-FRANKFURT-EU1` — operator-only, must happen before this draft runs
- `10.9.0.2 dev wg0 scope link` entry in main routing table — should auto-disappear when peer removed; verify in Phase 3
- Repo-side cleanup (if `scripts/bhn-frankfurt-exit.sh` exists in repo as well as on LA) — separate workstream
- Wireguard key rotation — wg0.conf was dumped to chat 2026-05-23 (LA PrivateKey + all peer PSKs visible). No observable compromise, but rotation is a future hardening item.
