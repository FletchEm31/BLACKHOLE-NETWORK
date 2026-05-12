# BHN Node Candidates — Privacy-Friendly Hosting Outside Five Eyes

Reference list of hosting providers in jurisdictions outside the Five Eyes intelligence-sharing arrangement, for future BHN node expansion. **No decisions made on any of these yet** — this is the menu, not the order.

Sweden's Bahnhof is the first concrete commitment (see `sweden-failover-architecture.md`). The rest are catalogued here for future Phase 6+ expansion or contingency if any current provider becomes unsuitable.

---

## Iceland 🇮🇸

**Jurisdiction:** outside 5/9/14 Eyes. IMMI (Icelandic Modern Media Initiative) framework offers some of the strongest press/source protections globally. No mandatory data retention for non-telecom hosting. Geographically isolated — fewer fiber paths means more constrained subpoena reach.

| Provider | Site | Notes |
|----------|------|-------|
| **1984 Hosting** | 1984.hosting | Named after Orwell. IMMI-aligned by design. VPS + dedicated. Crypto payment accepted. Long-standing reputation in privacy community. |
| **Flokinet** | flokinet.is | Hosts journalists, activists, anti-surveillance projects. Multi-jurisdiction (Iceland, Romania, Finland). Strong stance on resisting takedown requests. |

**Best fit for:** cold storage / data sovereignty roles where geographic isolation is the primary value. Latency to operator (PT) is ~140-160ms.

---

## Switzerland 🇨🇭

**Jurisdiction:** outside 5/9/14 Eyes (though some intel cooperation exists informally). Swiss Federal Act on Data Protection (FADP) provides stronger personal-data protections than GDPR in some respects. Long-standing neutrality and judicial independence. Bank-secrecy-tradition culture extends loosely to data-secrecy expectations.

| Provider | Site | Notes |
|----------|------|-------|
| **Infomaniak** | infomaniak.com | Major Swiss host, reputable. ISO 27001. Renewable energy. Good balance of professional reliability + privacy posture. |
| **ProtonMail infrastructure** (Proton Hosting if available, otherwise reference for Proton-aligned providers) | proton.me | Proton operates their own infrastructure for Mail/VPN/Drive in Switzerland; doesn't offer general VPS hosting publicly, but their data-center partners are useful references. |

**Best fit for:** general-purpose nodes where regulatory stability is more important than maximum-privacy posture. Latency to PT: ~150-170ms.

---

## Romania 🇷🇴

**Jurisdiction:** EU member but historically resists overbroad data-retention rules (struck down their first data-retention law as unconstitutional). Major fiber crossroads for SE Europe. Low cost-per-bandwidth.

| Provider | Site | Notes |
|----------|------|-------|
| **M247** | m247.com | Major European carrier, multi-region presence. High bandwidth, professional ops. Less "privacy brand" than 1984/Flokinet but solid jurisdictionally. |
| **FlaxyHost** | flaxyhost.com | Privacy-focused small host. Strong stance on resisting takedown attempts. |
| **SpeedyPage** | speedypage.com | Budget-friendly. Limited public reputation in privacy community — diligence required. |

**Best fit for:** high-bandwidth roles (exit relays, mirror nodes), budget-conscious deployments. Latency to PT: ~180-200ms.

---

## Sweden 🇸🇪

**Jurisdiction:** outside 5/9/14 Eyes. Strong constitutional free-expression protections (Freedom of the Press Act). No mandatory data retention for non-telecom hosting. Active Tor operator community, openly tolerated by authorities.

| Provider | Site | Notes |
|----------|------|-------|
| **Bahnhof** | bahnhof.se | **BHN's chosen provider for Phase 5 Sweden deployment.** Operates the famous Pionen data center (nuclear bunker conversion). Hosted WikiLeaks historically. Very public anti-surveillance stance. |
| **Njalla** | njal.la | Co-founded by Peter Sunde (Pirate Bay). Domain registration + hosting. Smaller scale than Bahnhof but stronger ideological alignment. |

