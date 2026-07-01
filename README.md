# Blackhole Network (BHN)

A privacy-focused personal infrastructure platform built on WireGuard with defense-in-depth security and algorithmic trading. **Single-operator network — no customers, no public service offering. Personal infrastructure only.**

> **Note:** Repo renamed 2026-05-11 from EventHorizon VPN to Blackhole Network. The PostgreSQL database name `eventhorizon` is preserved as a live-system identifier and will be migrated in a future coordinated session. Frankfurt (EU1) was decommissioned May 2026; configs archived in [`infrastructure/archive/frankfurt/`](infrastructure/archive/frankfurt/).

## Overview

Blackhole Network is a self-hosted private intelligence and trading infrastructure platform operated by a single operator. Built on battle-tested open-source tools with custom automation and systematic trading pipelines.

**Domain model:** BLACKHOLE-NETWORK (BHN) is the infrastructure platform. Four data domains run on it — **FinancialBHN**, **WeatherBHN**, **SecurityBHN**, and **PokemonBHN** — over shared infrastructure (WireGuard, PostgreSQL, Grafana, n8n). The naming pattern is `{Domain}BHN`; a thing earns a domain label only if it has its own distinct tables, scripts, and services.

### Shared infrastructure

WireGuard mesh VPN (4 nodes across US-West, US-East, and EU), PostgreSQL, Grafana, n8n, Tor relay network, dnscrypt-proxy, CrowdSec, Suricata. Serves all domains, belongs to none.

---

### FinancialBHN — trading & financial intelligence `[20%]`

Algorithmic paper trading via Alpaca, across 3 accounts with multiple strategies. Currently only **Strat 13 (`BHN-RSI-INTRADAY`)** is active as an operational test to validate execution and protocol; the remaining strategies are sidelined pending that validation. Financial intelligence is surfaced through 6 Grafana dashboards covering market regime, ETF prices, macro indicators, sentiment, commodities, energy, agriculture, prediction markets, and options flow.

---

### WeatherBHN — Kalshi temperature prediction market trading `[80%]`

