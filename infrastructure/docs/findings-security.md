# BHN Security Findings

Documented security incidents, misconfigurations, and exposure events. Each finding
includes discovery date, impact, remediation taken, and residual risk.

---

## FINDING-001 — LA True IP Exposed via Public GoDaddy DNS Records

| Field | Detail |
|---|---|
| **Severity** | High |
| **Discovered** | 2026-06-27 |
| **Status** | Remediated |

### What happened

Public A records on **eventhorizonvpn.com** in GoDaddy were pointing `@`, `dash`, and
`n8n` subdomains directly at `149.28.91.100` — LA's true Vultr public IP address. These
records were publicly resolvable from the open internet, allowing any observer to
trivially enumerate the real IP of the BHN hub node by querying the domain.

### Impact

- LA's VPS public IP was linkable to the domain `eventhorizonvpn.com` by anyone with a
  DNS resolver. This partially defeats the operational security model of routing all
  traffic through WireGuard and exit nodes.
- The `dash` record suggests a dashboard (likely n8n or an admin panel) was at some
  point intended or configured for public-facing access on that IP, which may mean
  services that should be mesh-only were transiently reachable from the internet.
- Exposure window unknown — records may have been live since initial domain setup.

### Remediation

- All three public A records (`@`, `dash`, `n8n`) deleted from GoDaddy DNS on
  2026-06-27.
- **Policy confirmed:** `eventhorizonvpn.com` subdomains are internal-only. All
  resolution is handled by AdGuard Home local DNS rewrites on LA (10.8.0.1), accessible
  only to WireGuard mesh peers. No public DNS records for BHN services will be created.

### Residual risk

- LA's IP `149.28.91.100` may be cached in passive DNS databases (SecurityTrails,
  RiskIQ, Shodan, etc.) and remain queryable there indefinitely. The IP itself cannot
  be scrubbed from third-party passive DNS.
- Recommended: assess whether the LA Vultr IP needs to be rotated. If any threat actor
  had already indexed the association, a new IP would break the link. Decision is
  operator's call given migration cost.
- Continue monitoring LA's UFW logs for unusual inbound traffic on non-VPN ports.

---

## FINDING-002 — AdGuard Admin Password Exposed in Session Chat

| Field | Detail |
|---|---|
| **Severity** | Medium |
| **Discovered** | 2026-06-27 |
| **Status** | Remediated |

### What happened

The AdGuard Home admin password was provided in plaintext during a Claude Code session
(`Polyester1-Liquid7-Washboard4-Purse1`). Session transcripts are stored locally but
the credential was in cleartext in a context window with no expiry guarantee.

### Impact

- AdGuard Home admin panel (http://10.8.0.1:3001) is mesh-only, so external access
  requires an active WireGuard session. Blast radius is limited to mesh peers.
- Risk: if any session log were accessed, the credential was directly usable.

### Remediation

- Password rotated 2026-06-27 via `/control/profile` API.
- New credential stored in **Proton Pass → BHN-AdGuard-Admin** only. Not to be
  referenced in plaintext in any future session.

### Residual risk

- Low. Panel is not internet-accessible. Old password is dead.
- Policy going forward: all BHN service credentials are referenced by Proton Pass entry
  name only in conversation. Never paste passwords in chat.
