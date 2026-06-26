# BHN README — Markup Proposal (2026-05-22)

> **What this is:** the current `README.md` reviewed against everything we've discussed
> (memory + recent commits + the `BHN DOMAIN AND ORG.txt` thinking doc + the Cryptometer
> Vault structure from the screenshots). Annotations show every proposed delta so you
> can review/redline before any live README edit.
>
> **How to read:** annotations are inline HTML comments. The text outside the comments
> is the proposed new README content. Look for:
>
> - 🟢 **ADD** — net-new content vs current README
> - 🟡 **CHANGE** — existing content revised (rationale given)
> - 🔴 **REMOVE** — existing content I'd delete (rationale given)
> - ❓ **DECIDE** — needs your call before I commit anything
> - ⚠️ **FLAG** — current README disagrees with live state / memory; flagged for fix
>
> Nothing in this file touches `README.md`. When you're done commenting, tell me which
> sections to apply and I'll merge them into the live README.

---

## Summary of proposed changes

| # | Section | Type | What changes |
|---|---------|------|--------------|
| 1 | Header note | 🟡 CHANGE | Tighten date framing; cross-reference Cryptometer Vault |
| 2 | Overview / Domain model | 🟢 ADD | Surface **BTEH**, **BlackboxBidder**, **BHNwave**, **IncubatorBHN** — they exist in your thinking doc + vault layout but not in README |
| 3 | PokemonBHN description | 🟡 CHANGE | Reconcile the "wild encounter" framing (README) vs "battle cutscene + rival trainer" framing (your `BHN DOMAIN AND ORG.txt`) — pick one |
| 4 | Companion repo blurb | 🟡 CHANGE | TEAM-ROCKET-BHN rename to `PokemonBlackhole` is pending per your thinking doc — flag here |
| 5 | Phase 1 / Phase 3 | 🟡 CHANGE | Phase 3 currently has 7 bullets; live roadmap has **M1–M10** (10 modules). Sync. Morning briefing line ⚠️ contradicts the no-daily-timer reversal (2026-05-13) |
| 6 | Phase 5 RESILIENCE | 🟡 CHANGE | Add Cryptometer Vault as the operator-PC-side resilience layer (alongside Sweden cold standby) |
| 7 | NEW SECTION — Backup architecture | 🟢 ADD | Full description of the BHN-BLACKBOX vault layout + server→vault flow + WG-unlock-triggers-pull behavior |
| 8 | Storage tiering | 🟢 ADD | Add operator-PC vault sub-section (currently only LA server tiering is documented) |
| 9 | FinancialBHN trading stack | ⚠️ FLAG | Strategy matrix conflicts with `project_strat_2_6_8_shared_account.md` (strat_6/7/8 aliased default key & disabled, strat_2 had no env vars & disabled, strat_13 was removed/re-added). Either update the matrix or add a "Configured vs Live" disclaimer |
| 10 | HORIZON roadmap (Phase 3 inside README) | 🟡 CHANGE | Reconcile with `infrastructure/docs/horizon-roadmap.md` M1–M10 |
| 11 | Repository layout | 🟢 ADD | New scripts from migration commit `4c63417` (frankfurt-recovery, kernel-patch, nightly-diagnostic{.sh,.service,.timer}, post-reboot-verify, security-sweep, status-check, horizon-fix). Also: `scripts/horizon/` has 9 files, README only lists 3 |
| 12 | n8n workflows | 🟢 ADD | `n8n-workflows/pokemon/` (2 files) and the live-only POKEMON-BLACKHOLE-SNIPER (not in repo, but worth a one-line pointer) |

---

# Proposed new README content (annotated)

<!-- ⛔ EVERYTHING BELOW THIS LINE IS THE DRAFT REPLACEMENT FOR README.md.
     Inline HTML comments mark each delta from the current README. -->

# Blackhole Network (BHN)

A privacy-focused personal infrastructure platform built on WireGuard with deep defense-in-depth security, AI-powered operations, and algorithmic trading. **Single-operator network — no customers, no public service offering. Personal infrastructure only.**

<!-- 🟡 CHANGE: Header note expanded to (a) cross-reference the Cryptometer Vault (BHN-BLACKBOX)
     as the operator-PC-side backup destination, and (b) flag that FinancialBHN is
     earmarked for extraction to its own repo per BHN DOMAIN AND ORG.txt.
     CURRENT: "Repo renamed 2026-05-11 from EventHorizon VPN to Blackhole Network..."
     PROPOSED: same content + 2 extra sentences. -->

