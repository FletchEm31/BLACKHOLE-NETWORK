# LA egress lockdown — proxy LA's outbound via Hillsboro tinyproxy

Routes all LA hub outbound HTTP/HTTPS through `BHN-HILLSBORO-US3`'s tinyproxy at `10.8.0.6:8888`. End state: LA's public IP (`149.28.91.100`, Vultr) no longer appears in Anthropic/Twilio/ElevenLabs/financial-data API access logs; that traffic exits via Hillsboro's IP (`5.78.94.237`, Hetzner).

Inbound webhooks (Twilio voice/SMS callbacks, n8n workflow webhook URLs, ElevenLabs async callbacks) continue to land directly on LA. That asymmetry is deliberate — see `memory/project_la_egress_isolation.md`.

## Prerequisites

1. tinyproxy already deployed + verified on Hillsboro (`infrastructure/services/tinyproxy/`). Confirm with:
   ```bash
   ssh hillsboro 'ss -lntp | grep 8888'
   curl -fsS -x http://10.8.0.6:8888 https://api.ipify.org   # from LA, expect 5.78.94.237
   ```
2. LA's outbound UFW rule for `10.8.0.6` already in place (it is — added 2026-05-13 in the WG resolution fix).

If either fails, **stop and fix before touching anything in this directory**. Pulling direct egress before the proxy path works cuts LA off from Anthropic / apt / certbot / n8n external calls instantly.

## Files

| File | Destination on LA | Purpose |
|---|---|---|
| `environment.snippet` | append to `/etc/environment` | system-wide `http_proxy`/`https_proxy` for any process that reads env at start (cron jobs, login shells, etc.) |
| `apt.conf.d/95bhn-proxy.conf` | `/etc/apt/apt.conf.d/95bhn-proxy.conf` | apt mirrors over the proxy (apt does NOT read `/etc/environment`) |
| `systemd/n8n.service.d/proxy.conf` | `/etc/systemd/system/n8n.service.d/proxy.conf` | n8n's Node.js HTTP client honors `HTTPS_PROXY`; systemd services don't inherit `/etc/environment` |
| `systemd/grafana-server.service.d/proxy.conf` | `/etc/systemd/system/grafana-server.service.d/proxy.conf` | same — Grafana's alert-webhook + plugin-update calls |
| `deploy.sh` | run on LA as root | copies all of the above to the right paths + reloads systemd + restarts n8n / grafana |
| `ufw-rewrite.sh` | run on LA as root | UFW changes in two modes — `add-proxy-route` (additive, safe) and `lockdown` (removes direct egress; cuts internet for non-proxied calls) |

## Deploy order

```bash
# === On LA, as root, after staging this directory under /opt/bhn-la-egress-lockdown ===
cd /opt/bhn-la-egress-lockdown

# 1. Add the explicit proxy egress rule (additive, no risk)
sudo bash ufw-rewrite.sh add-proxy-route

# 2. Drop config files into place (env vars, apt, systemd) + restart services
sudo bash deploy.sh

# 3. Verify each layer is using the proxy BEFORE removing direct egress:
#    a) Login shell sees the env vars (after `bash -l` or new SSH session):
echo "$https_proxy"   # → http://10.8.0.6:8888

#    b) apt routes via proxy:
sudo apt-get update -o Debug::Acquire::http=true 2>&1 | grep -i 'proxy\|connect'

#    c) n8n process inherited the proxy env:
systemctl show n8n -p Environment | tr ' ' '\n' | grep -i proxy

#    d) Grafana process inherited the proxy env:
systemctl show grafana-server -p Environment | tr ' ' '\n' | grep -i proxy

#    e) A direct external call from LA still works (because direct egress is
#       still allowed — we haven't pulled it yet):
curl -fsS https://api.ipify.org   # → 149.28.91.100  (LA direct — expected)

#    f) The same call via proxy:
https_proxy=http://10.8.0.6:8888 curl -fsS https://api.ipify.org   # → 5.78.94.237

# 4. ONLY when 3a–3f all pass, run the lockdown:
sudo bash ufw-rewrite.sh lockdown

# 5. Now confirm everything that used to work direct still works via proxy:
curl -fsS https://api.ipify.org   # → should now return 5.78.94.237 (via env vars)
sudo apt-get update                # → succeeds via apt proxy config
# n8n & grafana don't auto-test — exercise them by triggering a workflow / alert
```

## Rollback

```bash
sudo bash ufw-rewrite.sh restore-direct-egress    # restore the pre-lockdown UFW
sudo bash deploy.sh --uninstall                    # remove env vars, apt config, systemd drop-ins
sudo systemctl daemon-reload
sudo systemctl restart n8n grafana-server
```

LA is back to direct outbound. Re-bootstrap-recover safe.

## What's NOT proxied

- DNS (port 53) — stays via the local dnscrypt-proxy on `127.0.0.1`. dnscrypt-proxy itself uses DoH over 443 to reach upstream resolvers; that 443 traffic could be proxied but it would require additional config in `/etc/dnscrypt-proxy/dnscrypt-proxy.toml`. Deferred — DNS lookups don't reveal interesting metadata about LA's API consumers.
- NTP (port 123/udp) — `ufw allow out 123/udp` stays. NTP isn't HTTP; proxying isn't possible without UDP-aware tunneling.
- WireGuard underlay UDP (51820/51821 to known peer endpoints) — stays direct (this IS the tunnel layer).
- Intra-mesh traffic (`10.8.0.0/24`, `10.9.0.0/24`) — stays direct (peer-to-peer over WG).

## Sanity checks if something breaks

| Symptom | Likely cause |
|---|---|
| `apt-get update` times out | apt.conf.d/95bhn-proxy.conf not loaded — `ls /etc/apt/apt.conf.d/` |
| `curl https://anything` from operator login shell times out | env vars not in current shell — open a new `bash -l` or `source /etc/environment` |
| n8n workflow HTTP-request node hangs | systemd drop-in not active — `systemctl show n8n -p Environment` |
| Grafana alert webhook fails | same — check `systemctl show grafana-server -p Environment` |
| Everything fails | tinyproxy down on Hillsboro — `ssh hillsboro 'systemctl status tinyproxy'` |
| Anthropic API works but Twilio 403s | Twilio's IP allowlist may not include Hillsboro's `5.78.94.237` — add Hetzner IP to Twilio account allowlist |
