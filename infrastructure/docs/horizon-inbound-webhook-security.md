# HORIZON — Inbound Webhook Security (Item 1)

How HORIZON receives inbound webhooks from external services (Twilio, possibly eBay, possibly Stripe later) without re-opening LA's general public surface.

## Problem

Some HORIZON modules need inbound HTTP from named third-party services:

| Source | Use case | Verifiable? |
|--------|----------|-------------|
| **Twilio** | inbound SMS callback (M1 confirmation loop), inbound voice webhook (M7 reverse calls) | Yes — HMAC `X-Twilio-Signature` per [Twilio docs](https://www.twilio.com/docs/usage/webhooks/webhooks-security) |
| **eBay** | listing notifications, message events (M5) | Yes — HMAC sig + `client_id` echo |
| **OAuth callbacks** | Google Calendar consent flow (M9) | Yes — short-lived nonce |

LA's current public surface (after today's hardening): SSH/22 + WG/51820 + Shadowsocks/8388. HTTP/HTTPS taken offline. Re-opening 80/443 broadly resurrects the attack surface we just removed.

## Architecture

**Defense in depth, four layers:**

```
Twilio servers
   │  (1) Source IP allowlist  ─ UFW
   ▼
LA public IP, port 443
   │  (2) TLS termination      ─ nginx (re-enabled, NARROW server block)
   │  (3) Path allowlist       ─ nginx location /horizon/twilio/* only
   ▼
n8n webhook endpoint, internal
   │  (4) HMAC signature       ─ n8n Code node validates X-Twilio-Signature
   ▼
HORIZON workflow handles event
```

Each layer is independently sufficient. Together they make exploitation of misconfiguration (e.g., HMAC validation accidentally disabled) much harder.

## Layer 1 — UFW source IP allowlist

Twilio publishes its [outbound IP ranges](https://www.twilio.com/docs/voice/ip-addresses) — currently US East/West /24s. They rotate occasionally; revisit quarterly or when Twilio sends an EOL notice.

```bash
# To apply when Twilio account is provisioned. NOT applied yet.
# Keep these as comments in /etc/ufw/applications.d/twilio.rules and uncomment
# at deploy time.

ufw allow proto tcp from 54.252.254.64/26       to any port 443  comment 'twilio-au-east-1'
ufw allow proto tcp from 54.169.127.128/26      to any port 443  comment 'twilio-ap-southeast-1'
ufw allow proto tcp from 54.171.127.192/26      to any port 443  comment 'twilio-eu-west-1'
ufw allow proto tcp from 177.71.206.192/26      to any port 443  comment 'twilio-sa-east-1'
ufw allow proto tcp from 54.244.51.0/24         to any port 443  comment 'twilio-us-west-2'
ufw allow proto tcp from 54.172.60.0/23         to any port 443  comment 'twilio-us-east-1'
# Verify against https://www.twilio.com/docs/voice/ip-addresses at apply time —
# Twilio adjusts ranges; the snapshot above is from 2026-Q2 docs.
```

**This is NOT `0.0.0.0/0` on port 443.** Public scanners get TCP-refused at the edge, not just rate-limited.

## Layer 2 — nginx (narrow re-enable)

Re-enable `nginx` on LA but with a single, narrow `server` block — just the Twilio webhook subdomain. The general landing-page server stays disabled.

```nginx
# /etc/nginx/sites-available/horizon-webhook (sites-enabled symlink ONLY this one)
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name webhook.eventhorizonvpn.com;       # NEW DNS A record needed (operator action)

    ssl_certificate     /etc/letsencrypt/live/webhook.eventhorizonvpn.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/webhook.eventhorizonvpn.com/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;

    # Reject everything except the Twilio paths we expose
    location / {
        return 410;
    }

    location /horizon/twilio/ {
        # Forward to n8n webhook. n8n is bound to 10.8.0.1:5678 (VPN-only) —
        # this nginx server block is the ONLY thing on LA reaching across that
        # boundary, so the n8n webhook namespace stays VPN-protected from
        # everything except Twilio (which is gated by Layer 1 + Layer 4).
        proxy_pass         http://10.8.0.1:5678;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   X-Twilio-Signature $http_x_twilio_signature;
    }
}

# HTTP redirect for cert renewal only
server {
    listen 80;
    listen [::]:80;
    server_name webhook.eventhorizonvpn.com;
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://$host$request_uri; }
}
```

Operator action: create the `webhook.eventhorizonvpn.com` DNS A record pointing at `149.28.91.100`. Then `certbot --nginx -d webhook.eventhorizonvpn.com` to provision the cert.

The OLD `eventhorizonvpn.com` DNS A record stays deleted (we removed it 2026-05-09). The new `webhook.` subdomain is the only thing that resolves to LA publicly.

## Layer 3 — Path allowlist

Already enforced in the nginx config above. `/` returns 410, `/horizon/twilio/*` proxies, everything else is 404. No `try_files`, no automatic file serving.

## Layer 4 — HMAC validation in n8n

Twilio signs every webhook request with `X-Twilio-Signature`. The signature is computed as `HMAC-SHA1(auth_token, full_url + sorted_params)`. n8n's webhook receives the request; a Code node validates before any further processing.

```javascript
// n8n Code node — runs immediately after the Webhook node.
// Inputs: $json.headers, $json.body (parsed) or $json.params (form-encoded)
// Output: $json (passed through) if valid, throws if not.

const authToken = $env.TWILIO_AUTH_TOKEN;          // n8n credential, NOT in workflow JSON
if (!authToken) throw new Error('TWILIO_AUTH_TOKEN not in env');

const sig = $json.headers['x-twilio-signature'];
if (!sig) throw new Error('missing X-Twilio-Signature header');

// Build the signing string per Twilio spec
const fullUrl = `https://webhook.eventhorizonvpn.com${$json.path || ''}`;
const params  = $json.body || {};
const sortedKeys = Object.keys(params).sort();
let signingString = fullUrl;
for (const k of sortedKeys) signingString += k + (params[k] || '');

// Compute expected signature
const crypto = require('crypto');
const expected = crypto
  .createHmac('sha1', authToken)
  .update(signingString)
  .digest('base64');

// Constant-time compare
if (!crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected))) {
  throw new Error('Twilio signature mismatch');
}

