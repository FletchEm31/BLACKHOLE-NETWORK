# BHN Session Handoff — 2026-06-27

## Session Summary

Two workstreams completed: Helsinki EU exit node bootstrap (previous session, committed)
and guest services + onboarding (this session, committed and partially deployed).

---

## Infrastructure State

### WireGuard Mesh — Current Peers
| IP | Node | Role |
|---|---|---|
| 10.8.0.1 | BHN-LOSANGELES-US1 | Hub, HAProxy egress |
| 10.8.0.5 | BHN-NEWJERSEY-HF | Highfrequency node |
| 10.8.0.6 | BHN-HILLSBORO-US3 | Primary US exit, Tor BHNHeliosUS3 |
| 10.8.0.8 | BHN-HELSINKI-EU1 | Primary EU exit, Tor BHNAuroraEU1 |
| 10.8.0.20 | Jackie Harper | Guest — full + split profiles in Proton Pass |
| 10.8.0.21 | Charles Harper | Guest — full + split profiles in Proton Pass |

### HAProxy Egress (LA 127.0.0.1:8888)
Round-robin: Hillsboro (10.8.0.6:8888) + Helsinki (10.8.0.8:8888).
Stats: http://10.8.0.1:8890/stats

### Tor MyFamily
Both relays declared: `$CEBFF0886A263D4EA1D6D08A7ED86138F98D10AA,$6AA0F8D730220D992914DB599E6A305DB5384913`
- Hillsboro BHNHeliosUS3: CEBFF0886A263D4EA1D6D08A7ED86138F98D10AA
- Helsinki BHNAuroraEU1: 6AA0F8D730220D992914DB599E6A305DB5384913

---

## Guest Services — Deploy Status (as of session end)

| Service | Container | Port | Status |
|---|---|---|---|
| SearXNG | bhn-searxng | 10.8.0.1:8095 | Up ✓ (minor: update settings.yml secret_key + redis→valkey, restart) |
| Redlib | bhn-redlib | 10.8.0.1:8091 | **Healthy** ✓ |
| Invidious | bhn-invidious | 10.8.0.1:8088 | **Fixed + working** ✓ (added hmac_key via openssl rand -hex 32) |
| Piped frontend | bhn-piped-frontend | 10.8.0.1:8089 | Up ✓ (200 OK) |
| Piped backend | bhn-piped-backend | 10.8.0.1:8092 | **Fix in progress** — see below |
| Piped proxy | bhn-piped-proxy | 10.8.0.1:8093 | Up ✓ |
| Piped DB | bhn-piped-db | internal | Healthy ✓ |
| Homarr | bhn-homarr | 10.8.0.1:7575 | **Not deployed** — compose fix below, not yet run |

### Pending Fixes at Session End

**Piped backend (bhn-piped-backend) — config fix applied, verify next session**
- Root cause: config.properties used wrong key names (`DB_URL` instead of Hibernate/Liquibase format)
- Heredoc paste was also mangled in terminal (terminator on same line as last value)
- Fix written via printf (single line, no heredoc) at session end — may not have been confirmed yet
- Correct config at `/opt/bhn-piped/config.properties` must contain:
  ```
  hibernate.connection.url=jdbc:postgresql://bhn-piped-db:5432/piped
  hibernate.connection.username=piped
  hibernate.connection.password=Pp9mXwR3kNsQ7tLv
  hibernate.connection.driver_class=org.postgresql.Driver
  liquibase.datasource.url=jdbc:postgresql://bhn-piped-db:5432/piped
  liquibase.datasource.username=piped
  liquibase.datasource.password=Pp9mXwR3kNsQ7tLv
  ```
- Verify: `cat /opt/bhn-piped/config.properties` then `docker compose -f /opt/bhn-piped/docker-compose.yml restart bhn-piped-backend`
- If still failing: `docker logs bhn-piped-backend --tail 10`

**Homarr (bhn-homarr) — no container**
- Compose file never landed at /opt/bhn-homarr/ during SCP deploy
- Fix provided at end of session — paste this on LA:
```bash
cat > /opt/bhn-homarr/docker-compose.yml << 'EOF'
services:
  homarr:
    image: ghcr.io/ajnart/homarr:latest
    container_name: bhn-homarr
    restart: unless-stopped
    ports:
      - "10.8.0.1:7575:7575"
    volumes:
      - /opt/bhn-homarr/configs:/app/data/configs
      - /opt/bhn-homarr/icons:/app/public/icons
      - /opt/bhn-homarr/data:/data
      - /var/run/docker.sock:/var/run/docker.sock:ro
    environment:
      - PORT=7575
      - DEFAULT_COLOR_SCHEME=dark
EOF
cd /opt/bhn-homarr && docker compose up -d
```
- If Homarr 1.0+ (latest tag) still fails, pin image to `ghcr.io/ajnart/homarr:0.15.10`

**SearXNG — hmac_key warning**
- Container is running but settings.yml uses `redis: url:` (deprecated) and secret_key is placeholder
- Fix: update `/opt/bhn-searxng/settings.yml`:
  - Change `redis:` block to `valkey:` (same url)
  - Change `secret_key: "ultrasecretkey"` to actual value: `858f952a9107372ba4ea7ba820039246af4bfa54ce0caefee4f11bd2db5afd12`
  - Then `docker restart bhn-searxng`

