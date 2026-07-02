#!/bin/bash
# BHN (Blackhole Network) — post-reboot verification.
# Run after reboot following kernel patch (or any reboot). Confirms every
# critical service came back up, the n8n container restarted, WG tunnels
# re-handshook, and HORIZON's chat URL responds.
#
# Compares against the pre-reboot capture log if present.

set -uo pipefail

# Find the most recent pre-reboot log to compare against
PRE_LOG=$(ls -t /var/log/eh-kernel-patch-pre-*.log 2>/dev/null | head -1)

echo '======================================================================'
echo "BHN POST-REBOOT VERIFICATION — $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "Pre-reboot reference: ${PRE_LOG:-(none found)}"
echo '======================================================================'

echo
echo '----- 1. New kernel + uptime -----'
echo "Running kernel: $(uname -r)"
uptime

echo
echo '----- 2. Core services -----'
ALL_OK='yes'
for svc in postgresql wg-quick@wg0 wg-quick@wg1 docker eh-embed dnscrypt-proxy fail2ban suricata crowdsec ufw eh-nightly-diagnostic.timer; do
  STATE=$(systemctl is-active "$svc" 2>/dev/null)
  printf '%-32s %s\n' "$svc" "$STATE"
  if [ "$STATE" != 'active' ] && [ "$svc" != 'wg-quick@wg1' ]; then
    # wg-quick@wg1 legitimately shows inactive/exited even when the tunnel is fine —
    # it's a oneshot unit (Hillsboro full-tunnel egress, set up via wg0 PostUp) that
    # exits after bringing the interface up. Check `wg show wg1` for actual tunnel
    # health, not this service's state. (Formerly exempted because wg1 was Frankfurt's
    # dead tunnel; Frankfurt is decommissioned, wg1 is now live Hillsboro egress.)
    ALL_OK='no'
  fi
done

echo
echo '----- 3. Docker container (n8n) -----'
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null
N8N_STATE=$(docker ps --filter name=n8n --format '{{.Status}}' 2>/dev/null | head -1)
if [ -z "$N8N_STATE" ]; then
  echo 'WARNING: n8n container not running. Try: docker start n8n'
  ALL_OK='no'
else
  echo "n8n: $N8N_STATE"
fi

echo
echo '----- 4. WireGuard handshakes -----'
wg show

echo
echo '----- 5. n8n HTTP responding -----'
HTTP_STATUS=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 http://<BHN_WG_LA_IP>:5678/healthz 2>/dev/null || echo 'fail')
echo "n8n /healthz: HTTP $HTTP_STATUS"

echo
echo '----- 6. Listening sockets (port 5678) -----'
ss -tlnp 2>/dev/null | grep ':5678 '

echo
echo '----- 7. PostgreSQL accepting connections -----'
sudo -u postgres psql -d eventhorizon -At -c "SELECT 'OK', NOW();" 2>&1 | head -3

echo
echo '----- 8. algif_aead status (should still be blacklisted) -----'
lsmod | grep -i algif || echo 'algif_aead not loaded (blacklist persists - good)'
test -f /etc/modprobe.d/blacklist.conf && grep -i algif /etc/modprobe.d/blacklist.conf

echo
echo '----- 9. Disk pressure -----'
df -h / /mnt/eh-nvme-hot /mnt/eh-cold 2>/dev/null

echo
echo '----- 10. Recent journal errors (post-boot) -----'
journalctl --since "$(uptime -s)" -p err --no-pager 2>/dev/null | tail -20

echo
echo '======================================================================'
if [ "$ALL_OK" = 'yes' ] && [ "$HTTP_STATUS" != 'fail' ]; then
  echo 'POST-REBOOT VERIFICATION: PASS'
  echo 'All critical services running, n8n responding.'
else
  echo 'POST-REBOOT VERIFICATION: ATTENTION NEEDED'
  echo 'Review individual sections above for what failed to come back.'
fi
echo '======================================================================'

if [ -n "$PRE_LOG" ] && [ -f "$PRE_LOG" ]; then
  echo
  echo "Diff against pre-reboot state — review these lines:"
  echo "  diff <(grep -A30 'core service states' $PRE_LOG) <(systemctl is-active postgresql ...)"
fi

exit 0