return $json;
```

Reject reasons get logged to `node_logs` (source='twilio_webhook_reject') so we can detect anyone hitting the endpoint with bad sigs (which would mean Layer 1 leaked or Twilio rotated IPs without notice).

## When this gets activated

Activation order during M1 buildout:
1. Operator creates `webhook.eventhorizonvpn.com` DNS A record.
2. nginx config landed (sites-enabled symlink) + cert provisioned via certbot.
3. UFW rules applied for Twilio source ranges.
4. n8n webhook nodes created with HMAC validator code node prepended.
5. Twilio number configured to POST to `https://webhook.eventhorizonvpn.com/horizon/twilio/sms` and `/horizon/twilio/voice`.
6. End-to-end test: send SMS to Twilio number, verify n8n receives + signature validates + workflow fires.

Until step 1, none of this is live. Configs sit staged in `/etc/nginx/sites-available/horizon-webhook` (mode 0644, NOT symlinked into sites-enabled) so accidental `systemctl restart nginx` doesn't expose anything.

## Future: eBay webhooks (M5)

Same pattern. Different paths under `/horizon/ebay/*`, different HMAC algorithm (eBay uses HMAC-SHA1 with a different signing string), different source IP set. Layer 1-4 design extends cleanly.

## Future: Cloudflare Tunnel as alternative

If the operator ever wants to avoid having ANY public port on LA, Cloudflare Tunnel can replace Layers 1-2: `cloudflared` runs on LA, opens an outbound connection to Cloudflare's edge, and Cloudflare proxies inbound webhook traffic through that tunnel. Trade-offs:
- ✅ No public LA port at all
- ✅ Cloudflare's WAF + DDoS protection in front
- ⚠️ Adds Cloudflare as a trust dependency (TLS termination + traffic visibility)
- ⚠️ Tunnel binary runs as a daemon — small attack surface increase on LA itself
- ⚠️ Free tier is fine for our volume; behavior under heavy load not tested

Defer this decision to actual deploy time; nginx + UFW + HMAC is sufficient for the operator-only volume HORIZON will see.
