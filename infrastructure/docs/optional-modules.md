# Optional / Planned Self-Hosted Modules

Living list of free, open-source self-hosted services considered for the EH infrastructure. Each is Docker-deployable on any EH node. **Homarr is the central dashboard** — once provisioned, it surfaces everything else as tiles with health indicators.

This is a working catalog, not a commitment. Add/remove freely as new tools surface. Operator scans this list when picking the next thing to deploy.

---

## Deployment posture

The EH stack today (2 nodes) is constrained:

| Node | Role | Resource ceiling | Suitable for |
|------|------|------------------|--------------|
| **LA Hub** (149.28.91.100) | Network hub, n8n, PG, Grafana, Suricata, etc. | 2 GB RAM (already 60-80% used by core stack) | Lightweight tools only (≤ 100 MB RAM each) |
| **Frankfurt Exit** (192.248.187.208) | WG exit node | Light — mostly idle CPU | Privacy-front tools (Whoogle, SearXNG) and per-region services (Librespeed) |

**For heavier tools (Ollama, Stirling, JDownloader, Metube)**, plan a third dedicated services VPS — operator's call when to provision. Until then, defer those.

**At the edge**, eventually replace the planned narrow nginx (per `horizon-inbound-webhook-security.md`) with **Nginx Proxy Manager** so all public-facing services share one TLS-terminating front.

---

## Catalog

