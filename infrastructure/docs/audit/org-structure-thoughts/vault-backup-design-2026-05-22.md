# Cryptometer Vault Backup — Design Proposal (2026-05-22)

> **Status:** design doc for operator review. No code shipped yet.
> Saved to `org-structure-thoughts/` per operator's sole-writer rule — review/comment before any implementation lands in the repo.

---

## Design pivot (operator call, 2026-05-22)

**Original direction:** server runs daily pg_dump cron → stages artifacts in `/mnt/eh-hdd-cold/backup-staging/` → operator PC pulls on vault unlock.

**Revised direction:** **vault triggers the backup, not the other way.** Reason: the vault can be locked at any time. A server-scheduled cron firing into a locked-vault world either (a) drops the backup on the floor or (b) accumulates plaintext-at-rest on LA — both bad. Vault-initiated removes both problems.

**Consequences of the pivot:**
- ✅ Server has no plaintext-at-rest backup pressure (dumps are produced on-demand, streamed, deleted).
- ✅ Vault is the explicit consent surface — locked = no backup happens, which matches operator's mental model.
- ✅ Server never stages a known-cleartext dump that lives outside the vault.
- ⚠️ Cadence is "however often you unlock the vault." If you don't unlock for a week, no backup for a week. Mitigation: HORIZON nags you via SMS if last successful backup is > N days old.

---

## Architecture

```
                                  OPERATOR PC                                    LA HUB
                                  ───────────                                    ──────
   ┌────────────────────┐         ┌────────────────────────┐                  ┌─────────────────────┐
   │ Cryptomator        │ unlock  │ WMI subscription       │   SSH/rsync      │ bhn-backup-produce  │
   │ BHN-BLACKBOX vault │────────▶│ watching for E:\       │  over WG tunnel  │ .sh                 │
   │ (ciphertext on D:) │  →E:\   │ DeviceID arrival       │◀────────────────▶│                     │
   └────────────────────┘         │                        │                  │ pg_dump eventhorizon│
                                  │ bhn-vault-sync.ps1     │                  │ → zstd → stdout     │
                                  │  1. verify sentinel    │                  │                     │
                                  │  2. ping 10.8.0.1 (WG) │                  │ n8n workflow export │
                                  │  3. acquire lock       │                  │ → tar.zst → stdout  │
                                  │  4. for each artifact: │                  │                     │
                                  │     ssh + stream pull  │                  │ (artifacts ephemeral│
                                  │     verify sha256      │                  │  on /tmp, deleted   │
                                  │     write to E:\       │                  │  after stream)      │
                                  │  5. local-bundle repos │                  └─────────────────────┘
                                  │     from D:\GITHUB...  │
                                  │  6. log to E:\_log\    │
                                  │  7. release lock       │
                                  └────────────────────────┘
                                       │
                                       ▼
                          E:\ (cleartext vault root)
                          ├─ BLACKHOLE NETWORK-BACKUP\
                          ├─ PokemonBHN\…
                          ├─ StandaloneBHN\…
                          └─ _backup-log\
```

---

## Components

### 1. Trigger (operator PC)

**Mechanism:** WMI permanent event subscription on `__InstanceCreationEvent` filtered to `Win32_LogicalDisk WHERE DeviceID = 'E:'`.

```powershell
# Register-CimIndicationEvent (in scheduled task at user logon)
Register-CimIndicationEvent `
    -Query "SELECT * FROM __InstanceCreationEvent WITHIN 2 WHERE TargetInstance ISA 'Win32_LogicalDisk' AND TargetInstance.DeviceID = 'E:'" `
    -SourceIdentifier "BHN-Vault-Unlock" `
    -Action { Start-Process pwsh -ArgumentList "-NoProfile","-File","C:\BHN\bhn-vault-sync.ps1" }
