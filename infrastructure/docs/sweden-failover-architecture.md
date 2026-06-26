# Sweden — Cold Standby Hub + Dark Replication Architecture

Phase 5 of the BHN build. Sweden node is a **dark replica** of the LA hub: receives encrypted replication continuously, runs no public services during normal operation, becomes the active hub on a single-command failover. Provides resilience against LA loss (hardware, account, jurisdiction pressure) and serves as a Tor relay in a privacy-friendly jurisdiction.

**Status:** design committed 2026-05-11; build pending.

---

## 1. Overview & threat model

### What this defends against

| Threat | Today (no Sweden) | With Sweden cold standby |
|--------|-------------------|--------------------------|
| LA hardware failure (drive, host, network) | Total BHN outage until LA is repaired or rebuilt from backups | Failover in minutes; minimal data loss (≤1h with default replication cadence) |
| Vultr account compromise / suspension | All Vultr-hosted nodes (LA + FRA + NJ) potentially lost simultaneously | Sweden is on a different provider in a different jurisdiction; survives a Vultr-wide event |
| US legal pressure on LA (subpoena, NSL) | Operator's only PG + n8n state is on US soil | Live replica in Sweden under different jurisdiction; failover lifts compute out of US legal reach |
| Datacenter physical compromise | LA contents at risk | Sweden replica is geographically separated, jurisdictionally separated |
| Operational mistake (rm -rf, bad migration) | Recovery from restic backup (already exists); minutes to hours | Faster recovery: promote replica → cut over in minutes |

### Why Sweden

- **Strong free-expression law** (constitution + press freedom act)
- **No mandatory data-retention regime** for non-telecom hosting
- **Tor-friendly jurisdiction** — middle relays + exits operated openly without legal interference
- **Outside Five Eyes** and outside the 9/14 Eyes intel-sharing arrangements
- **Operator-aligned hosts available** (Bahnhof — see §7)

### Why a separate provider (not Vultr Stockholm)

