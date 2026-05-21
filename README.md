# Blackhole Network (BHN)

A privacy-focused personal infrastructure platform built on WireGuard with deep defense-in-depth security, AI-powered operations, and algorithmic trading. **Single-operator network — no customers, no public service offering. Personal infrastructure only.**

> **Note:** Repo renamed 2026-05-11 from EventHorizon VPN to Blackhole Network. Vultr-side server display names updated to `BHN|VPS-LOSANGELES-US1`, `BHN|VPS-FRANKFURT-EU1`, `BHN|VPS-NEWJERSEY-US2`. LA-deployed script paths (`/usr/local/sbin/eh-*`, `/opt/eh-diagnostics/*`), PostgreSQL database name `eventhorizon`, email domain `eventhorizonvpn.com`, and n8n credential names (`Postgres EventHorizon`, `EventHorizonVPN-Claude`) are intentionally preserved as live-system identifiers until a coordinated migration session. The "EventHorizon VPN" name is reserved for the future separate commercial product.

## Overview

Blackhole Network is a self-hosted private intelligence and trading infrastructure platform operated by a single operator. Built on battle-tested open-source tools with custom automation and AI-driven monitoring.

**Infrastructure:** WireGuard mesh VPN (4 nodes across US-West, US-East, EU), PostgreSQL, Grafana, n8n, Tor relay network, dnscrypt-proxy, CrowdSec, Suricata, Shadowsocks

**Trading Stack:** Algorithmic paper trading via Alpaca — 5 active strategies across 3 accounts, $150k total capital, real-time signal generation and execution on NJ trading node

**Financial Intelligence:** 6 Grafana dashboards covering market regime classification, ETF price data, macro indicators, sentiment, commodities, energy, agriculture, prediction markets, and options flow across 71 PostgreSQL tables

**AI Agent — HORIZON:** HORIZON is the autonomous intelligence layer of the Blackhole Network — an n8n-based AI agent powered by Claude with full read access to all 71 PostgreSQL tables. HORIZON operates as both a personal assistant and autonomous infrastructure manager across three domains:

*Operations:* Monitors all 4 nodes in real time, reads security events, anomalies, pulse reports, and node health. Triggers alerts via SMS/voice (Twilio + ElevenLabs) for P1/P2 security events, node outages, and storage pressure. Can execute restricted actions — restart services, trigger fail2ban bans, run smoke tests, and activate the trading killswitch on operator command.

*Trading & Finance:* Full read access to all financial intelligence tables — market regime, ETF price data, macro indicators, sentiment, earnings, analyst ratings, commodities, energy, agriculture, prediction markets, and options flow. Monitors paper trade performance across all 5 strategies, delivers daily PnL summaries, and flags reconciliation mismatches.

*Memory & Context:* pgvector semantic memory (384-dim) for long-term context, Redis for short-term session state. HORIZON builds a persistent model of operator preferences, infrastructure state, and trading history across all interactions.

*Interface:* SMS, voice call, and VPN-only web chat. Operator can query any aspect of the stack conversationally — "How are my strategies performing?", "Any threats in the last 24 hours?", "What's the current market regime?" — or issue commands — "HALT trading", "Restart n8n", "Run smoke tests".

*Goal:* Single conversational interface to the entire BHN stack — infrastructure, security, trading, and financial intelligence — available 24/7 via SMS from anywhere.

Any future public VPN product is a separate concern (different servers, different protocol, different holding entity) and is not part of this repository.

## Architecture

### Five-phase build plan

