# BHN Tor relay — non-exit middle relay on NJ

Runs a Tor **non-exit middle relay** on the NJ trading node. Adds capacity to BHN's privacy stack and pairs with Frankfurt's relay via MyFamily to widen the consensus footprint without compromising circuit security.

**Not an exit.** ExitRelay=0, ExitPolicy=reject all.
**Not a bridge.** Listed in public consensus.
**Bandwidth-reduced** vs Frankfurt: 512 KB/s sustained rate, 750 GB/month cap — leaves room for trading-API workloads on the same host.

## Deploy-order constraint

NJ's WG tunnel is currently broken (Vultr cross-region block suspected, parked 2026-05-11). **The Tor relay does NOT depend on the tunnel** — its primary function is the public ORPort on 9001/tcp, reachable on NJ's public IP. So this can deploy now, before the tunnel is fixed.

The only thing that doesn't work until the tunnel is restored:
- The local SocksPort binding (`10.10.0.2:9050`) is unreachable from anything inside the BHN VPN. Not a problem today — NJ has no other BHN services that need a Tor upstream proxy. SearXNG is Frankfurt-only.

## Trading-API coexistence note

When this relay is listed in Tor consensus (~24h after first start), NJ's public IP appears on every public Tor-relay scraper list. Some financial APIs auto-flag Tor-listed IPs. **Mitigation:** trading workloads make their outbound calls directly from NJ's public IP — they do NOT route through this relay's SocksPort. So the relay's reputation and the trading client's reputation are separate flows. Real-world risk is low for non-exit relays but not zero. Monitor Alpaca + Polymarket/Kalshi for unusual rate-limiting or CAPTCHA challenges over the first 1-2 weeks; if observed, the relay can be stopped without losing trading capability.

## Prerequisites

- Docker + docker compose installed on NJ
- Public TCP 9001 reachable from the internet → **REQUIRES a UFW change** (see below)
- Vultr ToS check: ✅ non-exit relays permitted

## Pre-deploy: open UFW for ORPort

```bash
# On NJ — BEFORE first `docker compose up`
ufw allow 9001/tcp comment 'Tor relay ORPort'
ufw status numbered | grep 9001
```

## Deploy

```bash
# 1. Create the service dir on NJ (SSH from operator's PC to NJ's public IP
#    — does NOT require the WG tunnel)
sudo mkdir -p /opt/bhn-tor-relay
cd /opt/bhn-tor-relay

# 2. Copy Dockerfile + docker-compose.yml + torrc + .env.example from repo
cp .env.example .env
# BIND_IP=10.10.0.2 default — leave as-is even though tunnel is broken;
# the binding just won't accept connections until tunnel is fixed.

# 3. Build + start
docker compose up -d --build
docker compose logs --tail 50
```

What to look for in the log over the first ~30 minutes — same as Frankfurt's relay:

| Time | Log line | Meaning |
|------|----------|---------|
| 0-30s | `Bootstrapped 100% (done)` | Tor connected to network |
| ~5-15 min | `Self-testing indicates your ORPort is reachable` | UFW + port mapping working |
| 12-48 h | listed on `metrics.torproject.org` | publicly visible in consensus |

If `Your server has not managed to confirm reachability for its ORPort(s)` shows after 30+ min, the UFW rule on NJ is missing or NJ's Vultr firewall (separate from UFW) is blocking 9001.

## MyFamily — required follow-up after NJ is bootstrapped

Frankfurt is decommissioned (2026-05-28) — the 3-way MyFamily plan this section originally described (Frankfurt + NJ + Hillsboro) is stale. The currently-deployed MyFamily pair is Hillsboro (`BHNHeliosUS3`) + Helsinki (`BHNAuroraEU1`), per `infrastructure/docs/nodes/BHN-HILLSBORO-US3.md` and `BHN-HELSINKI-EU1.md`. **Unconfirmed: whether NJ's relay (if still running) needs to join that family too** — verify NJ's current `torrc` MyFamily line live before assuming it's still 3-way or needs updating.

After NJ's relay has completed bootstrapping (24-48h to be fully consensus-published):

```bash
# Get NJ's fingerprint (SSH to NJ public IP)
ssh root@<nj-public-ip> 'docker exec bhn-tor-relay cat /var/lib/tor/fingerprint'
# Returns something like: BHNNebulaUS2 9876543210FEDCBA...
```

Then update the torrc's MyFamily line to include NJ's fingerprint alongside whichever relays it should be grouped with (confirm the current live set first — see note above):

```
# In /opt/bhn-tor-relay/torrc:
MyFamily $NJ_FP,...
```

Restart the container:

```bash
cd /opt/bhn-tor-relay && docker compose restart
```

Verify on `metrics.torproject.org` — relays should show the "Family" field populated with each other's fingerprints within a few hours.

**Why this matters:** without MyFamily set, Tor's consensus might build a 3-hop circuit that passes through both BHN relays — defeating the privacy benefit (your two relays would see both the entry and the middle of the same circuit). MyFamily declares the relationship so the consensus refuses such circuits.

Also commit the torrc updates to the repo with a follow-up commit. Don't let them only live in production.

## Verifying the relay is alive

```bash
# Direct check from NJ host
docker logs bhn-tor-relay 2>&1 | grep -E 'Bootstrapped|Self-testing|Now checking|published descriptor'

# From outside, after consensus publication
curl -fsS "https://onionoo.torproject.org/details?search=BHNNebulaUS2" | head -50
```

## Bandwidth comparison

| Node | RelayBandwidthRate | AccountingMax | Rationale |
|------|--------------------|---------------|-----------|
| Frankfurt | 1 MB/s | 1500 GB/month | privacy-node dedicated; full capacity |
| **NJ** | **512 KB/s** | **750 GB/month** | **trading-node shared; halved for coexistence** |
| LA | (not running Tor — hub) | — | — |

If after a few weeks the trading workloads stay well under bandwidth budget, NJ's relay can be bumped to match Frankfurt's rate. Edit `torrc`, commit, redeploy.

## Resource footprint

- Image: ~70 MB
- RAM: ~40 MB idle, ~150 MB at full bandwidth
- Disk: ~50 MB descriptors/state, ~500 MB/month logs (smaller than Frankfurt's due to halved bandwidth)
