# HORIZON — Roadmap & Architecture

Consolidated design document for the HORIZON AI assistant. Source of truth for module specs, voice stack architecture, data model, retention policy, jurisdictional posture, and build phasing.

Companion to the operator's freeform plan at `HORIZON PLAN.txt` (repo root). Where the two differ, decisions captured in this file are the latest and override the txt.

> "Named after the Event Horizon Telescope (which photographed Sagittarius A* + M87*). Fits the BHN brand (project renamed from EventHorizon 2026-05-11; the EventHorizon VPN name is reserved for a separate future commercial product). Sounds natural spoken. Tone: professional, warm, concise — personal chief of staff."

---

## Identity

| Attribute | Value |
|-----------|-------|
| Name | **HORIZON** |
| n8n workflow | `HORIZON` (was `EventHorizon AI Agent v1.0`) |
| Email | `horizon@eventhorizonvpn.com` (operator-provisioned) |
| Google account | `horizon@gmail.com` (operator-provisioned, calendar management) |
| Primary voice | Professional female — ElevenLabs library voice (specific ID TBD at setup) |
| Secondary voice | Operator's voice — cloned via ElevenLabs Professional Voice Clone (used when HORIZON represents the operator on outbound calls) |
| Tone | Professional, warm, concise. Direct without bluster. Match operator's terse cadence. No filler ("Great question", "Certainly!"). |
| Underlying model | Claude Sonnet 4.6 (with Haiku 4.5 as classifier router for cost-cascade — see Query Architecture) |

---

## Build phasing

| Phase | Modules | Pre-reqs |
|-------|---------|----------|
| **Built** | BHN Network Ops, pgvector memory layer (Pulse writes + HORIZON reads) | — |
| **Done this session** | JARVIS → HORIZON rename (workflow + memories + repo) | — |
| **Session 1 — voice foundation** | M1 Voice Pipeline, M2 Morning Briefing | All operator-action accounts provisioned (Twilio, ElevenLabs Creator, Google, OpenWeatherMap, NewsAPI, Alpaca) |
| **Session 2 — daily rhythm** | M3 Evening Briefing, M4 Intraday Alerts | Session 1 |
| **Session 3 — eBay + trading** | M5 eBay Integration, M6 Trading Integration | Session 1; eBay API approved (already done); Alpaca paper |
| **Session 4 — outbound + email** | M7 Outbound Calling, M8 Email Management | Session 1; Whisper deployed on LA |
| **Session 5 — calendar polish** | M9 Calendar Management | Session 4 |
| **Session 5.5 — memory plumbing** | Redis hot-memory layer (working memory in front of pgvector) | Session 5; HORIZON workflow Format-Memory + token-logging fixes landed |
| **Later (Phase 6)** | M10 Job Search | Conversation memory mature enough for tone-matched cover letters |

---

## Module specifications

### M1 — Voice Pipeline

**Goal:** wire ElevenLabs + Twilio into n8n; establish the SMS confirmation loop pattern that all later modules reuse.

**Components:**
- ElevenLabs API → n8n HTTP Request node, audio output cached on LA at `/mnt/eh-nvme-hot/horizon/audio-cache/`
- Twilio API → n8n credential, outbound number registered with operator's caller-ID display name `HORIZON`
- HORIZON voice persona: ElevenLabs library female voice (specific ID picked at setup, stored as `EH-ElevenLabs-VoiceID-HORIZON`)

**Test flow:**
1. HORIZON SMS to operator: "Trading opportunity: AAPL hit RSI 30. Buy 5 shares paper-account? Reply 1=YES, 2=NO."
2. Operator replies `1` or `2`
3. HORIZON acts on response (paper-trade execution or stand down)
4. HORIZON SMS confirmation back: "Done. AAPL paper buy 5 @ $X.XX."

**Confirmation timeout:** **default deny, 10 min.** No reply within window → HORIZON treats as NO, stands down, logs the unresponded prompt. No retry-and-escalate (avoids notification fatigue).

### M2 — Morning Briefing

**Trigger:** daily cron at operator-defined time (default 7:00 AM operator-local).

