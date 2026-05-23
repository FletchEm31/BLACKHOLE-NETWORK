# Frankfurt (EU1) — Archive

Frankfurt was the BHN EU exit node (`BHN|VPS-FRANKFURT-EU1`, public IP `192.248.187.208`). **Decommissioned May 2026** in favor of a simplified single-egress topology — all LA-originated operational/API traffic now exits via Hillsboro tinyproxy (`10.8.0.6:8888`) on Hetzner.

This folder is the resting place for FRA-specific configuration and documentation. Decommission-completion doc moves landed 2026-05-23.

## What Frankfurt hosted while it was live

| Service | Role | Status post-decommission |
|---------|------|--------------------------|
| WireGuard `wg1` interface (10.9.0.0/24) | EU side of the BHN mesh | Removed from LA hub |
| `BHNFornaxEU1` (Tor middle relay) | MyFamily participant | Removed; MyFamily reduced to `BHNHeliosUS3` (Hillsboro) + `BHNNebulaUS2` (NJ) |
| Frankfurt Tor SOCKS at `10.9.0.2:9050` | Unlinkable-circuit egress option | Removed |
| SearXNG (`10.9.0.2:8089`) | Private meta-search | Offline; relocation TBD (operator-PC, LA, or Hillsboro candidates) |
| LibreSpeed (`10.9.0.2:8088`) | EU speedtest endpoint | Offline; no relocation planned |
| Operator full-tunnel exit (`192.248.187.208`) | Jurisdictional isolation for personal browsing | Off the table — personal browsing rides Hillsboro or stays on local ISP |

## Archived contents (2026-05-23 cleanup)

Moved into this folder:

- `bhn-frankfurt-exit-routing.md` — FRA MASQUERADE / fwmark routing design
- `bhn-frankfurt-scoping.md` — FRA role scoping (Tor relay, SOCKS, SearXNG, LibreSpeed)
- `frankfurt-exit-backlog.md` — open issues and what-not-to-retry on the broken FRA exit

Rewritten in place (not archived) — FRA references stripped, current topology only:

- `infrastructure/docs/bhn-network-data-flow.md`

All files in this folder are **historical** — they describe what FRA *did*, not what BHN does today.

## Known follow-ups (operator-decision items)

CC2's 2026-05-23 server-side cleanup flagged a separate leftover FRA egress peer + table-100 policy route still present on LA's `wg0`. Not blocking — treated as an operator-decision item for a dedicated session.

Bootstrap policies / module configs still referencing `wg1` or `10.9.0.0/24` may exist; sweep in a later commit.

## Why FRA was cut (one-line)

Single-egress through Hillsboro is simpler operationally, removes the broken FRA MASQUERADE workstream, reduces the Vultr surface, and means there's only one external IP to manage in vendor allowlists. The jurisdictional-isolation argument for an EU exit was deprioritized in favor of operational continuity.