```
Phase 1: NETWORK                        [✅ ~90% complete]
├─ LA hub (BHN|VPS-LOSANGELES-US1) — hub, PostgreSQL, n8n, HORIZON, Grafana
├─ Frankfurt exit node (BHN|VPS-FRANKFURT-EU1) — EU exit, LibreSpeed, SearXNG, Tor relay
├─ NJ trading node (BHN|VPS-NEWJERSEY-US2) — Alpaca paper trading, 5 strategies live
├─ Hillsboro proxy node (BHN-HILLSBORO-US3) — LA egress proxy via tinyproxy, Tor relay
├─ WireGuard hub-and-spoke mesh — all nodes + operator devices connected, PSK on most peers
├─ Bootstrap script v4 (declarative node types + modular install)
├─ Frankfurt exit routing — BROKEN, FRA MASQUERADE fix pending
└─ Future nodes: Sweden (Bahnhof), Iceland via snapshot deployment

Phase 2: DASHBOARD                      [✅ ~85% complete]
├─ PostgreSQL on encrypted NVMe — 71 tables, live financial + security data
├─ 6 Grafana dashboards (VPN-only access):
│   ├─ BHN Market Intelligence — regime, ETFs, macro, sentiment, earnings, analyst ratings
│   ├─ BHN Trade Execution & Operations — signals, PnL, paper trades, reconciliation
│   ├─ BHN Derivatives & Options Markets — IV, Greeks, open interest, options chain
│   ├─ BHN Prediction & Alternative Markets — weather, Kalshi/Polymarket, corporate actions
│   ├─ BHN Commodities & Tangible Asset Markets — energy, agriculture, precious metals
│   └─ BHN Infrastructure & Security Operations — nodes, security events, anomalies, pulse
├─ n8n for action automation and AI orchestration
├─ Financial intelligence layer — 32 ETF tickers, FRED macro, USDA agriculture, EIA energy
└─ Grafana alerting — not yet wired

Phase 3: AI INTEGRATION                 [in progress, ~60% complete]
├─ pgvector memory layer (operational — 19 entries)
├─ Redis short-term session memory (operational)
├─ HORIZON workflow (operational — stale workflow, re-import pending)
├─ Voice ops interface (Twilio + ElevenLabs — staged, A2P 10DLC in review)
├─ HORIZON financial table access — pending workflow re-import
├─ HORIZON restricted action executor — not yet built
└─ Proactive alerting + auto-response — not yet built

Phase 4: PER-NODE SERVICES              [~80% complete]
├─ Trading stack live on NJ — 5 strategies, 3 Alpaca accounts, first trades firing
├─ Wallos (LA) — subscription / cost tracking [✅] http://10.8.0.1:8090
├─ SearXNG (Frankfurt) — private meta-search [✅] http://10.9.0.2:8089
├─ LibreSpeed Frankfurt (EU speedtest) [✅] http://10.9.0.2:8088
├─ tinyproxy (Hillsboro) — LA egress proxy [✅] verified, lockdown pending
├─ Tor relays: BHNFornaxEU1 (Frankfurt, live), BHNHeliosUS3 (Hillsboro, bootstrapping),
│              BHNNebulaUS2 (NJ, deployed not live)
└─ MyFamily fingerprint exchange — pending (after all relays 24h+)

Phase 5: RESILIENCE                     [designed, not built]
├─ Sweden cold standby + dark replication node (Bahnhof hosting, outside Vultr)
├─ Tor hidden-service replication LA to Sweden (no Vultr cross-region correlation)
├─ Single-command failover (bhn-failover-activate.sh)
├─ Sweden Tor middle relay (joins MyFamily with FRA + NJ)
└─ Iceland exit node EU3
```

### Storage tiering (LA hub)

```
NVMe (101 GB encrypted, hot tier)       [✅ operational]
  ├─ /mnt/eh-nvme-hot/postgres          PostgreSQL data (live writes)
  ├─ /mnt/eh-nvme-hot/pcap              Active packet captures
  ├─ /mnt/eh-nvme-hot/logs              Active logs
  └─ /mnt/eh-nvme-hot/grafana           Grafana state

HDD (399 GB encrypted, cold tier)       [✅ operational]
  ├─ /mnt/eh-hdd-cold/archives/         Compressed daily archives
  ├─ /mnt/eh-hdd-cold/snapshots         Hourly stats snapshots (kept forever)
  └─ /mnt/eh-hdd-cold/reports           Weekly analysis reports
```

Both volumes use LUKS2 with auto-unlock keyfiles, XFS filesystem, and persistent mounts via `/etc/crypttab` and `/etc/fstab`.

### PostgreSQL schema

78 tables in the `eventhorizon` database covering:

