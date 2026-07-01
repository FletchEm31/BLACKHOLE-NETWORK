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

**Timer status:** `bhn-weather-orchestrator.timer` — active (waiting), next trigger confirmed firing every 5 minutes. Three consecutive clean cycles at 10:32, 10:37, 10:42 UTC.

**Deployment-window failures (note for Fletch, not a security issue):**
4 orchestrator cycles failed between 10:12–10:27 UTC with exit code 1. Root cause: mid-deployment inconsistency — cp4_kelly_sizer.py and exit_audit_logger.py were being updated in sequence during Task 4 of the WeatherBHN session. Intermediate file state caused import/schema errors. Recovered cleanly at 10:32 after final file set was deployed. No data was corrupted; paper trades are intact.

**Paper trade baseline as of 10:42 UTC 2026-07-01:**

| station | target_date | bucket | contract_ticker | current_no_ask¢ | entry_no_ask¢ | contracts | last_updated | settled |
|---|---|---|---|---|---|---|---|---|
| KDEN | 2026-07-01 | 91-92 | KXHIGHDEN-26JUN30-B91.5 | 4¢ | 6¢ | 2499 | 01:53 UTC | N |
| KDEN | 2026-07-02 | 86-87 | KXHIGHDEN-26JUL01-B86.5 | 86¢ | 75¢ | 116 | 02:13 UTC | N |
| KDEN | 2026-07-02 | 88-89 | KXHIGHDEN-26JUL01-B88.5 | 69¢ | 75¢ | 144 | 05:45 UTC | N |
| KDEN | 2026-07-02 | 90-91 | KXHIGHDEN-26JUL01-B90.5 | 59¢ | 80¢ | 169 | 05:45 UTC | N |
| KDEN | 2026-07-02 | 92-93 | KXHIGHDEN-26JUL01-B92.5 | 81¢ | 82¢ | 123 | 05:45 UTC | N |
| KLAX | 2026-07-02 | 69-70 | KXHIGHLAX-26JUL01-B69.5 | 61¢ | 69¢ | 163 | 05:45 UTC | N |
| KLAX | 2026-07-02 | 71-72 | KXHIGHLAX-26JUL01-B71.5 | 50¢ | 67¢ | 200 | 05:45 UTC | N |
| KMIA | 2026-07-02 | 90-91 | KXHIGHMIA-26JUL01-B90.5 | 77¢ | 90¢ | 129 | 03:03 UTC | N |
| KMIA | 2026-07-02 | 92-93 | KXHIGHMIA-26JUL01-B92.5 | 38¢ | 63¢ | 263 | 10:42 UTC | N |
| KMIA | 2026-07-02 | 94-95 | KXHIGHMIA-26JUL01-B94.5 | 84¢ | 84¢ | 119 | 10:42 UTC | N |

**Totals:** 10 open positions, 0 settled, last signal at 10:42:57 UTC.

**Notes for Fletch:**
- All `contract_ticker` values are now real Kalshi format (TICKET-W1 fix applied).
- KDEN Jul 1 91-92 settles at 22:00 UTC today — first real settlement event.
- KDEN is outside market hours (04:xx MDT) so last_updated shows earlier timestamps; correct behaviour.
- KDEN 91-92 shows 2499 contracts — calculated at current 4¢ price, not 6¢ entry price. Known artefact of dedup keeping newest row; `entry_no_ask_cents = 6¢` is now recorded correctly. Not a bug in the current code.
- KMIA 94-95 is a new position that appeared this session (first recorded 10:42 UTC).
