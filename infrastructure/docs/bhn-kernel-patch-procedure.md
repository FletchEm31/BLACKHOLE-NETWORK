# BHN Kernel Patch — Safe Upgrade Procedure

Authoritative runbook for applying kernel updates (CVE patches, routine maintenance) to BHN nodes. Originally written for CVE-2026-31431 ("Copy Fail") in May 2026; designed to be the template for any future kernel patching on BHN.

**Always run this end-to-end, even for "routine" patches.** The blanket `apt upgrade -y && reboot` will eat the eventhorizon PostgreSQL cluster on LA the first time it hits a major-version jump in apt.

---

## When to use this

- Vendor security advisory affecting Linux kernel (CVE-2026-XXXXX class)
- Scheduled maintenance window for general apt upgrade
- After Vultr emails about patched kernels available
- Routine quarterly hygiene

## Risk model — what can go wrong

| Risk | Why | Mitigation in this runbook |
|------|-----|----------------------------|
| Major-version PG upgrade silently applied | LA has `postgresql-18` sitting in apt's pending list while cluster runs on `postgresql-14`. Blanket `apt upgrade -y` runs `pg_upgrade` implicitly + destroys the eventhorizon DB | **Pin postgresql packages BEFORE any upgrade** (Phase 2 below) |
| Forwarding rules lost across reboot | Some `iptables -P` policies / NAT rules don't persist by default | n/a — BHN's UFW + iptables-persistent config survives reboots (verified in past kernel reboots) |
| n8n container fails to auto-restart | If Docker daemon updated mid-flight | Check `docker ps` post-reboot; `--restart unless-stopped` policy should bring it back |
| WG tunnels don't re-handshake | Rare; usually handshakes within 30s of network coming up | Post-reboot verify includes `wg show` handshake check |
| Kernel package didn't actually update | apt's available kernel may already be installed | Phase 5 checks `uname -r` before/after; if no change, document why |

## Pre-requisites (one-time, both nodes)

```bash
# On each node, confirm prerequisites exist
mkdir -p /mnt/eh-hdd-cold/backups   # LA only — Frankfurt has no PG so no dumps
ls -ld /mnt/eh-hdd-cold/backups
df -h /mnt/eh-hdd-cold | tail -1    # ensure space for PG dump

# Verify apt-mark + dpkg-query work
which apt-mark dpkg-query
```

---

## Phase 1 — Pre-flight snapshot

Captures current state for diff verification later + PG safety net.

```bash
# === Run on LA ===
TS=$(date +%Y%m%d-%H%M)
uname -r > /tmp/kernel-pre.txt
cat /tmp/kernel-pre.txt

# PG safety net (LA only)
sudo -u postgres pg_dumpall --globals-only \
  | gzip > /mnt/eh-hdd-cold/backups/pg-globals-pre-patch-${TS}.sql.gz
sudo -u postgres pg_dump -Fc -d eventhorizon \
  -f /mnt/eh-hdd-cold/backups/eventhorizon-pre-patch-${TS}.dump
ls -lh /mnt/eh-hdd-cold/backups/*pre-patch-${TS}*

# n8n DB snapshot (LA only)
cp /root/.n8n/database.sqlite /root/.n8n/database.sqlite.snap-pre-patch-${TS}
ls -lh /root/.n8n/database.sqlite.snap-pre-patch-${TS}

# Current service inventory for post-reboot diff
systemctl list-units --type=service --state=active --no-pager > /tmp/services-pre.txt
wg show all > /tmp/wg-pre.txt
docker ps --format '{{.Names}}\t{{.Status}}' > /tmp/docker-pre.txt 2>/dev/null
```

```bash
# === Run on Frankfurt ===
TS=$(date +%Y%m%d-%H%M)
uname -r > /tmp/kernel-pre.txt
systemctl list-units --type=service --state=active --no-pager > /tmp/services-pre.txt
wg show all > /tmp/wg-pre.txt
# No PG, no n8n on Frankfurt — skip those snapshots
```

**Stop if any command above errors.** Don't proceed to Phase 2 until snapshots verify.

---

## Phase 2 — Pin PostgreSQL (defensive both nodes; load-bearing on LA)

```bash
# Run on BOTH nodes — safe no-op if no postgresql packages installed
apt update

# Discover all installed postgresql-* packages
dpkg-query -W -f='${Package}\n' | grep -E '^postgresql(-|$)' | tee /tmp/pg-installed.txt

# Hold them — apt cannot upgrade or remove these until explicitly unheld
if [ -s /tmp/pg-installed.txt ]; then
  apt-mark hold $(cat /tmp/pg-installed.txt)
fi
apt-mark showhold
```

On LA you should see `postgresql`, `postgresql-14`, `postgresql-client-14`, `postgresql-client-common`, `postgresql-common`, `postgresql-contrib`, `postgresql-14-pgvector` held. On Frankfurt the list is likely empty (no PG installed) — that's fine.

Holds survive reboots. They're a dpkg state, not a session setting.

---

## Phase 3 — Dry-run + human review gate

