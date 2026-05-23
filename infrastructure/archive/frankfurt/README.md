# Frankfurt (EU1) — Archive

Frankfurt was the BHN EU exit node (`BHN|VPS-FRANKFURT-EU1`, public IP `192.248.187.208`). **Decommissioned May 2026** in favor of a simplified single-egress topology — all LA-originated operational/API traffic now exits via Hillsboro tinyproxy (`10.8.0.6:8888`) on Hetzner.

This folder is the resting place for FRA-specific configuration and documentation as the decommission completes. **It is intentionally empty at archive-folder creation time** (2026-05-22) — actual file moves from `infrastructure/docs/` and `infrastructure/services/` will follow as the FRA-specific docs are retired.

## What Frankfurt hosted while it was live

| Service | Role | Status post-decommission |
|---------|------|--------------------------|
| WireGuard `wg1` interface (10.9.0.0/24) | EU side of the BHN mesh | Removed from LA hub |
| `BHNFornaxEU1` (Tor middle relay) | MyFamily participant | Removed; MyFamily reduced to `BHNHeliosUS3` (Hillsboro) + `BHNNebulaUS2` (NJ) |
| Frankfurt Tor SOCKS at `10.9.0.2:9050` | Unlinkable-circuit egress option | Removed |
| SearXNG (`10.9.0.2:8089`) | Private meta-search | Offline; relocation TBD (operator-PC, LA, or Hillsboro candidates) |
| LibreSpeed (`10.9.0.2:8088`) | EU speedtest endpoint | Offline; no relocation planned |
| Operator full-tunnel exit (`192.248.187.208`) | Jurisdictional isolation for personal browsing | Off the table — personal browsing rides Hillsboro or stays on local ISP |

## Stale references still pending cleanup

The following docs reference FRA in ways that became wrong with the decommission. They are *expected* to be either moved into this folder, deleted, or rewritten in a follow-up commit:

- `infrastructure/docs/bhn-frankfurt-exit-routing.md`
- `infrastructure/docs/bhn-frankfurt-scoping.md`
- `infrastructure/docs/frankfurt-exit-backlog.md`
- `infrastructure/docs/bhn-network-data-flow.md` — FRA-specific traffic classes (operator personal browsing, FRA Tor SOCKS); needs trimming
- Bootstrap policies / module configs referring to `wg1` or 10.9.0.0/24

Until those land here, treat them as **historical** — they describe what FRA *did*, not what BHN does today.

## Why FRA was cut (one-line)

Single-egress through Hillsboro is simpler operationally, removes the broken FRA MASQUERADE workstream, reduces the Vultr surface, and means there's only one external IP to manage in vendor allowlists. The jurisdictional-isolation argument for an EU exit was deprioritized in favor of operational continuity.