- Market data: `market_daily`, `market_bars_*`, `market_ticks`, `market_regimes`, `market_sentiment`, `market_events`, `market_signals`
- Macro: `macro_daily`, `macro_indicators`
- Trading: `paper_trades`, `signals_log`, `order_events`, `circuit_breaker_log`, `strategy_performance`, `trading_rules`, `trading_strategies`, `reconciliation_heartbeat`
- Financial intelligence: `earnings_data`, `analyst_data`, `options_chain_snapshots`, `prediction_market_data`, `crypto_market_data`, `investment_signals`, `alpaca_news`
- Alternative data: `agriculture_prices`, `energy_prices`, `weather_snapshots`, `corporate_actions`
- Security: `security_events`, `anomalies`, `pulse_reports`, `node_logs`, `node_logs_summary`, `fail2ban_events`, `crowdsec_decisions`
- Infrastructure: `nodes`, `node_resource_stats`, `node_bandwidth_stats`, `node_disk_stats`, `node_patch_status`, `wg_peer_stats`, `wg_sessions`, `tor_relay_stats`
- AI: `memories` (pgvector 384-dim), `agent_token_log`, `call_transcripts`, `conversation_sessions`, `qa_cache`
- Collectibles (Pokémon graded-card market — see [Pokémon graded-card data pipeline](#pokémon-graded-card-data-pipeline)): `card_catalog` (scraper search queue), `pop_reports` (CGC/PSA population counts), `sold_listings` (eBay sold comps), `grade_catalog` (per-grader grade scale + FK validation source), `grading_criteria_catalog` (condition factors + PSA qualifiers)

## Security stack

Each node runs:

- **WireGuard** — encrypted mesh tunnel, hub-and-spoke topology, PSK on all peers
- **Shadowsocks** — DPI-resistant traffic obfuscation (exit nodes)
- **dnscrypt-proxy** — encrypted DNS rotating across 6 resolvers (Mullvad, Quad9, Cloudflare, AdGuard, NextDNS, Digitale Gesellschaft)
- **Fail2ban** — automated intrusion blocking with VPN-tunnel whitelist
- **CrowdSec** — collaborative threat intelligence, shared ban list
- **Suricata** — IDS/IPS deep packet inspection, logs to PostgreSQL
- **UFW** — host firewall, default deny in/out, explicit whitelist only
- **LUKS2** — full-disk encryption for storage volumes (LA hub, NVMe + HDD)
- **SSH hardening** — key-only root login, passwords disabled, non-standard port on NJ (2222)
- **tinyproxy** — LA API egress via Hillsboro IP (Anthropic, Twilio, ElevenLabs never see LA IP)

LA hub additional layers:
- LUKS2 encrypted NVMe (hot) + HDD (cold) storage
- PostgreSQL role-based access control (7 roles, least privilege)
- WireGuard PSK on all peers (quantum-resistant key exchange)

## Trading stack

Runs on NJ trading node (BHN|VPS-NEWJERSEY-US2). Paper trading via Alpaca.

```
Account 1 — BHN-STRAT-PRIMARY (PA39LSUT2NW8)    $100,000
  Strat 6  — BHN-NASDAQ-LONG      enabled    $40,000   Mon 9:40am ET
  Strat 7  — BHN-NASDAQ-SHORT     disabled   $40,000   pending Strat 6 validation
  Strat 8  — BHN-SECTOR-ROTATION  enabled    $20,000   daily 3:55pm ET

Account 2 — BHN-STRAT-FUNDAMENTAL (PA3AZX0UE3JC) $25,000
  Strat 3  — BHN-MEAN-REVERSION   enabled    $20,000   daily

Account 3 — BHN-STRAT-SIGNALS (PA37PRN150AG)     $25,000
  Strat 4  — BHN-MOMENTUM         enabled    $12,500   daily
  Strat 13 — BHN-RSI-INTRADAY     enabled    $12,500   every 30min market hours

Parked (pending API keys):
  Strat 1  — Congress Trading      (Quiver Quantitative API — $25/mo)
  Strat 5  — Weather Arbitrage     (Kalshi API key)
```

## Pokémon Graded Card Data Pipeline

A self-contained collectibles-intelligence subsystem feeding HORIZON. It tracks two market signals
for WOTC-era Pokémon cards — **scarcity** (graded population counts) and **price** (eBay sold comps) —
both keyed off a single watchlist of cards worth following.

### Source of truth — `card_catalog`

`card_catalog` (in `eventhorizon`) is the shared search queue. Every scraper reads
`WHERE active = true` and pulls `set_name, card_number` (plus `card_name, variant`), so adding a card
to the watchlist is a single `INSERT … active = true` and it auto-enrolls across all collectors.
Covers 8 sets (Base Set, Fossil, Jungle, Team Rocket, Gym Heroes, Gym Challenge, Wizards Black Star
Promos, Best of Game) with PriceCharting reference prices. As of 2026-05-21 the six main WOTC sets are
audited to **full canonical completeness** against Bulbapedia + pkmncards — every card carries its
standard editions (1st Edition + Unlimited; Base Set also Shadowless) — for **637 distinct cards /
1,355 variant rows** total. Error/alternate-print variants (errors, no-symbol, jumbo, staff stamps)
are tracked opportunistically, not exhaustively.

### Data flow

```
card_catalog  (active = true → set_name, card_number)
   │
   ├─ CGC pop scraper ── native fetch, ccg-ops JSON API ─────────────┐
   │   infrastructure/scrapers/cgc-pop-scrape.js                      │
   │   LA weekly cron: bhn-cgc-pop-refresh.timer (Sun 03:00 UTC)      ├─ cgc-pop-load.js ─→ pop_reports
   │                                                                  │   (grader-agnostic upsert)
   ├─ PSA pop scraper ── stealth browser, runs OFF-LA (residential) ──┘
   │   infrastructure/scrapers/psa-pop-scrape.js
   │   clears Cloudflare → POST /Pop/GetSetItems → emits JSON → shipped to LA for load
   │
   └─ n8n sold-data workflow ── eBay sold comps ─────────────────────→ sold_listings
```

### Scrapers (`infrastructure/scrapers/`)

- **CGC** (`cgc-pop-scrape.js`) — CGC exposes a clean public population JSON API (no auth, no
  browser). The driver scrapes every tracked set, asserts completeness against the API's
  `TotalCount`, and loads via `cgc-pop-load.js`. Deployed on LA as the
  `bhn-cgc-pop-refresh.{service,timer}` weekly job.
- **PSA** (`psa-pop-scrape.js`) — PSA has **no** population API and its pages sit behind a Cloudflare
  managed challenge, so this uses a **decoupled residential fetch model**: a stealth browser
  (`puppeteer-extra` + stealth) clears Cloudflare once, then calls the page's own
  `POST /Pop/GetSetItems` endpoint in-page so `cf_clearance` rides along. It **never runs on LA**
  (datacenter IPs get challenged hardest) — it runs on a residential box, emits CGC-shaped JSON, and
  LA only ingests it via `cgc-pop-load.js`. Catalog `set_name` → PSA heading is curated in
  `psa-sets.json` (PSA slugs aren't derivable: "Base Set" → `pokemon-game`, "Team Rocket" →
  `pokemon-rocket`). **7 of 8 catalog sets are mapped**; Wizards Black Star Promos is the lone
  exception — PSA fragments it across multiple year-headings, so it's flagged for multi-heading
  support and skipped until then.

### Tables

- **`card_catalog`** — watchlist / scraper queue (637 distinct cards / 1,355 variant rows, 8 sets, `active` flag, PriceCharting prices).
- **`pop_reports`** — graded-card population counts per `(grader, set, card, grade)`. Grader-agnostic;
  CGC live, PSA built, SGC/BGS planned. `grade` is verbatim ("Gem Mint 10", "9.5", "Authentic") and
  **FK-constrained to `grade_catalog(grader, raw_label)`** — an unknown grade is rejected at insert.
- **`sold_listings`** — eBay sold comps (price, grade, grader, sale type, seller, raw title);
  `item_id` unique for idempotent ingest. `grade` is `text` (verbatim raw_label) and
  **FK-constrained to `grade_catalog`**; raw/ungraded sales must set `grade = NULL`. Bootstrapped with
  651 rows (Base Set, Team Rocket, Fossil, Gym Heroes/Challenge) across CGC/PSA, dates through 2026-05-21.
- **`grade_catalog`** — canonical grade scale per grader (CGC/PSA/BGS/SGC), keyed by the verbatim
  `raw_label` scrapers emit (both full labels like `Gem Mint 10` and bare numerics like `10`). Carries
  `numeric_grade`, `tier_label`, `market_equiv_10`, `is_authentic`. It is the validation source for the
  `pop_reports`/`sold_listings` grade FKs, so every emitted grade string must exist here.
- **`grading_criteria_catalog`** — the four condition factors (Centering / Corners / Edges / Surface)
  broken out per grader with `subgrades_published` (BGS publishes subgrades; PSA/CGC/SGC grade overall),
  plus PSA qualifiers (OC/MC/ST/MK/PD/OF).

## HORIZON roadmap

HORIZON is built in phases. Each phase enables the next.

```
Phase 1 — Foundation
  [✅] n8n workflow operational
  [✅] Claude API integration
  [✅] SMS/voice via Twilio + ElevenLabs
  [✅] pgvector semantic memory
  [✅] Redis short-term session memory
  [✅] PostgreSQL read access (agent_reader role)
  [✅] Security + infrastructure table access

Phase 2 — Financial Intelligence
  [ ] Re-import bhn-horizon.json — unlock financial table access
  [ ] Market regime + ETF data queries
  [ ] Trading performance queries
  [ ] Daily PnL summary via SMS
  [ ] Morning market briefing (8am PT)
  [ ] Finance news category added to n8n poller

Phase 3 — Active Monitoring
  [ ] Per-node health checks every 5 minutes
  [ ] Auto-alert if any node goes offline
  [ ] Auto-alert if NVMe > 80% full
  [ ] P1/P2 security event SMS within minutes
  [ ] WireGuard peer stats monitoring
  [ ] Tor relay bandwidth monitoring

Phase 4 — Restricted Action Executor
  [ ] Restart services via SMS command
  [ ] Trigger fail2ban ban on attacker IP
  [ ] Run smoke tests on demand
  [ ] Trading killswitch on "HALT" SMS
  [ ] Push rules.json to NJ trading node
  [ ] Confirm all actions before executing

Phase 5 — Autonomous Management
  [ ] Pattern detection across all 71 tables
  [ ] Proactive threat intelligence (CVE feeds, CrowdSec)
  [ ] Weekly threat + performance digest
  [ ] Trading strategy optimization suggestions
  [ ] Infrastructure cost analysis
  [ ] Anomaly correlation across security + trading + market data
```

## Repository layout

```
.
├── README.md                        Project overview (this file)
├── infrastructure/
│   ├── bootstrap/                   v4 modular bootstrap
│   │   ├── bhn-node-bootstrap.sh    Master script (open → install → lockdown)
│   │   ├── node-types/              hub.sh, exit.sh, scan.sh, proxy.sh
│   │   ├── modules/                 wireguard, crowdsec, suricata, shadowsocks,
│   │   │                            dnscrypt, firewall, ssh-hardening, storage,
│   │   │                            network-policy, backup
│   │   └── policies/                Declarative network policies per node type
│   ├── docs/                        Architecture docs, roadmap, session updates
│   │   └── BHN SESSION UPDATES/     Per-session handoff docs
│   ├── grafana/dashboards/          All 6 Grafana dashboard JSONs
│   ├── services/                    tor-relay, tinyproxy, searxng, librespeed, wallos
│   └── scrapers/                    Graded-card pop scrapers (CGC cron + PSA stealth) + psa-sets.json
├── scripts/                         Production scripts (deployed to LA)
│   ├── trading/                     5-strategy trading framework (Python)
│   │   ├── trading_core.py          Core Alpaca + PostgreSQL integration
│   │   ├── strategy_*.py            Individual strategy implementations
│   │   ├── master_killswitch.py     Emergency halt + flatten all positions
│   │   ├── daily_summary.py         Daily PnL summary via HORIZON/SMS
│   │   └── reconciliation_daemon.py Position reconciliation
│   └── horizon/                     Financial data collectors
│       ├── macro_collector.py       FRED macro data (daily)
│       ├── market_collector.py      Alpaca ETF price data (daily)
│       └── sentiment_collector.py   Fear/greed, AAII sentiment (daily)
├── n8n-workflows/                   Exported n8n workflow JSONs
│   ├── bhn-horizon.json             HORIZON AI agent workflow
│   └── bhn-pulse-2h.json            2-hour pulse report workflow
└── sql/                             PostgreSQL schemas
```

## Naming conventions

```
Standalone resources (VPS):
  BHN|VPS-LOCATION-COUNTRY+SEQINDEX
  Examples: BHN|VPS-LOSANGELES-US1, BHN|VPS-FRANKFURT-EU1, BHN|VPS-NEWJERSEY-US2

Attachments (block storage):
  DEVICE-LOCATION-COUNTRY+SEQINDEX
  Examples: SSD-LOSANGELES-US1, HDD-FRANKFURT-DE1

Tor relay nicknames:
  BHN + [AstroName] + [RegionCode] + [SeqNum] — alphanumeric only, no hyphens
  Examples: BHNFornaxEU1, BHNHeliosUS3, BHNNebulaUS2
```

## Console terminology

| Term | Definition |
|------|------------|
| **REMOTE BROWSER WINDOW** | noVNC web console (Vultr/Hetzner) — emergency fallback only |
| **PC LA CONSOLE** | SSH from operator PC to LA hub (`ssh root@149.28.91.100`) |
| **PC GE CONSOLE** | SSH from operator PC to Frankfurt (`ssh root@192.248.187.208`) |
| **PC NJ CONSOLE** | SSH from operator PC to NJ (`ssh -p 2222 root@140.82.4.35`) — port 2222 |
| **PC Hillsboro** | SSH from operator PC to Hillsboro (`ssh root@5.78.94.237`) |
| **LA→Frankfurt** | `ssh frankfurt` (alias from LA, via WireGuard tunnel) |
| **LA→NJ** | `ssh nj` (alias from LA, via WireGuard tunnel) |
| **LA→Hillsboro** | `ssh hillsboro` (alias from LA, via WireGuard tunnel) |

## Services map (VPN required)

```
n8n:              http://10.8.0.1:5678
HORIZON chat:     http://10.8.0.1:5678/webhook/ec1592c6-8715-4b0f-8ee8-5bc02f551a27/chat
Grafana:          http://10.8.0.1:3000
Wallos:           http://10.8.0.1:8090
PostgreSQL:       psql -h 10.8.0.1 -U <role> -d eventhorizon
LibreSpeed (EU):  http://10.9.0.2:8088
SearXNG:          http://10.9.0.2:8089
tinyproxy:        http://10.8.0.6:8888 (LA egress proxy, WireGuard only)
```

## Bootstrap (new node)

```bash
# Copy bootstrap files to new node (repo is private — use scp not git clone)
scp -r "D:\GITHUB REPOSITORY\BLACKHOLE-NETWORK\infrastructure" root@<IP>:/opt/bhn/

# On new node:
export TUNNEL_IP_OVERRIDE=<TUNNEL_IP>
export EH_BOOTSTRAP_PG_DSN='postgresql://bootstrap_writer:BHN-Bootstrap-2026@10.8.0.1/eventhorizon'
export ADMIN_PUBKEYS_FILE=/root/admin_pubkeys
export INSTALL_SURICATA=1
bash /opt/bhn/infrastructure/bootstrap/bhn-node-bootstrap.sh NAME IP wg0 TYPE REGION
# Types: hub, exit, scan, proxy

# After bootstrap — run on LA immediately:
ufw allow out to <PUBLIC_IP> port 51821 proto udp
ufw allow out to <TUNNEL_IP>
ping -c 3 <TUNNEL_IP>
```

## License

Private — all rights reserved.