A systematic, model-driven strategy trading daily high temperature contracts on [Kalshi](https://kalshi.com), the U.S.-regulated prediction market exchange. The core thesis: NWS probabilistic forecast data, processed through an ensemble modeling layer and calibrated against historical actuals, produces probability estimates that diverge measurably from Kalshi's market-implied probabilities. That divergence is the tradeable signal.

The pipeline is end-to-end and fully automated: four independent forecast sources (NWS Gridpoint API, Open-Meteo GFS, Visual Crossing, NOAA GHCND actuals) are ingested continuously into bronze tables, conformed to a standard schema in silver, and synthesized into a gold feature set refreshed daily. Contracts where model probability diverges from market-implied probability beyond a threshold are sized using half-Kelly and tracked in a contract ledger.

**Active cities:** Denver (KDEN), Los Angeles (KLAX), Miami (KMIA) — Kalshi KXHIGHDEN / KXHIGHLAX / KXHIGHMIA tmax markets.

**Pipeline (all live on LA hub):**
- **CP1** — data sanity gate (NWS forecast + Kalshi snapshot existence/validity)
- **CP2** — structural arb scan across all buckets (logged, non-blocking)
- **CP3** — XGBoost tmax inference; test RMSE 2.13°F vs 2.42°F calibrated NWS baseline (+0.29°F edge). Emergency fallback to calibrated NWS if model unavailable.
- **CP4** — half-Kelly position sizer; NO-side only strategy ("Tail-No"); 10% bankroll cap per contract

**Currently:** live signal generation in DRY_RUN mode. Model trained on 7,056 historical + 51 live rows. YES-side extension deferred until ≥60 live ledger entries validate NO-side calibration.

→ See [`docs/kalshi-weather-trading.md`](docs/kalshi-weather-trading.md) for the full technical write-up.

---

### SecurityBHN — security telemetry & audit `[100%]`

Defense-in-depth signals across the mesh: `security_events`, `anomalies`, `fail2ban_events`, `crowdsec_decisions`, plus per-node resource, bandwidth, WireGuard, and Tor stats. Live Grafana dashboard covering node health, security events, and pulse reports.

**Audit layer:** **BTEH** — *Beyond The EventHorizon* (repo `BTEH-Beyond-The-EventHorizon`) is the audit framework for the whole platform. 10-section protocol covering Infrastructure, Security, Database, Workflow & Data Pipeline, Code Quality, Financial & Trading, Legal & Compliance, Consumer Applications, and Future Architecture. v1.0 scaffolded May 2026.

---

### PokemonBHN — graded-card market `[50%]`

WOTC-era graded-card data pipeline. `master_card_catalog` (637 cards / 1,354 variant rows, 8 sets) feeds three streams — sold comps (`sold_listings`), active eBay listings (`ebay_listings`), and graded population reports (`pop_reports`) — with CGC/PSA/BGS/SGC grade normalization via `master_grade_catalog`.

This data will be used to research and identify personal investment/collection opportunities, and will serve as the backend data for **Pokemon Blackhole** — a GBA-style FireRed/LeafGreen battle interface built on top of real card market intelligence.

→ See [Pokémon Graded Card Data Pipeline](#pokémon-graded-card-data-pipeline) and [Data standards & authority](#data-standards--authority).

---

### Companion repo — Pokemon Blackhole (the game)

**Pokemon Blackhole** (repo `TEAM-ROCKET-BHN`) is a separate front-end — not part of this repository — and an independent consumer of PokemonBHN data. It reads `master_card_catalog`, `pop_reports`, and `sold_listings` that PokemonBHN populates and renders card market data as a GBA-style Pokémon battle interface. **BLACKHOLE-NETWORK produces the card-market data; Pokemon Blackhole renders it.**

---

*Any future public VPN product is a separate concern (different servers, protocol, and holding entity) and is not part of this repository.*

## Architecture

### Five-phase build plan

```
Phase 1: FOUNDATION                      [✅ complete]
├─ LA hub — PostgreSQL, Grafana, n8n, full security stack
├─ NJ trading node — Alpaca paper trading
├─ Hillsboro proxy — LA egress via tinyproxy, Tor relay (BHNHeliosUS3)
├─ Helsinki EU exit node — commissioned 2026-06-27, Tor relay (BHNAuroraEU1)
├─ WireGuard hub-and-spoke mesh — all nodes + operator devices, PSK on all peers
└─ Bootstrap script v4 — declarative node types + modular install

Phase 2: DATA PLATFORM                   [✅ complete]
├─ PostgreSQL eventhorizon — 150+ tables, medallion bronze/silver/gold architecture
├─ 6 Grafana dashboards (VPN-only):
│   ├─ BHN Market Intelligence — regime, ETFs, macro, sentiment, earnings
│   ├─ BHN Trade Execution & Operations — signals, P&L, paper trades
│   ├─ BHN Derivatives & Options Markets — IV, Greeks, open interest
│   ├─ BHN Prediction & Alternative Markets — weather, Kalshi/Polymarket
│   ├─ BHN Commodities & Tangible Asset Markets — energy, agriculture, metals
│   └─ BHN Infrastructure & Security Operations — nodes, security events, pulse
├─ n8n — workflow automation and pipeline orchestration
└─ Financial intelligence — FRED macro, Alpaca market data, sector/sentiment feeds

Phase 3: TRADING                         [🔄 in progress]
├─ WeatherBHN — Kalshi tmax contract trading pipeline [80%]
│   ├─ CP1–CP4 orchestrator live, every 5 min
│   ├─ XGBoost model trained and deployed (test RMSE 2.13°F)
│   ├─ DRY_RUN mode active — paper P&L only, no live Kalshi orders
│   └─ Next: flip DRY_RUN=false after NO-side calibration passes (≥60 entries)
└─ FinancialBHN — Alpaca paper trading [20%]
    ├─ Strat 13 (BHN-RSI-INTRADAY) — operational test, live
    └─ Remaining strategies sidelined pending Strat 13 validation

Phase 4: COLLECTIBLES                    [🔄 in progress — 50%]
├─ master_card_catalog — 637 cards / 1,354 variants, 8 WOTC sets
├─ CGC pop scraper — weekly cron on LA
├─ PSA pop scraper — residential fetch model (runs off-LA)
├─ eBay sold comps — 15,497 rows loaded; card_id recovery at 82.9%
└─ eBay live listings scraper — TLS fingerprint solution deployed, parser update pending

Phase 5: RESILIENCE                      [designed, not built]
├─ Sweden cold standby + dark replication node (Bahnhof hosting)
├─ Tor hidden-service replication LA → Sweden
├─ Single-command failover (bhn-failover-activate.sh)
└─ Additional EU exit coverage
```

### Storage tiering (LA hub)

```
NVMe (101 GB encrypted, hot tier)       [✅ operational]
  PostgreSQL data (live writes), active packet captures, active logs, Grafana state

HDD (399 GB encrypted, cold tier)       [✅ operational]
  Compressed daily archives, hourly stats snapshots, weekly analysis reports
```

Both volumes use LUKS2 with auto-unlock keyfiles, XFS filesystem, and persistent mounts.

### PostgreSQL schema

150+ tables in the `eventhorizon` database, grouped into functional categories:

- **Market data** — daily/bars/ticks, regimes, sentiment, events, signals
- **Macro** — daily macro + indicator series
- **Trading** — paper trades, signals log, order events, circuit breaker, strategy performance + rules, reconciliation heartbeat
- **Financial intelligence** — earnings, analyst data, options chain snapshots, prediction markets, crypto, investment signals, news
- **Alternative data** — agriculture, energy, corporate actions
- **Weather (WeatherBHN)** — bronze/silver/gold weather pipeline tables; Kalshi market snapshots; contract ledger
- **Security** — security events, anomalies, pulse reports, node logs, fail2ban, crowdsec decisions
- **Infrastructure** — node metadata, resource/bandwidth/disk/patch stats, WireGuard peer + session stats, Tor relay stats
- **Collectibles (PokemonBHN)** — `master_card_catalog`, `pop_reports`, `sold_listings`, `master_grade_catalog`, `master_grading_criteria_catalog`, `master_set_catalog`

**Authority:** the live DB is ground truth; canonical DDL lives in [`sql/`](sql/). The exhaustive table reference is in [`infrastructure/docs/bhn-network-data-flow.md`](infrastructure/docs/bhn-network-data-flow.md).

## Security stack

Each node runs:

- **WireGuard** — encrypted mesh tunnel, hub-and-spoke topology, PSK on all peers
- **Unbound** — fully recursive resolver on LA; queries root servers directly, DNSSEC auto-managed
- **dnscrypt-proxy** — encrypted DoH transport; Cloudflare + Mullvad-base-doh as fallback
- **Fail2ban** — automated intrusion blocking with VPN-tunnel whitelist
- **CrowdSec** — collaborative threat intelligence, shared ban list
- **Suricata** — IDS/IPS deep packet inspection, logs to PostgreSQL
- **UFW** — host firewall, default deny in/out, explicit whitelist only
- **LUKS2** — full-disk encryption for storage volumes (LA hub, NVMe + HDD)
- **SSH hardening** — key-only root login, passwords disabled
- **tinyproxy** — LA API egress via Hillsboro; LA IP never exposed to external APIs
- **Shadowsocks** — DPI-resistant traffic obfuscation (exit nodes)

LA hub additional layers: PostgreSQL role-based access control (7 roles, least privilege), WireGuard PSK (quantum-resistant key exchange).

## FinancialBHN — trading stack

Runs on NJ trading node. Paper trading via Alpaca across 3 accounts.

> **Status:** only **Strat 13 (`BHN-RSI-INTRADAY`)** is active as an operational test. All other strategies are sidelined pending validation.

Configured strategy set (not all active):

```
Account 1 — BHN-STRAT-PRIMARY            $100,000
  Strat 6  — BHN-NASDAQ-LONG      sidelined  $40,000
  Strat 7  — BHN-NASDAQ-SHORT     sidelined  $40,000   pending Strat 6
  Strat 8  — BHN-SECTOR-ROTATION  sidelined  $20,000

Account 2 — BHN-STRAT-FUNDAMENTAL        $25,000
  Strat 3  — BHN-MEAN-REVERSION   sidelined  $20,000

Account 3 — BHN-STRAT-SIGNALS            $25,000
  Strat 4  — BHN-MOMENTUM         sidelined  $12,500
  Strat 13 — BHN-RSI-INTRADAY     ACTIVE     $12,500   every 30min market hours
```

## Pokémon Graded Card Data Pipeline

A self-contained collectibles-intelligence subsystem. Tracks two market signals for WOTC-era Pokémon cards — **scarcity** (graded population counts) and **price** (eBay sold comps) — both keyed off a single watchlist of cards worth following.

### Source of truth — `master_card_catalog`

`master_card_catalog` is the shared search queue. Every scraper reads `WHERE active = true` and pulls `set_name, card_number`, so adding a card to the watchlist is a single `INSERT … active = true` and it auto-enrolls across all collectors. Covers 8 sets (Base Set, Fossil, Jungle, Team Rocket, Gym Heroes, Gym Challenge, Wizards Black Star Promos, Best of Game) with PriceCharting reference prices. The six main WOTC sets are audited to **full canonical completeness** — every card carries its standard editions (1st Edition + Unlimited; Base Set also Shadowless) — for **637 distinct cards / 1,354 variant rows** total.

### Data flow

```
master_card_catalog  (active = true → set_name, card_number)
   │
   ├─ CGC pop scraper ── native fetch, ccg-ops JSON API ─────────────┐
   │   infrastructure/scrapers/cgc-pop-scrape.js                      │
   │   LA weekly cron: bhn-cgc-pop-refresh.timer (Sun 03:00 UTC)      ├─ cgc-pop-load.js → pop_reports
   │                                                                   │   (grader-agnostic upsert)
   ├─ PSA pop scraper ── stealth browser, runs OFF-LA (residential) ──┘
   │   infrastructure/scrapers/psa-pop-scrape.js
   │   clears Cloudflare → POST /Pop/GetSetItems → ships JSON to LA for load
   │
   └─ eBay sold comps pipeline ────────────────────────────────────→ sold_listings / ebay_transactions
```

### Scrapers (`infrastructure/scrapers/`)

- **CGC** (`cgc-pop-scrape.js`) — CGC exposes a clean public population JSON API (no auth, no browser). The driver scrapes every tracked set, asserts completeness against the API's `TotalCount`, and loads via `cgc-pop-load.js`. Deployed on LA as the `bhn-cgc-pop-refresh.{service,timer}` weekly job.
- **PSA** (`psa-pop-scrape.js`) — PSA has no population API and its pages sit behind a Cloudflare managed challenge. Uses a **decoupled residential fetch model**: a stealth browser (`puppeteer-extra` + stealth) clears Cloudflare once, then calls the page's own `POST /Pop/GetSetItems` endpoint. **Never runs on LA** — runs on a residential box, emits CGC-shaped JSON, LA ingests via `cgc-pop-load.js`. Catalog `set_name` → PSA heading is curated in `psa-sets.json`.

### Tables

- **`master_card_catalog`** — watchlist / scraper queue (637 distinct cards / 1,354 variant rows, 8 sets, `active` flag). A compatibility view **`card_catalog`** aliases it for legacy consumers.
- **`pop_reports`** — graded-card population counts per `(grader, set, card, grade)`. Grader-agnostic; CGC live, PSA built, SGC/BGS planned. `grade` FK-constrained to `master_grade_catalog(grader, raw_label)`.
- **`sold_listings`** — eBay sold comps (price, grade, grader, sale type, seller, raw title); `item_id` unique for idempotent ingest. `grade` FK-constrained to `master_grade_catalog`; raw/ungraded sales set `grade = NULL`.
- **`master_grade_catalog`** — canonical grade scale per grader (CGC/PSA/BGS/SGC), keyed by verbatim `raw_label`. Carries `numeric_grade`, `tier_label`, `market_equiv_10`, `is_authentic`.
- **`master_grading_criteria_catalog`** — the four condition factors (Centering / Corners / Edges / Surface) per grader, `subgrades_published`, PSA qualifiers.

## Data standards & authority

The PokemonBHN data domain is governed by a single authoritative standard plus a set of canonical catalog tables.

### The authority (binding)

| Artifact | Location | Role |
|----------|----------|------|
| **`collectibles-data-standard.md`** | `infrastructure/docs/pokemonbhn/` | **THE single source of truth** for the PokemonBHN data domain — table/column naming, canonical value vocabularies, the verbatim-`raw_label` grade model, identity model, and enforcement rules. Where this file disagrees with the live DB, the DB wins and this file is corrected. |
| Schema DDL | `sql/` | Schema files define tables and constraints; the schema *enforces* the standard (FKs, CHECKs, NOT NULL). |

### Core rules (defined in full in the standard doc)

- **Naming:** `master_` prefix = reference/source-of-truth tables; plural nouns = observation data (`pop_reports`, `sold_listings`, `ebay_listings`); `snake_case` throughout; same concept = same column name everywhere. American spelling `catalog`.
- **Identity:** a surrogate `card_id` is the join key. Unique card identity = the `(set_name, card_number, edition, print_variant)` composite.
- **Variant model:** `edition` (`1st Edition` / `Unlimited` / `Shadowless` / `N/A`) and `print_variant` (`Standard` / `Holo` / `Winner` / `Jumbo` / `No Symbol` / `Error` / stamps), `print_variant` NOT NULL DEFAULT `'Standard'`.
- **Grades:** stored as the verbatim `raw_label` (text), FK-constrained to `master_grade_catalog`. Numeric/tier values are derived by JOIN, never re-stored. Raw/ungraded sales set `grade = NULL`.
- **Grade enforcement is tiered:** hard FK on controlled tables (`sold_listings`, `pop_reports`); soft validate-and-log on live feed (`ebay_listings`).
- **Prices:** `listed_price` (asking) and `sold_price` (actual sale) are distinct columns; valuation uses sold only.

## Repository layout

```
.
├── README.md                          Project overview (this file)
├── docs/                              Public technical documentation
│   ├── kalshi-weather-trading.md      WeatherBHN — prediction market trading strategy
│   └── matrixbhn.md                   MatrixBHN — private communications network
├── infrastructure/
│   ├── bootstrap/                     v4 modular bootstrap
│   │   ├── bhn-node-bootstrap.sh      Master script (open → install → lockdown)
│   │   ├── node-types/                hub.sh, exit.sh, scan.sh, proxy.sh
│   │   ├── modules/                   wireguard, crowdsec, suricata, shadowsocks,
│   │   │                              dnscrypt, firewall, ssh-hardening, storage,
│   │   │                              network-policy, backup
│   │   └── policies/                  Declarative network policies per node type
│   ├── docs/                          Architecture docs and audit findings
│   ├── grafana/dashboards/            All 6 Grafana dashboard JSONs
│   ├── services/                      tor-relay, tinyproxy, searxng, librespeed, wallos
│   └── scrapers/                      Graded-card pop scrapers (CGC cron + PSA stealth)
├── scripts/                           Production scripts (deployed to LA)
│   ├── trading/                       FinancialBHN + WeatherBHN trading framework (Python)
│   │   ├── trading_core.py            Core Alpaca + PostgreSQL integration
│   │   ├── strategy_*.py              Individual strategy implementations
│   │   ├── master_killswitch.py       Emergency halt + flatten all positions
│   │   ├── weather_*.py               WeatherBHN collectors, orchestrator, settlement recon
│   │   └── reconciliation_daemon.py   Position reconciliation
│   └── collectors/                    Financial data collectors
│       ├── macro_collector.py         FRED macro data (daily)
│       ├── market_collector.py        Alpaca ETF price data (daily)
│       └── sentiment_collector.py     Fear/greed, AAII sentiment (daily)
├── n8n-workflows/                     Exported n8n workflow JSONs
│   └── bhn-pulse-2h.json              2-hour pulse report workflow
└── sql/                               PostgreSQL schemas
```

## Naming conventions

```
Standalone resources (VPS):
  BHN|VPS-LOCATION-COUNTRY+SEQINDEX
  Examples: BHN|VPS-LOSANGELES-US1, BHN|VPS-NEWJERSEY-US2, BHN|VPS-FRANKFURT-EU1

Attachments (block storage):
  DEVICE-LOCATION-COUNTRY+SEQINDEX
  Examples: SSD-LOSANGELES-US1, HDD-FRANKFURT-DE1

Tor relay nicknames:
  BHN + [AstroName] + [RegionCode] + [SeqNum] — alphanumeric only, no hyphens
  Examples: BHNHeliosUS3, BHNNebulaUS2, BHNAuroraEU1
```

## Bootstrap (new node)

```bash
# Clone repo on new node
git clone https://github.com/FletchEm31/BLACKHOLE-NETWORK /opt/bhn

# Run bootstrap (see infrastructure/bootstrap/ for full parameter reference)
bash /opt/bhn/infrastructure/bootstrap/bhn-node-bootstrap.sh NAME IP wg0 TYPE REGION
# Types: hub, exit, scan, proxy
```

## License

Source-available — all rights reserved. This repository is public for portfolio and reference purposes. No license is granted to use, copy, modify, or distribute any part of this codebase without explicit written permission from the operator.