| Tool | Purpose | Repo | License | Recommended node |
|------|---------|------|---------|------------------|
| **Ollama** | Local LLM runtime — run Llama / Mistral / Phi etc. without external APIs. Foundation for offline HORIZON fallback if Anthropic is unreachable. | [github.com/ollama/ollama](https://github.com/ollama/ollama) | MIT | **Dedicated services VPS** with ≥ 16 GB RAM (or GPU for larger models). Not LA hub — too constrained. |
| **PrivateBin** | Encrypted, ephemeral pastebin. Client-side AES; server never sees plaintext. Good for sharing diagnostic snippets without trusting the host. | [github.com/PrivateBin/PrivateBin](https://github.com/PrivateBin/PrivateBin) | zlib | LA hub — light footprint |
| **ConvertX** | Web file-format converter (images, audio, video, docs). All conversion happens locally — no upload to external services. | [github.com/C4illin/ConvertX](https://github.com/C4illin/ConvertX) | AGPL-3.0 | Dedicated services VPS — CPU-spikes during media conversion |
| **DashDot** | Modern per-host dashboard (CPU / RAM / disk / network real-time). One instance per node so each shows its own stats. | [github.com/MauriceNino/dashdot](https://github.com/MauriceNino/dashdot) | MIT | **Per-host** (deploy on each node, not centralized) |
| **Homarr** | Central dashboard / homepage for all self-hosted services. Tile-based, health checks, SSO-ready. **Primary access point for everything in this list.** | [github.com/ajnart/homarr](https://github.com/ajnart/homarr) | MIT | **LA hub** (central) |
| **Excalidraw** | Collaborative whiteboard / hand-drawn-style diagrams. Useful for HORIZON architecture docs, planning sketches. | [github.com/excalidraw/excalidraw](https://github.com/excalidraw/excalidraw) | MIT | LA hub — light footprint |
| **Draw.io** | Full-featured diagramming (network maps, flowcharts, ERDs). Heavier than Excalidraw but exports to many formats. | [github.com/jgraph/docker-drawio](https://github.com/jgraph/docker-drawio) | Apache-2.0 | LA hub — light when idle |
| **JDownloader** | Headless download manager for direct links + many host services. Resumable, parallelized. | [hub.docker.com/r/jaymoulin/jdownloader](https://hub.docker.com/r/jaymoulin/jdownloader) | Free use (closed source — community Docker image) | Dedicated services VPS — bandwidth heavy |
| **MeTube** | Web UI for yt-dlp. Queue YouTube / video downloads to a shared folder. | [github.com/alexta69/metube](https://github.com/alexta69/metube) | AGPL-3.0 | Dedicated services VPS — bandwidth + storage |
| **Netdata** | Real-time per-host monitoring. Auto-discovers services, ML-based anomaly detection. Heavier than DashDot but more thorough. | [github.com/netdata/netdata](https://github.com/netdata/netdata) | GPL-3.0 | **Per-host** — modest agent (~100 MB RAM) |
| **Nginx Proxy Manager** | Reverse proxy with web UI for cert / route management. Replaces hand-rolled nginx for the public-facing webhook subdomain in M1. | [github.com/NginxProxyManager/nginx-proxy-manager](https://github.com/NginxProxyManager/nginx-proxy-manager) | MIT | **LA hub** (edge, public-facing) |
| **Stirling PDF** | Comprehensive PDF toolkit (merge, split, OCR, redact, sign). Local processing — no upload to external. | [github.com/Stirling-Tools/Stirling-PDF](https://github.com/Stirling-Tools/Stirling-PDF) | MIT | Dedicated services VPS — JVM-heavy, CPU spikes during OCR |
| **LibreSpeed** | Self-hosted speedtest endpoint. Useful per-region — operator can speed-test from each EH exit to verify routing. | [github.com/librespeed/speedtest](https://github.com/librespeed/speedtest) | LGPL-3.0 | **Per-node** — deploy on each exit (FRA, future Tokyo) |
| **OpenSpeedTest** | Alternative speedtest — pure-browser implementation, no client needed. Smaller resource footprint than LibreSpeed. | [github.com/openspeedtest/Speed-Test](https://github.com/openspeedtest/Speed-Test) | GPL-3.0 | **Per-node** (alternate to LibreSpeed) |
| **Uptime Kuma** | Status-page / uptime monitor for the EH stack itself. HTTP/TCP/ICMP probes. Push notifications via various channels (overlap with M4). | [github.com/louislam/uptime-kuma](https://github.com/louislam/uptime-kuma) | MIT | LA hub or dedicated monitoring node |
| **Pingvin Share** | File-sharing service (alternative to WeTransfer). Encrypted, expiring links, no third-party trust. | [github.com/stonith404/pingvin-share](https://github.com/stonith404/pingvin-share) | BSD-2-Clause | LA hub — moderate (depends on file sizes) |
| **Wallos** | Subscription tracker — recurring payments, renewal reminders, total monthly burn. Good fit for tracking the EH cost stack itself (Vultr × 2, Anthropic, ElevenLabs, Twilio, etc.). | [github.com/ellite/Wallos](https://github.com/ellite/Wallos) | AGPL-3.0 | LA hub — light |
| **Whoogle** | Privacy-respecting Google search proxy. Strips tracking, no cookies, optional Tor backend. | [github.com/benbusby/whoogle-search](https://github.com/benbusby/whoogle-search) | MIT | **Frankfurt** (privacy-front role) or LA hub |
| **SearXNG** | Meta-search engine — aggregates results from many engines while preserving privacy. More comprehensive than Whoogle. | [github.com/searxng/searxng](https://github.com/searxng/searxng) | AGPL-3.0 | **Frankfurt** (privacy-front) or dedicated VPS |

---

## Categorized view (for picking what to deploy first)

### Lightweight, deployable on LA hub today (≤ 100 MB RAM each)

PrivateBin, Excalidraw, Draw.io (idle), Whoogle, Wallos, Homarr, OpenSpeedTest, Pingvin Share (small files only), Uptime Kuma.

Combined ≈ 500 MB RAM if all running. Within hub's remaining headroom.

### Per-host monitoring (deploy on every node)

DashDot, Netdata. Lightweight per-instance, gives operator real-time per-node visibility on Homarr.

### Privacy-front (Frankfurt's natural role)

Whoogle, SearXNG. Egress from EU, unlinks search history from operator's identity.

### Edge / public-facing

Nginx Proxy Manager (replaces planned narrow nginx for M1 Twilio webhook subdomain — single TLS termination, easier cert management).

### Defer until dedicated services VPS

Ollama (LLM hosting — needs ≥ 16 GB RAM and ideally GPU), ConvertX (CPU-bursty), JDownloader (bandwidth + storage), MeTube (bandwidth + storage), Stirling PDF (JVM, OCR-heavy).

---

## Integration with HORIZON

Each tool can either run independently or surface as a HORIZON tool:

- **Health-monitored** — Uptime Kuma probes feed back into HORIZON via webhook → memories table
- **Searchable** — SearXNG/Whoogle could be exposed as a HORIZON `web_search` tool, supplementing built-in knowledge
- **Local LLM fallback** — Ollama could be a fallback model when Anthropic API is rate-limited or down (Haiku 4.5 router could pick Ollama for cheap classification queries)
- **Dashboarded** — all tools surface on Homarr; HORIZON can answer "what services are running" by querying Homarr's API

---

## Deployment baseline (reusable Docker compose snippets — TODO)

Future addition: a `docker-compose.yml` per tool in `infrastructure/optional/<tool>/` so each is a one-command deploy with EH-standard volume locations + UFW-compatible defaults.

---

## Operator workflow for adding a tool

1. Pick from this list (or add a new one — research repo + license, slot into the table above)
2. Decide deployment node based on resource needs
3. Write or copy the docker-compose.yml
4. Set up DNS / reverse proxy entry (NPM once it's live)
5. Add tile to Homarr
6. Document any operator-only credentials in Proton Pass with `EH-{tool}-...` naming