```

**Why WMI not polling:** event-driven (fires within ~2s of E: appearing), no CPU burn between unlocks, survives sleep/wake.

**Sentinel check:** the script first asserts `Test-Path E:\.bhn-vault-identity` — if missing, abort (could be a USB drive that happened to mount as E:). The sentinel file is a one-line marker created during initial vault setup, never modified.

**Re-entrancy guard:** lock file at `E:\_backup-log\.sync-lock` containing PID. Stale lock (PID dead) is cleaned up. Concurrent unlock-relock-unlock doesn't fire two syncs.

**Debounce:** if `E:\_backup-log\last-success-timestamp` is < 1h old, log "recent sync, skipping" and exit. Force-run mode bypasses (manual invocation by operator).

### 2. Client script `bhn-vault-sync.ps1` (operator PC)

Lives at `C:\BHN\bhn-vault-sync.ps1`. **Not** in the BHN repo — it's operator-PC-specific config. Versioned via Cryptomator's own version history if desired.

Pseudocode:

```powershell
$VAULT = "E:\"
$LOG_DIR = "$VAULT\_backup-log"
$LOCK = "$LOG_DIR\.sync-lock"
$LA_SSH = "ssh root@10.8.0.1"

# 1. Sanity
if (-not (Test-Path "$VAULT\.bhn-vault-identity")) { Write-Error "Not BHN-BLACKBOX vault"; exit 2 }
if (-not (Test-Connection 10.8.0.1 -Count 2 -Quiet)) { LogAbort "WG tunnel not reachable"; exit 3 }

# 2. Debounce
$last = Get-Content "$LOG_DIR\last-success-timestamp" -ErrorAction SilentlyContinue
if ($last -and ((Get-Date) - [DateTime]$last).TotalHours -lt 1) {
    Log "Recent sync ($last), skipping"; exit 0
}

# 3. Lock
if (Test-Path $LOCK) {
    $stalePid = Get-Content $LOCK
    if (Get-Process -Id $stalePid -ErrorAction SilentlyContinue) { LogAbort "Sync already running (PID $stalePid)"; exit 4 }
}
$PID | Set-Content $LOCK

try {
    # 4. Server-pulled artifacts (manifest-driven)
    foreach ($a in $SERVER_ARTIFACTS) {
        $remote = "/tmp/bhn-backup-$(Get-Random).tmp"
        & $LA_SSH "bhn-backup-produce.sh $($a.id) $remote"
        $hash_expected = & $LA_SSH "sha256sum $remote | cut -d' ' -f1"
        & scp "root@10.8.0.1:$remote" "$VAULT\$($a.dest)\$($a.filename)"
        $hash_actual = (Get-FileHash "$VAULT\$($a.dest)\$($a.filename)" -Algorithm SHA256).Hash.ToLower()
        if ($hash_actual -ne $hash_expected) { throw "sha256 mismatch on $($a.id)" }
        & $LA_SSH "rm $remote"
        Log "✓ $($a.id) → $($a.filename) ($hash_actual)"
    }

    # 5. Local-bundle artifacts (no server roundtrip)
    foreach ($r in $LOCAL_REPOS) {
        if (-not (Test-Path $r.source)) { Log "skip $($r.id) (not on this PC)"; continue }
        $bundle = "$VAULT\$($r.dest)\$($r.id)-$(Get-Date -Format 'yyyyMMdd-HHmm').bundle"
        Push-Location $r.source
        & git bundle create $bundle --all
        Pop-Location
        Log "✓ $($r.id) → $bundle"
    }

    # 6. Success marker
    (Get-Date -Format "o") | Set-Content "$LOG_DIR\last-success-timestamp"
    Log "=== sync complete ==="

} finally {
    Remove-Item $LOCK -Force -ErrorAction SilentlyContinue
}
```

### 3. Server producer `bhn-backup-produce.sh` (LA hub)

Single producer, takes artifact ID + tmpfile path. Streams the result, never persists.

```bash
#!/usr/bin/env bash
# /usr/local/sbin/bhn-backup-produce.sh
set -euo pipefail
ARTIFACT="$1"
OUT="$2"

