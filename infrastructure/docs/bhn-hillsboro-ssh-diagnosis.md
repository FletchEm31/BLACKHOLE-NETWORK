# Hillsboro SSH — Hanging-Connection Diagnosis Runbook

Exact, ordered steps to diagnose and recover a **hanging SSH** to Hillsboro (`BHN-HILLSBORO-US3`, Hetzner). Built for the operator to execute in a live session — this doc does not run anything.

**Context:** Hillsboro is BHN's operational egress proxy (tinyproxy `10.8.0.6:8888`). SSH to it normally goes over the WireGuard mesh to `10.8.0.6`. A wedge on 2026-05-13 self-recovered by 2026-05-14 (WG handshake came back healthy); this runbook is the preserved playbook for next time.

**Addresses:** WG mesh `10.8.0.6` (wg0) · public `5.78.94.237` (Hetzner) · provider out-of-band: Hetzner Cloud Console (vKVM).

---

## Triage principle

"SSH hangs" is ambiguous — isolate **which layer** is stuck before changing anything. Work bottom-up: WG handshake → ICMP over tunnel → TCP/22 → SSH auth. Whichever layer first fails is the real problem; don't restart sshd if the tunnel is down.

## Step 0 — What kind of hang?

```bash
ssh -vvv root@10.8.0.6
```

Read where it stalls:
- Stalls at `Connecting to 10.8.0.6 ...` → **network/tunnel layer** (Steps 1–3).
- Reaches `Connection established` then hangs before banner → **TCP up, sshd or MTU** (Steps 3–4).
- Gets the SSH banner then hangs at auth → **sshd/PAM/host load** (Step 5).

## Step 1 — WireGuard handshake (most likely culprit)

A stale handshake is what wedged it on 2026-05-13.

```bash
# From LA (the hub):
ssh root@10.8.0.1 'wg show wg0 | grep -A4 <hillsboro-pubkey-or-just-look-for-10.8.0.6>'
# Look at "latest handshake" — should be < ~2-3 min. If "never" or many minutes old, handshake is dead.
```

Fix attempts, least-disruptive first:
```bash
# a) Force a fresh handshake by sending traffic toward Hillsboro from LA:
ssh root@10.8.0.1 'ping -c 3 10.8.0.6'

# b) If still stale, bounce the peer from LA side (does NOT drop the whole interface):
#    (re-add the peer / or restart wg-quick on the affected end)
ssh root@10.8.0.1 'systemctl restart wg-quick@wg0'   # affects LA hub briefly — confirm timing is OK
```

## Step 2 — Is the host even alive? (out-of-band)

If the tunnel can't be coaxed back, confirm Hillsboro itself is up before assuming SSH is the problem:

```bash
ping -c 3 5.78.94.237          # Hetzner public IP — ICMP may be filtered, not definitive
```

If no signs of life: **Hetzner Cloud Console → vKVM** for the Hillsboro server. This is the true out-of-band path — you get a console regardless of SSH/WG/network state. From the console you can:
- `systemctl status wg-quick@wg0` and `wg show` (is WG up on Hillsboro's side?)
- `systemctl status sshd`
- check `journalctl -u wg-quick@wg0 --since "30 min ago"`
- reboot if wedged at the OS level.

## Step 3 — TCP reachability to port 22 over the tunnel

```bash
# From LA:
ssh root@10.8.0.1 'nc -vz -w 5 10.8.0.6 22'   # or: timeout 5 bash -c "</dev/tcp/10.8.0.6/22"
```
- Connection refused → sshd down (Step 5 via console).
- Timeout → still a tunnel/routing/firewall issue (back to Steps 1–2; also check Step 4 MTU).

## Step 4 — MTU / packet-size hang (handshake OK, session hangs)

Double-encapsulation can cause large packets to black-hole — symptom is "connects then freezes on first big output."
```bash
# Confirm small packets pass but large ones don't (from LA):
ssh root@10.8.0.1 'ping -c 3 -M do -s 1200 10.8.0.6 && ping -c 3 -M do -s 1400 10.8.0.6'
```
If 1200 passes but 1400 fails → MTU. Workaround for the session:
```bash
ssh -o IPQoS=throughput root@10.8.0.6
# Durable fix is lowering wg MTU on the affected interface (live change — operator session).
```

## Step 5 — sshd / host-side (banner seen, or refused)

Via vKVM console (Step 2) on Hillsboro:
```bash
systemctl status sshd
journalctl -u sshd --since "1 hour ago" | tail -50
# Self-lockout check — fail2ban/CrowdSec may have banned the source after retries:
fail2ban-client status sshd 2>/dev/null
cstatus 2>/dev/null; cscli decisions list 2>/dev/null   # if CrowdSec present
# Load/OOM check:
uptime; dmesg | tail -20   # OOM-killer would show here
```
Unban if self-locked:
```bash
fail2ban-client set sshd unbanip <your-source-ip>
# or CrowdSec:
cscli decisions delete --ip <your-source-ip>
```

---

## Decision tree (fast path)

| Symptom | First action |
|---------|--------------|
| `ssh -vvv` stalls at "Connecting" | Step 1 — check/refresh WG handshake from LA |
| Handshake "never"/stale, ping won't revive | Step 2 — Hetzner vKVM console |
| Connects, freezes on output | Step 4 — MTU test |
| `Connection refused` | sshd down → Step 5 via console |
| Banner then auth hang | Step 5 — fail2ban/CrowdSec self-lock, or host load |

## Recovery escalation order

1. Force handshake from LA (Step 1a) — non-disruptive.
2. Bounce the WG peer / `wg-quick@wg0` on LA (Step 1b) — brief.
3. Hetzner vKVM console → restart WG/sshd on Hillsboro (Step 2/5).
4. Reboot Hillsboro from console — last resort; tinyproxy comes back on boot, LA egress (once locked down) is interrupted until it does.

> **Tie-in:** once LA egress lockdown is executed (`la-egress-lockdown/`), a wedged Hillsboro means LA can't reach Anthropic/Twilio/etc. until recovered — so prioritize this runbook if both alerts fire together.