---

## AdGuard DNS Rewrites — Status Unknown

The rewrite loop (8 subdomains) was provided but may or may not have been run.
Subdomains: `portal`, `chat`, `watch`, `search`, `browse`, `pipe`, `pipe-api`, `pipe-proxy` → 10.8.0.1

Verify: `curl --noproxy '*' http://127.0.0.1:3001/control/rewrite/list -u "FletchEm88:<from Proton Pass BHN-AdGuard-Admin>" | python3 -m json.tool`

Run loop if not done (use new password from Proton Pass → BHN-AdGuard-Admin):
```bash
for sub in portal chat watch search browse pipe pipe-api pipe-proxy; do
  curl --noproxy '*' -s -X POST http://127.0.0.1:3001/control/rewrite/add \
    -u "FletchEm88:<PASSWORD>" \
    -H "Content-Type: application/json" \
    -d "{\"domain\": \"${sub}.eventhorizonvpn.com\", \"answer\": \"10.8.0.1\"}"
done
```

---

## Homarr Board Setup (after container is up)

UI at http://10.8.0.1:7575. Configure two boards:

**Operator board:** Grafana (3000), AdGuard (3001), Netdata (19999), Prometheus (9090),
n8n (5678), Dozzle (9999), Homarr, SearXNG (8095), Redlib (8091), Invidious (8088),
Piped (8089), Wallos (8090)

**Guest board:** SearXNG (search.eventhorizonvpn.com), Redlib (browse.eventhorizonvpn.com),
Invidious (watch.eventhorizonvpn.com), Matrix/Element (chat.eventhorizonvpn.com)

Both boards: dark theme, black background. Export JSON configs and commit to
`infrastructure/services/homarr/configs/` when done.

---

## Security Findings (this session)

See `infrastructure/docs/findings-security.md` (committed).

- **FINDING-001 (High):** GoDaddy public A records (`@`, `dash`, `n8n`) exposed LA's
  true IP (149.28.91.100) via eventhorizonvpn.com. Records deleted 2026-06-27.
  Residual risk: passive DNS caches (SecurityTrails, Shodan) may retain the association.
  Consider IP rotation if threat model requires it.
- **FINDING-002 (Medium):** AdGuard admin password in session chat. Rotated 2026-06-27.
  New credential: Proton Pass → BHN-AdGuard-Admin. Old password dead.

**Policy confirmed:** eventhorizonvpn.com is internal-only. No public DNS records ever.
All resolution via AdGuard Home local rewrites (mesh peers only).

---

## Remaining Backlog (not started this session)

- **Hillsboro restart + Ubuntu 24.04 upgrade** — system restart pending, upgrade deferred
- **Hillsboro Tor exporter** — expose port 9051 in compose, deploy tor-exporter (quay.io gated — build from source)
- **Helsinki PG node registration** — snapshot eventhorizon DB first, then apply /tmp/helsinki-register.sql on LA
- **security.exit_node_events table** — snapshot before schema change, then create table
- **LA systemd services** — restart to inherit http_proxy from /etc/environment
- **Proton Pass:** Add Helsinki Shadowsocks password `hzXxyGF1hdAoZNBlBoGL7hR0nVK6KyRK` as BHN-HELSINKI-SS (if not done)
- **WeatherBHN data** — NOAA CSV files untracked in repo, needs decision on whether to commit or store elsewhere
- **Traffic governance policy doc** — exit node logging policy, guest AUP, operator procedures

---

## Credentials Reference (Proton Pass entries)

| Entry | What |
|---|---|
| BHN-AdGuard-Admin | AdGuard Home — FletchEm88 (rotated 2026-06-27) |
| BHN-WG-Jackie-Harper-Full | WireGuard full tunnel — 10.8.0.20 |
| BHN-WG-Jackie-Harper-Split | WireGuard split tunnel — 10.8.0.20 |
| BHN-WG-Charles-Harper-Full | WireGuard full tunnel — 10.8.0.21 |
| BHN-WG-Charles-Harper-Split | WireGuard split tunnel — 10.8.0.21 |
| BHN-HELSINKI-SS | Helsinki Shadowsocks password |
| BHN-Grafana-LA | Grafana admin — http://10.8.0.1:3000 |

---

## Repo State

Branch: main. All session work committed and pushed to GitHub (FletchEm31/BLACKHOLE-NETWORK).
LA does not have a git clone — deploy via SCP or `git clone https://github.com/FletchEm31/BLACKHOLE-NETWORK.git` on LA.

Last commits:
- `262c50d` security: findings-security.md — LA IP exposure + AdGuard credential rotation
- `7f47958` feat(services): guest services stack + WireGuard profile redaction
- `1ad1d2d` feat(infra): WireGuard guest profiles — Jackie + Charles Harper
- `962a780` feat(infra): Helsinki EU exit — tinyproxy, Tor relay, HAProxy LB
