#!/bin/bash
# BHN (Blackhole Network) — kernel patch runbook for CVE-2026-31431 ("Copy Fail").
#
# Run on LA. Captures pre-reboot state, applies updates, prints kernel
# version diff, then prompts for reboot. Designed so an interrupted run is
# safe — apt is transaction-safe, snapshot is taken first.
#
# Pair with bhn-post-reboot-verify.sh which runs after reboot to confirm all
# services came back up.

set -euo pipefail

SNAP_DIR=/root/.n8n
PRE_LOG=/var/log/eh-kernel-patch-pre-$(date +%Y%m%d-%H%M).log

echo '======================================================================'
echo "EH KERNEL PATCH — $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "Pre-reboot capture log: $PRE_LOG"
echo '======================================================================'

# ---------- Pre-snapshot ----------
echo
echo '----- 1. n8n DB snapshot (rollback point) -----'
SNAP="${SNAP_DIR}/database.sqlite.snap-prereboot-$(date +%Y%m%d-%H%M)"
cp "${SNAP_DIR}/database.sqlite" "$SNAP"
ls -la "$SNAP"

# ---------- Pre-reboot state capture ----------
{
  echo "=== Pre-reboot state captured at $(date) ==="
  echo
  echo '--- kernel ---'
  uname -r
  echo
  echo '--- uptime ---'
  uptime
  echo
  echo '--- core service states ---'
  for svc in postgresql wg-quick@wg0 wg-quick@wg1 docker eh-embed dnscrypt-proxy fail2ban suricata crowdsec ufw eh-nightly-diagnostic.timer; do
    printf '%-32s %s\n' "$svc" "$(systemctl is-active $svc 2>/dev/null)"
  done
  echo
  echo '--- docker containers ---'
  docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
  echo
  echo '--- wireguard ---'
  wg show
  echo
  echo '--- ufw ---'
  ufw status verbose | head -25
  echo
  echo '--- listening sockets ---'
  ss -tlnp | head -20
  echo
  echo '--- disk usage ---'
  df -h /
  df -h /mnt/eh-nvme-hot 2>/dev/null
  df -h /mnt/eh-cold 2>/dev/null
  echo
  echo '--- algif_aead module loaded? ---'
  lsmod | grep -i algif || echo '(blacklisted/not loaded)'
} | tee "$PRE_LOG"

# ---------- apt update ----------
echo
echo '======================================================================'
echo '2. apt update (refreshes package index, no actual upgrades yet)'
echo '======================================================================'
apt-get update

# ---------- See what would upgrade ----------
echo
echo '======================================================================'
echo '3. Pending upgrades (preview before applying)'
echo '======================================================================'
apt list --upgradable 2>/dev/null | grep -v 'Listing...'

# Check if linux-image (kernel) is in the list
KERNEL_UPGRADE=$(apt list --upgradable 2>/dev/null | grep -E '^linux-(image|generic|headers)' | head -3)
if [ -n "$KERNEL_UPGRADE" ]; then
  echo
  echo 'KERNEL UPGRADE DETECTED:'
  echo "$KERNEL_UPGRADE"
  echo '(This is the CVE-2026-31431 patch.)'
else
  echo
  echo 'NO KERNEL UPGRADE in pending list — this run will only install non-kernel updates.'
  echo 'CVE patch may already be applied OR not yet available in the repo.'
fi

# ---------- apt upgrade ----------
echo
echo '======================================================================'
echo '4. apt upgrade (applying)'
echo '======================================================================'
DEBIAN_FRONTEND=noninteractive apt-get -y upgrade

# ---------- Post-upgrade kernel check ----------
echo
echo '======================================================================'
echo '5. Post-upgrade state'
echo '======================================================================'
echo "Currently RUNNING kernel: $(uname -r)"
echo "Latest INSTALLED kernel: $(ls -t /boot/vmlinuz-* | head -1 | sed 's|/boot/vmlinuz-||')"
echo
if [ "$(uname -r)" = "$(ls -t /boot/vmlinuz-* | head -1 | sed 's|/boot/vmlinuz-||')" ]; then
  echo 'Running kernel matches latest installed — no reboot needed for kernel.'
  REBOOT_RECOMMENDED='no'
else
  echo 'New kernel installed but not yet running. REBOOT REQUIRED to activate.'
  REBOOT_RECOMMENDED='yes'
fi

# ---------- Decision point ----------
echo
echo '======================================================================'
if [ "$REBOOT_RECOMMENDED" = 'yes' ]; then
  echo 'KERNEL PATCH STAGED — REBOOT REQUIRED TO ACTIVATE'
  echo '======================================================================'
  echo
  echo 'Pre-reboot state log: '"$PRE_LOG"
  echo 'DB snapshot:           '"$SNAP"
  echo
  echo 'Run this when ready (can be in a separate shell):'
  echo '  reboot'
  echo
  echo 'After ~90 seconds, SSH back in and run:'
  echo '  bash /opt/eh-diagnostics/eh-post-reboot-verify.sh'
  echo
  echo 'Compare against pre-reboot state in: '"$PRE_LOG"
else
  echo 'NO REBOOT NEEDED — patches applied without kernel change.'
  echo '======================================================================'
fi
exit 0
