# MatrixBHN — Internal Chat Service

**Protocol:** Matrix (Synapse homeserver)  
**Client:** Element (iOS / Android / Desktop / Web)  
**Status:** Live on LA hub  
**Access:** WireGuard mesh only — HTTP `10.8.0.1:8008` / HTTPS `10.8.0.1:8448`  
**DNS alias:** `chat.bhn.local` → `10.8.0.1` (AdGuard rewrite)

---

## Connection details

| Field | Value |
|-------|-------|
| Homeserver URL (HTTPS) | `https://10.8.0.1:8448` (recommended — mobile Element) |
| Homeserver URL (HTTP) | `http://10.8.0.1:8008` (desktop Element, no cert warning) |
| Server name | `BHN-LOSANGELES-US1.local` |
| TLS cert | Self-signed, SAN=IP:10.8.0.1, expires 2036-06-23 |
| Federation | **Disabled** — no external Matrix servers |
| Registration | **Disabled** — accounts created by admin only |

**WireGuard must be active** before connecting. Both ports bind to `10.8.0.1`
only and are unreachable from the public internet.

### TLS cert trust (one-time, per device)

Element mobile will show a certificate warning on first connect — tap
**Proceed anyway** / **Trust**. The cert is self-signed for `10.8.0.1`
and stays valid until 2036. Desktop Element on HTTP 8008 avoids this entirely.

---

## Element client setup

**Desktop (HTTP — no cert prompt):**
1. Download Element: https://element.io/download
2. Open Element → **Sign in** → **Edit** homeserver URL
3. Enter: `http://10.8.0.1:8008`
4. Sign in with your BHN credentials

**Mobile (HTTPS — accept self-signed cert):**
1. Install Element mobile (iOS / Android)
2. Sign in → **Edit** homeserver URL
3. Enter: `https://10.8.0.1:8448`
4. Accept the self-signed certificate warning
5. Sign in with your BHN credentials

---

## Admin accounts

| Username | Admin |
|----------|-------|
| `@superiorlefthand88:BHN-LOSANGELES-US1.local` | yes |
| `@fletchem88:BHN-LOSANGELES-US1.local` | yes |

Credentials stored in Proton Pass → `BHN-Matrix-Synapse-LA`.

---

## Create a new user (admin only)

Run on LA:
```bash
/opt/venvs/matrix-synapse/bin/register_new_matrix_user \
  -u <username> -p <password> -a \
  -c /etc/matrix-synapse/homeserver.yaml \
  http://10.8.0.1:8008
```

Remove `-a` for non-admin accounts.

---

## Guest / visitor onboarding

See `infrastructure/docs/bhn-guest-wireguard-onboarding.md` for the full
WireGuard config template and MatrixBHN connection steps sent to new peers.

Guest peers use `10.8.0.10+` and receive a **split-tunnel config** by default:
DNS routes through WireGuard (AdGuard protection), all other traffic uses
the guest's own ISP directly.

---

## Service config

| File | Location |
|------|----------|
| Main config (secrets redacted) | `infrastructure/services/matrix-synapse/homeserver.yaml` |
| Live config | `/etc/matrix-synapse/homeserver.yaml` on LA |
| Database | `/mnt/eh-nvme-hot/matrix-synapse/homeserver.db` (SQLite, LUKS2 encrypted) |
| TLS cert | `/etc/matrix-synapse/tls.crt` |
| TLS key | `/etc/matrix-synapse/tls.key` |
| Signing key | `/etc/matrix-synapse/homeserver.signing.key` |
| Logs | `journalctl -u matrix-synapse -f` |

```bash
# Restart
systemctl restart matrix-synapse

# Status
systemctl status matrix-synapse

# View users
sqlite3 /var/lib/matrix-synapse/homeserver.db \
  "SELECT name, admin, creation_ts FROM users;"
```

---

## UFW rules

```bash
# HTTP 8008 — WireGuard mesh only
ufw allow in on wg0 to any port 8008 proto tcp
ufw deny in on enp1s0 to any port 8008 proto tcp

# HTTPS 8448 — WireGuard mesh only
ufw allow in on wg0 to any port 8448 proto tcp
ufw deny in on enp1s0 to any port 8448 proto tcp
```
