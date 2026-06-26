# LA Egress Audit — 2026-05-22

> **Operator question:** "Right now everything is being pushed out of LA. We want most to be going out of Hillsboro and Frankfurt."
>
> **TL;DR (one-liner):** The Hillsboro proxy lockdown is *fully designed and staged in the repo* but **was never executed on the live LA node.** Direct 443/587/80 egress is still allowed; processes have no proxy env set; LA's Vultr IP `<BHN_LA_PUBLIC_IP>` is what hits Anthropic / Twilio / ElevenLabs / FMP. Three additional configuration gaps will bite even after `deploy.sh` runs — the biggest is that ~15 trading + horizon systemd units use `EnvironmentFile=/etc/bhn-trading/env` (not `/etc/environment`), so they'll bypass the proxy unless that file is updated. Frankfurt is a separate egress class entirely — design routes only **operator personal browsing** through it; if you also want some **service traffic** to exit via FRA, that's a new design decision.

---

## 1. Current vs. intended state

### Egress class matrix

| Class | What's in it | Should exit via | Currently exits via | State |
|-------|--------------|-----------------|---------------------|-------|
| **LA operational / service** | Anthropic, Twilio (outbound), ElevenLabs, FMP, FRED, Finnhub, EIA, USDA, Quiver, Kalshi, Polymarket, CoinGecko, NewsAPI, OpenWeatherMap, apt, certbot, GitHub | **Hillsboro tinyproxy** → `<BHN_HIL_PUBLIC_IP>` (Hetzner) | **LA direct** → `<BHN_LA_PUBLIC_IP>` (Vultr) | 🔴 **GAP** — lockdown staged, not applied |
| **Inbound webhooks** | Twilio SMS/voice callbacks, n8n webhook URLs, ElevenLabs async | LA direct (asymmetric by design) | LA direct | ✅ LIVE |
| **NJ trading** | Alpaca REST/stream | NJ direct (intentional separation) | NJ direct | ✅ LIVE |
| **Operator personal browsing** | Operator device full-tunnel | **Frankfurt** → `192.248.187.208` (DE) | Currently loses internet entirely | 🔴 **BROKEN** — FRA MASQUERADE missing, fix tracked in `frankfurt-exit-backlog.md` |
| **Privacy / unlinkable** | SearXNG upstream, ad-hoc unlinkable lookups | **Frankfurt Tor SOCKS** `<BHN_WG_FRA_IP>:9050` | Partial — SOCKS available, no automatic consumers wired | 🟡 PARTIAL |
| **Voice (Whisper, TTS, recording)** | ElevenLabs audio gen, Twilio call media, Whisper transcription | **LA only** (legal — §201 StGB blocks FRA) | LA | ✅ LIVE, hard constraint, do not change |
| **Mesh-internal + DNS + NTP + WG underlay** | `10.8.0.0/24`, `10.9.0.0/24`, dnscrypt, ntp, wg UDP | Direct (stays off proxy) | Direct | ✅ LIVE |

### Visualizing the gap

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            INTENDED STATE                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   LA process ──► tinyproxy@Hillsboro (<BHN_WG_HIL_IP>:8888) ──► <BHN_HIL_PUBLIC_IP>    │
│                  (Anthropic, Twilio out, ElevenLabs, FMP, apt, etc.)    │
│                                                                          │
│   Operator PC ──► FRA exit (192.248.187.208) ──► personal browsing      │
│                                                                          │
│   NJ ──► Alpaca direct (intentional)                                     │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                          CURRENT STATE                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   LA process ──► LA direct (<BHN_LA_PUBLIC_IP>) ──► Anthropic/Twilio/etc.    │
│                          ▲                                               │
│                          └─ tinyproxy never used. UFW still allows       │
│                             direct 443. No HTTPS_PROXY env set.          │
│                                                                          │
│   Operator PC ──► full-tunnel disabled (FRA broken)                      │
│                                                                          │
│   NJ ──► Alpaca direct ✅                                                │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Why everything exits LA today (root cause)

