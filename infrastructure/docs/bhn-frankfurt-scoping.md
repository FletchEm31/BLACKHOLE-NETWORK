# Frankfurt — Role Scoping

What Frankfurt (`BHN|VPS-FRANKFURT-EU1`, `192.248.187.208`, wg1 `10.9.0.2`) is *for*. The mechanics of each capability live in their own docs; this defines the node's role and what's load-bearing vs pending.

**Status:** scoping/intent doc, 2026-05-22. Frankfurt's role is **broadening from "pure WG exit" to "exit + privacy routing."**

---

## Role: Tor-heavy privacy + jurisdictional exit

Frankfurt is the **non-US, privacy-leaning** node. Three capabilities sit under that role:

| Capability | Purpose | State |
|------------|---------|-------|
| **Jurisdictional exit** | Operator personal browsing exits a DE IP (outside 5 Eyes) instead of LA's US IP | **[BROKEN]** — see MASQUERADE fix below |
| **Tor SOCKS proxy** | Unlinkable circuits for SearXNG upstream + ad-hoc tooling | **[AVAILABLE]** at `10.9.0.2:9050` |
| **Tor relay (non-exit)** | Adds capacity to BHN's privacy stack; middle/bridge relay only (NOT exit — keeps legal exposure low) | **[DESIGNED]** — roadmap "Per-node service deployment" |
| **SearXNG meta-search** | Self-hosted, tracking-stripped search; can route upstream via the Tor SOCKS | **[DESIGNED]** |

Deliberately **not** on Frankfurt: voice processing. All HORIZON voice infra stays on LA (US) for jurisdictional reasons — see the roadmap's "Jurisdictional posture" table. Don't move voice to FRA.

---

## Load-bearing gap: the MASQUERADE fix

The jurisdictional-exit capability is blocked on **one missing NAT rule on Frankfurt**:

```bash
iptables -t nat -A POSTROUTING -s 10.8.0.0/24 -o enp1s0 -j MASQUERADE
```

Without it, packets that LA policy-routes into the FRA tunnel (source `10.8.0.x`) arrive at Frankfurt but can't be SNAT'd to Frankfurt's IP, so the return path fails and full-tunnel clients lose internet. FRA's current MASQUERADE is scoped to `10.9.0.0/24` only.

- The installer exists: **`scripts/bhn-frankfurt-masquerade-fix.sh`** (in repo, **not yet deployed**).
- This is the load-bearing first step. Per `frankfurt-exit-backlog.md`, the LA-side fwmark marking is also unverified — apply the FRA MASQUERADE fix *first*, then re-test, then instrument the LA-side `bhn-frankfurt-exit.sh` if still broken.
- **Do not** remove LA's unconditional `MASQUERADE -o enp1s0` PostUp without a working fwmark replacement — that broke general LA egress on 2026-05-13 (see backlog "What NOT to try again").

> Deploy is operator-side and touches live FRA iptables — out of scope for repo-only doc work. This doc records *what* and *why*; execution happens in a live session.

---

## SOCKS proxy — available now

A Tor SOCKS proxy is reachable at **`10.9.0.2:9050`** from the mesh. This is the immediate, working privacy primitive on Frankfurt (independent of the broken full-tunnel exit). Anything on the mesh that wants a Tor circuit — SearXNG upstream, a one-off `curl --socks5-hostname 10.9.0.2:9050`, or backup-via-Tor (see roadmap) — can use it today.

**MyFamily note:** when the FRA Tor *relay* (not just the SOCKS client) goes live alongside NJ's and Sweden's relays, all torrc files must declare the joint MyFamily fingerprint so consensus never routes one circuit through two BHN relays. Tracked in `infrastructure/services/tor-relay/README.md`.

---

## Related docs

- `bhn-frankfurt-exit-routing.md` — Phase 1 exit-routing design + deploy steps
- `frankfurt-exit-backlog.md` — debugging history, what-not-to-retry, next-session plan
- `bhn-network-data-flow.md` — where Frankfurt sits in the overall egress map
- `horizon-roadmap.md` — "Per-node service deployment" (SearXNG + Tor relay scope)
