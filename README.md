# Blackhole Network (BHN)

A privacy-focused personal infrastructure platform built on WireGuard with deep defense-in-depth security, AI-powered operations, and algorithmic trading. **Single-operator network — no customers, no public service offering. Personal infrastructure only.**

> **Note:** Repo renamed 2026-05-11 from EventHorizon VPN to Blackhole Network. Vultr-side server display names updated to `BHN|VPS-LOSANGELES-US1`, `BHN|VPS-FRANKFURT-EU1`, `BHN|VPS-NEWJERSEY-US2`. LA-deployed script paths (`/usr/local/sbin/eh-*`, `/opt/eh-diagnostics/*`), PostgreSQL database name `eventhorizon`, email domain `eventhorizonvpn.com`, and n8n credential names (`Postgres EventHorizon`, `EventHorizonVPN-Claude`) are intentionally preserved as live-system identifiers until a coordinated migration session. The "EventHorizon VPN" name is reserved for the future separate commercial product.

## Overview

Blackhole Network is a self-hosted private intelligence and trading infrastructure platform operated by a single operator. Built on battle-tested open-source tools with custom automation and AI-driven monitoring.

**Infrastructure:** WireGuard mesh VPN (4 nodes across US-West, US-East, EU), PostgreSQL, Grafana, n8n, Tor relay network, dnscrypt-proxy, CrowdSec, Suricata, Shadowsocks

**Trading Stack:** Algorithmic paper trading via Alpaca — 5 active strategies across 3 accounts, $150k total capital, real-time signal generation and execution on NJ trading node

**Financial Intelligence:** 6 Grafana dashboards covering market regime classification, ETF price data, macro indicators, sentiment, commodities, energy, agriculture, prediction markets, and options flow

**AI Agent:** HORIZON — n8n-based AI agent powered by Claude, with SMS/voice interface via Twilio, pgvector memory, Redis session management, and read access to all PostgreSQL tables

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

71 tables in the `eventhorizon` database covering:

- Market data: `market_daily`, `market_bars_*`, `market_ticks`, `market_regimes`, `market_sentiment`, `market_events`, `market_signals`
- Macro: `macro_daily`, `macro_indicators`
- Trading: `paper_trades`, `signals_log`, `order_events`, `circuit_breaker_log`, `strategy_performance`, `trading_rules`, `trading_strategies`, `reconciliation_heartbeat`
- Financial intelligence: `earnings_data`, `analyst_data`, `options_chain_snapshots`, `prediction_market_data`, `crypto_market_data`, `investment_signals`, `alpaca_news`
- Alternative data: `agriculture_prices`, `energy_prices`, `weather_snapshots`, `corporate_actions`
- Security: `security_events`, `anomalies`, `pulse_reports`, `node_logs`, `node_logs_summary`, `fail2ban_events`, `crowdsec_decisions`
- Infrastructure: `nodes`, `node_resource_stats`, `node_bandwidth_stats`, `node_disk_stats`, `node_patch_status`, `wg_peer_stats`, `wg_sessions`, `tor_relay_stats`
- AI: `memories` (pgvector 384-dim), `agent_token_log`, `call_transcripts`, `conversation_sessions`, `qa_cache`

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
│   └── services/                    tor-relay, tinyproxy, searxng, librespeed, wallos
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
