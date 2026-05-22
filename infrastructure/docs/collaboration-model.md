# BHN Collaboration Model — who reads, who writes, what's off-limits

**Audience:** any AI assistant or future collaborator working on this repo.
**Status:** authoritative as of 2026-05-22. Read this before suggesting structural or naming changes.

The Blackhole Network repo is **private**, single-operator (Hayden / `FletchEm31`), no collaborators added. Work happens through three roles with deliberately different permissions. Respecting the split is what keeps the repo from drifting against live system state.

## The three roles

| Role | Access | Responsibility |
|------|--------|----------------|
| **Operator** (Hayden) | Full read/write — GitHub + local repo + live systems | Directs all work. Sole human. Final authority on every change. |
| **App Claude** (claude.ai project) | **Read-only** via project-knowledge connection — can search/read committed files, **cannot write or push** | Advises on development, suggests changes, **owns the n8n workflow design**. Suggestions are applied by Claude Code only when the operator directs it. |
| **Claude Code** (CLI) | Read/write — GitHub + local repo + live DB/SSH | **Sole writer to the repo.** Writes only when the operator explicitly directs. Verifies suggestions against live state before applying. |

## Sole-writer rule

**Claude Code is the only thing that writes to this repo.** App Claude produces content — sections, drafts, flow designs, specs — but never hands over whole files to paste wholesale, because its copies go stale against live `HEAD`. (A full-file README paste once nearly reverted four committed changes.)

When content comes from App Claude (or the operator relaying it), Claude Code applies it as **targeted in-place merges against the current repo file** — read live `HEAD` first, splice the new sections, preserve everything already committed. Never blind-overwrite. **Live/repo state is authoritative; chat copies are planning-grade.**

## App Claude's read window

**Can read** (committed to the repo): `README.md` files, `infrastructure/`, `scripts/`, `sql/`, and `n8n-workflows/` — including the live flow definitions (`bhn-horizon.json` is HORIZON's full node graph, system prompt, and tool wiring).

**Cannot see** (not in the repo — Claude Code is the bridge that confirms these against reality):
- **n8n runtime state** — which flows are active/inactive, whether credentials are actually wired, execution history. The JSON shows a flow's *shape* and credential *references* (`id` + `name`), not the live binding or whether it's toggled on.
- **The eBay sniper flow** (POKEMON-BLACKHOLE-SNIPER) — live-only, not among the committed JSONs.
- **Live PostgreSQL data** in the `eventhorizon` database — catalog completeness, pop-report rows, FK-valid grades, row counts. App Claude reads the *docs about* the schema; it cannot query the tables.

For anything runtime- or DB-stateful, defer to Claude Code to confirm what's actually running or stored.

## Preservation contracts — do NOT suggest renaming these

The repo was renamed EventHorizon VPN → Blackhole Network on 2026-05-11, but a set of identifiers are **intentionally preserved as live-system contracts** until a coordinated migration session. They look like inconsistencies but are not. Do not propose `eh-*` → `bhn-*` renames or "cleanup" of these:

- PostgreSQL database name: **`eventhorizon`**
- Email domain: **`eventhorizonvpn.com`**
- n8n credential names: **`Postgres EventHorizon`**, **`EventHorizonVPN-Claude`**, **`EH-Twilio`**, **`EH-ElevenLabs`**, **`EH-Horizon-Email`** (and other `EH-*` credentials)
- LA-deployed script paths: **`/usr/local/sbin/eh-*`**, **`/opt/eh-diagnostics/*`**
- `EH_*` environment variables, Proton Pass `EH-*` entries
- n8n workflow files prefixed `eh-*` mirror live flow names — same rule applies

The "EventHorizon VPN" name is reserved for a future separate commercial product. See the preservation note at the top of `README.md`. When in doubt, ask the operator before renaming anything that crosses the repo↔live-system boundary.
