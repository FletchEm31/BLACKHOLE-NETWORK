# BHN Wallos — subscription tracker

Local subscription tracker for BHN's recurring costs (Vultr × N, Anthropic, ElevenLabs, Twilio, etc.). Self-hosted, no external dependencies, no accounts.

**LA-only.** Bound to LA's WG tunnel — operator-only access. The morning briefing module (M2) will eventually pull from this for the "cost section" of the briefing, once a sync workflow into PG is built (follow-up session).

## Prerequisites

- Docker + docker compose on LA
- WG tunnel up (LA = 10.8.0.1 on wg0)

## Deploy

```bash
# 1. Create the service dir on LA
sudo mkdir -p /opt/bhn-wallos
cd /opt/bhn-wallos

# 2. Copy docker-compose.yml + .env.example from repo
# Then:
cp .env.example .env
# BIND_IP=10.8.0.1 is default; no edit usually needed

# 3. Bring it up
docker compose up -d
docker compose logs --tail 30

# 4. First-run setup happens in the browser:
#    http://10.8.0.1:8090/
#    Create the operator admin account, set master password
```

## Initial population

Manual via the web UI. The operator enters each BHN subscription one-time:

| Subscription | Renewal | Cost |
|--------------|---------|------|
| Vultr LA VPS | monthly | $12 |
| Vultr Frankfurt VPS | monthly | $12 |
| Vultr NJ VPS (when provisioned) | monthly | $XX |
| Vultr NVMe storage | monthly | $10.10 |
| Vultr HDD storage | monthly | $9.97 |
| Anthropic API | metered (pay-as-you-go) | ~$3-5/mo + Claude Max $100-200 |
| ElevenLabs Creator | monthly | $22 |
| Twilio | metered + number | ~$15/mo |
| NewsAPI | free | $0 |
| OpenWeatherMap | free | $0 |
| Backups (restic SFTP target — future) | TBD | $2.40/mo (current hot-only) |

Wallos handles the math; renewal reminders fire as in-app + (when HORIZON sync is wired) via SMS through HORIZON's M4 alert channel.

## HORIZON integration (deferred to follow-up session)

Wallos exposes a REST API (`/api/`). A new n8n workflow `bhn-wallos-sync` will:

1. Poll Wallos's API every 6h
2. Insert/update rows into a new PG table `subscriptions (id, name, cost, currency, billing_cycle, next_renewal_at, category, active, raw_payload, synced_at)`
3. HORIZON reads via existing `query_db` tool
4. M2 morning briefing module reads "next 7-day renewals + monthly burn" from PG

Schema + workflow are out of scope for this session. Open as a separate ticket when ready.

## VPN-only verification

```bash
# On LA, after deploy
ss -tlnp | grep 8090
# Should show LISTEN on 10.8.0.1:8090, NOT 0.0.0.0:8090

# Public IP must NOT serve:
curl --max-time 3 http://<BHN_LA_PUBLIC_IP>:8090/ 2>&1 | head -3
# Should fail (connection refused or timeout)

# From operator's PC over WG:
curl http://10.8.0.1:8090/ | head -10
# Should return Wallos HTML
```

## Resource footprint

- Image: ~200 MB
- RAM: ~50 MB
- Disk: SQLite DB grows slowly (~10 MB after a year of usage)

Fits comfortably in LA's 2 GB RAM remaining headroom.

## Backup

The SQLite DB at `/opt/bhn-wallos/db/` should be added to LA's restic backup target. After deploy:

```bash
# Append to BHN backup config (one-time)
# Edit /root/.eh-backup.env to include /opt/bhn-wallos/db in BACKUP_PATHS,
# or rely on the eh-backup script's existing all-/opt/* glob if it has one.
```

(Verify against current `scripts/bhn-backup.sh` content — adjust as needed.)
