# BHN Security Audit Findings

Findings surfaced during code audit, git history scan, or live-state review.
Each finding maps to a BTEH finding ID where applicable.

---

## Open

### SEC-GIT-001 — Grafana Reader Password in Git History

| Field | Value |
|-------|-------|
| **BTEH ref** | SEC-001 (credential hygiene) |
| **Severity** | MEDIUM |
| **Status** | OPEN — password NOT yet rotated |
| **Found** | 2026-06-26 — git history scan during public release prep |
| **Commit** | `676a8c2` — "Dashboard consolidation — migrate to LA Grafana" |
| **File** | `infrastructure/docs/BHN SESSION UPDATES/BHN-SESSION-HANDOFF/BHN-SESSION-HANDOFF-2026-06-25.md` |
| **Credential** | `grafana_reader` PostgreSQL role password |
| **Value scrubbed from history** | Literal password → `REDACTED` (git filter-branch rewrite, Jun 26) |

**What happened:** A session handoff doc committed during the Grafana consolidation session included the literal `grafana_reader` PG password. The password was set during that session to enable Grafana on LA to read from `eventhorizon`; the value was captured in the doc and committed.

**Mitigation applied:** Git history rewritten using `git filter-branch` — the literal value replaced with `REDACTED` across all commits. Remote force-pushed.

**Remediation pending:** Rotate the `grafana_reader` password on LA and update the Grafana datasource config on the same node:
```sql
ALTER ROLE grafana_reader WITH PASSWORD '<new-strong-password>';
```
Then update `GF_DATABASE_GRAFANA_READER_PASSWORD` in `/etc/bhn-trading/env` on LA (or wherever Grafana reads it) and restart Grafana. Store new password in Proton Pass → `EH-PG-grafana_reader`.

---

## Resolved

*(none yet)*