```bash
# Run on each node
apt-get upgrade --simulate 2>&1 | tee /tmp/apt-dryrun.txt

# Quick summary
echo "--- Packages to install/upgrade ---"
grep -E '^Inst' /tmp/apt-dryrun.txt | head -40
echo
echo "--- Packages held back (good — postgresql should be here on LA) ---"
grep -A30 'kept back' /tmp/apt-dryrun.txt | head -30
echo
echo "--- Packages to REMOVE (any here is worth pausing over) ---"
grep -E '^Remv' /tmp/apt-dryrun.txt
```

**STOP here.** Inspect `/tmp/apt-dryrun.txt` against these criteria before proceeding:

| Check | Must be true |
|-------|--------------|
| All `postgresql-*` packages appear under "kept back" on LA | ✅ |
| Any `linux-image-*` package appears under Inst | (only if CVE fix is in apt; otherwise see Phase 5) |
| No unexpected `Remv` lines | ✅ |
| No surprising new dependencies (Docker major version bump, glibc, etc.) | Document if so, but usually fine |

Paste the dry-run output back into chat — I'll verify before green-lighting Phase 4.

---

## Phase 4 — Apply

```bash
# Use `upgrade` (NOT `full-upgrade` / `dist-upgrade`) — those can override holds in rare cases
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y

# Remove obsolete packages + old kernel images so /boot doesn't fill
apt-get autoremove -y --purge

# Confirm PG is STILL on 14 (LA only)
if dpkg -l | grep -q '^ii\s\+postgresql-14\s'; then
  echo "✓ PG still on 14: $(dpkg -l | grep '^ii\s\+postgresql-14\s' | awk '{print $2,$3}')"
else
  echo "PG check skipped (no postgresql-14 — expected on Frankfurt)"
fi

# What kernels are present for next boot?
ls -1 /boot/vmlinuz-* | sort -V
```

**Hard stop:** if `dpkg -l` shows `postgresql-18` anywhere after the upgrade, do NOT reboot. The pin was overridden. Investigate before continuing.

---

## Phase 5 — CVE confirmation

For the current advisory (CVE-2026-31431), or any specific CVE:

```bash
# Look for the CVE ID in the running kernel's changelog
apt changelog linux-image-$(uname -r) 2>/dev/null \
  | grep -iE 'CVE-2026-31431|copy.?fail' | head -5

# Also check the generic kernel package
apt-get changelog linux-image-generic 2>/dev/null \
  | grep -iE 'CVE-2026-31431' | head -5
```

