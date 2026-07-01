# SecurityBHN — Security Telemetry & Audit

**Status:** Live across all nodes | **Progress:** 100%

## What It Is

Defense-in-depth security telemetry stack running across the full BHN node mesh. Signals from multiple detection layers are normalized into PostgreSQL and surfaced through a Grafana operations dashboard covering node health, security events, and pulse reports.

---

## Security Stack

Every node runs the following layers:

**Network & traffic:**
- **WireGuard** — encrypted mesh tunnel, hub-and-spoke topology, PSK on all peers (quantum-resistant key exchange)
- **Shadowsocks** — DPI-resistant traffic obfuscation on exit nodes
- **UFW** — host firewall, default deny inbound and outbound, explicit whitelist only
- **tinyproxy** — LA API egress via Hillsboro; LA IP not exposed to external services

**DNS:**
- **Unbound** — fully recursive resolver on LA; queries root servers directly, DNSSEC auto-managed, no third-party DNS provider in the chain
- **dnscrypt-proxy** — encrypted DoH transport; Cloudflare + Mullvad as fallback; forwards to Unbound first

**Intrusion detection & prevention:**
- **Suricata** — IDS/IPS deep packet inspection; alerts logged to PostgreSQL `security_events`
- **CrowdSec** — collaborative threat intelligence; shared blocklist, decisions logged to `crowdsec_decisions`
- **fail2ban** — automated brute-force blocking with WireGuard-tunnel whitelist; events logged to `fail2ban_events`

**Storage & hardening:**
- **LUKS2** — full-disk encryption for NVMe (hot) and HDD (cold) storage volumes on LA hub
- **SSH hardening** — key-only root login, passwords disabled across all nodes
- **PostgreSQL RBAC** — 7 roles, least-privilege access per service

---

## Telemetry Tables (PostgreSQL `eventhorizon`)

| Table | Contents |
|---|---|
| `security_events` | Suricata IDS/IPS alerts, normalized per-node |
| `anomalies` | Cross-source anomaly detections |
| `fail2ban_events` | Brute-force and intrusion blocking events |
| `crowdsec_decisions` | CrowdSec blocklist decisions |
| `pulse_reports` | Periodic node health snapshots |
| `node_logs` | Per-node operational logs |
| `resource_stats` | CPU, memory, disk per node over time |
| `bandwidth_stats` | Per-interface bandwidth per node |
| `wireguard_peer_stats` | WireGuard peer handshake and transfer stats |
| `tor_relay_stats` | Tor relay bandwidth and consensus flags |

---

## Node Mesh

| Node | Role | Tor Relay |
|---|---|---|
| BHN-LOSANGELES-US1 | LA hub — PostgreSQL, Grafana, n8n, security stack | — |
| BHN-NEWJERSEY-US2 | NJ trading node — Alpaca paper trading | BHNNebulaUS2 (deployed, not live) |
| BHN-HILLSBORO-US3 | Proxy node — LA egress, Tor relay | BHNHeliosUS3 (active) |
| BHN-HELSINKI-EU1 | EU exit node — tinyproxy, Tor relay | BHNAuroraEU1 (active) |

---

## Audit Framework

**BTEH** (*Beyond The EventHorizon*, repo `BTEH-Beyond-The-EventHorizon`) is the audit protocol for the entire BHN platform. 10-section protocol covering: Infrastructure, Security, Database, Workflow & Data Pipeline, Code Quality, Financial & Trading, Legal & Compliance, Consumer Applications, Future Architecture. v1.0 scaffolded May 2026.