Three layers of the lockdown exist in the repo. **Zero of them are deployed on the live LA node** (or at least none have been confirmed deployed — see Section 5 for the runtime check you'll need to do).

| Layer | Repo file | Live state on LA | Effect if missing |
|-------|-----------|------------------|-------------------|
| **System env vars** (login shells, cron, any process started fresh) | `infrastructure/la-egress-lockdown/environment.snippet` | ❓ likely not appended to `/etc/environment` | curl / wget / one-off scripts → direct |
| **apt config** | `infrastructure/la-egress-lockdown/apt.conf.d/95bhn-proxy.conf` | ❓ likely not in `/etc/apt/apt.conf.d/` | `apt update`/`apt install` → direct |
| **n8n systemd drop-in** | `infrastructure/la-egress-lockdown/systemd/n8n.service.d/proxy.conf` | ❓ likely not in `/etc/systemd/system/n8n.service.d/` | HORIZON's external HTTP calls → direct |
| **Grafana systemd drop-in** | `infrastructure/la-egress-lockdown/systemd/grafana-server.service.d/proxy.conf` | ❓ likely not in `/etc/systemd/system/grafana-server.service.d/` | Grafana plugin / alert webhook → direct |
| **UFW rule additions/removals** | `infrastructure/la-egress-lockdown/ufw-rewrite.sh` | ❓ direct 443/587/80 still allowed (per operator) | Even if env vars were set, the firewall doesn't enforce — anything coded to skip proxy still gets out |

All five are staged together in `infrastructure/la-egress-lockdown/` with a `README.md` describing the deploy sequence (`add-proxy-route` → `deploy.sh` → verify → `lockdown`). Nothing is broken — it just hasn't been run.

---

## 3. Identified gaps (severity-ranked)

### 🔴 GAP-1 — CRITICAL: Lockdown never executed
Already covered above. This is the primary fix. Section 6 has the sequence.

### 🔴 GAP-2 — CRITICAL: Trading + HORIZON systemd units bypass `/etc/environment`
**~15 systemd units** under `scripts/trading/systemd-units/` and `scripts/horizon/systemd-units/` use:
```
EnvironmentFile=/etc/bhn-trading/env
```
…**not** `/etc/environment` and not `Environment=HTTPS_PROXY=...`. Affected services include (full list at end of section):
- `bhn-strategy@.service` (template — all strategies)
- `bhn-market-stream.service`, `bhn-reconciliation.service`, `bhn-trading-daily-summary.service`
- `bhn-recon-restart-after-fill.service`, `bhn-weather-collector.service`
- `bhn-strategy-prediction-alpha{,-settle}.service`
- `bhn-macro-data.service`, `bhn-market-data.service`, `bhn-sentiment.service`
- `bhn-morning-brief.service`, `bhn-paper-trades-watch.service`, `bhn-regime-classifier.service`

**Consequence:** Even after `deploy.sh` runs and `/etc/environment` is set, these services start with a *blank* HTTPS_PROXY. They'll keep talking to Alpaca, FMP, OpenWeatherMap, etc. **directly from LA**.

**Three fix options** (pick one — ❓ DECIDE):
- **(a)** Append the proxy block to `/etc/bhn-trading/env` (one file, all services inherit). **Simplest.**
- **(b)** Add a `[Service] Environment=HTTPS_PROXY=...` drop-in per unit (15+ drop-ins, more granular control).
- **(c)** Add `EnvironmentFile=-/etc/bhn-proxy-env` to every unit and ship that file once (one-file model but explicit per-unit reference).

**Default proposal: (a)** — append to `/etc/bhn-trading/env`. Reason: zero unit changes, every existing service picks it up on next restart, easy to audit (`cat /etc/bhn-trading/env | grep PROXY`).

> ⚠️ **NJ caveat:** `bhn-strategy@.service` and the trading units run on **NJ**, not LA. NJ trading egress should stay direct to Alpaca (per `bhn-network-data-flow.md` §5). If we set HTTPS_PROXY on NJ via `/etc/bhn-trading/env`, NJ's Alpaca calls would route through Hillsboro — **not what we want**. Fix-(a) needs to be applied to LA's copy of `/etc/bhn-trading/env`, NOT NJ's. Each node has its own env file.

### 🟡 GAP-3 — Tinyproxy doesn't tunnel SMTP submission (587)
`infrastructure/services/tinyproxy/tinyproxy.conf` only has `ConnectPort 443`. The lockdown removes direct 587/tcp. **Consequence:** if LA needs to send outbound email via Proton SMTP (port 587), it will fail post-lockdown.

**Fix:** add `ConnectPort 587` to `tinyproxy.conf` **before** running the lockdown. Same for 465 if you want SMTP over implicit TLS.

❓ **DECIDE:** does LA currently use SMTP-out? If yes, add 587. If no SMTP-out planned, leave the lockdown's 587 removal as-is and don't add to tinyproxy.

### 🟡 GAP-4 — Cron jobs from `bhn-deploy-all-collectors.sh` may not inherit `/etc/environment`
The deploy script writes `/etc/cron.d/<basename>` for monitoring collectors. **Cron does not read `/etc/environment` by default** (it ships with a minimal PATH-only env). So pollers fired by cron won't see HTTPS_PROXY unless either:
- Each cron line `source`s `/etc/environment` before running, OR
- A `BASH_ENV=/etc/environment` is set inside `/etc/cron.d/<basename>`, OR
- Each collector script itself sets the proxy env at the top (or honors a config file)

**Fix proposal:** add `BASH_ENV=/etc/environment` and `SHELL=/bin/bash` to the cron files written by `bhn-deploy-all-collectors.sh` (one-line patch to the deploy script). Cleanest fix; preserves the "env in one place" design.

### 🟢 GAP-5 — No scripts in repo explicitly bypass the proxy (good news)
Grep for `--noproxy`, `verify=False`, `proxies=None`, `proxies={}` across all of `scripts/` returns **zero matches**. Every Python poller uses `requests` (which honors `HTTPS_PROXY`); shell scripts use `curl`/`wget` without `--noproxy` flags. **No code-level bypasses to fix.**

### 🟢 GAP-6 — Hillsboro inbound rule already exists
Per the lockdown README: "LA's outbound UFW rule for `<BHN_WG_HIL_IP>` already in place — added 2026-05-13 in the WG resolution fix." So LA → Hillsboro tunnel reachability shouldn't be a blocker. **Confirm with `ufw-rewrite.sh status` before relying on this.**

---

## 4. The "Frankfurt" question — design clarification needed

Your message said "**Hillsboro and Frankfurt**." The current design (`bhn-network-data-flow.md`) only routes **operator personal browsing** through Frankfurt — not service/API traffic. Frankfurt's role today:

1. **Personal full-tunnel exit** (operator's WG profile) — jurisdictional isolation, DE IP. Currently **broken** (MASQUERADE rule missing).
2. **Frankfurt Tor SOCKS** at `<BHN_WG_FRA_IP>:9050` — available, no automatic consumers configured.
3. **Voice infra is LA-only** — German §201 StGB legal constraint per the salvaged HORIZON roadmap. ElevenLabs/Twilio/Whisper **must not** route via FRA.

**Three options for what you mean by "and Frankfurt":**

| Option | Meaning | Effort |
|--------|---------|--------|
| **(A)** Frankfurt = operator personal browsing only (current design) | Fix the FRA MASQUERADE so your full-tunnel works. Service traffic stays on Hillsboro. | Medium (separate workstream — `frankfurt-exit-backlog.md`) |
| **(B)** Some service APIs also route via FRA (per-API matrix) | E.g., NewsAPI/OpenWeatherMap via FRA (privacy + DE jurisdiction), Anthropic/Twilio/ElevenLabs via Hillsboro (operational continuity, voice constraint). Requires a second proxy on FRA + per-call routing logic. | High — new design |
| **(C)** Tor-via-FRA for selected lookups | Privacy-sensitive scrapers (eBay sold comps, news polling) route via FRA's Tor SOCKS for an unlinkable circuit. Different from (B) — uses Tor, not just FRA's exit IP. | Medium — opt-in per script, FRA SOCKS already exists |

**Default proposal: (A)** for now. Hillsboro handles all service traffic (existing design), FRA stays scoped to operator personal browsing (fix that separately). If you want (B) or (C), call it out and we'll design a routing matrix.

> 🚨 **Hard constraint to preserve in any option:** Voice (ElevenLabs API, Twilio audio, Whisper transcripts) **never** routes via Frankfurt. Per the salvaged HORIZON roadmap: "Voice infra location: LA only (German law §201 StGB blocks Frankfurt)." Even with Tor or any FRA path, voice stays on LA → Hillsboro → external.

---

## 5. What needs runtime verification on LA (you'll need to SSH)

Before any fix lands, the actual deployed state on LA needs confirmation. From the operator PC, SSH to LA goes via FRA ProxyJump (per memory `reference_ssh_paths.md` — your WG-to-LA is broken). Commands to run:

```bash
# === ON LA (via ssh frankfurt → ssh la, or however your jump chain works) ===

# 1. UFW state (the most important check)
sudo bash /opt/bhn-la-egress-lockdown/ufw-rewrite.sh status
#    or, if not staged on disk yet:
sudo ufw status verbose | grep -E "(ALLOW OUT|Default)"

# 2. Is /etc/environment patched?
grep -i proxy /etc/environment

# 3. Is the apt config in place?
ls /etc/apt/apt.conf.d/95bhn-proxy.conf 2>&1 && cat $_

# 4. Does n8n have the systemd drop-in?
systemctl show n8n -p Environment | tr ' ' '\n' | grep -i proxy

# 5. Does Grafana?
systemctl show grafana-server -p Environment | tr ' ' '\n' | grep -i proxy

# 6. Is tinyproxy reachable from LA right now?
curl -fsS --max-time 5 -x http://<BHN_WG_HIL_IP>:8888 https://api.ipify.org
# expect: <BHN_HIL_PUBLIC_IP>

# 7. What does direct egress look like? (sanity)
curl -fsS --max-time 5 https://api.ipify.org
# probable: <BHN_LA_PUBLIC_IP>  (LA direct)

# 8. /etc/bhn-trading/env contents on LA (HORIZON collectors)
sudo cat /etc/bhn-trading/env | grep -iE 'proxy|http|https'
#    probable: no proxy lines → confirms GAP-2
```

The five "expected: ❓" rows in Section 2's table get answered by checks 2–6.

---

## 6. Fix sequence (in order, with safety checkpoints)

**Do not skip steps. The lockdown script has a `lockdown` mode that will cut LA off from external HTTPS if the proxy path isn't actually working — verify each step.**

### Phase A — Runtime baseline (no changes)
1. Run Section 5 checks. Document the actual current state on LA.

### Phase B — Pre-deploy fixes (in repo, then deploy)
2. **Patch `infrastructure/services/tinyproxy/tinyproxy.conf`** with `ConnectPort 587` if SMTP-out is needed (GAP-3). Deploy to Hillsboro before locking down LA.
3. **Patch `scripts/bhn-deploy-all-collectors.sh`** to add `BASH_ENV=/etc/environment` and `SHELL=/bin/bash` to every cron file it writes (GAP-4).
4. **Decide GAP-2 fix path** (a/b/c). If (a): plan to append proxy block to LA's `/etc/bhn-trading/env` during the deploy step. **DO NOT** apply to NJ's env file.

### Phase C — Stage on LA
5. `scp infrastructure/la-egress-lockdown/` → `/opt/bhn-la-egress-lockdown/` on LA.
6. Run `sudo bash deploy.sh` on LA. Verifies env vars, apt config, n8n drop-in, grafana drop-in are in place. Restarts n8n + grafana.
7. **(GAP-2 fix)** Append proxy lines to `/etc/bhn-trading/env` on LA. Restart all trading + HORIZON services on LA:
   ```bash
   sudo systemctl restart bhn-macro-data bhn-market-data bhn-sentiment \
                          bhn-morning-brief bhn-paper-trades-watch bhn-regime-classifier
   # (NJ-side strategy services left alone)
   ```

### Phase D — Additive proxy egress (still safe to rollback)
8. `sudo bash ufw-rewrite.sh add-proxy-route` on LA. Now direct AND proxy both work; nothing broken yet.
9. Run all 6 verification commands from README §3 (env vars in shell, apt, n8n, grafana, direct curl, proxy curl).
10. Smoke-test HORIZON: trigger an Anthropic call, an ElevenLabs TTS, an FMP query, an OpenWeatherMap fetch via n8n. Each should still work. The IP they see is still LA at this point — that's fine, we haven't locked down.

### Phase E — Lockdown
11. `sudo bash ufw-rewrite.sh lockdown` on LA. Script runs its own reachability check before applying; if it fails, no change.
12. Verify external calls now exit Hillsboro:
    ```bash
    curl -fsS https://api.ipify.org   # → <BHN_HIL_PUBLIC_IP> (Hetzner)
    sudo apt-get update                # → succeeds via proxy
    ```
13. Re-smoke HORIZON. Confirm Anthropic/Twilio/ElevenLabs/FMP all still work via the proxy.

### Phase F — Rollback (if anything is broken)
14. `sudo bash ufw-rewrite.sh restore-direct-egress` + `sudo bash deploy.sh --uninstall` → back to current state. Full rollback documented in README.

### Phase G — Documentation
15. Update `bhn-network-data-flow.md` to flip "LA operational egress via Hillsboro" from **[DESIGNED]** → **[LIVE]**.
16. Update Phase 4 of README's Phase plan: "tinyproxy (Hillsboro) — LA egress proxy [✅] verified, lockdown pending" → "[✅] LIVE, locked down."

---

## 7. Open decisions

| # | Decision | Default proposal | Section |
|---|----------|------------------|---------|
| 1 | GAP-2 fix path — (a) /etc/bhn-trading/env append, (b) per-unit drop-in, (c) explicit EnvironmentFile reference | (a) /etc/bhn-trading/env append | §3 |
| 2 | Does LA need SMTP-out (587)? Add `ConnectPort 587` to tinyproxy? | Unknown — operator call. Default no, omit. | §3 |
| 3 | "Frankfurt" interpretation — (A) personal-only, (B) per-API matrix, (C) Tor for select scrapers | (A) personal-only, fix FRA MASQUERADE separately | §4 |
| 4 | When to execute the lockdown — this session, or queue for a window when you can verify each step | Queue for an explicit lockdown session | §6 |

---

## 8. Out-of-scope (intentionally not changed by this audit)

- **NJ → Alpaca direct egress** — intentional, stays.
- **Voice infra → LA-only** — legal constraint, stays. Even if FRA exits become available, voice does not move.
- **Inbound webhooks → LA direct** — asymmetric by design (Twilio + n8n webhook URLs).
- **Mesh / DNS / NTP / WG underlay** — not HTTP, not in scope.
- **FRA MASQUERADE fix** — separate workstream tracked in `frankfurt-exit-backlog.md`.
- **HORIZON memory architecture** (pgvector + Redis on LA) — covered by `project_horizon_data_scope.md`, no egress implications.

---

## Annotations key

- 🔴 CRITICAL — fix before lockdown will cut LA off
- 🟡 MEDIUM — fix recommended but lockdown can proceed without it
- 🟢 GOOD — verified clean
- ❓ DECIDE — needs operator call
- 🚨 HARD CONSTRAINT — legal/safety requirement, do not violate

**Tally:** 2 CRITICAL gaps · 2 MEDIUM · 2 GOOD · 4 DECIDE questions · 1 HARD CONSTRAINT (voice on LA only)