Outcomes:
- **Hits found**: backport is in the current kernel package; reboot will activate it (kernel modules reload from the new package)
- **No hits + Phase 4 upgraded `linux-image-*`**: new kernel installed; reboot loads it; changelog may just lag
- **No hits + nothing upgraded**: either (a) the CVE was already patched in a prior point release and `algif_aead` blacklist mitigation is sufficient (per BHN's CVE-2026-31431 memory), or (b) Canonical hasn't shipped the patch yet via standard apt — check Ubuntu Security Notices manually

Document the outcome in the post-patch memory update.

---

## Phase 6 — Reboot

```bash
echo "Patch reboot at $(date -u) — CVE-2026-31431 / routine kernel maintenance" \
  >> /var/log/bhn-reboots.log

# Reboot (severs SSH; that's expected — wait 60-90s before reconnecting)
systemctl reboot
```

For the reboot timing across both nodes: **do LA first, fully verify (Phase 7), then Frankfurt.** Never reboot both simultaneously — one always stays as a known-good vantage point for diagnosing the other.

---

## Phase 7 — Post-reboot verification

```bash
# Run after SSH is back (60-90s post-reboot)

echo "=== Kernel version diff ==="
echo "Before: $(cat /tmp/kernel-pre.txt)"
echo "After:  $(uname -r)"

echo
echo "=== Core services ==="
for svc in postgresql wg-quick@wg0 wg-quick@wg1 docker eh-embed dnscrypt-proxy fail2ban suricata crowdsec ufw; do
  printf '%-22s %s\n' "$svc" "$(systemctl is-active $svc 2>/dev/null)"
done

echo
echo "=== n8n container (LA only) ==="
docker ps --filter name=n8n --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null
curl -sS -o /dev/null -w "n8n healthz: %{http_code}\n" http://10.8.0.1:5678/healthz 2>/dev/null

echo
echo "=== WireGuard handshakes ==="
wg show all latest-handshakes

echo
echo "=== PostgreSQL — still on 14, eventhorizon DB intact (LA only) ==="
sudo -u postgres psql -d eventhorizon -c "SELECT version();" 2>/dev/null
sudo -u postgres psql -d eventhorizon -c "
  SELECT 'pulse'   AS t, COUNT(*) FROM pulse_reports
  UNION ALL SELECT 'weather', COUNT(*) FROM weather_snapshots
  UNION ALL SELECT 'news',    COUNT(*) FROM news_articles
  UNION ALL SELECT 'memories',COUNT(*) FROM memories;" 2>/dev/null

echo
echo "=== PG pins still in place ==="
apt-mark showhold | sort

echo
echo "=== Timers (eh-*/bhn-*) ==="
systemctl list-timers --all --no-pager | grep -E 'eh-|bhn-|NEXT' | head -15

echo
echo "=== Embed service (LA only) ==="
curl -sS -m 5 -X POST http://127.0.0.1:8001/embed \
  -H 'content-type: application/json' -d '{"text":"post-reboot probe"}' \
  | jq -r '"embed dim=" + (.vector | length | tostring)' 2>/dev/null

echo
echo "=== End-to-end (LA only): manual pulse trigger from n8n UI, then ==="
sudo -u postgres psql -d eventhorizon -c "
  SELECT id, period_end, important, model_used
  FROM pulse_reports ORDER BY generated_at DESC LIMIT 1;" 2>/dev/null
```

**Pass criteria** for declaring the patch successful:

| Check | Expected |
|-------|----------|
| `uname -r` | Newer than pre OR unchanged with documented reason |
| All core services | `active` |
| n8n container (LA) | `Up`, healthz returns 200 |
| WG peers | Handshake within 2 min of reboot |
| PG (LA) | Version still 14.x; row counts ≥ pre-reboot |
| PG holds | Still in `apt-mark showhold` |
| Timers | All `NEXT` populated with future times |
| Embed (LA) | Returns `dim=384` |
| Manual pulse | New row in pulse_reports |

If any check fails: stop, diagnose before proceeding to the second node.

---

## Per-node specifics

### LA (149.28.91.100, hub)

- **Stakes:** Highest. Holds PG cluster + n8n + HORIZON + all BHN orchestration state.
- **PG pin:** Mandatory. Always Phase 2.
- **Backup before:** `pg_dumpall --globals` + `pg_dump -Fc eventhorizon` + n8n sqlite snapshot. Phase 1 covers this.
- **Reboot expected downtime:** ~60-90s for SSH back, +30s for services + n8n + WG handshakes to settle.
- **Order:** Always patch LA first.

### Frankfurt (192.248.187.208, exit + privacy node)

- **Stakes:** Lower. Exit-only routing role; no persistent state critical to BHN's operation.
- **PG pin:** Defensive no-op (no PG installed).
- **Backup before:** None needed; nothing persistent worth backing up.
- **Reboot expected downtime:** ~60-90s for SSH back. WG tunnel re-handshake from LA side happens automatically.
- **Order:** After LA is fully verified.
- **SSH:** Via `ssh frankfurt` from LA (wg1 tunnel) OR Vultr console fallback. NO direct public-IP SSH (Vultr cross-region TCP block).

### NJ (140.82.4.35, trading node) — when applicable

- **Stakes:** Medium. Trading workloads (when live) tolerate downtime if scheduled.
- **PG pin:** Defensive (no PG on NJ as of 2026-05-12).
- **Backup before:** Trading state — once it exists, snapshot before patching.
- **Reboot expected downtime:** ~60-90s. WG tunnel re-handshakes via LA's wg0 hub.
- **Order:** After LA + Frankfurt verified.
- **SSH:** `ssh nj` from LA (wg0 tunnel) OR direct `ssh -p 2222 root@140.82.4.35` from operator's PC.

---

## Historical lessons (don't re-learn these)

### LA's PostgreSQL 14→18 trap (caught 2026-05-10)

- Routine `apt list --upgradable` showed `postgresql-18` sitting in pending.
- Blanket `apt upgrade -y` would have run `pg_upgrade` implicitly + destroyed the eventhorizon DB.
- **Mitigation:** Phase 2 pins all postgresql packages first. Confirmed working — LA stayed on 14.x across the May 10 reboot.
- **PG major-version migration is a separate project**, not an apt-upgrade ride-along. When ready, requires: `pg_upgradecluster` or dump/restore + pgvector extension reinstall + HORIZON + n8n verify cycle. Scope its own session.

### LA kernel 5.15.0-177 was already CVE-2026-31431-patched (per `algif_aead` blacklist mitigation)

- No `linux-image-*` was in apt's pending list at the time of the May 10 reboot.
- The mitigation (blacklisting the `algif_aead` kernel module) was already in place on both LA and Frankfurt.
- Full CVE patch (real kernel update with backport) may already be in 5.15.0-177; check Phase 5 changelog grep to confirm.

### libpq5 client library can be newer than PG server major version

- `libpq5 18.x` against a `postgresql-14` server cluster is supported and routine (libpq forward-compatibility is explicitly guaranteed by Postgres).
- n8n's pg driver is pure JS (doesn't even use libpq), so the libpq major bump doesn't affect n8n.
- Safe to let libpq5 upgrade alongside other packages even with PG pinned.

---

## Document the run

After a successful patch cycle, update:

1. `STATUS.md` — node sections, "Kernel" or "Last patched" row
2. Memory — `project_cve_<id>_kernel_patch.md` (mark as applied + retire if CVE is closed)
3. `node_logs` table on LA — INSERT a row tagged `source='kernel-patch'` for the audit trail

The audit-trail entry helps HORIZON answer "when was this node last patched" via `query_db`.
