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

### SEC-DNS-001 — Native App Hardcoded IP Bypass for DNS-Blocked Social Media

| Field | Value |
|-------|-------|
| **BTEH ref** | — |
| **Severity** | LOW (informational — DNS blocking is best-effort) |
| **Status** | OPEN — iptables fix deferred |
| **Found** | 2026-06-26 — during AdGuard social media blocking implementation |

**Description:** Instagram, Facebook, TikTok, and Snapchat native mobile apps use hardcoded IP addresses and/or certificate pinning, bypassing DNS-based blocking at the app level. Browser-based access to these services IS blocked via AdGuard Home (HaGeZi Social list + blocked_services IDs). App-level traffic routes around DNS entirely.

**Affected services:** `instagram`, `facebook`, `tiktok`, `snapchat`
**Not affected:** `twitter`/X, `threads` (use standard DNS resolution — DNS block is effective)

**Current posture:** DNS blocking only. Browser access blocked; native app access NOT blocked on mobile devices connected to the WireGuard mesh.

**Remediation (future — iptables):**
Add `iptables` DROP rules for outbound traffic to known hardcoded IP ranges used by these apps. Meta's hardcoded ranges (Instagram/Facebook/Threads) are documented in ASN 32934. TikTok (ByteDance) uses ASN 396986/138699. This requires periodic IP range maintenance and is higher operational burden than DNS.

```bash
# Example skeleton (not yet implemented)
# ipset create meta_hardcoded hash:net
# ipset add meta_hardcoded 179.60.192.0/22   # Meta hardcoded range
# iptables -I FORWARD -m set --match-set meta_hardcoded dst -j DROP
```

---

## Resolved

*(none yet)*
