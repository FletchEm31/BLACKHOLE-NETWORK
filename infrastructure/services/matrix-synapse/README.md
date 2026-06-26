# MatrixBHN — Internal Chat Service

**Protocol:** Matrix (Synapse homeserver)  
**Client:** Element (iOS / Android / Desktop / Web)  
**Status:** Live on LA hub  
**Access:** WireGuard mesh only — `http://10.8.0.1:8008`  
**DNS alias:** `http://chat.bhn.local:8008` (resolves via AdGuard on 10.8.0.1)

---

## Connection details

| Field | Value |
|-------|-------|
| Homeserver URL | `http://10.8.0.1:8008` |
| Server name | `BHN-LOSANGELES-US1.local` |
| Port | 8008 (HTTP, no TLS — mesh-internal only) |
| Federation | **Disabled** — no external Matrix servers |
| Registration | **Disabled** — accounts created by admin only |

**WireGuard must be active** before connecting. The homeserver binds to
`10.8.0.1` only and is not reachable from the public internet.

---

## Element client setup

1. Download Element: https://element.io/download
2. Open Element → **Sign in** → tap **Edit** next to the homeserver URL
3. Enter: `http://10.8.0.1:8008`
4. Sign in with your BHN credentials

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
| Database | `/var/lib/matrix-synapse/homeserver.db` (SQLite) |
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
# 8008 denied on public interface (already applied)
ufw deny in on enp1s0 to any port 8008

# 8008 allowed on WireGuard mesh
ufw allow in on wg0 to any port 8008
```