> **Note:** Repo renamed 2026-05-11 from EventHorizon VPN to Blackhole Network. Vultr-side server display names updated to `BHN|VPS-LOSANGELES-US1`, `BHN|VPS-FRANKFURT-EU1`, `BHN|VPS-NEWJERSEY-US2`. LA-deployed script paths (`/usr/local/sbin/eh-*`, `/opt/eh-diagnostics/*`), PostgreSQL database name `eventhorizon`, email domain `eventhorizonvpn.com`, and n8n credential names (`Postgres EventHorizon`, `EventHorizonVPN-Claude`) are intentionally preserved as live-system identifiers until a coordinated migration session. The "EventHorizon VPN" name is reserved for the future separate commercial product.
>
> **Backup destination:** Operator-PC-side, all BHN repos + the live `eventhorizon` Postgres database back up into a Cryptomator vault (**BHN-BLACKBOX**) organized by domain. See [Backup architecture](#backup-architecture).
>
> **Extraction roadmap:** FinancialBHN is earmarked for extraction to its own repo (`FINANCIALBHN`). Until that lands, FinancialBHN code/data lives inside this repository.

## Overview

Blackhole Network is a self-hosted private intelligence and trading infrastructure platform operated by a single operator. Built on battle-tested open-source tools with custom automation and AI-driven monitoring.

<!-- 🟡 CHANGE: Domain model paragraph expanded. The current README mentions only 3 domains
     (Pokemon, Financial, Security). Your BHN DOMAIN AND ORG.txt + the vault structure
     show 5 organizational buckets: 3 active domains + IncubatorBHN + StandaloneBHN.
     ❓ DECIDE: Is IncubatorBHN officially a domain, or just a holding pen for
     in-development projects (e.g., Beyond The Horizon)? The vault has it as a peer of
     the other domains. Your thinking doc doesn't mention it. -->

**Domain model:** BLACKHOLE-NETWORK (BHN) is the infrastructure platform. It hosts three active **data domains** — **PokemonBHN**, **FinancialBHN**, and **SecurityBHN** — over shared infrastructure (HORIZON, WireGuard, PostgreSQL, n8n). The naming pattern is `{Domain}BHN`; a thing earns a domain label only if it has its own distinct tables, scripts, and services. Two additional buckets exist for **organization only** (not domains): **IncubatorBHN** (in-development projects pre-graduation) <!-- ❓ DECIDE confirm definition --> and **StandaloneBHN** (projects that don't depend on the `eventhorizon` database — e.g. `BHNwave`).

### Projects by domain

<!-- 🟢 ADD: This per-domain project list is net-new. Current README only describes
     the data shape of each domain, not the projects/repos that live inside it.
     Pulled from BHN DOMAIN AND ORG.txt + vault layout. -->

| Domain | Project / repo | Status |
|--------|----------------|--------|
| **SecurityBHN** | BLACKHOLE-NETWORK (this repo) — security tables, scripts, n8n collectors | live |
| **SecurityBHN** | **BTEH — Beyond The EventHorizon** (`BTEH-Beyond-The-EventHorizon`) — system-wide audit protocol, 10 sections + 4 appendices | <!-- ❓ DECIDE: status? scaffolded? --> |
| **FinancialBHN** | BLACKHOLE-NETWORK (this repo) — trading stack, financial intelligence collectors | live |
| **FinancialBHN** | **FINANCIALBHN** (new repo) — extraction pending | planned |
| **PokemonBHN** | BLACKHOLE-NETWORK (this repo) — `master_card_catalog`, scrapers, `sold_listings`, `pop_reports` | live |
| **PokemonBHN** | **PokemonBlackhole** (`TEAM-ROCKET-BHN` — rename pending) — GBA-style battle interface | live (separate repo) |
| **PokemonBHN** | **BlackboxBidder** (`BLACKBOX-BIDDER` — new repo) — eBay sniper + price intelligence + reseller toolkit | new |
| **IncubatorBHN** | <!-- ❓ DECIDE: which project lives in IncubatorBHN? Vault folder is "BEYOND THE HORIZON-BACKUP" — is that a separate project from BTEH? --> | <!-- ❓ --> |
| **StandaloneBHN** | **BHNwave** — offline beep-tone cipher (self-contained HTML), independent of `eventhorizon` | live |

### Shared infrastructure

WireGuard mesh VPN (4 nodes across US-West, US-East, EU), PostgreSQL, Grafana, n8n, Tor relay network, dnscrypt-proxy, CrowdSec, Suricata, Shadowsocks. Serves all domains, belongs to none.

---

### FinancialBHN — trading & financial intelligence

Algorithmic paper trading via Alpaca on the NJ trading node, across 3 accounts / $150k total capital. As of 2026-05-21, only **Strat 13 (`BHN-RSI-INTRADAY`)** is active as an operational test to validate execution and protocol; the remaining strategies are sidelined pending that validation. Financial intelligence is surfaced through 6 Grafana dashboards covering market regime, ETF prices, macro indicators, sentiment, commodities, energy, agriculture, prediction markets, and options flow.

<!-- 🟡 CHANGE: Add the extraction-pending note here to mirror the header. -->

> Earmarked for extraction to its own repo (`FINANCIALBHN`) at a future session.

---

### PokemonBHN — graded-card market

WOTC-era graded-card data pipeline. `master_card_catalog` (637 cards / 1,354 variant rows, 8 sets) feeds three streams — sold comps (`sold_listings`), active eBay listings (`ebay_listings`), and graded population reports (`pop_reports`) — with CGC/PSA/BGS/SGC grade normalization via `master_grade_catalog`.
→ See [Pokémon Graded Card Data Pipeline](#pokémon-graded-card-data-pipeline) and [Data standards & authority](#data-standards--authority).

This data drives three downstream uses: (1) the operator's personal collecting / investment research, (2) potential B2B/B2C inventory & valuation software (similar to ChartPricing), and (3) the backend hard data for **Pokemon Blackhole** (the game). PokemonBHN also hosts **BlackboxBidder**, a desktop snipe-engine + price-intelligence + reseller toolkit aimed at eBay graded-card auctions; it pulls and stores its data under PokemonBHN tables in `eventhorizon`, replacing the broken Gixen integration.

<!-- 🟡 CHANGE: Re-flowed the existing paragraph to cleaner sentences + added the
     BlackboxBidder one-liner. Original paragraph was 3 run-on sentences with two typos
     ("reserach", "FInally"). -->

### SecurityBHN — security telemetry

Defense-in-depth signals across the 4-node mesh: `security_events`, `anomalies`, `fail2ban_events`, `crowdsec_decisions`, plus per-node resource, bandwidth, WireGuard, and Tor stats. Governance and audit are layered on top via **BTEH (Beyond The EventHorizon)** — a separate repo housing the 10-section system-wide audit protocol.

<!-- 🟢 ADD: BTEH cross-reference. Currently absent from README. -->

---

### HORIZON — AI agent (shared infrastructure)

The autonomous intelligence layer — an n8n-based AI agent powered by Claude with full read access to all PostgreSQL tables. Shared infrastructure, not a domain: it serves every domain but belongs to none. It acts as both personal assistant and autonomous infrastructure manager:

- **Operations** — real-time monitoring of all 4 nodes (health, security events, anomalies, pulse). SMS/voice alerts (Twilio + ElevenLabs) for P1/P2 events, outages, and storage pressure. Executes restricted actions on operator command: restart services, fail2ban bans, smoke tests, trading killswitch.
- **Querying & control** — full read access across every domain's tables. Conversational over SMS, voice, and VPN-only web chat: *"How are my strategies performing?"*, *"Any threats in the last 24 hours?"*, *"HALT trading"*, *"Restart n8n"*.
- **Memory** — pgvector semantic memory (384-dim) for long-term context, Redis for short-term session state; a persistent model of operator preferences, infra state, and history.

**Goal:** one conversational interface to the entire BHN stack — infrastructure, security, trading, financial intelligence — 24/7 via SMS from anywhere.

> Full build plan and module specs live in [`infrastructure/docs/horizon-roadmap.md`](infrastructure/docs/horizon-roadmap.md) — 10 modules (M1 Voice Pipeline → M10 Job Search), phased across 5+ sessions.

<!-- 🟢 ADD: Roadmap doc cross-reference. The README's "Phase 3 — 7 bullets" no longer
     matches the live 10-module plan. -->

---

### Companion repo — Pokemon Blackhole (the game)

**Pokemon Blackhole** (repo `TEAM-ROCKET-BHN` — rename to `PokemonBlackhole` pending) is a separate front-end — not part of this repository — and an independent *consumer* of PokemonBHN data.

<!-- 🟡 CHANGE: The current README describes this as a "wild encounter" where a listing
     renders as a Pokemon. Your BHN DOMAIN AND ORG.txt describes it differently:
     "When HORIZON detects a card deal alert, it triggers a full Pokemon battle cutscene.
     The seller becomes your rival trainer." Those are two different game-loop framings.
     ❓ DECIDE which is current:
       (A) Browse-driven: every listing = a wild encounter (current README)
       (B) Alert-driven: HORIZON deal alert = cutscene + rival trainer (your .txt)
     I've kept the current README's framing below as the safer default — swap if (B). -->

It's a GBA-style FireRed/LeafGreen interface that turns card trading into gameplay: a listing renders as a wild Pokémon encounter, where the card is the Pokémon, the HP bar is the deal quality (listed price vs. market value — greener/fuller = better deal), the level is the grade, the badge is the grading company, and rarity tiers map to population scarcity. The player can BUY (open the listing), WATCH, ANALYZE (open in Claude), or RUN.

It reads the same `master_card_catalog`, `pop_reports`, and `sold_listings` that PokemonBHN populates — that's the data connection. The game is **not built or orchestrated by HORIZON**; it's an independent app, not driven by the AI agent. HORIZON's deal recommendations *can surface inside it* as advice (e.g. *"in the RED — 68% below market, recommend BUY"*), the same way they appear elsewhere in the stack — but the game stands on its own. In short: a genuinely fun, GameBoy-style way to trade graded Pokémon cards online, built on top of the PokemonBHN market data. **BLACKHOLE-NETWORK produces the card-market data; Pokemon Blackhole is an independent game that renders it as a battle.**

---

*Any future public VPN product is a separate concern (different servers, protocol, and holding entity) and is not part of this repository.*

## Architecture

### Five-phase build plan

<!-- 🟡 CHANGE: Phase 3 sub-bullets reorganized. Current README's Phase 3 has 7 bullets
     that don't match the 10-module M1–M10 plan in horizon-roadmap.md. Replaced with
     a one-line summary that points to the roadmap doc. Phase 1's "Frankfurt routing —
     BROKEN" line preserved, but ⚠️ verify status (no commit since `c99a619` references
     a fix landing). -->

```
Phase 1: NETWORK                        [✅ ~90% complete]
├─ LA hub (BHN|VPS-LOSANGELES-US1) — hub, PostgreSQL, n8n, HORIZON, Grafana
├─ Frankfurt exit node (BHN|VPS-FRANKFURT-EU1) — EU exit, LibreSpeed, SearXNG, Tor relay
├─ NJ trading node (BHN|VPS-NEWJERSEY-US2) — Alpaca paper trading, Strat 13 active (operational test), others sidelined
├─ Hillsboro proxy node (BHN-HILLSBORO-US3) — LA egress proxy via tinyproxy, Tor relay
├─ WireGuard hub-and-spoke mesh — all nodes + operator devices connected, PSK on most peers
├─ Bootstrap script v4 (declarative node types + modular install)
├─ Frankfurt exit routing — BROKEN, FRA MASQUERADE fix pending  ⚠️ verify status
└─ Future nodes: Sweden (Bahnhof), Iceland via snapshot deployment

Phase 2: DASHBOARD                      [✅ ~85% complete]
├─ PostgreSQL on encrypted NVMe — 78 tables, live financial + security data
├─ 6 Grafana dashboards (VPN-only access):
│   ├─ BHN Market Intelligence
│   ├─ BHN Trade Execution & Operations
│   ├─ BHN Derivatives & Options Markets
│   ├─ BHN Prediction & Alternative Markets
│   ├─ BHN Commodities & Tangible Asset Markets
│   └─ BHN Infrastructure & Security Operations
├─ n8n for action automation and AI orchestration
├─ Financial intelligence layer — 32 ETF tickers, FRED macro, USDA agriculture, EIA energy
└─ Grafana alerting — not yet wired

Phase 3: AI INTEGRATION                 [in progress]
└─ See infrastructure/docs/horizon-roadmap.md (10 modules M1–M10:
   Voice Pipeline, Morning Briefing, Evening Briefing, Intraday Alerts,
   eBay, Trading, Outbound Calling, Email, Calendar, Job Search)

Phase 4: PER-NODE SERVICES              [~80% complete]
├─ Trading stack live on NJ — Strat 13 operational test (others sidelined), 3 Alpaca accounts
├─ Wallos (LA) — subscription / cost tracking [✅] http://<BHN_WG_LA_IP>:8090
├─ SearXNG (Frankfurt) — private meta-search [✅] http://<BHN_WG_FRA_IP>:8089
├─ LibreSpeed Frankfurt (EU speedtest) [✅] http://<BHN_WG_FRA_IP>:8088
├─ tinyproxy (Hillsboro) — LA egress proxy [✅] verified, lockdown pending
├─ Tor relays: BHNFornaxEU1 (Frankfurt, live), BHNHeliosUS3 (Hillsboro, bootstrapping),
│              BHNNebulaUS2 (NJ, deployed not live)
└─ MyFamily fingerprint exchange — pending (after all relays 24h+)

Phase 5: RESILIENCE                     [designed, not built]
├─ Sweden cold standby + dark replication node (Bahnhof hosting, outside Vultr)
├─ Tor hidden-service replication LA to Sweden (no Vultr cross-region correlation)
├─ Single-command failover (bhn-failover-activate.sh)
├─ Sweden Tor middle relay (joins MyFamily with FRA + NJ)
├─ Iceland exit node EU3
└─ Cryptometer Vault (operator-PC, BHN-BLACKBOX) — Cryptomator-encrypted
   backup of all repos + live PG dumps, auto-pulled on WG-up + vault-unlock.
   See Backup architecture.                                       🟢 NEW
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

<!-- 🟢 ADD: Operator-PC storage tier (Cryptomator vault). New section. -->

### Operator-PC storage (Cryptometer Vault)

```
BHN-BLACKBOX (Cryptomator vault)
  Ciphertext at rest: D:\BHN-BLACKBOX\BHN-BLACKBOX\
  Mounts on unlock to: E:\
  └─ E:\
     ├─ BLACKHOLE NETWORK-BACKUP/      BHN repo + legacy EH repo + eventhorizon PG dumps
     ├─ SecurityBHN/                   (placeholder — populates when domain extracted)
     ├─ FinancialBHN/                  (placeholder — populates when extracted)
     ├─ PokemonBHN/
     │   ├─ BLACKBOX BIDDER-BACKUP/    BLACKBOX-BIDDER repo
     │   └─ POKEMON BLACKHOLE-TEAM ROCKET BHN-BACKUP/   TEAM-ROCKET-BHN repo
     ├─ IncubatorBHN/
     │   └─ BEYOND THE HORIZON-BACKUP/                  ❓ which repo
     └─ StandaloneBHN/
         └─ BHNwave-BACKUP/             BHNwave repo
```

### PostgreSQL schema

78 tables in the `eventhorizon` database covering:

<!-- 🟡 CHANGE: Schema list trimmed for brevity. Current README is encyclopedic;
     proposal preserves all 9 categories but adds a one-line pointer to the canonical
     DDL source instead of inlining every column. ❓ DECIDE: keep full list or trim?
     I'll keep the full list below since it's useful at-a-glance — flag if you'd prefer trimmed. -->

- Market data: `market_daily`, `market_bars_*`, `market_ticks`, `market_regimes`, `market_sentiment`, `market_events`, `market_signals`
- Macro: `macro_daily`, `macro_indicators`
- Trading: `paper_trades`, `signals_log`, `order_events`, `circuit_breaker_log`, `strategy_performance`, `trading_rules`, `trading_strategies`, `reconciliation_heartbeat`
- Financial intelligence: `earnings_data`, `analyst_data`, `options_chain_snapshots`, `prediction_market_data`, `crypto_market_data`, `investment_signals`, `alpaca_news`
- Alternative data: `agriculture_prices`, `energy_prices`, `weather_snapshots`, `corporate_actions`
- Security: `security_events`, `anomalies`, `pulse_reports`, `node_logs`, `node_logs_summary`, `fail2ban_events`, `crowdsec_decisions`
- Infrastructure: `nodes`, `node_resource_stats`, `node_bandwidth_stats`, `node_disk_stats`, `node_patch_status`, `wg_peer_stats`, `wg_sessions`, `tor_relay_stats`
- AI: `memories` (pgvector 384-dim), `agent_token_log`, `call_transcripts`, `conversation_sessions`, `qa_cache`
- Collectibles (PokemonBHN — see [Pokémon graded-card data pipeline](#pokémon-graded-card-data-pipeline)): `master_card_catalog`, `pop_reports`, `sold_listings`, `ebay_listings`, `master_grade_catalog`, `master_grading_criteria_catalog`, `master_set_catalog`

<!-- 🟢 ADD: master_set_catalog (added 2026-05-21, commit ff03672) — missing from current README. -->

## Security stack

*(unchanged — current README content stands; section preserved as-is)*

## Backup architecture                                              <!-- 🟢 NEW SECTION -->

BHN backs up to an operator-PC-side Cryptomator vault — **BHN-BLACKBOX** — organized by domain to mirror the project structure. The vault is the single recovery surface for both repo content and live database state.

### Server-side artifact production

```
LA hub                                    Hillsboro                          NJ
  ├─ pg_dump eventhorizon (daily)          (no backup role)                    (no backup role)
  ├─ tar BHN repo snapshot
  ├─ tar EH legacy snapshot
  └─ stage artifacts at /mnt/eh-hdd-cold/backup-staging/
       └─ <DOMAIN>-BACKUP/<artifact>.{tar.zst,sql.zst}
```

### Operator-PC pull (WG-up + vault-unlock)

```
Trigger: Cryptomator unlocks BHN-BLACKBOX  AND  WireGuard handshake fresh
   ↓
Hook script: bhn-vault-sync.ps1
   ↓
For each domain folder in vault:
   - rsync (or restic) pull from LA staging → vault subfolder
   - verify sha256 of latest artifact
   - prune old artifacts per retention policy
   ↓
Vault closes (Cryptomator auto-lock) → encrypted at rest
```

### Retention (proposed defaults — open for revision)

| Artifact | Frequency | Keep |
|----------|-----------|------|
| `eventhorizon` pg_dump | daily | 30 dailies + 12 monthlies + 5 yearlies |
| BHN repo snapshot | weekly | 8 weeklies (git history covers the rest) |
| EH legacy repo snapshot | weekly | 4 weeklies |
| Per-project repo snapshots | weekly | 4 weeklies each |

> ❓ **Open design questions** (for the Phase 5 backup build, not the README itself):
> 1. **rsync vs restic** — restic gives encryption + dedup + retention out of the box, but adds a binary on both sides. rsync is simpler but you handle retention by hand. Default proposal: restic, with the repo *also* encrypted server-side (defense-in-depth — vault encryption alone leaves the staging area plaintext on LA).
> 2. **WG-unlock trigger** — Cryptomator on Windows doesn't have a first-class post-unlock hook. Options: (a) PowerShell scheduled task polling for drive `E:\` every 30s while WG is up, (b) systray helper watching `WIN32_LogicalDisk` WMI events for E: arrival, (c) Cryptomator's experimental `--on-unlock` flag if your build supports it. **Default proposal:** (b) — WMI event subscription is lighter than polling and triggers immediately on unlock; (a) as fallback if WMI is flaky.
> 3. **Where does `BEYOND THE HORIZON-BACKUP` map?** The vault has it under `IncubatorBHN/`, but neither memory nor your `BHN DOMAIN AND ORG.txt` defines what "Beyond The Horizon" is as a project distinct from BTEH (Beyond The EventHorizon). Need this resolved before the backup script can populate it.

## FinancialBHN — trading stack

Runs on NJ trading node (BHN|VPS-NEWJERSEY-US2). Paper trading via Alpaca.

<!-- ⚠️ FLAG: This matrix conflicts with the per-strategy Alpaca isolation cleanup
     (`project_strat_2_6_8_shared_account.md`). Per memory:
       - strat_6/7/8 aliased the default key (disabled)
       - strat_2 had no env vars (disabled)
       - strat_13 aliased strat_4 with no rules block (removed; later re-added)
     The matrix below shows strat_6/7/8 in Account 1 and strat_13 in Account 3 as
     normal enabled rows. That's the *intended* layout once paper accounts come back,
     but it's not the *current live* layout. Recommend changing the heading from
     "Status: only Strat 13 active" → "Configured layout (post-2026-05-19 cleanup
     pending real paper accounts):" so the matrix is honestly framed as future-state. -->

> **Status (2026-05-21):** only **Strat 13 (`BHN-RSI-INTRADAY`)** is active, as an operational test;
> all other strategies are **sidelined** pending validation. The matrix below is the *configured*
> strategy set (capital/schedule), not the current live set.
>
> **Per-strategy isolation cleanup pending real paper accounts.** As of 2026-05-19,
> strat_6/7/8 aliased the default API key (disabled), strat_2 had no env vars (disabled),
> and strat_13 was removed/re-added after aliasing strat_4 without a rules block.
> See `project_strat_2_6_8_shared_account.md` for the audit trail.                <!-- 🟢 ADD -->

```
Account 1 — BHN-STRAT-PRIMARY (<ALPACA_PAPER_ACCOUNT_ID>)    $100,000
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

*(content largely unchanged — current README is accurate as of 2026-05-22)*

<!-- 🟡 CHANGE: Add a one-line pointer to the live-only POKEMON-BLACKHOLE-SNIPER n8n
     workflow per memory `project_pokemon_blackhole_sniper.md`. Currently absent. -->

> **Live-only:** the **POKEMON-BLACKHOLE-SNIPER** n8n workflow (an eBay sniper feeding `sold_listings` + `pop_reports`) runs on the live n8n instance but is **not exported into the repo**. Schema constraints + 3 recurring bugs documented in operator memory (2026-05-20).

## Data standards & authority

*(unchanged)*

## BLACKHOLE-NETWORK roadmap

<!-- 🟡 CHANGE: This is the duplicate roadmap section near the bottom of the current
     README. It overlaps with the Phase 1-5 block at the top and partially contradicts
     the HORIZON M1-M10 plan. Proposal: delete this block entirely (it's a stale clone
     of the HORIZON roadmap) and replace with a one-line pointer to the canonical
     horizon-roadmap.md. -->

> See [`infrastructure/docs/horizon-roadmap.md`](infrastructure/docs/horizon-roadmap.md) for the canonical phased build plan (10 modules across 5+ sessions).

## Repository layout

<!-- 🟡 CHANGE: Expanded to reflect what's actually in scripts/ today.
     Current README claims scripts/ contains only `trading/` and `horizon/` subdirs,
     but the migration commit (4c63417) + accumulated work put ~50 BHN-* scripts at
     the scripts/ root. Updated tree below. -->

```
.
├── README.md                        Project overview (this file)
├── BACKUP.md                        Backup architecture deep-dive
├── STATUS.md                        Current build status snapshot
├── infrastructure/
│   ├── bootstrap/                   v4 modular bootstrap
│   │   ├── bhn-node-bootstrap.sh    Master script (open → install → lockdown)
│   │   ├── node-types/              hub.sh, exit.sh, scan.sh, proxy.sh
│   │   ├── modules/                 wireguard, crowdsec, suricata, shadowsocks,
│   │   │                            dnscrypt, firewall, ssh-hardening, storage,
│   │   │                            network-policy, backup
│   │   └── policies/                Declarative network policies per node type
│   ├── docs/                        Architecture docs, roadmap, session updates
│   │   ├── horizon-roadmap.md       HORIZON M1–M10 build plan + module specs
│   │   ├── pokemonbhn/              collectibles-data-standard.md + design source
│   │   ├── audit/                   Comprehensive-audit workspace, screenshots
│   │   └── BHN session updates/     Per-session handoff docs
│   ├── grafana/dashboards/          All 6 Grafana dashboard JSONs
│   ├── services/                    tor-relay, tinyproxy, searxng, librespeed, wallos
│   └── scrapers/                    Graded-card pop scrapers (CGC cron + PSA stealth) + psa-sets.json
├── scripts/                         Production scripts (deployed to LA)
│   ├── bhn-*.sh / bhn-*.py          ~50 operational scripts: collectors (CrowdSec,
│   │                                Suricata, fail2ban, conntrack, DNS, docker,
│   │                                iptables, n8n stats, PG stats, resource, vnstat,
│   │                                WG/Tor stats), pollers (Alpaca, CoinGecko, EIA,
│   │                                Finnhub, FMP, FRED, Kalshi, Polymarket, Quiver,
│   │                                USDA), diagnostics (nightly-diagnostic, status-check,
│   │                                security-sweep, post-reboot-verify, kernel-patch),
│   │                                recovery (frankfurt-recovery, node-offline-recover,
│   │                                la-restore, purge), HORIZON helpers (briefing,
│   │                                seed-persona, weekly-report)
│   ├── trading/                     FinancialBHN trading framework (Python)
│   │   ├── trading_core.py          Core Alpaca + PostgreSQL integration
│   │   ├── strategy_*.py            12 strategy implementations
│   │   ├── master_killswitch.py     Emergency halt + flatten all positions
│   │   ├── daily_summary.py         Daily PnL summary via HORIZON/SMS
│   │   ├── reconciliation_daemon.py Position reconciliation
│   │   ├── config-templates/        Per-strategy config skeletons
│   │   └── systemd-units/           Service/timer units
│   └── horizon/                     HORIZON-side collectors + generators
│       ├── macro_collector.py       FRED macro data (daily)
│       ├── market_data_collector.py Alpaca ETF price data (daily)
│       ├── sentiment_collector.py   Fear/greed, AAII sentiment (daily)
│       ├── morning_brief_generator.py
│       ├── paper_trades_watch.py
│       ├── pattern_detector.py
│       ├── regime_classifier.py
│       ├── events_calendar.py
│       └── systemd-units/
├── n8n-workflows/                   Exported n8n workflow JSONs
│   ├── bhn-horizon.json             HORIZON AI agent workflow
│   ├── bhn-voice-test.json          Voice pipeline smoke test
│   ├── eh-network-pulse-2h.json     2-hour pulse report workflow
│   ├── eh-news-poll.json            News poller
│   ├── eh-weather-poll.json         Weather poller
│   └── pokemon/                     PokemonBHN-specific workflows
│       ├── pokemon-bhn-vintage-cgc.json
│       └── pokemon-bhn-vintage-psa.json
└── sql/                             PostgreSQL schemas
```

## Naming conventions

*(unchanged)*

## Console terminology

*(unchanged)*

## Services map (VPN required)

*(unchanged)*

## Bootstrap (new node)

*(unchanged)*

## License

Private — all rights reserved.

---

# Open decisions for operator

Collected from `❓ DECIDE` markers above:

1. **IncubatorBHN definition** — Is it an organizational bucket for in-development projects, or something more formal? What graduates from Incubator → its own domain?
2. **BEYOND THE HORIZON repo identity** — Vault has `IncubatorBHN/BEYOND THE HORIZON-BACKUP/`. Is "Beyond The Horizon" a distinct project from BTEH (Beyond The EventHorizon, the audit framework)? Or are they the same and the vault folder name is just the longer form?
3. **PokemonBlackhole framing** — Browse-driven (current README) or alert-driven (your `BHN DOMAIN AND ORG.txt`)?
4. **BTEH status** — Just scaffolded? Live? Operating? The audit-tool screenshots in `infrastructure/docs/audit/screenshots/` show GitHub repo views — is BTEH already a working repo?
5. **PostgreSQL schema list — full or trimmed** in the README? Current is encyclopedic; could shrink to categories + pointer to `sql/`.
6. **Frankfurt routing status** — README still says "BROKEN, FRA MASQUERADE fix pending". Latest scripts (`bhn-frankfurt-recovery.sh`, migration commit) suggest active work; verify before this line ships.

---

# Annotations key

- 🟢 **ADD** — net-new content in this proposal
- 🟡 **CHANGE** — existing content revised (rationale given inline)
- 🔴 **REMOVE** — existing content I'd delete
- ❓ **DECIDE** — needs operator call
- ⚠️ **FLAG** — current README disagrees with live state / memory

Tally:
- 🟢 ADD: 9
- 🟡 CHANGE: 10
- 🔴 REMOVE: 1 (the duplicate roadmap section near the bottom)
- ❓ DECIDE: 6
- ⚠️ FLAG: 2 (FinancialBHN matrix, Frankfurt routing status)