Operator chose **Bahnhof** specifically (not Vultr's Stockholm region). Reasoning:
- Vultr account compromise wipes all Vultr-hosted nodes simultaneously — Sweden on a separate provider survives that scenario
- Different legal entity = different subpoena/NSL surface
- Bahnhof's track record on resisting external legal pressure (famously hosted WikiLeaks; operates Pionen data center)

---

## 2. Components on Sweden node

What runs at all times in dark mode:

| Component | State | Bind | Notes |
|-----------|-------|------|-------|
| **PostgreSQL** | `hot_standby` (read-only replica) | `127.0.0.1` + unix socket only | Receives periodic encrypted snapshots from LA; never reachable from anywhere except localhost |
| **n8n** | Container exists, **stopped** | n/a | Workflow files + sqlite DB mirrored on disk; daemon starts only on failover |
| **Restic repo** | Encrypted backup target | Reachable only via Tor hidden service (see §3) | This is what LA pushes to |
| **Tor daemon** | Running | ORPort `9001` public; `HiddenServiceDir` for inbound SFTP | Two roles: middle relay (consensus participant) + hidden service for incoming replication |
| **SFTP server** | Running | `127.0.0.1:8022` only | Behind Tor hidden service; never bound to public interface |
| **WireGuard** | Configured but **not active** | n/a | Hub config sealed on disk; `bhn-failover-activate.sh` brings it up |
| **Public ports** | None except Tor 9001/tcp | — | No HTTP, no SSH on the public IP for BHN-related services (operator's admin SSH is via Tor hidden service too — see §4) |

What does NOT run in dark mode:
- HORIZON (n8n down)
- Grafana
- Shadowsocks
- dnscrypt-proxy
- Any public-facing service except the Tor middle relay

---

## 3. Replication architecture — periodic snapshot over Tor hidden service

### Approach: encrypted restic snapshot, hourly, over .onion

LA pushes restic snapshots to Sweden every hour via a Tor hidden service. Restic snapshots are deduplicated + encrypted at rest; the hidden service hides the LA↔Sweden correlation from Vultr's edge.

### Why not PostgreSQL streaming replication (over .onion)

Streaming PG replication over Tor is feasible for the personal-use write volume but:
- Tor circuit latency (100-500 ms) becomes replica lag on every write
- Tor circuit drops mid-write are common; PG reconnects but logs noisily
- Restic snapshots are deduplicated + compressed; PG WAL streaming isn't
- Snapshot approach is **simpler to monitor** (one cron job per hour; success/failure is binary)

Streaming replication can be added as a Phase 5.6 upgrade if hourly lag turns out to be too coarse. For a cold standby, hourly is generally sufficient.

### Replication flow

```
LA (Vultr LA)                           Sweden (Bahnhof)
─────────────                           ────────────────
PG dump (Fc)                            tor (HiddenServiceDir)
   │                                       │
   ▼                                       ▼ (publishes .onion address)
restic push                            SFTP daemon
   │   sftp://user@<onion-addr>:22         │ (bound 127.0.0.1:8022 only)
   │                                       │
   ▼                                       ▼
Tor SOCKS proxy ──── Tor network ─────► restic repo
(127.0.0.1:9050)                        (encrypted at rest)
                                            │
                                            ▼
                                        PG hot_standby
                                        (consumes latest dump
                                         on cron tick)
```

### LA side

```bash
# /etc/cron.d/bhn-sweden-replicate
# Hourly: dump PG, push via restic through Tor to Sweden's hidden-service SFTP
0 * * * * root /usr/local/sbin/bhn-sweden-replicate.sh
```

`bhn-sweden-replicate.sh` (lives in repo at `scripts/bhn-sweden-replicate.sh` — Phase 5.2 task):
1. `pg_dump -Fc eventhorizon` → temp file
2. Snapshot `/root/.n8n` (sqlite + config) → temp file
3. `restic backup --repo sftp:bhn-sweden.onion:repo` (through Tor SOCKS)
4. `restic forget --keep-hourly 24 --keep-daily 7 --keep-weekly 4 --prune`
5. Log success/failure to `node_logs` so HORIZON's pulse can flag stalls

### Sweden side

Sweden runs a small SFTP-only user account (`backup`) restricted to a chroot containing the restic repo. PG and n8n are pulled OUT of the restic repo by a separate cron on Sweden (every 30min, or trigger after each successful push from LA — phase 5.3 detail).

### Tor hidden service config on Sweden

```
# /etc/tor/torrc additions on Sweden (managed via the existing tor-relay-sweden compose)
HiddenServiceDir /var/lib/tor/bhn-replication/
HiddenServicePort 22 127.0.0.1:8022
HiddenServiceVersion 3
```

The `.onion` address (50+ characters, v3) is generated automatically on first Tor start. It lives in `/var/lib/tor/bhn-replication/hostname`. **Treat the .onion address as sensitive** — it's not a public secret in the cryptographic sense (anyone who scans the Tor network could find services pointing at it), but BHN doesn't want it indexed against LA. Store the actual address in a sealed file on LA (`/root/.bhn-sweden-replication.onion`, mode 0600).

### LA side: Tor client + SSH config for the .onion endpoint

LA needs:
- Tor daemon running (already does via the planned Frankfurt relay's pattern — but Sweden replication needs its own Tor daemon on LA, separate from the consensus relay; can share)
- SSH config entry routing through Tor:

```
# /root/.ssh/config on LA
Host bhn-sweden
    Hostname <onion-address-stored-in-sealed-file>
    User backup
    Port 22                    # remote SSH listens on 22 inside the hidden service
    ProxyCommand /bin/nc -X 5 -x 127.0.0.1:9050 %h %p
    IdentityFile /root/.ssh/bhn-sweden-replication
```

---

## 4. Dark operation mode

Sweden during normal LA operation:

- **Inbound public**: Tor ORPort 9001/tcp only. Every other port is closed at UFW.
- **Inbound hidden-service**: SFTP for replication (only LA's restic client can reach it; the .onion address is not published)
- **Outbound**: Tor consensus traffic; apt mirrors for system updates; outbound Tor SOCKS for fetching restic if reverse-pull mode is enabled
- **DNS**: no A/AAAA record points to Sweden's public IP for any BHN identifier. Sweden's hosting record (Bahnhof's PTR for the IP) is the only public attribution.
- **Operator admin access**: via Tor hidden service for SSH (separate .onion from the replication one). The admin .onion address goes in operator's password manager only. Vultr-style web-console access via Bahnhof's panel is the fallback.

What an external observer sees:
- Bahnhof customer renting a Stockholm VPS; running a Tor relay called `BHNSweden`
- No correlation to BHN's LA / Frankfurt / NJ infrastructure unless they correlate Tor relay operator emails (`admin@eventhorizonvpn.com` is the same across relays — **this is the strongest correlation signal**; consider whether Sweden's relay should use a different ContactInfo email if the goal is full isolation)

**Open question to revisit at Phase 5.1:** does Sweden's Tor relay declare MyFamily with Frankfurt + NJ? Doing so explicitly couples the three relays as same-operator. NOT doing so means consensus might route a circuit through two BHN relays. The privacy benefit of MyFamily and the privacy benefit of separation are in tension here. Default in initial deploy: **MyFamily declared** (avoid the circuit issue); revisit if operator wants Sweden's relay to look unrelated.

---

## 5. Failover sequence — `bhn-failover-activate.sh`

Script lives in repo at `scripts/bhn-failover-activate.sh`. Trigger: **operator runs it manually** on Sweden via Tor SSH (or Bahnhof console). No automated probe-based failover — too risky for false positives during network blips.

### Pre-flight (script does, refuses to proceed if any check fails)

1. Confirm LA unreachable from 3+ probe paths (Tor, direct, third-party ping service via Tor)
2. Verify Sweden's PG replica is consistent: `pg_isready` + last successful pull is within 2h
3. Verify n8n files are mirrored and the most recent push is within 2h
4. Verify WG hub config seal file exists and parses
5. Operator types `FAILOVER ACTIVATE` to confirm (no auto-confirm flag — this is one-way during a session)

### Steps

```bash
1. PG: pg_ctl promote                              # replica → primary
   verify writable: psql -c "INSERT INTO ..."

2. n8n: docker start bhn-n8n                       # config already in place
   wait for healthz

3. WireGuard: wg-quick up wg0                      # hub config (peers expect Sweden's public IP)
   verify wg show

4. DNS update (operator-defined hook):
   - Calls a pre-configured DNS API (Vultr DNS, Cloudflare, etc.)
   - Updates `hub.<bhn-domain>` to point at Sweden's public IP
   - API token in /root/.bhn-failover-dns.env (sealed)
   - Operator-defined hook script; failover-activate.sh just invokes it

5. Public endpoints (optional, per node-type pattern):
   - Shadowsocks
   - dnscrypt-proxy
   - n8n VPN-only access on <BHN_WG_LA_IP> (Sweden takes over the same WG subnet)

6. Notify operator:
   - ntfy push (works without LA if Sweden has internet)
   - SMS via Twilio (works without LA if Twilio API key + auth survive on Sweden — they do, mirrored via n8n config)
   - Email via Proton SMTP

7. Mark failover state:
   - touch /var/lib/bhn/failover-active-since
   - Logs the failover timestamp + operator confirmation to a new PG table `failover_events`
```

### After failover

- LA, if still alive, becomes a stale data source. Operator's discretion to:
  - Power off LA and rebuild from Sweden when ready
  - Promote LA back to primary via reverse-replication (Phase 5.5 task)

- WG clients: their `.conf` files have a hard-coded `Endpoint = la.hub.bhn:51820`. After failover, the operator-updated DNS now resolves that name to Sweden. Clients reconnect on PersistentKeepalive.
  - **Alternative for faster client recovery**: dual-endpoint client configs (some WG clients support a fallback endpoint). Out of scope for initial Sweden deployment; revisit if failover drills show client recovery is too slow.

### One-way during a session

Once Sweden is promoted, LA is NOT automatically demoted to standby on its next boot. Operator must explicitly run a reverse-failover procedure (Phase 5.5) to bring LA back as the active hub or as Sweden's new standby. Prevents split-brain (both hubs writing to PG with no replication between them).

---

## 6. Sweden Tor relay — middle relay, MyFamily with Frankfurt + NJ

Sweden runs the same non-exit middle relay pattern as Frankfurt + NJ. Specifics:

| Setting | Value | Rationale |
|---------|-------|-----------|
| Nickname | `BHNSweden` | consistent naming |
| ContactInfo | `admin@eventhorizonvpn.com` | (consider differentiating per §4 dark-mode discussion) |
| ExitRelay | 0 | non-exit, consistent with FRA + NJ |
| RelayBandwidthRate | 1 MB/s | Sweden Bahnhof bandwidth allowances are generous; match Frankfurt |
| AccountingMax | 1500 GB/mo | match Frankfurt |
| MyFamily | `$FRA_FP,$NJ_FP,$SE_FP` | declare all three relays as same family — see §4 discussion of trade-off |

Exit relay is **not** chosen for initial deploy because:
- Even in Sweden, Vultr-equivalent providers' ToS often forbid exit relays (need to confirm Bahnhof's stance)
- Abuse-complaint handling is a real operational load
- Inconsistent with the rest of the BHN relay fleet

If operator wants to upgrade Sweden to exit relay in a future session, it's a torrc edit + Bahnhof ToS check + abuse-handling email setup.

---

## 7. Hosting — Bahnhof

**Provider chosen:** Bahnhof (Sweden, Stockholm)

Why Bahnhof:
- Operates Pionen data center (decommissioned nuclear bunker, strong physical security narrative)
- Historical track record: hosted WikiLeaks, refuses to comply with most foreign legal requests, very public anti-surveillance stance
- Their stated principles align with BHN's design intent
- Stockholm latency to operator's PT location: ~150-180ms (acceptable for cold standby; not for active hub)

What to configure at Bahnhof account-creation time:
- VPS plan: ~$15-25/mo for a comparable spec to LA (2 vCPU, 4+ GB RAM, 50+ GB SSD)
- Payment method: **avoid linking to operator's primary identity** if jurisdictional isolation is a goal. Crypto payment (BTC/Monero) preferred; failing that, a dedicated card.
- Email used for the account: separate from BHN's main email if isolation matters.
- ToS: read the Acceptable Use Policy specifically for Tor relay operation. Non-exit relays generally permitted; confirm before deploy.

Cost addition to monthly burn: **~$20/mo** (Bahnhof VPS) + transit (likely included in the VPS plan).

### Why not Vultr Stockholm

Vultr Stockholm exists and would be the path of least resistance, but it defeats the threat model: a Vultr account compromise (or a Vultr-wide legal pressure event) wipes LA + FRA + NJ simultaneously and Sweden too if it's on Vultr. Bahnhof = different legal entity, different infrastructure, different account → survives that scenario.

### Alternative providers if Bahnhof doesn't work out

See `bhn-node-candidates.md` for the full candidate list. Top alternatives:
- **Njalla** (Sweden) — co-founded by Peter Sunde; similar privacy stance, smaller scale
- **1984 Hosting** (Iceland) — named after Orwell, IMMI framework
- **Flokinet** (Iceland/Romania/Finland) — hosts activists/journalists, anti-surveillance positioning

---

## 8. Implementation phases

| Phase | Scope | Estimated effort |
|-------|-------|------------------|
| **5.1 — Provision** | Bahnhof account, VPS up, standard BHN bootstrap, install Tor middle relay (no replication yet), join MyFamily with FRA+NJ | 2-3h |
| **5.2 — Replication plumbing** | LA writes to Sweden's hidden-service SFTP. Tor on both sides. `bhn-sweden-replicate.sh` script committed + cron'd. | 3-4h |
| **5.3 — Initial sync** | First full PG dump + n8n config pushed. Sweden's PG goes into hot_standby. Verify replica consistency. | 1-2h |
| **5.4 — Failover script** | Write `bhn-failover-activate.sh` with all preflight checks. **Test in DRY-RUN mode first** (every step prints what it would do without actually doing it). | 4-6h |
| **5.5 — Failover drill** | Actually fail over to Sweden. Verify everything works: PG writeable, n8n responding, WG clients reconnect, HORIZON's chat-trigger works on the new hub. Then fail BACK to LA. Schedule quarterly. | 2-3h per drill |
| **5.6 (later)** | Optional: PG streaming replication upgrade if hourly lag proves too coarse. Optional: dual-endpoint WG client configs for faster failover. | TBD |

**Prerequisite for Phase 5.1:** operator opens Bahnhof account, confirms ToS on Tor relays, completes initial payment.

**Prerequisite for Phase 5.4:** Phase 5.3 must be verified — failover script depends on the replica being known-good.

---

## 9. Risks & open questions

| Risk | Mitigation |
|------|-----------|
| `.onion` address leaks (in restic config, in git history, in operator's password manager) | Sealed files on each node (mode 0600); never commit to repo; document that the .onion is a soft-secret |
| Sweden's Tor middle relay → BHN identity correlation (via ContactInfo email) | See §4 — option to use a different ContactInfo email for Sweden's relay if isolation matters more than operator-contact transparency |
| Bahnhof account compromise itself (different threat model than Vultr) | Strong 2FA, separate email from BHN's main identity, payment isolation if feasible |
| Replication staleness (LA had writes Sweden hasn't received when LA goes down) | Maximum 1h data loss with hourly cadence; document as acceptable risk for a personal-use system; revisit if write volume grows |
| Failover split-brain (both hubs accept writes) | One-way failover during a session; explicit reverse-failover procedure to fail back |
| WG client recovery time after failover | Default: minutes (PersistentKeepalive). Optional: dual-endpoint client configs as Phase 5.6 |
| MyFamily declaration links Sweden to BHN identity | Documented as the trade-off in §4; default is "declare for circuit safety", revisit if isolation matters more |
| HORIZON outage during failover | Acceptable — HORIZON is a convenience layer, not a critical path. Operator can run failover via plain Bahnhof console SSH without HORIZON's help. |

---

## 10. Related docs + memories

- `bhn-node-candidates.md` — broader list of privacy-friendly hosting candidates for future BHN expansion
- `horizon-roadmap.md` — Phase 5: Resilience section references this doc
- `project_node_expansion_plans` memory — Sweden's role specified
- `project_blackhole_network_rename` memory — BHN vs EventHorizon VPN naming separation (relevant to what gets surfaced publicly on Sweden's Tor relay descriptor)
- `infrastructure/services/tor-relay/README.md` — MyFamily setup process (extends to Sweden's relay when it joins)
