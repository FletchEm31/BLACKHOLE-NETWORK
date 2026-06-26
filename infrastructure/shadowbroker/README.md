# BHN ShadowBroker deployment

ShadowBroker is the real-time OSINT geospatial dashboard from
`github.com/bigbodycobain/Shadowbroker` (operator's fork at
`github.com/brockmisner/shadowbroker` is a working clone of the same).
60+ data layers — ADS-B, AIS, satellites, USGS quakes, GDELT news, etc.
— behind a Next.js frontend and a FastAPI backend.

## Status — 2026-05-28

**Deployment attempted on LA, rolled back.** ShadowBroker's backend needs
~1.5–2 GB resident at steady state. Initial deploy on LA (1.9 GB total)
pushed the box into swap thrash: backend hit its 1 GB container limit
at 94 % within 2 minutes of boot, frontend healthcheck never went green,
LA available RAM dropped to 220 MB with 1.6 GB of swap in use.

**Queued for a 4 GB+ dedicated VPS** to be provisioned in a future
session. This directory holds the BHN-specific files that go alongside
the upstream clone when the new host comes online.

## Files

- `docker-compose.override.yml` — overrides the upstream compose to bind
  the frontend to a chosen mesh IP:port and suppress the backend's host
  port mapping (frontend proxies via the Docker bridge). Defaults target
  `<BHN_WG_LA_IP>:8099` for LA; change the `BIND` env var and the override
  port for a different host.
- `.env.example` — template for the deploy host's `.env`. Real secrets
  never go in the repo.

## Deploy procedure (for the next-session VPS)

Recipe assumes Docker + Docker Compose v2 on the target. The target
already on WG mesh with a UFW that defaults to deny.

```bash
mkdir -p /opt/bhn && cd /opt/bhn
git clone https://github.com/brockmisner/shadowbroker.git
cd shadowbroker

# Drop in BHN overlay
cp /path/to/bhn-repo/infrastructure/shadowbroker/docker-compose.override.yml .
cp /path/to/bhn-repo/infrastructure/shadowbroker/.env.example .env
chmod 600 .env

# Edit .env — fill in BIND (this host's WG IP), generate ADMIN_KEY and
# MESH_PEER_PUSH_SECRET via `openssl rand -hex 32`, paste API keys.
$EDITOR .env

docker compose pull
docker compose up -d

# UFW (mesh-only)
ufw allow from 10.8.0.0/24 to any port 8099 proto tcp comment "ShadowBroker mesh-only"
```

Health checks:
- `curl http://<host>:8099/api/health` returns 200
- `docker compose ps` — both services Up + healthy
- Browse to `http://<host>:8099` — dashboard renders, layers populate

## HORIZON integration (item 12, also deferred)

ShadowBroker exposes an HMAC-signed agentic command channel for an
external AI agent to read/write the map. The plan is to wire HORIZON in
as the co-analyst. Pending until ShadowBroker is actually up on the new
host. Will use the existing `EventHorizonVPN-Claude` n8n credential
(operator's constraint: don't create new Claude credentials).

## Sizing recommendation

- **CPU:** 2 vCPU minimum, 4 better for the initial sync burst.
- **RAM:** 4 GB minimum. Backend wants 1.5–2 GB, frontend ~100 MB,
  Docker overhead ~200 MB, OS ~300 MB. 4 GB leaves headroom for the SDR
  collectors when the hardware lands.
- **Disk:** 40 GB. Image volume + cached layer data.
- **Network:** UDP 51820 for WG, TCP 8099 to mesh only. No other public
  ingress.

## Lessons learned from the 2026-05-28 attempt

1. ShadowBroker's backend is genuinely heavy. The container limit of 1 GB
   in the override file is *too tight* — raise it to 2 GB on the next
   host, or remove the limit and let the OS scheduler manage it.
2. Frontend's healthcheck uses `wget --spider http://localhost:3000/`.
   On the LA attempt it stayed "starting" → "unhealthy" while the
   frontend served HTTP 200 externally — the in-container wget hit
   "Connection refused", suggesting Next.js was crashing under memory
   pressure even though it had served at least one early request. Should
   resolve cleanly on a bigger host; flag it again if it recurs.
3. The upstream compose hardcodes `BIND` default to `127.0.0.1`. Override
   replaces the port mapping wholesale, which is why the override uses
   `ports: !override` to clear and re-set.
