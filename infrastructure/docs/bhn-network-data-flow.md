# BHN Network Data-Flow Blueprint

How traffic moves across the BHN mesh — by traffic *class*, not just by node. This is the unifying map; the per-mechanism detail lives in the linked docs.

**Status:** architecture/intent doc. Each flow below is tagged **[LIVE]**, **[DESIGNED]** (built, not yet deployed), or **[BROKEN]** (attempted, not working). Do not read this as "all of this is running" — check the tags.

> **Why this doc exists:** egress is split across three nodes for three different reasons (operational anonymity, jurisdictional privacy, personal browsing), and the pieces were documented separately. This ties them together so the operator toggle and the "which IP does X exit from" question have one answer.

---

## Nodes & addresses

| Node | Role | WG (tunnel) | Public IP | Provider |
|------|------|-------------|-----------|----------|
| **LA** (`BHN\|VPS-LOSANGELES-US1`) | Hub — PG, n8n, HORIZON, Grafana | `10.8.0.1` (wg0) | `149.28.91.100` | Vultr (US) |
| **Frankfurt** (`BHN\|VPS-FRANKFURT-EU1`) | Exit + privacy routing | `10.9.0.2` (wg1) | `192.248.187.208` | (DE) |
| **NJ** (`BHN\|VPS-NEWJERSEY-US2`) | Trading (Alpaca) | `10.8.0.5` | — | (US) |
| **Hillsboro** (`BHN-HILLSBORO-US3`) | Operational egress proxy | `10.8.0.6` (wg0) | `5.78.94.237` | Hetzner (US) |

---

## Traffic classes & their egress

### 1. LA operational / service egress → **Hillsboro** (primary) — [DESIGNED]

LA's outbound API calls (Anthropic, Twilio, ElevenLabs, financial data, apt, certbot) route through Hillsboro's tinyproxy (`10.8.0.6:8888`) and exit Hillsboro's Hetzner IP (`5.78.94.237`), so LA's Vultr IP (`149.28.91.100`) stops appearing in those vendors' access logs.

```
LA process ──http(s)_proxy──► 10.8.0.6:8888 (tinyproxy on Hillsboro)
                                   │  MASQUERADE
                                   ▼
                            exits 5.78.94.237 (Hetzner)
```

- **Inbound** API callbacks (Twilio voice/SMS webhooks, n8n webhook URLs, ElevenLabs async callbacks) still land **directly on LA**. The asymmetry is deliberate — see `infrastructure/la-egress-lockdown/README.md`.
- **State:** the proxy config + UFW lockdown are built and staged but **not executed on the live node**. Until `ufw-rewrite.sh lockdown` runs, LA still egresses direct. Mechanism + deploy order: **`infrastructure/la-egress-lockdown/README.md`**.

### 2. Operator personal browsing → **Frankfurt** (jurisdictional exit) — [BROKEN]

The operator's "full-tunnel" WG profile is intended to exit Frankfurt's DE IP (`192.248.187.208`) for jurisdictional isolation (non-US, out of 5 Eyes) of personal browsing.

```
op device (full profile, AllowedIPs=0.0.0.0/0)
   ──► LA wg0 ──fwmark policy route──► LA wg1 ──► FRA wg1 (10.9.0.2)
                                                     │ MASQUERADE
                                                     ▼
                                              exits 192.248.187.208 (DE)
```

- **State: not working.** Full-tunnel clients currently lose internet entirely. Root cause: FRA is missing the `-s 10.8.0.0/24 -o enp1s0 -j MASQUERADE` rule (and the LA-side fwmark marking is unverified). Fix path + what-not-to-retry: **`infrastructure/docs/frankfurt-exit-backlog.md`** and **`bhn-frankfurt-exit-routing.md`**.
- The operator's **"admin"** (split-tunnel, mesh-only) profile works fine — internet stays on local ISP, only mesh traffic tunnels. Daily use is on admin until the FRA exit is fixed.

### 3. Privacy / unlinkable traffic → **Frankfurt Tor** — [PARTIAL]

Frankfurt runs a Tor SOCKS proxy at **`10.9.0.2:9050`** (reachable from the mesh). Intended consumers: SearXNG upstream, and any tool that wants an unlinkable circuit. The broader Tor *relay* role (non-exit) is **[DESIGNED]** per the roadmap. Scope detail: **`infrastructure/docs/bhn-frankfurt-scoping.md`**.

### 4. The operator toggle (personal traffic)

For personal/operator traffic the intended model is a **toggle between two exits**:

| Toggle position | Exit | Use |
|-----------------|------|-----|
| **Hillsboro** | `5.78.94.237` (US, Hetzner) | Lower latency, US presence, operational continuity |
| **Frankfurt** | `192.248.187.208` (DE) + optional Tor `10.9.0.2:9050` | Jurisdictional isolation, Tor-heavy general + personal |

Today the toggle is **aspirational on the Frankfurt side** (exit broken) and the Hillsboro path is operational-service-scoped (proxy, not yet a full-tunnel personal exit). Wiring the operator's personal full-tunnel to *either* exit on demand is the target state; it depends on (a) the FRA MASQUERADE fix landing and (b) deciding whether personal traffic uses the tinyproxy path or a dedicated full-tunnel route through Hillsboro.

### 5. Trading egress (NJ) — [LIVE]

NJ's trading API calls (Alpaca) go out **NJ's own interface directly**, never through the tunnel. Tunnel carries only intra-mesh BHN traffic for NJ. Keeps trading flows separate from any Tor/relay presence on the same host.

### 6. What never leaves the mesh / stays direct — [LIVE]

- Intra-mesh `10.8.0.0/24` ↔ `10.9.0.0/24` — peer-to-peer over WG.
- WireGuard underlay UDP (`51820`/`51821`) — this *is* the tunnel layer, stays direct.
- DNS — local dnscrypt-proxy on `127.0.0.1` (DoH upstream over 443).
- NTP `123/udp` — direct.

---

## State summary (snapshot — re-verify against live before acting)

| Flow | State |
|------|-------|
| LA operational egress via Hillsboro | **[DESIGNED]** — staged, lockdown not executed |
| Operator full-tunnel exit via Frankfurt | **[BROKEN]** — MASQUERADE fix pending |
| Frankfurt Tor SOCKS `10.9.0.2:9050` | **[PARTIAL]** — SOCKS available; relay role designed |
| NJ trading direct egress | **[LIVE]** |
| Mesh-internal + underlay + DNS + NTP | **[LIVE]** |

**Related docs:** `la-egress-lockdown/README.md` · `bhn-frankfurt-exit-routing.md` · `frankfurt-exit-backlog.md` · `bhn-frankfurt-scoping.md` · `bhn-hillsboro-ssh-diagnosis.md`
