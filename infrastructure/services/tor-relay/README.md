# BHN Tor relay — non-exit middle relay on Frankfurt

Runs a Tor **non-exit middle relay** on Frankfurt. Formalizes Frankfurt's role as a privacy-routing node. Local SearXNG (and future BHN services on Frankfurt) can route their egress through Tor via the SOCKS proxy on `<BHN_WG_FRA_IP>:9050`.

**Not an exit.** ExitRelay=0, ExitPolicy=reject all. Keeps legal exposure minimal — middle relays only forward traffic between other Tor nodes; they never see plaintext or attribute traffic to operators of the relay.

**Not a bridge.** Listed in public Tor consensus. Helps the network capacity-wise but doesn't help users in censored regions. (Future: switch to bridge with obfs4 transport if operator wants the more-altruistic posture.)

## Prerequisites

- Docker + docker compose installed on Frankfurt
- Public TCP 9001 reachable from the internet → **REQUIRES a UFW change** (see below)
- Vultr ToS compatible: ✅ non-exit relays permitted; only exit nodes are forbidden

## Pre-deploy: open UFW for ORPort

```bash
# On Frankfurt — BEFORE first `docker compose up`
ufw allow 9001/tcp comment 'Tor relay ORPort'
ufw status numbered | grep 9001
```

Without this, the relay will fail reachability self-test and won't be published in the consensus.

## Deploy

```bash
# 1. Create the service dir on Frankfurt
sudo mkdir -p /opt/bhn-tor-relay
cd /opt/bhn-tor-relay

# 2. Copy Dockerfile + docker-compose.yml + torrc + .env.example from repo
# Then:
cp .env.example .env
# BIND_IP=<BHN_WG_FRA_IP> is the default; no edit usually needed

# 3. Build + start (first run pulls debian:bookworm-slim + builds, ~30s)
docker compose up -d --build
docker compose logs --tail 50

# 4. Verify the relay started cleanly
docker logs bhn-tor-relay 2>&1 | grep -E 'Bootstrapped|Self-testing|Now checking|published descriptor'
```

What to look for in the log over the first ~30 minutes:

| Time | Expected log message | Meaning |
|------|---------------------|---------|
| 0-30s | `Bootstrapped 100% (done)` | Tor connected to network |
| ~1-5 min | `Now checking whether ORPort <IP>:9001 is reachable` | self-test starting |
| ~5-15 min | `Self-testing indicates your ORPort is reachable from the outside. Excellent.` | UFW + port mapping working |
| ~30 min | `Performing bandwidth self-test` | consensus is evaluating us |
| 12-48 h | Listed in consensus | publicly visible on https://metrics.torproject.org/ |

If you see `Your server has not managed to confirm reachability for its ORPort(s)` after 30+ min, the UFW rule is missing or the port-mapping is broken.

## Verify SOCKS proxy works locally

After bootstrapping completes (~1-2 minutes from start):

```bash
# Test from Frankfurt host (the WG tunnel IP)
curl --socks5h <BHN_WG_FRA_IP>:9050 https://check.torproject.org/api/ip
# Should return JSON with "IsTor": true
```

## Verify it's listed on Tor metrics (12-48h after first start)

Visit https://metrics.torproject.org/rs.html and search for `BHNFornaxEU1`. Or use the API:

```bash
curl -fsS "https://onionoo.torproject.org/details?search=BHNFornaxEU1" | python3 -m json.tool
```

You should see a JSON entry with your fingerprint, observed bandwidth, etc.

## Bandwidth control

| Setting in torrc | Value | Effect |
|------------------|-------|--------|
| `RelayBandwidthRate` | 1 MB/s | sustained rate cap |
| `RelayBandwidthBurst` | 2 MB/s | short-burst allowance |
| `AccountingMax` | 1500 GB | hard monthly cap (Vultr allowance is 2 TB; this leaves 500 GB headroom) |
| `AccountingStart` | `month 1 00:00` | counter resets 00:00 UTC on 1st of each month |

If the monthly cap is hit, the relay hibernates (stays running, refuses new connections) until the next cycle. To adjust, edit `torrc` in repo + redeploy.

