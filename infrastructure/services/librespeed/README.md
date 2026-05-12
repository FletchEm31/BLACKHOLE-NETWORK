# BHN LibreSpeed — per-node speedtest endpoint

Self-hosted speedtest server. One instance per BHN node, used by `bhn-speedtest-probe` (cron) to log per-pair latency + bandwidth into PostgreSQL `speedtest_results`. HORIZON reads the table for latency-trend monitoring.

**VPN-only.** Binds to the node's WG tunnel interface, never to 0.0.0.0.

## Prerequisites

- Docker + docker compose installed on the target node
- PG schema applied on LA: `sudo -u postgres psql -d eventhorizon -f sql/speedtest-schema.sql`
- Node's tunnel IP known (Frankfurt = 10.9.0.2, LA = 10.8.0.1, NJ = 10.10.0.2)

## Deploy on a node

```bash
# 1. Create the service dir on the target node
sudo mkdir -p /opt/bhn-librespeed
sudo chown -R 1000:1000 /opt/bhn-librespeed
cd /opt/bhn-librespeed

# 2. Copy compose file + .env.example from repo (scp or git pull)
# Then create .env with this node's tunnel IP:
cp .env.example .env
$EDITOR .env    # set BIND_IP=10.9.0.2 (Frankfurt) or 10.8.0.1 (LA), etc.

# 3. Bring it up
docker compose up -d

# 4. Verify the endpoint responds on the tunnel
curl -fsS http://$(grep ^BIND_IP .env | cut -d= -f2):8088/ | head -5

# 5. Verify NOT exposed publicly
ss -tlnp | grep 8088
# Should show LISTEN on $BIND_IP:8088, NOT 0.0.0.0:8088
```

## Smoke test from the operator's PC (over WG)

```powershell
# Browser:
http://10.9.0.2:8088/      # Frankfurt
http://10.8.0.1:8088/      # LA

# Or curl:
curl http://10.9.0.2:8088/  | findstr -i librespeed
```

A successful response is the LibreSpeed web UI HTML. Click "Start" in the browser to run an interactive test (download + upload + ping + jitter).

## Per-node first targets

| Node | BIND_IP | Status |
|------|---------|--------|
| Frankfurt | `10.9.0.2` | Deploy now |
| LA hub | `10.8.0.1` | Deploy now |
| NJ | `10.10.0.2` | Deploy after tunnel unblocks |
| (future) | TBD | Add row |

## Architecture note

The endpoint is intentionally **passive**. The data-collection side is the per-node probe (`bhn-speedtest-probe.sh` — separate session) which runs `librespeed-cli` against this endpoint plus the peer endpoints, on a cron schedule:

- Hourly: ping/jitter only (light, ~5 KB)
- Daily: full bandwidth test (heavier, ~50 MB transferred — stagger nodes to avoid concurrent saturation of the tunnel)

Both write to PG `speedtest_results`. Grafana panel + HORIZON read are downstream consumers of the same table.

## UFW

No UFW rule needed — the host-binding `${BIND_IP}:8088` is on the WG tunnel interface (wg0 / wg1 / wgN), and UFW's default-allow on the tunnel-interface zones covers it. The public-facing interface (eth0 / enp1s0) has no rule for 8088 and won't accept connections.

## Resource footprint

- Image size: ~150 MB
- Memory: ~20-30 MB idle, ~80 MB during an active test
- CPU: negligible idle, brief spike during test
- Disk: minimal config dir (~5 MB)

Fits comfortably in Frankfurt's idle CPU + LA's 2 GB RAM headroom.
