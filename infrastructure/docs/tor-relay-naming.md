# BHN Tor relay naming convention

All BHN Tor relays use nicknames drawn from astronomy — fitting the **B**lack**h**ole **N**etwork brand. The format encodes node identity AND BHN's internal region/sequence labeling, so the relay name doubles as a pointer to which BHN node it runs on.

## Format

```
BHN<AstroName><RegionCode><Sequence>
```

**No hyphens, no underscores, no separators of any kind** — Tor enforces `[a-zA-Z0-9]` only on the `Nickname` directive and rejects any other character at config-validation time, killing the relay's startup with `Failed to parse/validate config: nicknames must contain only the characters [a-zA-Z0-9]`. Nicknames must also be 1–19 characters inclusive. All current and planned names fit (`BHNEridanusEU3` is the longest at 14 chars).

- **AstroName** — single-word astronomical name (constellation, celestial body, phenomenon). First letter loosely matches the city/location when possible, but thematic fit (e.g. Aurora for Nordic) takes precedence over strict initial-letter matching.
- **RegionCode** — `US` or `EU`. Matches the region used in the BHN node name (e.g. `BHN-VPS-FRANKFURT-EU1` → `EU1`).
- **Sequence** — bootstrap order within the region, counting ALL BHN nodes in that region (hub + exits + proxies), not just relays. So `US1` is taken by the LA hub even though LA runs no relay.

## Full roster

| BHN node | Node name | Relay nickname | Astro reasoning |
|---|---|---|---|
| LA (hub) | `BHN-VPS-LA-US1` | *(no relay — hub stays dark)* | — |
| New Jersey | `BHN-VPS-NEWJERSEY-US2` | `BHNNebulaUS2` | Nebula = cosmic cloud; N matches New Jersey |
| Hillsboro | `BHN-HILLSBORO-US3` | `BHNHeliosUS3` | Helios = Greek sun god / star; H matches Hillsboro |
| ~~Frankfurt~~ *(decommissioned 2026-05-28)* | ~~`BHN-VPS-FRANKFURT-EU1`~~ | ~~`BHNFornaxEU1`~~ | Fornax = real galaxy constellation; F matches Frankfurt. EU1 slot was later reassigned. |
| Helsinki | `BHN-HELSINKI-EU1` | `BHNAuroraEU1` | Aurora = Northern Lights; thematic fit for Nordic location. ⚠️ **Naming conflict:** this doc's original plan reserved "Aurora"/EU2 for a future Sweden node — Helsinki took EU1 (Frankfurt's vacated slot) using the Aurora name instead. Sweden's future relay will need a different astro name; flag for operator decision, not resolved here. |
| Sweden (future) | `BHN-VPS-SWEDEN-EU2` | *(name TBD — see conflict note above)* | — |
| Iceland (future) | `BHN-VPS-ICELAND-EU3` | `BHNEridanusEU3` | Eridanus = river constellation; E matches Europe/Iceland |

## ContactInfo

All relays use a single `ContactInfo` value in their `torrc`:

```
ContactInfo admin@eventhorizonvpn.com
```

This is the operator-of-record on `metrics.torproject.org` and the inbox abuse desks contact for any complaint. Don't vary it per relay — keeping it uniform makes it obvious all relays are operated by the same entity, which combined with `MyFamily` removes ambiguity for the Tor consensus and for any external observer auditing the family.

## MyFamily

Once a relay is bootstrapped and `docker exec bhn-tor-relay cat /var/lib/tor/fingerprint` returns a value, append that fingerprint to a `MyFamily` line in **every deployed torrc** (this repo and the live containers). Restart each container after editing.

Example (with 3 relays deployed):

```
MyFamily $0123abc...FORNAX_FP,$4567def...NEBULA_FP,$89abcde...HELIOS_FP
```

Tor consensus then refuses to build a circuit that passes through any two relays in the family. Without `MyFamily`, two BHN relays could land on the same circuit and observe both ends — defeating the privacy benefit of running multiple relays.

Fingerprints are public information (they're in the consensus). Commit the actual fingerprints to the repo — they're not secrets.

## Adding a future relay

When Sweden or Iceland (or anything else) comes online:

1. Pick an unused astronomy name following the format. Reserved names: `Fornax`, `Nebula`, `Helios`, `Aurora`, `Eridanus`.
2. Confirm the region+sequence matches the BHN node name (e.g. `BHN-VPS-ICELAND-EU3` → suffix `EU3`).
3. Create `infrastructure/services/tor-relay-<city>/` mirroring the existing relay configs (`torrc`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `README.md`).
4. Add a row to the roster table above.
5. After deployment + fingerprint published, update `MyFamily` across all deployed torrc files and commit.

## Why the change happened

Original nicknames were the bare city name (`BHNFrankfurt`, `BHNNewJersey`). Two problems:

1. **Leaks** the operating city to anyone reading the Tor consensus. The astronomical names provide a thin layer of indirection — the city is still derivable from the relay's published IP, but the nickname itself no longer states it.
2. **Doesn't pattern-match** the BHN brand. Astronomy / black-hole-network theme is more on-brand.

The rename was applied in repo and on the live Frankfurt relay on 2026-05-12.

### Why no hyphens (correction landed 2026-05-12 same day)

The first revision of this convention used `BHN<Astro>-<Region><Seq>` with a hyphen separator (`BHNFornax-EU1`, etc.). On the first deploy of `Nickname BHNFornax-EU1` to the live Frankfurt container, Tor crashed in a loop with:

> `[warn] Failed to parse/validate config: Nickname 'BHNFornax-EU1', nicknames must be between 1 and 19 characters inclusive, and must contain only the characters [a-zA-Z0-9].`

Tor allows letters and digits only. The hyphen-separated names were correct in spirit (encoding region+sequence as a suffix) but unimplementable. Dropped the hyphen across all 5 names + this doc. Frankfurt was restored to `BHNFornaxEU1` within ~2 minutes; fingerprint stayed identical so no reputation loss.

**Lesson for future relays:** when adding a new relay, validate the proposed nickname by checking it matches `^[a-zA-Z0-9]{1,19}$` before staging the torrc. A pre-commit hook would be overkill for a five-relay roster but is the right answer if BHN ever scales to a dozen.
