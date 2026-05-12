# BHN Tor relay — non-exit middle relay on Hillsboro

Runs a Tor **non-exit middle relay** on the Hillsboro egress-proxy node (BHN-HILLSBORO-US3, Hetzner US-WEST, 5.78.94.237). Third relay in the BHN family — joins Frankfurt and NJ to triangulate consensus presence across providers (Vultr / Hetzner) and continents.

**Not an exit.** ExitRelay=0, ExitPolicy=reject all.
**Not a bridge.** Listed in public consensus.
**Bandwidth-reduced** vs Frankfurt: 512 KB/s sustained rate, 750 GB/month cap — leaves room for the tinyproxy egress workload (all LA outbound API calls) and Shadowsocks on the same host.

## Provider ToS check — DO THIS FIRST

Hetzner's stance on Tor non-exit relays has historically been more restrictive than Vultr's. Their current ToS permits non-exit relays but the abuse desk reacts faster than Vultr's to consensus listings.

**Before `docker compose up`, confirm:**

1. Read Hetzner's latest acceptable use policy for the Hillsboro VPS product line — search the dashboard or contact support for "non-exit Tor relay" written confirmation. Save the response in your records.
2. If Hetzner declines, this relay does NOT deploy. Hillsboro continues as egress-proxy + Shadowsocks + future LibreSpeed endpoint without Tor.

The BHN family loses no privacy property if Hillsboro doesn't run a relay — Frankfurt and NJ already provide two-relay diversity. Hillsboro's relay would be a bonus, not a load-bearing piece.

## Prerequisites

- Docker + docker compose installed on Hillsboro
- Public TCP 9001 reachable from the internet → **REQUIRES a UFW change** (see below)
- Hetzner ToS check: ⚠️ verify before deploy (see above)

## Pre-deploy: open UFW for ORPort

```bash
# On Hillsboro — BEFORE first `docker compose up`
ufw allow 9001/tcp comment 'Tor relay ORPort'
ufw status numbered | grep 9001
```

## Deploy

```bash
# 1. Create the service dir on Hillsboro
sudo mkdir -p /opt/bhn-tor-relay
cd /opt/bhn-tor-relay

# 2. Copy Dockerfile + docker-compose.yml + torrc + .env.example from repo
cp .env.example .env
# BIND_IP=10.8.0.6 default — Hillsboro's IP on LA's wg0.

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

If `Your server has not managed to confirm reachability for its ORPort(s)` shows after 30+ min, the UFW rule on Hillsboro is missing or Hetzner's network-side firewall (separate from UFW, configured in Hetzner Cloud Console) is blocking 9001.

## MyFamily — required follow-up

After Hillsboro's relay is in consensus (24-48h), update the MyFamily line on ALL THREE relays to include all three fingerprints:

```bash
# Get all three fingerprints
ssh frankfurt 'docker exec bhn-tor-relay cat /var/lib/tor/fingerprint'
ssh nj        'docker exec bhn-tor-relay cat /var/lib/tor/fingerprint'
ssh hillsboro 'docker exec bhn-tor-relay cat /var/lib/tor/fingerprint'
```

Then update `MyFamily` in `/opt/bhn-tor-relay/torrc` on each node to:

```
MyFamily $FRANKFURT_FP,$NJ_FP,$HILLSBORO_FP
```

Restart each:

```bash
ssh frankfurt 'cd /opt/bhn-tor-relay && docker compose restart'
ssh nj        'cd /opt/bhn-tor-relay && docker compose restart'
ssh hillsboro 'cd /opt/bhn-tor-relay && docker compose restart'
```

Commit the torrc updates to the repo — don't let them only live in production.

## Egress-proxy coexistence note

Hillsboro is LA hub's outbound proxy for all external API calls. When this relay is listed in Tor consensus, Hillsboro's public IP appears on every Tor-relay scraper list. **Some APIs (Anthropic, Twilio, ElevenLabs, financial-data providers) auto-flag Tor-listed IPs and may rate-limit or block them**, which would break LA's outbound entirely.

**Mitigation:** monitor LA's outbound API success rate for the first 1-2 weeks after Hillsboro joins consensus. If Anthropic / Twilio / financial APIs start returning 403s or CAPTCHA challenges, the relay must be stopped — the egress-proxy role takes precedence. The relay can resume on a future dedicated relay-only node.

This is a real risk specific to running a Tor relay on an egress proxy. If in doubt, deploy the relay on a different node or skip it on Hillsboro.

## Verifying the relay is alive

```bash
docker logs bhn-tor-relay 2>&1 | grep -E 'Bootstrapped|Self-testing|Now checking|published descriptor'
curl -fsS "https://onionoo.torproject.org/details?search=BHNHelios-US3" | head -50
```

## Bandwidth comparison

| Node | RelayBandwidthRate | AccountingMax | Rationale |
|------|--------------------|---------------|-----------|
| Frankfurt | 1 MB/s | 1500 GB/month | privacy-node dedicated; full capacity |
| NJ | 512 KB/s | 750 GB/month | trading-node shared; halved for coexistence |
| **Hillsboro** | **512 KB/s** | **750 GB/month** | **egress-proxy shared; halved for coexistence** |
| LA | (not running Tor — hub) | — | — |

## Resource footprint

- Image: ~70 MB
- RAM: ~40 MB idle, ~150 MB at full bandwidth
- Disk: ~50 MB descriptors/state, ~500 MB/month logs