**Mechanism:** Twilio places an outbound call to operator's phone. ElevenLabs voice reads the briefing. Sequence:

```
"Good morning, Hayden."
🌤  Weather — current + today's forecast (OpenWeatherMap)
📅  Calendar — today's events (Google Calendar API)
🔒  Security overnight — BHN network status, blocked IPs, anomalies (PG queries against security_events, node_logs, anomalies)
📈  Markets overnight — SPY, BTC, watchlist movers (FMP + Alpaca)
🃏  eBay — new messages, offers, actions needed (eBay API)
🚨  Trading opportunities — anything overnight hitting M6 parameters
📰  Breaking news — top 3 stories (NewsAPI)
"Have a great day, Hayden."
```

**Voice budget:** ~90 seconds → ~15K characters TTS → about half of Creator-tier monthly quota. Need to cap-and-summarize aggressively (top 3 of each category, not exhaustive).

### M3 — Evening Briefing

**Trigger:** daily cron at operator-defined time (default 6:00 PM operator-local).

**Sequence:**
- Market close summary (SPY/QQQ/BTC + watchlist)
- eBay daily summary (new comps, listings, customer messages handled / pending)
- BHN security daily (counts by severity from `security_events` + `node_logs`, top blocked sources, any anomalies)
- Tomorrow's calendar preview
- Overnight opportunities to watch (anything HORIZON identified that's near M6 parameter thresholds but not yet fired)

### M4 — Intraday Alerts

Real-time monitoring with channel selection per trigger:

| Trigger | Alert channel | Reasoning |
|---------|---------------|-----------|
| Stock hits parameter | Voice call + SMS | Trading needs immediate attention |
| eBay deal found | Push notification + SMS | Time-sensitive but not urgent |
| Security anomaly | Voice call (immediate) | Network-level threats can't wait |
| Card offer received (eBay) | Push notification | Can review later |
| Market volatility spike | SMS | Awareness, not action |
| Calendar reminder | SMS | Standard reminder |
| eBay message received | Push + SMS | Customer-service responsiveness |

Channel mix avoids alert fatigue — voice calls reserved for tier-1 (trading + security).

### M5 — eBay Integration

**Watch list:** Pokémon TCG cards, target sets:
- Team Rocket 1st Edition (1999)
- Gym Challenge

**Target grades:** PSA 10/9 · CGC 10/9.5 (esp. Blue Label) · SGC 10

**Data flow:**
1. eBay Browse API: poll active listings matching watchlist filters
2. eBay Finding API (`completedItems=true`): pull 90-day sold comps
3. Compute rolling 90-day average price per (set, card, grade) tuple
4. Alert if active listing price < (avg × threshold). Threshold operator-tunable in `trading_rules` PG table.
5. Operator-confirmation purchase loop:
   ```
   HORIZON finds deal → SMS + push with details + link
   Operator replies 1=BUY, 2=PASS, 3=MORE_INFO
   On BUY → HORIZON places offer/buy via eBay API → confirms back
   ```

**Customer service messages:** read from inbox → categorize (refund / shipping question / product question / offer) → draft response → operator confirms or edits → send.

**Schema additions:**
- `ebay_watchlist (id, set_name, card_name, grade, max_price_pct_of_90d_avg, active, notes)`
- `ebay_listings_seen (listing_id, title, current_price, seller, ...)`
- `ebay_messages (msg_id, type, draft_response, sent_response, status)`

### M6 — Trading Integration

**Stack:**
- **Alpaca** — official API, free, paper + live. Paper-only initially.
- **FMP** — market data (already MCP-connected).

**Rules engine:** `trading_rules` PG table holds operator-defined parameters:
```
| symbol | direction | trigger_type    | threshold | action     | active |
| AAPL   | long      | rsi_below       | 30        | buy 5      | true   |
| SPY    | hedge     | pct_drop_5min   | 1.5       | sell short | true   |
```

**Confirmation flow** (mandatory, no auto-execute):
1. Trigger fires (data point crosses threshold)
2. Validate against `active` rule + cooldown window
3. HORIZON SMS + voice call: ElevenLabs reads opportunity ("AAPL RSI hit 28, rule says buy 5 paper")
4. Operator presses `1=YES` / `2=NO` on Twilio DTMF
5. HORIZON executes via Alpaca paper API (or stands down)
6. Confirmation SMS back with order ID + fill price

**Live promotion gate:** paper trading only until operator explicitly flips a STATUS.md "PROMOTE TO LIVE" entry per ruleset. No bulk promotion — each rule promotes individually with operator review.

**Excluded:** Robinhood (unofficial API, TOS risk).

### M7 — Outbound Calling

**Capability:** HORIZON places calls on operator's behalf — appointments, product inquiries, customer service, info gathering.

**Flow:**
```
Operator request (chat/voice)
    ↓
HORIZON dials via Twilio
    ↓
ElevenLabs speaks (operator-voice clone for "I'm calling on behalf of Hayden" framing)
    ↓
[Business calls only] 3-second disclosure prefix: "This call may be recorded for quality and assistance purposes."
    ↓
Whisper STT (local on LA, tiny model) — streaming transcription
    ↓
Raw audio deleted IMMEDIATELY post-STT (no QA hold; see Recording Posture)
    ↓
Transcript → PG (call_transcripts table)
    ↓
HORIZON summarizes via Sonnet → embedding → memories table (memory_type='conversation')
    ↓
Outcome SMS to operator
```

### M8 — Email Management

**Capability:** read + categorize + draft + (with confirmation) send.

**Identity:** all sends from `horizon@eventhorizonvpn.com`. Operator-as-sender pattern: HORIZON drafts in operator's voice, operator confirms important ones, HORIZON sends.

**Routine handling:** auto-respond to standard eBay messages (shipping ETA, basic product questions) without operator intervention, using draft templates approved during testing.

**Important emails:** confirmation gate before send. Same SMS confirmation pattern as M1.

### M9 — Calendar Management

**Capability:** Google Calendar API via `horizon@gmail.com`.

- Create events with attendees (sends invites on operator's behalf)
- Read schedule (feeds morning briefing M2)
- Set reminders / block time
- Detect conflicts when scheduling

**Professional persona for outbound:** "Hi, this is Horizon, Hayden's assistant. I'm calling to schedule…"

### M10 — Job Search (Phase 6, later)

**Capability:** monitor ZipRecruiter + Indeed for matching roles, alert on strong matches, draft tailored cover letters using conversation memory for context.

**Why "later":** depends on conversation memory being mature enough that drafts sound like the operator. Premature use risks generic/awkward letters.

**Schema:** `job_applications (id, source, role, company, salary_range, applied_at, status, follow_up_on, ...)`. Follow-up reminders fire via M4 alert channel.

---

## Per-node service deployment (planned 2026-05-11)

Each BHN node hosts a small set of self-hosted services beyond its core network role. All Docker, all VPN-only by default, all monitored by HORIZON.

### LA Hub
- **Wallos** — subscription tracker. Reads from PostgreSQL (new `subscriptions` table, or its own SQLite + a HORIZON sync workflow — TBD at build time) so HORIZON can surface BHN service-cost state in the morning briefing and flag upcoming renewals. Light footprint (~50 MB), fits inside LA's remaining 2 GB headroom. VPN-only access.

### Frankfurt — exit node + privacy node
Frankfurt's formal role broadens from pure WG exit to **exit + privacy routing**:
- **SearXNG** — self-hosted meta-search. Queries multiple search engines, strips tracking, returns aggregated results. VPN-only access from operator's devices.
- **Tor bridge/relay** — non-exit Tor node (bridge or middle relay, NOT exit — keeps legal exposure low). Formalizes Frankfurt as a privacy-routing layer; SearXNG can optionally route upstream via this Tor circuit for unlinkable search.
- **LibreSpeed** — EU-region speed-test endpoint. Per-node instance.

### NJ (trading node — currently blocked on tunnel issue)
- **LibreSpeed** — US-East speed-test endpoint. Per-node instance.
- **Tor bridge/relay (non-exit middle relay)** — adds capacity to the privacy stack; pairs with Frankfurt's relay via MyFamily so consensus never routes a circuit through both. Bandwidth halved vs Frankfurt (512 KB/s rate, 750 GB/month cap) to leave headroom for trading-API workloads on the same host. Deployable independently of the WG tunnel since ORPort 9001 is on NJ's public IP. **Trading-API coexistence note:** NJ's public IP will appear on Tor-relay scraper lists once consensus-published; trading workloads make their calls directly (not through Tor SocksPort), so flows are separate, but monitor Alpaca/Polymarket/Kalshi for unusual rate-limiting in the first 1-2 weeks after first start.

### All nodes (baseline)
- **LibreSpeed** is a baseline module: every node hosts an instance. Results land in PostgreSQL (new `speedtest_results` table) via a per-node cron probing both its own endpoint and peer endpoints. HORIZON consumes the table for latency-trend monitoring — surfaces in pulse cycles and morning briefing when a node's RTT degrades beyond a rolling-baseline threshold.

### Deployment shape

These services slot into `bhn-node-bootstrap.sh` v4's module/node-type structure:
- `modules/librespeed.sh` — baseline, sourced on every node-type
- `modules/wallos.sh` — sourced only on hub.sh
- `modules/searxng.sh` + `modules/tor-relay.sh` — sourced on `exit.sh` (Frankfurt's dual exit+privacy role) and on `trading.sh` (NJ, when the trading node-type lands — Tor relay only, not SearXNG)

**MyFamily — post-deploy bookkeeping:** once Frankfurt + NJ relays are both bootstrapped, the operator must update BOTH torrc files with the joint MyFamily fingerprint declaration so Tor consensus never builds a circuit passing through both. Documented in `infrastructure/services/tor-relay/README.md` under "MyFamily — REQUIRED post-deploy step".

Build order: LibreSpeed baseline first (gives latency telemetry on day one), then Wallos (closes the cost-monitoring gap before HORIZON's morning briefing comes online), then SearXNG + Tor on Frankfurt (privacy stack — biggest scope, lowest urgency).

---

## Phase 5: Resilience — Sweden cold standby (planned 2026-05-11)

A dark replica of LA hosted on **Bahnhof Sweden** (deliberately non-Vultr — different provider, different jurisdiction, survives Vultr-wide compromise). Receives encrypted replication continuously via Tor hidden service so Vultr's edge never sees LA↔Sweden correlation. Sits dark during normal operation. Single operator-triggered command failover via `bhn-failover-activate.sh` brings up WireGuard hub, PostgreSQL primary, n8n, HORIZON on Sweden in minutes.

Sweden also runs a Tor non-exit middle relay (`BHNSweden`, joins MyFamily with Frankfurt + NJ) — adds capacity to BHN's privacy stack from a Tor-friendly jurisdiction (strong free-expression law, no mandatory data retention, active Tor operator community).

**Why this is its own phase:** every prior phase increases functionality (more nodes, more services, more capability). Resilience is the first phase that addresses the failure modes the rest of the build has now accumulated — single-region (US) hub, single-provider (Vultr) for active compute, single point of failure (LA) for HORIZON + PG state. Sweden is the answer.

**Implementation phases:**
- 5.1 — Provision Bahnhof, bootstrap, Tor relay live, MyFamily declared with FRA + NJ
- 5.2 — Replication plumbing: LA's restic-via-Tor pushes to Sweden's hidden-service SFTP
- 5.3 — Initial PG + n8n sync; replica goes into hot_standby
- 5.4 — `bhn-failover-activate.sh` written + DRY-RUN tested
- 5.5 — Quarterly failover drills (fail to Sweden, verify, fail back to LA)
- 5.6 — Future: optional streaming PG replication; dual-endpoint WG client configs

**Cost addition:** ~$20/mo (Bahnhof VPS).

**Full design:** `infrastructure/docs/sweden-failover-architecture.md`. Threat model, replication flow, dark-mode component table, failover sequence, MyFamily-vs-isolation tradeoff, risks + open questions.

**Broader expansion menu:** `infrastructure/docs/bhn-node-candidates.md` — Iceland (1984, Flokinet), Switzerland (Infomaniak), Romania (M247, FlaxyHost), Sweden (Bahnhof, Njalla), Germany (Hetzner), Netherlands (Worldstream, NFOrce). No commitments beyond Sweden Phase 5; the list exists so Phase 6+ has a sourced shortlist when expansion need arises.

---

## Voice stack architecture

```
┌──────────────────┐     SMS / call     ┌──────────────────┐
│   Operator phone │◄──────────────────►│      Twilio      │
└──────────────────┘                     └────────┬─────────┘
                                                  │ HTTPS webhook
                                                  ▼
┌─────────────────────────────────────────────────────────────┐
│                       LA hub (10.8.0.1)                     │
│                                                             │
│  ┌────────────────┐   ┌──────────────────┐                  │
│  │      n8n       │──►│   ElevenLabs     │ (TTS — outbound) │
│  │  HORIZON       │   │     API          │                  │
│  │  workflow      │   └──────────────────┘                  │
│  └───────┬────────┘                                         │
│          │                                                  │
│          │  audio file → Twilio plays                       │
│          │                                                  │
│  ┌───────▼────────┐    ┌──────────────────┐                 │
│  │   Whisper      │◄───┤  Audio file      │ (STT — inbound) │
│  │   (tiny, CPU)  │    │  (transient)     │                 │
│  └───────┬────────┘    └──────────────────┘                 │
│          │                     │                            │
│          │ transcript          │ DELETED IMMEDIATELY        │
│          │                     │                            │
│          ▼                                                  │
│  ┌────────────────┐   ┌──────────────────┐                  │
│  │  PostgreSQL    │   │   pgvector       │                  │
│  │  (transcripts) │──►│   memories       │                  │
│  └────────────────┘   └──────────────────┘                  │
└─────────────────────────────────────────────────────────────┘
```

**Whisper model:** **`tiny`** (~75 MB). Selected because LA has 2 GB RAM and `base`/`small`/`medium` models would compete with PG, Grafana, n8n. Trade-off: lower transcription accuracy on noisy audio. Acceptable for Phase 1 (mostly clear quiet calls). Can upgrade to `base` if a scan-type node is added later with dedicated RAM.

**Why no FRA voice processing:** see "Jurisdictional posture" below.

---

## Recording posture

### Active policy (for the foreseeable future)

| Audio type | Retention |
|------------|-----------|
| All raw audio (any phase) | **Deleted immediately after transcription** |
| Transcripts | 90 days hot (NVMe) → 1 year cold (HDD) → purge |
| ElevenLabs TTS audio cache | Hot tier, 7-day retention (re-fetch if needed) |

The data model includes a forward-hook `recording_phase` enum (`'friend_family'`, `'business_test'`, `'production'`) for future flexibility, but the codepath today collapses to "delete immediately" regardless.

### Deferred policy (will activate when operator flips to business calls)

When operator signals business-call testing begins (notification expected per operator):
- Phase `business_test`: 48-hour QA hold for raw audio (review against transcripts to catch STT errors), then permanent delete.
- Phase `production`: revert to delete-immediately.

Implementation deferred until that signal. For now, no `eh-horizon-audio-prune` cron and no audio staging directory.

### Disclosure prefix

All outbound calls to non-friends-and-family: 3-second TTS prefix:

> "This call may be recorded for quality and assistance purposes."

Universally accepted across all 50 US states (including 10 two-party-consent states), EU + GDPR, Canada, UK. Stronger jurisdiction protection than server-shopping.

---

## Jurisdictional posture

| Layer | Triggered by | Mitigation |
|-------|--------------|-----------|
| **Server location** (§ 201 StGB Germany, 179bis StGB Switzerland, etc.) | Voice infra location | **All voice infra on LA (US)**. FRA never touches voice data. |
| **Operator location** (US federal Wiretap Act, applicable state laws) | Operator's residency | Server-shopping doesn't help. Federal one-party covers self-recording. |
| **Recorded-party location** (CA/FL/IL/MD/MA/MT/NV/NH/PA/WA two-party consent + non-US frameworks) | Recipient's residency | Server-shopping doesn't help. **Disclosure prefix** is the universal answer. |

**Net posture:** voice infra runs only on LA (US), with 3-sec disclosure prefix for any non-friends-and-family call. This is defensible in any plaintiff jurisdiction.

If/when EU-resident voice processing is needed (Phase 4+), separate node with proper GDPR/§ 201 design path. Not in current scope.

---

## Memory & data architecture

### Three memory lanes (all in PostgreSQL pgvector)

| Lane | Purpose | Lifecycle |
|------|---------|-----------|
| `conversation` | Every chat/call turn | Hot 90d → compress + cold forever |
| `security_event` | Anything triggering alerts | Hot 90d → cold forever (audit trail) |
| `market_data` | eBay + financial signals | Hot 90d → cold 1y → purge |

Existing `memories` table's `memory_type` enum already supports the abstractions; will add or remap as needed during M1/M5/M6 builds.

### Hot-memory layer (Redis) — Phase 5.5, designed 2026-05-13

**Problem:** pgvector retrieval is ~50 ms per turn and doesn't intrinsically know what "this conversation so far" is. n8n's session window helps but is opaque, lives inside the workflow, and isn't queryable. The result is HORIZON occasionally losing track of details mentioned 2-3 turns earlier in the same chat — a working-memory failure that pgvector can't fix because it's a long-term semantic store, not a turn buffer.

**Shape:** add Redis as a hot working-memory tier in front of pgvector. Redis holds the last N turns + running summary keyed by `session_id`; pgvector stays the long-term semantic store; PostgreSQL `conversation_sessions` is the durable system of record for session metadata.

```
turn arrives ─► n8n loads working memory (Redis GET, ~1 ms)
                  │
                  ├─► HORIZON synthesis (Sonnet) ◄── pgvector RAG for semantic recall only
                  │
                  ▼
              n8n writes turn to Redis (LPUSH + LTRIM to N) + summary upsert every K
                  │
                  ▼
            session close (TTL expiry or explicit end)
                  │
                  ▼
        summarize → embed → INSERT into memories (pgvector)
        + UPSERT conversation_sessions (closed_at, summary, turn_count)
        + DEL Redis keys
```

**Components:**

- **Redis service** — Docker container on LA, VPN-only on `10.8.0.1:6379`, `requirepass` from new Proton Pass entry `EH-Redis-Password`. AOF + RDB persistence **off** (Redis is a cache; durable state lives in PG + pgvector). Deploy doc lives at `infrastructure/services/redis/docker-run.md`.
- **PG schema** — new file `sql/redis-memory-schema.sql` containing one table: `conversation_sessions(id uuid PK, channel text, started_at timestamptz, closed_at timestamptz NULL, turn_count int, summary text, summary_embedding vector(384))`. One row per session, `closed_at` NULL while live; the summary embedding lets pgvector search across past sessions by topic. Granted to `n8n_user` write + `agent_reader` read.
- **Redis key shape:**
  - `horizon:sess:<session_id>:turns` — list, LPUSH new turn JSON, LTRIM to last 20
  - `horizon:sess:<session_id>:summary` — string, rewritten every K turns (K=5)
  - `horizon:sess:<session_id>:meta` — hash (channel, started_at, last_seen)
  - TTL on all three: 12 h sliding window, refreshed on every read/write
- **n8n wiring** — two new nodes added to the HORIZON workflow plus one new sub-workflow:
  - *Load working memory* (before Sonnet): GET turns + summary, inject into system prompt as `{{recent_turns}}` + `{{running_summary}}`.
  - *Write working memory* (after Sonnet response): LPUSH the new (user, assistant) turn pair, decide whether to re-summarize (every 5 turns) via a cheap Haiku call, bump TTL.
  - *Session-close sweep* (new sub-workflow, cron every 15 min): find Redis sessions whose `meta.last_seen` is older than 30 min → Sonnet summary → embedding → `memories` INSERT + `conversation_sessions` UPSERT (`closed_at = NOW()`) → Redis DEL keys.

**Why Phase 5.5 (between M9 and M10), not now:**

1. The existing HORIZON workflow has known disconnects (Format Memory Block, token logging) — fix those *before* layering new memory plumbing on top, or the new nodes inherit the same wiring fragility.
2. M10 Job Search wants tone-matched cover letters; that quality comes from long-term semantic recall (pgvector) maturing on accumulated transcripts, **not** from the Redis hot layer. Redis improves in-conversation coherence, which is a separate axis — useful, but not what unblocks M10.
3. New service on LA (Redis ≈ 30 MB) is cheap, but operationalizing it (no backup needed, monitoring via existing `bhn-docker-stats-collector`, password rotation, firewall) is worth a dedicated session rather than a side-quest during voice/trading rollout.

**Build order when activated:**

1. Deploy Redis Docker on LA per `docker-run.md`, password set, UFW restricted to `10.8.0.0/24` only.
2. Apply `sql/redis-memory-schema.sql` (only `conversation_sessions`).
3. Wire the two in-conversation nodes; test on the chat channel first (lowest blast radius — no voice cost).
4. Add the session-close sweep sub-workflow; verify pgvector inserts land cleanly.
5. Confirm Sonnet's effective context window stays bounded (Redis summary should keep it flat, not balloon it as turn count grows).
6. Once stable for one week, add `redis_hit_ratio` + `conversation_sessions` open-count to the morning briefing (M2) health block.

**Out of scope for this layer:** Redis-as-pubsub, Redis-as-job-queue, Redis-Streams. Single use case: short-term working memory for HORIZON. If those other use cases appear, justify and add separately; do not silently expand this service.

### Retention policy

| Data type | Hot (NVMe) | Cold (HDD) | Purge |
|-----------|------------|------------|-------|
| Conversations | 90 days | Forever (compressed) | Never |
| Security events | 90 days | Forever | Never |
| Market data | 90 days | 1 year | After 1y |
| Call transcripts | 90 days | 1 year | After 1y |
| **Raw call audio** | **Deleted immediately** | — | — |
| News/weather cache | 7 days | Not archived | After 7d |
| ElevenLabs TTS audio cache | 7 days | Not archived | After 7d |

### Query architecture (RAG-first cost cascade)

```
Operator query
    │
    ▼
1. PG keyword/exact lookup .................. (free, ~5ms)
    │ miss
    ▼
2. pgvector semantic similarity on memories .. (free, ~50ms)
    │ miss
    ▼
3. Cached Q&A pairs ......................... (free, ~5ms)
    │ miss
    ▼
4. Haiku 4.5 router classifies query ........ (~$0.50/mo for routing only)
    │ "needs LLM synthesis"
    ▼
5. Sonnet 4.6 with full context ............. (paid path — only genuinely-novel queries)

Background: Pulse nightly digest summarization (already running)
```

Net effect: most operator queries answered locally from PG/pgvector. Sonnet API spend bounded to genuinely-novel-synthesis cases. Haiku as classifier gate keeps Sonnet calls to ~5-10% of queries instead of 100%.

---

## Storage estimate

| Item | Per year |
|------|----------|
| Conversation memory | ~5 MB |
| Market data | ~100 MB |
| eBay scan data | ~50 MB |
| Call transcripts | ~200 KB |
| News/weather cache | ~10 MB |
| ElevenLabs audio cache | ~1 GB heavy use |
| Raw call recordings | 0 (deleted immediately) |
| **Total/year** | **~1.2 GB** |

Against 86 GB free on NVMe + 396 GB free on HDD: **2-3 year runway minimum.** `eh-purge` extension required to apply the new retention rules to the new tables — straightforward addition, not a redesign.

---

## Cost estimate (when fully built)

| Service | Cost |
|---------|------|
| ElevenLabs Creator | $22 |
| Twilio (number + ~10min/day calls + ~30 SMS/day) | ~$15-25 |
| OpenWeatherMap | Free |
| NewsAPI | Free |
| Google Calendar API | Free |
| eBay API | Free |
| Alpaca | Free |
| FMP | Free (MCP) |
| Whisper | Free (local LA) |
| Haiku 4.5 query router | ~$0.50/mo |
| **HORIZON monthly add** | **~$37-47/mo** |

Combined with existing infra (LA + FRA VPS + storage + backups + Claude subscription + API): **~$167-280/mo** at full HORIZON build.

---

## Operator action items (before Session 1)

1. ☐ Create Twilio account → register a US local phone number (recommended)
2. ☐ Create ElevenLabs account → upgrade to **Creator tier** ($22/mo, required for Professional Voice Clone of operator's voice). Pick a library voice for HORIZON-primary at setup.
3. ☐ Create `horizon@gmail.com` Google account → enable Calendar API → set up OAuth consent for n8n's Calendar credential
4. ☐ Sign up OpenWeatherMap → get free API key
5. ☐ Sign up NewsAPI → get free API key
6. ☐ Sign up Alpaca → get paper API key (live key behind STATUS.md gate)
7. ☐ Record 30+ seconds of clean audio sample (operator's voice) → upload to ElevenLabs PVC for cloning
8. ☐ Store all keys in Proton Pass under `EH-` naming convention (see secrets inventory in STATUS.md)

Once 1-7 done and keys are in PM, Session 1 can build M1 (Voice Pipeline) and start on M2 (Morning Briefing).

---

## Decisions log

| Decision | Value | Locked |
|----------|-------|--------|
| Voice infra location | LA only | ✅ |
| Whisper model | tiny (75 MB) | ✅ |
| HORIZON voice | Professional female (ElevenLabs library) | ✅ |
| Operator voice | Cloned via ElevenLabs PVC (Creator tier) | ✅ |
| Recording disclosure | 3-sec prefix on business calls | ✅ |
| Raw audio retention | Deleted immediately, all phases | ✅ (with deferred 48h hold for future business-test phase) |
| Confirmation timeout | Default deny, 10 min | ✅ |
| Alpaca | Paper first, live behind gate | ✅ |
| ElevenLabs tier | Creator ($22/mo) | ✅ |
| Robinhood | Excluded (unofficial API, TOS risk) | ✅ |
| Working-memory tier | Redis (Docker on LA, cache only — PG + pgvector remain source of truth) | ✅ Designed (Phase 5.5, build deferred) |
| Twilio number country | US local (assumed default) | Pending operator confirmation |
| Calendar OAuth scope | read+write+freebusy on horizon@'s primary | Pending operator confirmation |
| Caller-ID display name | "HORIZON" | Pending operator confirmation |

---

## Future considerations (not in current scope, captured for completeness)

- **Backup-via-Tor** *(planned feature — intent only, no implementation yet)* — route BHN backup pushes over a Tor circuit so the transport is unlinkable and the storage endpoint never sees a BHN origin IP. Phase 5.2 already specifies the concrete instance of this (LA's `restic`-via-Tor push to Sweden's hidden-service SFTP). This entry generalizes the intent: any backup destination should be reachable over Tor, using the **FRA Tor SOCKS proxy at `10.9.0.2:9050`** (already available — see `bhn-frankfurt-scoping.md`) for nodes that don't run their own onion routing. Out of scope until Phase 5 backup plumbing lands; captured here so backup design defaults to Tor transport rather than clearnet.
- **EU voice processing** — N/A under personal-only direction. Voice infrastructure stays on LA. (Any future public VPN product is a separate concern on different infrastructure under a different entity — out of scope here.)
- **Multi-region failover** — HORIZON depends on LA single-host today. Future: replication of `memories` + `call_transcripts` to a secondary node for DR.
- **Voice biometric authentication** — operator-voice match could gate sensitive actions (live trades, large eBay purchases). Not yet planned.
- **Other LLM providers** — current architecture is Sonnet 4.6-locked via n8n credential. Multi-provider fallback (Gemini, GPT-4) is possible but not designed.
