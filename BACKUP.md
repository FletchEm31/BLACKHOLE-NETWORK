# Blackhole Network (BHN) — Backup & Restore

> **Note:** LA-deployed script and path references (e.g. `eh-backup`, `/root/.eh-backup.env`, `/etc/cron.d/eh-backup`) reflect current operational state. LA migration to `bhn-*` paths is deferred to a separate coordinated session — see `project_blackhole_network_rename` memory.

## What's backed up

Daily encrypted snapshot of the LA hub:

| Artifact | Source | Notes |
|----------|--------|-------|
| `pg_globals.sql` | `pg_dumpall --globals-only` | Roles + scram password hashes |
| `eventhorizon.dump` | `pg_dump -Fc eventhorizon` | Custom format, compressed, parallel-restorable |
| `n8n-database.sqlite` | `sqlite3 .backup` of `/root/.n8n/database.sqlite` | Hot copy, no n8n downtime |
| `n8n-files.tar.zst` | `/root/.n8n` (config + nodes + storage) | Excludes WAL/SHM, event logs, `.bak` files. **Contains the n8n encryption key (`config`) — required to decrypt credentials on restore.** |

Encryption: restic AES-256-CTR + Poly1305. Repo password lives in `/root/.eh-backup.env` (mode 0600, root-only) on LA, and in the operator's password manager.

Retention: 7 daily + 4 weekly + 6 monthly snapshots.

## Schedule

`/etc/cron.d/eh-backup` on LA:

- Daily backup at **02:30 UTC**
- Weekly integrity check (10% read-data) **Sundays 03:30 UTC**

Logs: `/var/log/eh-backup.log` (logrotate weekly, 8 weeks retained).

## Operations

```bash
eh-backup                # full run (cron uses this)
eh-backup status         # show snapshots + repo size
eh-backup check          # restic integrity check
eh-backup restore-test   # extract latest to /tmp for inspection
```

## Current storage target

`/mnt/eh-hdd-cold/backup-restic` (LUKS2-encrypted HDD on LA).

This is **on-host**, not offsite. It survives disk corruption but not host loss. Move to Hetzner Storage Box before treating it as a real DR plan.

## Switching to Hetzner Storage Box (offsite)

1. Provision a Hetzner Storage Box via the Hetzner robot panel.
2. On LA, generate a dedicated SSH key for the box:
   ```bash
   ssh-keygen -t ed25519 -f /root/.ssh/hetzner_storagebox -N "" -C "eh-la-backup"
   ```
3. Upload the public key to the Storage Box (Hetzner robot panel → Storage Box → Sub-accounts → SSH keys).
4. Add to `/root/.ssh/config`:
   ```
   Host hetzner-sb
       Hostname uXXXXXX.your-storagebox.de
       User uXXXXXX
       IdentityFile /root/.ssh/hetzner_storagebox
       Port 23
   ```
5. Test: `sftp hetzner-sb` then `mkdir backup-restic`.
6. Edit `/root/.eh-backup.env`:
   ```
   RESTIC_REPOSITORY=sftp:hetzner-sb:backup-restic
   ```
   (Keep `RESTIC_PASSWORD` unchanged — same password works.)
7. Re-init the repo on the new target:
   ```bash
   set -a; . /root/.eh-backup.env; set +a
   restic init
   ```
8. First run will be a full upload (~7 MiB today, will grow with PG).
9. The local repo at `/mnt/eh-hdd-cold/backup-restic` can be kept as a hot fallback or deleted.

Outbound port required: **23/tcp** to the Storage Box host. Add to LA UFW egress allowlist.

## Restoring

```bash
set -a; . /root/.eh-backup.env; set +a
restic snapshots
restic restore <snapshot-id> --target /tmp/restore
```

Then:
- PG: `createdb eventhorizon && pg_restore -d eventhorizon /tmp/restore/.../eventhorizon.dump`
- PG roles: `psql -f /tmp/restore/.../pg_globals.sql` (run before the restore above if rebuilding from scratch)
- n8n: stop n8n, replace `/root/.n8n/database.sqlite`, restore `config` from `n8n-files.tar.zst`, start n8n