**Best fit for:** primary privacy + resilience deployment (BHN's current Phase 5 use case). Latency to PT: ~150-180ms.

---

## Germany 🇩🇪

**Jurisdiction:** EU member, mixed surveillance posture. Strong consumer-privacy law (BDSG, GDPR enforcement leader). German constitutional court has struck down overreaching surveillance laws. **However:** §201 StGB criminalizes recording private speech — relevant only if the node touches voice/audio data (see HORIZON's jurisdictional posture doc; Frankfurt voice is on LA precisely because of §201).

| Provider | Site | Notes |
|----------|------|-------|
| **Hetzner** | hetzner.com | Major German host. Reputable. No record of compliance with overseas NSLs (German law largely doesn't honor them). Strong price/performance. Frankfurt or Falkenstein DC. |

**Already active in BHN:** Frankfurt (Vultr Frankfurt). Hetzner would be a Vultr-replacement candidate if Vultr Frankfurt becomes unsuitable.

**Best fit for:** EU exit nodes, general-purpose nodes where strong consumer-privacy law is the value, voice infrastructure is OUT of scope. Latency to PT: ~150-170ms.

---

## Netherlands 🇳🇱

**Jurisdiction:** EU member. Major fiber and bandwidth hub of Europe (AMS-IX). Mixed surveillance posture — Netherlands historically has been somewhat cooperative with foreign requests, but courts increasingly limit overreach. Strong tech-sector privacy culture.

| Provider | Site | Notes |
|----------|------|-------|
| **Worldstream** | worldstream.nl | Tier-1 carrier presence, high bandwidth. Less "privacy brand" but reliable Dutch operator. |
| **NFOrce** | nforce.com | Specifically known in privacy/Tor community for permissive policies on Tor relays + similar use cases. Strong DDoS protection. |

**Best fit for:** high-bandwidth nodes, exit-relay candidates (NFOrce particularly Tor-friendly). Latency to PT: ~160-180ms.

---

## Comparison matrix

| Factor | Iceland | Switzerland | Romania | Sweden | Germany | Netherlands |
|--------|---------|-------------|---------|--------|---------|-------------|
| Outside 5/9/14 Eyes | ✅ | ✅ | ⚠️ EU | ✅ | ⚠️ EU | ⚠️ EU |
| Strong free-expression law | ✅ IMMI | ✅ | ⚠️ mixed | ✅ | ✅ | ⚠️ mixed |
| Tor-friendly jurisdiction | ✅ | ✅ | ✅ | ✅✅ | ✅ | ✅✅ |
| Bandwidth headroom | ⚠️ limited fiber | ✅ | ✅ excellent | ✅ | ✅ excellent | ✅✅ |
| Latency to PT | ~140-160ms | ~150-170ms | ~180-200ms | ~150-180ms | ~150-170ms | ~160-180ms |
| Cost (VPS, ~2 vCPU/4 GB) | ⚠️ premium | ⚠️ premium | ✅ cheap | mid | ✅ cheap | mid |
| Already in BHN | — | — | — | (planned 5.1) | (Frankfurt, Vultr) | — |

---

## How to use this list

When BHN's expansion needs a new node for a specific role:

1. Identify the role (exit relay / cold standby / data sovereignty / regional latency optimization / etc.)
2. Match against the "best fit for" lines above
3. Read the linked provider's current ToS for the specific use case (especially Tor exit, file-sharing, etc.)
4. Confirm payment method aligns with operator's jurisdictional isolation goals (crypto preferred for max isolation; standard payment fine for general hosting)
5. Update `project_node_expansion_plans` memory with the new commitment
6. Provision + bootstrap per the standard `bhn-node-bootstrap.sh` v4 process

---

## What this list deliberately excludes

- **Five Eyes hosting** (US, UK, Canada, Australia, NZ) — already represented by LA + NJ for compute-locality reasons; not appropriate for privacy-role nodes
- **9 Eyes additions** (Denmark, France, Norway) — France has a more aggressive surveillance posture; Denmark and Norway have intel-sharing cooperation that defeats some of the isolation goals
- **14 Eyes additions** (Belgium, Germany via SIGINT-Allies, Italy, Spain, Sweden — Sweden's status here is debated) — included Germany + Sweden anyway because their judicial pushback record is strong despite the listing; Belgium/Italy/Spain not catalogued because they offer no compelling alternative to options listed
- **Unrated/uncommon jurisdictions** (Russia, China, etc.) — outside operator's threat model
