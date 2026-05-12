# BHN SearXNG — private meta-search

Self-hosted SearXNG instance for the operator. Aggregates results from many search engines while preserving privacy. **Frankfurt-only** (privacy node role). VPN-only access.

Pairs with **Tor relay on Frankfurt** in phase 2: search-engine requests route upstream via SOCKS proxy, unlinking the operator's queries from search-engine fingerprinting. Uncomment the `outgoing.proxies` block in `settings.yml` after the Tor relay is up and reachable on `10.9.0.2:9050`.

## Prerequisites

- Docker + docker compose installed on Frankfurt
- WG tunnel up (Frankfurt = 10.9.0.2 on wg1)

## Deploy

```bash
# 1. Create the service dir
sudo mkdir -p /opt/bhn-searxng
cd /opt/bhn-searxng

# 2. Copy compose, settings, .env.example from repo (scp or git pull)
# Then:
cp .env.example .env

# 3. Generate the secret key
SECRET=$(openssl rand -hex 32)
sed -i "s/^SEARXNG_SECRET=.*/SEARXNG_SECRET=$SECRET/" .env

# 4. Verify .env has both values populated
grep -E '^(BIND_IP|SEARXNG_SECRET)=' .env

# 5. Bring it up
docker compose up -d
docker compose logs --tail 30

# 6. Verify endpoint
curl -fsS http://10.9.0.2:8089/ | head -10
# Should return SearXNG HTML

# 7. Verify NOT publicly reachable
ss -tlnp | grep 8089
# Should show LISTEN on 10.9.0.2:8089, NOT 0.0.0.0:8089
```

## First-search test from operator's PC (over WG)

```
Browser:  http://10.9.0.2:8089/
Search:   "test query"
```

Should return aggregated results from multiple engines.

## Phase 2 — Tor upstream wire-up

After the Tor relay on Frankfurt is up and `nc -zv 10.9.0.2 9050` from any container succeeds:

1. Edit `settings.yml`, uncomment the `outgoing.proxies` block
2. `docker compose restart searxng` (no rebuild needed — settings.yml is mounted)
3. Verify in SearXNG: the "Engine response time" in results should now show Tor-route latency (typically +500-2000 ms)
4. Optional: `curl --socks5h 10.9.0.2:9050 https://check.torproject.org/api/ip` to confirm Tor circuit is working

## Operator-side notes

- **No external accounts needed** — all default engines (Google, DuckDuckGo, Bing, Brave, Wikipedia, GitHub) work via scraping
- **JSON API enabled** — HORIZON can later use this instance as a `web_search` tool by calling `GET http://10.9.0.2:8089/search?q=<query>&format=json`
- **No rate limiting** — fine for an operator-only instance, but if you ever share access change `server.limiter: true` in settings.yml
- **Resource budget** — ~150 MB combined RAM (SearXNG ~100, Redis ~50), negligible CPU idle

## UFW

No UFW change needed — SearXNG and Redis are tunnel-bound. The host port mapping `10.9.0.2:8089:8080` is on the WG interface (wg1), not the public eth0/enp1s0.

## When you visit /stats

SearXNG exposes a `/stats` endpoint with per-engine response times. Useful for tuning which engines to disable if any are consistently slow. Reach via `http://10.9.0.2:8089/stats`.