case "$ARTIFACT" in
  pg-eventhorizon)
    sudo -u postgres pg_dump -Fc eventhorizon | zstd -19 > "$OUT"
    ;;
  n8n-workflows)
    # Export all workflows via n8n CLI
    docker exec n8n n8n export:workflow --all --output=/tmp/n8n-export.json
    docker cp n8n:/tmp/n8n-export.json - | tar c -C /tmp n8n-export.json | zstd > "$OUT"
    docker exec n8n rm /tmp/n8n-export.json
    ;;
  bhn-repo-snapshot)
    # Server-side mirror clone (in case operator-PC repo gets clobbered)
    git -C /opt/bhn bundle create "$OUT" --all
    ;;
  *)
    echo "unknown artifact: $ARTIFACT" >&2; exit 1 ;;
esac

# sha256 emitted to stdout for client verification
sha256sum "$OUT" | cut -d' ' -f1
```

### 4. Artifact manifest

```powershell
$SERVER_ARTIFACTS = @(
    @{ id = "pg-eventhorizon"   ; dest = "BLACKHOLE NETWORK-BACKUP"; filename = "eventhorizon-$(Get-Date -Format yyyyMMdd-HHmm).dump.zst" },
    @{ id = "n8n-workflows"     ; dest = "BLACKHOLE NETWORK-BACKUP"; filename = "n8n-workflows-$(Get-Date -Format yyyyMMdd-HHmm).tar.zst" },
    @{ id = "bhn-repo-snapshot" ; dest = "BLACKHOLE NETWORK-BACKUP"; filename = "bhn-server-$(Get-Date -Format yyyyMMdd-HHmm).bundle" }
)

