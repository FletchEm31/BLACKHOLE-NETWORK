# BHN Network Data-Flow Blueprint

How traffic moves across the BHN mesh — by traffic *class*, not just by node. This is the unifying map; the per-mechanism detail lives in the linked docs.

**Status:** architecture/intent doc. Each flow below is tagged **[LIVE]**, **[DESIGNED]** (built, not yet deployed), or **[BROKEN]** (attempted, not working). Do not read this as "all of this is running" — check the tags.

> **Why this doc exists:** post-Frankfurt-decommission (May 2026) BHN is a simplified single-egress topology — LA-originated operational/API traffic exits through Hillsboro, NJ trading egresses directly, intra-mesh stays on WG. This doc ties the pieces together so "which IP does X exit from" has one answer. Frankfurt-specific design history is archived under `infrastructure/archive/frankfurt/`.

---

## Nodes & addresses

| Node | Role | WG (tunnel) | Public IP | Provider |
|------|------|-------------|-----------|----------|
| **LA** (`BHN\|VPS-LOSANGELES-US1`) | Hub — PG, n8n, HORIZON, Grafana | `10.8.0.1` (wg0) | `149.28.91.100` | Vultr (US) |
| **NJ** (`BHN\|VPS-NEWJERSEY-US2`) | Trading (Alpaca) | `10.8.0.5` (wg0) | — | (US) |
| **Hillsboro** (`BHN-HILLSBORO-US3`) | Operational egress proxy | `10.8.0.6` (wg0) | `5.78.94.237` | Hetzner (US) |

Frankfurt (`BHN|VPS-FRANKFURT-EU1`, `192.248.187.208`, `10.9.0.2/wg1`) was decommissioned May 2026 — see `infrastructure/archive/frankfurt/README.md`.

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

### 2. Operator personal browsing — [LOCAL ISP]

Operator's daily browsing rides the local ISP. The "admin" (split-tunnel, mesh-only) WG profile keeps mesh traffic on the tunnel without touching general internet. Personal jurisdictional-exit options were retired with Frankfurt; if a future full-tunnel exit is needed it will route through Hillsboro (decision deferred).

### 3. Trading egress (NJ) — [LIVE]

NJ's trading API calls (Alpaca) go out **NJ's own interface directly**, never through the tunnel. Tunnel carries only intra-mesh BHN traffic for NJ.

### 4. What never leaves the mesh / stays direct — [LIVE]

- Intra-mesh `10.8.0.0/24` — peer-to-peer over WG (wg0).
- WireGuard underlay UDP (`51820`) — this *is* the tunnel layer, stays direct.
- DNS — local dnscrypt-proxy on `127.0.0.1` (DoH upstream over 443).
- NTP `123/udp` — direct.

---

## State summary (snapshot — re-verify against live before acting)

| Flow | State |
|------|-------|
| LA operational egress via Hillsboro | **[DESIGNED]** — staged, lockdown not executed |
| Operator personal browsing | **[LOCAL ISP]** — FRA exit retired |
| NJ trading direct egress | **[LIVE]** |
| Mesh-internal + underlay + DNS + NTP | **[LIVE]** |

**Related docs:** `la-egress-lockdown/README.md` · `bhn-hillsboro-ssh-diagnosis.md` · Frankfurt-era design + backlog: `infrastructure/archive/frankfurt/`.

---

## Schema reference

78 tables in `eventhorizon`, by functional category. **The live DB is ground truth**; this listing is for orientation. Canonical DDL is in [`sql/`](../../sql/); per-table column-level reference is generated from the live schema and is not duplicated here.

### Market data
`market_daily`, `market_bars_1min`, `market_bars_5min`, `market_bars_1hour`, `market_ticks`, `market_regimes`, `market_sentiment`, `market_events`, `market_signals`

### Macro
`macro_daily`, `macro_indicators`

### Trading
`paper_trades`, `signals_log`, `order_events`, `circuit_breaker_log`, `strategy_performance`, `trading_rules`, `trading_strategies`, `reconciliation_heartbeat`

### Financial intelligence
`earnings_data`, `analyst_data`, `options_chain_snapshots`, `prediction_market_data`, `crypto_market_data`, `investment_signals`, `alpaca_news`

### Alternative data
`agriculture_prices`, `energy_prices`, `weather_snapshots`, `corporate_actions`

### Security
`security_events`, `anomalies`, `pulse_reports`, `node_logs`, `node_logs_summary`, `fail2ban_events`, `crowdsec_decisions`

### Infrastructure
`nodes`, `node_resource_stats`, `node_bandwidth_stats`, `node_disk_stats`, `node_patch_status`, `wg_peer_stats`, `wg_sessions`, `tor_relay_stats`

### AI (HORIZON)
`memories` (pgvector 384-dim BAAI/bge-small-en-v1.5), `agent_token_log`, `call_transcripts`, `conversation_sessions`, `qa_cache`

### Collectibles (PokemonBHN)
`master_card_catalog` (637 cards / 1,354 variant rows, 8 sets — scraper search queue), `pop_reports` (CGC/PSA population counts), `sold_listings` (eBay sold comps), `ebay_listings` (active listings), `master_grade_catalog` (per-grader scale + FK validation source), `master_grading_criteria_catalog` (condition factors + PSA qualifiers), `master_set_catalog` (set dimension — name, era, editions, PSA heading)

### Access roles
`agent_reader` is HORIZON's read role — SELECT across all categories above. Service-specific writer roles (`bootstrap_writer`, the trading service role, collector ingest roles) are narrower; see `sql/` for grants.