## Connecting SearXNG to this relay (phase 2)

After the relay is bootstrapped and the SocksPort works:

1. In `/opt/bhn-searxng/settings.yml`, uncomment the `outgoing.proxies` block (already wired to point at `socks5h://<BHN_WG_FRA_IP>:9050`)
2. `docker compose restart searxng` in the SearXNG dir
3. Verify in SearXNG search results: response times should jump (+500-2000 ms typical Tor circuit overhead)

## When you stop the relay

If the relay's been running long enough to be in the consensus (12+ hours):

```bash
# Graceful shutdown — sends Tor a signal to drain existing circuits cleanly
docker exec bhn-tor-relay tor --hash-password ""
docker compose stop
```

A hard stop is fine for short-running relays; only matters once you've been listed for days+.

## Memory / disk footprint

- Image: ~70 MB (debian:bookworm-slim + tor + geoip)
- RAM: ~40 MB idle, up to ~150 MB when actively relaying
- Disk: ~50 MB descriptors + state in tor-data volume; ~1 GB/month log rotation in tor-logs

## MyFamily — REQUIRED post-deploy step (multi-relay family)

The BHN relay family currently has FornaxEU1 (this one), with NebulaUS2 (NJ, planned) and HeliosUS3 (Hillsboro, deploying) joining shortly. AuroraEU2 (Sweden) and EridanusEU3 (Iceland) will follow. **After EACH relay bootstraps, MyFamily MUST be updated on EVERY deployed torrc** — without it, Tor consensus may build a circuit that passes through any two BHN relays, partially defeating the privacy benefit. See `infrastructure/docs/tor-relay-naming.md` for the full roster.

### Process (run after every new relay reaches consensus, ~24-48h after first start)

```bash
# Collect every running relay's fingerprint
ssh frankfurt 'docker exec bhn-tor-relay cat /var/lib/tor/fingerprint'
# Example output: BHNFornaxEU1 ABCDEF0123456789ABCDEF0123456789ABCDEF01

ssh root@<nj-public-ip> 'docker exec bhn-tor-relay cat /var/lib/tor/fingerprint'
# Example output: BHNNebulaUS2 0123456789ABCDEF0123456789ABCDEF01234567

ssh hillsboro 'docker exec bhn-tor-relay cat /var/lib/tor/fingerprint'
# Example output: BHNHeliosUS3 76543210FEDCBA9876543210FEDCBA9876543210

# Build the MyFamily line — same on every torrc
# MyFamily $FORNAX_FP,$NEBULA_FP,$HELIOS_FP
```

### Update every torrc file in repo, then deploy

Edit `infrastructure/services/tor-relay*/torrc` for every relay — uncomment and populate the `MyFamily` line at the bottom of each. Fingerprints are public info; safe to commit.

```bash
# In the repo (after editing every relay's torrc):
git add infrastructure/services/tor-relay*/torrc
git commit -m "tor: declare MyFamily across Fornax + Nebula + Helios relays"
git push

# Then deploy on every node running a relay:
ssh frankfurt 'cd /opt/bhn-tor-relay && git pull && docker compose restart'
ssh root@<nj-public-ip> 'cd /opt/bhn-tor-relay && git pull && docker compose restart'
ssh hillsboro 'cd /opt/bhn-tor-relay && git pull && docker compose restart'
```

### Verify

Within a few hours every relay republishes its descriptor. Check on Tor metrics:

```bash
curl -fsS "https://onionoo.torproject.org/details?search=BHNFornaxEU1" | grep -i family
curl -fsS "https://onionoo.torproject.org/details?search=BHNNebulaUS2" | grep -i family
curl -fsS "https://onionoo.torproject.org/details?search=BHNHeliosUS3" | grep -i family
```

Each should show the OTHER fingerprints in its `family` field.

### Adding a future relay (AuroraEU2, EridanusEU3, etc.)

When the next BHN relay joins, repeat: collect its fingerprint, append to the MyFamily line in ALL torrcs, commit, redeploy each. MyFamily is N-way — every relay declares every other relay in the family.
