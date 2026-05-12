# BHN tinyproxy — egress proxy on Hillsboro for LA outbound

Single forward HTTP/HTTPS proxy that LA's outbound traffic flows through. All external API calls from LA hub (Anthropic, Twilio outbound SMS/voice, ElevenLabs TTS, NewsAPI polling, OpenWeatherMap polling, FMP, Quiver, Alpaca REST + WebSocket-CONNECT, Polymarket, Kalshi, apt, certbot, CrowdSec central, anything else on 443) exits via this proxy with Hillsboro's public IP (5.78.94.237, Hetzner).

LA INBOUND webhooks (Twilio voice/SMS callbacks, n8n workflow webhook URLs, ElevenLabs async callbacks) are NOT affected — they continue to land directly on LA's public IP via Vultr. Asymmetric by design — see `[[project-la-egress-isolation]]` in memory.

## Why tinyproxy and not Squid/3proxy

- Squid: full caching reverse-proxy. ~30 MB of features we won't use. Overkill.
- 3proxy: fine, but its config dialect is bespoke and harder to audit.
- tinyproxy: single-binary, ~100 KB, RFC-compliant HTTP proxy with CONNECT support for HTTPS. One config file, ~20 active lines. Right-sized.

## Threat model

tinyproxy binds to `10.8.0.6:8888` — Hillsboro's WG tunnel IP — never to the public NIC. Public-internet access to the proxy is blocked at two layers:

1. **Bind address.** The socket is on the WG interface only; the public NIC (enp1s0 with IP 5.78.94.237) never has a listening socket on 8888.
2. **UFW.** `ufw allow from 10.8.0.0/24 to 10.8.0.6 port 8888 proto tcp` — only the BHN mesh can reach it.

If both layers fail, tinyproxy still requires the request originate from `Allow 10.8.0.0/24` per its own ACL.

`DisableViaHeader Yes` strips the proxy-identifying header so external APIs don't see they're being proxied. `ConnectPort 443` limits HTTPS CONNECT tunneling to standard TLS — we're not proxying SSH or other protocols.

## Deploy

```bash
# On Hillsboro, after bootstrap completes and WG tunnel to LA is up:
sudo mkdir -p /opt/bhn-tinyproxy
sudo cp infrastructure/services/tinyproxy/* /opt/bhn-tinyproxy/
cd /opt/bhn-tinyproxy
sudo bash install.sh
```

## Verify from LA

```bash
# Direct (without proxy) — should leak LA's public IP (149.28.91.100)
curl -fsS https://api.ipify.org && echo
# (After LA UFW rewrite blocks direct 443 egress, this will fail with timeout — that's the goal.)

# Via tinyproxy — should return Hillsboro's public IP (5.78.94.237)
curl -fsS -x http://10.8.0.6:8888 https://api.ipify.org && echo

# Anthropic reachability through proxy
curl -fsS -x http://10.8.0.6:8888 https://api.anthropic.com/v1/messages \
     -H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01" \
     -H "content-type: application/json" \
     -d '{"model":"claude-haiku-4-5-20251001","max_tokens":10,"messages":[{"role":"user","content":"ping"}]}' \
     | head -c 200; echo
```

## Wire LA to use the proxy

System-wide via `/etc/environment` (covers most processes that read env at start):

```bash
# On LA, as root:
cat >> /etc/environment <<'EOF'
http_proxy=http://10.8.0.6:8888
https_proxy=http://10.8.0.6:8888
HTTP_PROXY=http://10.8.0.6:8888
HTTPS_PROXY=http://10.8.0.6:8888
no_proxy=localhost,127.0.0.1,10.8.0.0/24,10.9.0.0/24
NO_PROXY=localhost,127.0.0.1,10.8.0.0/24,10.9.0.0/24
EOF
```

apt mirrors:

```bash
cat > /etc/apt/apt.conf.d/95bhn-proxy <<'EOF'
Acquire::http::Proxy "http://10.8.0.6:8888";
Acquire::https::Proxy "http://10.8.0.6:8888";
EOF
```

systemd services that don't inherit `/etc/environment` (n8n, grafana, postgresql) need drop-in units:

```bash
mkdir -p /etc/systemd/system/n8n.service.d
cat > /etc/systemd/system/n8n.service.d/proxy.conf <<'EOF'
[Service]
Environment=HTTP_PROXY=http://10.8.0.6:8888
Environment=HTTPS_PROXY=http://10.8.0.6:8888
Environment=NO_PROXY=localhost,127.0.0.1,10.8.0.0/24,10.9.0.0/24
EOF

systemctl daemon-reload
systemctl restart n8n
```

Repeat for grafana-server if it makes outbound calls (it does — alert webhooks, plugin updates).

## LA UFW rewrite — the partner change

Per `[[project-la-egress-isolation]]`, after tinyproxy is verified working, LA's outbound UFW rules collapse to:

```bash
# Remove old direct-egress whitelist (one at a time, verify nothing breaks)
ufw delete allow out 443/tcp        # was: HTTPS to apt/Anthropic/CrowdSec direct
ufw delete allow out 587/tcp        # was: SMTP submission direct
ufw delete allow out to ...         # see STATUS.md:37 for current list

# Add the only outbound rule LA still needs (besides WG underlay + intra-mesh):
ufw allow out to 10.8.0.6 port 8888 proto tcp comment 'egress via Hillsboro tinyproxy'

# Keep:
#   - 51821/udp to FRA  (WG underlay)
#   - 51820/udp to NJ, Hillsboro (WG underlay for hub-side listen)
#   - any to 10.8.0.0/24, 10.9.0.0/24  (intra-mesh)
```

Do this LAST, after the proxy is verified end-to-end. Pulling direct egress before the proxy is working will cut LA off from its API workloads.

## Monitoring

Watch `/var/log/tinyproxy/tinyproxy.log` for the first 24h after cutover. Spikes in 502/504 mean upstream APIs are unreachable from Hillsboro; spikes in 403 mean an API has flagged Hillsboro's IP (e.g. shared with Tor relay — see `tor-relay-hillsboro/README.md` coexistence note).

Future: ship `tinyproxy.log` into the existing log pipeline (`scripts/bhn-log-shipper.py`) so HORIZON can answer "is the egress proxy healthy?" from PG.