$LOCAL_REPOS = @(
    @{ id = "bhn-pc"           ; source = "D:\GITHUB REPOSITORY\BLACKHOLE-NETWORK"            ; dest = "BLACKHOLE NETWORK-BACKUP" },
    @{ id = "bteh"             ; source = "D:\GITHUB REPOSITORY\BTEH-Beyond-The-EventHorizon" ; dest = "IncubatorBHN\BEYOND THE HORIZON-BACKUP" },  # ❓ confirm domain
    @{ id = "team-rocket-bhn"  ; source = "D:\GITHUB REPOSITORY\TEAM ROCKET BHN"              ; dest = "PokemonBHN\POKEMON BLACKHOLE-TEAM ROCKET BHN-BACKUP" },
    @{ id = "bhnwave"          ; source = "D:\GITHUB REPOSITORY\BHNWAVE"                      ; dest = "StandaloneBHN\BHNwave-BACKUP" },
    @{ id = "blackbox-bidder"  ; source = "D:\GITHUB REPOSITORY\BLACKBOX-BIDDER"              ; dest = "PokemonBHN\BLACKBOX BIDDER-BACKUP" }   # repo not on PC yet — script will skip
)
```

> ❓ **DECIDE:** `BTEH-Beyond-The-EventHorizon` — your `BHN DOMAIN AND ORG.txt` puts BTEH under **SecurityBHN**, but the vault has `IncubatorBHN/BEYOND THE HORIZON-BACKUP/` and no folder under SecurityBHN. Which is correct? Three options:
> - (a) BTEH = SecurityBHN; rename vault folder to `SecurityBHN\BTEH-BACKUP\`
> - (b) BTEH = IncubatorBHN (audit framework is still in incubation); keep current vault folder
> - (c) "Beyond The Horizon" is a *different* project from BTEH, and BTEH gets its own folder later
>
> **Default in this proposal:** (b), to match the vault layout you've already built. Trivial to change.

> ⚠️ **NOT BACKED UP:** `D:\GITHUB REPOSITORY\HARPUR HERALDRY FAMILY PROJECT\` — this is on your PC but not a BHN project. Likely belongs in OPERATION TANGO or OPERATION ROMEO vault. Explicitly excluded from this manifest.

### 5. Vault sentinel file (one-time setup)

```powershell
# Create once after the vault is mounted at E:
"BHN-BLACKBOX vault — created 2026-05-22 — do not delete" | Set-Content "E:\.bhn-vault-identity"
New-Item -ItemType Directory -Path "E:\_backup-log" -ErrorAction SilentlyContinue
```

---

## Failure modes

| Failure | Behavior | Operator visibility |
|---------|----------|---------------------|
| WG tunnel down at unlock time | Abort, log, exit 3 | Log file `E:\_backup-log\sync-{TS}.log`, no banner. ❓ Want HORIZON SMS? |
| Server pg_dump fails | Abort that artifact, continue with others, exit 1 at end | Log + ❓ SMS |
| sha256 mismatch | Delete the corrupt local copy, abort that artifact | Log + ❓ SMS |
| Disk full on E: | Abort, log, exit 5 | Log + force banner via PowerShell `MessageBox` |
| Stale lock file (sync crashed last time) | Detect dead PID, reclaim lock | Log only |
| Vault unlocked within 1h of last success | Skip with "recent sync" log | Log only |
| Repo not present on PC (e.g. BLACKBOX-BIDDER yet to be cloned) | Skip that repo, continue | Log only |
| Sentinel file missing | Abort, exit 2 (probably wrong E: drive) | Log + force banner |

---

## Retention

**Default:** keep everything. Cryptomator's transparent encryption + cheap disk (BHN-BLACKBOX is on D:\ which is your operator PC drive — confirm how much free space?) means hoarding old dumps is fine until it isn't.

**When operator wants to prune:** a separate `bhn-vault-prune.ps1` (manually run, never automatic) keeps:
- `pg-eventhorizon`: last 30 dailies, last 12 month-ends, all year-ends
- `n8n-workflows`: last 12 weeklies
- `bhn-repo-snapshot`: last 8 (git history covers older)
- Local bundles: last 4 each (GitHub remote covers older)

> ❓ **DECIDE retention preference.** Default proposal: hoard until operator runs prune manually. Alternative: auto-prune to the schedule above. I lean hoard — encryption + cheap storage + you'll never wish you had fewer backups.

---

## Bootstrap order (implementation tasks)

If the design above is approved, here's the implementation order:

1. **One-time vault setup** (manual, operator)
   - Create `E:\.bhn-vault-identity` sentinel
   - Create domain folder skeleton (mostly already exists per vault screenshots)
   - Create `E:\_backup-log\`

2. **Server-side producer** (Task #5 today)
   - Write `/usr/local/sbin/bhn-backup-produce.sh` on LA
   - Test each artifact ID produces a valid dump
   - chmod 700, owned by root

3. **Client-side sync script** (Task #6 today)
   - Write `C:\BHN\bhn-vault-sync.ps1`
   - Test with `--dry-run` flag (lists what it would do, doesn't write)
   - Test end-to-end manual run

4. **Trigger registration** (Task #6 today)
   - WMI scheduled task at user logon
   - Test by locking + unlocking vault, confirm sync fires

5. **HORIZON staleness nag** (separate future task)
   - Daily check: if `E:\_backup-log\last-success-timestamp` (mirrored to LA via a small ping-on-success) > N days old, HORIZON SMSs operator: "Vault not unlocked for backup in N days."

---

## Open questions (consolidated)

1. **BTEH domain mapping** — SecurityBHN or IncubatorBHN? (see §4)
2. **HORIZON SMS on failure** — alert on each failure, or only on N consecutive failures, or never (log-only)?
3. **Retention default** — hoard vs auto-prune?
4. **Server-side bhn-repo snapshot** — do we *also* want a server-side mirror clone of the BHN repo, so a clobber on the operator PC is recoverable from LA? Or is "GitHub remote + operator PC working copy + local-bundle in vault" sufficient redundancy?
5. **First-sync size estimate** — pg_dump of `eventhorizon` (78 tables, financial + security data, ~weeks of history) is probably a few hundred MB compressed. Want me to do a sizing pass on LA when implementation starts?

---

## Naming for the implementation work

When this design is approved and you say go, the work breaks into:

- **Task #5** (`Implement EventHorizon Postgres → BHN backup folder dump job`) becomes "Implement `bhn-backup-produce.sh` on LA" — and the title shifts from "dump job" (sounds like a cron) to "on-demand producer."
- **Task #6** (`Implement WG-unlock-triggers-Vault-pull on operator PC`) stays as-is — covers `bhn-vault-sync.ps1` + WMI registration.

---

## Annotations key

- ❓ **DECIDE** — needs operator call before implementation
- ⚠️ **NOTE** — design choice flagged for awareness

Tally: 5 DECIDE questions, 1 NOTE.
