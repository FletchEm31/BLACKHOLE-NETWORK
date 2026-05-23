#!/bin/bash
# BHN (Blackhole Network) — security sweep, focused threat-indicator check across LA + Frankfurt.
# Run on LA. Probes Frankfurt remotely (WG handshake + reachability only,
# since FRA SSH is currently broken).
#
# Output: structured report with GREEN/YELLOW/RED verdict at the end.
# Pipe to a log file for clean paste-back.

set -uo pipefail

FRA_HOST=192.248.187.208
ALERTS=()  # accumulator for anything noteworthy

echo '======================================================================'
echo "BHN SECURITY SWEEP — $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo '======================================================================'

# ---------- 1. LA host integrity ----------
echo
echo '----- 1. LA HOST INTEGRITY -----'
echo "Kernel:    $(uname -r)"
echo "Uptime:    $(uptime -p)"
echo "Load avg:  $(awk '{print $1, $2, $3}' /proc/loadavg)"

# algif_aead — the CVE-2026-31431 mitigation
if lsmod | grep -q '^algif_aead'; then
  echo "algif_aead: LOADED  -- mitigation broken!"
  ALERTS+=("CRITICAL: algif_aead module is loaded; CVE-2026-31431 attack surface re-exposed")
else
  echo 'algif_aead: not loaded (mitigation intact)'
fi
test -f /etc/modprobe.d/blacklist.conf && grep -i algif /etc/modprobe.d/blacklist.conf >/dev/null && echo 'blacklist.conf: persists across reboot' || ALERTS+=("WARN: algif_aead not in blacklist.conf — won't survive reboot")

# Critical services
echo
echo '----- 2. CRITICAL SERVICES -----'
for svc in postgresql wg-quick@wg0 docker eh-embed dnscrypt-proxy fail2ban suricata crowdsec ufw; do
  STATE=$(systemctl is-active "$svc" 2>/dev/null)
  printf '%-22s %s\n' "$svc" "$STATE"
  case "$svc:$STATE" in
    postgresql:active|wg-quick@wg0:active|docker:active|eh-embed:active|fail2ban:active|crowdsec:active|ufw:active) ;;
    suricata:*|dnscrypt-proxy:*) [ "$STATE" != 'active' ] && ALERTS+=("WARN: $svc not active (state=$STATE)") ;;
    *) [ "$STATE" != 'active' ] && ALERTS+=("CRITICAL: $svc not active (state=$STATE)") ;;
  esac
done

# n8n container
N8N_STATE=$(docker ps --filter name=n8n --format '{{.Status}}' 2>/dev/null | head -1)
echo "n8n container: ${N8N_STATE:-NOT RUNNING}"
[ -z "$N8N_STATE" ] && ALERTS+=("CRITICAL: n8n container not running")

# ---------- 3. Listening sockets ----------
echo
echo '----- 3. LISTENING SOCKETS (audit) -----'
ss -tlnp 2>/dev/null | awk 'NR==1 || NR>1' | head -30

# Flag anything binding 0.0.0.0 except SSH
UNEXPECTED=$(ss -tlnp 2>/dev/null | awk 'NR>1 && $4 ~ /^0\.0\.0\.0:/ && $4 !~ /:22$/' | head -5)
if [ -n "$UNEXPECTED" ]; then
  ALERTS+=("WARN: unexpected service binding 0.0.0.0 (not just SSH): see listening sockets")
fi

# ---------- 4. UFW rules ----------
echo
echo '----- 4. UFW STATE -----'
ufw status verbose 2>/dev/null | head -30

# ---------- 5. fail2ban activity ----------
echo
echo '----- 5. FAIL2BAN -----'
fail2ban-client status 2>/dev/null
echo
for jail in sshd recidive; do
  STATUS=$(fail2ban-client status $jail 2>/dev/null)
  CURR=$(echo "$STATUS" | grep 'Currently banned' | grep -oE '[0-9]+$')
  TOTAL=$(echo "$STATUS" | grep 'Total banned' | grep -oE '[0-9]+$')
  printf 'jail=%-12s currently=%s total=%s\n' "$jail" "${CURR:-?}" "${TOTAL:-?}"
done

# ---------- 6. CrowdSec ----------
echo
echo '----- 6. CROWDSEC ACTIVE DECISIONS -----'
cscli decisions list -o table 2>/dev/null | head -25
DECISION_COUNT=$(cscli decisions list -o json 2>/dev/null | jq 'length' 2>/dev/null)
echo "active_decisions=${DECISION_COUNT:-?}"

# ---------- 7. SSH access patterns last 24h ----------
echo
echo '----- 7. SSH ACTIVITY (last 24h) -----'
SSH_FAIL_24H=$(journalctl --since "24 hours ago" -u ssh -u sshd 2>/dev/null | grep -ciE 'failed password|invalid user' || echo 0)
SSH_SUCCESS_24H=$(journalctl --since "24 hours ago" -u ssh -u sshd 2>/dev/null | grep -ciE 'accepted (password|publickey)' || echo 0)
SSH_UNIQUE_BAD_IPS=$(journalctl --since "24 hours ago" -u ssh -u sshd 2>/dev/null | grep -E 'Failed password|Invalid user' | grep -oE 'from [0-9.]+' | sort -u | wc -l)
echo "Failed login attempts: $SSH_FAIL_24H"
echo "Successful logins:     $SSH_SUCCESS_24H"
echo "Unique attacker IPs:   $SSH_UNIQUE_BAD_IPS"

if [ "${SSH_FAIL_24H:-0}" -gt 500 ]; then
  ALERTS+=("WARN: SSH failed-attempt volume above usual baseline ($SSH_FAIL_24H in 24h)")
fi

# ---------- 8. Suricata alert volume ----------
echo
echo '----- 8. SURICATA ALERT VOLUME (last 24h) -----'
SURI_LOG="/var/log/suricata/eve.json"
if [ -f "$SURI_LOG" ]; then
  TOTAL_ALERTS=$(grep -c '"event_type":"alert"' "$SURI_LOG" 2>/dev/null || echo 0)
  HIGH_ALERTS=$(grep '"event_type":"alert"' "$SURI_LOG" 2>/dev/null | grep -c '"severity":1' || echo 0)
  echo "total_alerts_24h=$TOTAL_ALERTS  high_severity=$HIGH_ALERTS"
  if [ "${HIGH_ALERTS:-0}" -gt 5 ]; then
    ALERTS+=("WARN: $HIGH_ALERTS Suricata high-severity alerts in 24h (steady-state baseline rare)")
  fi
else
  echo '(suricata eve.json not found)'
fi

# ---------- 9. EH PostgreSQL — application-level threat picture ----------
echo
echo '----- 9. EH SECURITY EVENTS (PG, last 24h) -----'
sudo -u postgres psql -d eventhorizon -At -F '|' -c "
SELECT severity, COUNT(*)
FROM security_events
WHERE detected_at > NOW() - INTERVAL '24 hours'
GROUP BY severity
ORDER BY severity;
" 2>/dev/null

CRIT_COUNT=$(sudo -u postgres psql -d eventhorizon -At -c "SELECT COUNT(*) FROM security_events WHERE severity='critical' AND detected_at > NOW() - INTERVAL '24 hours';" 2>/dev/null)
[ "${CRIT_COUNT:-0}" -gt 0 ] && ALERTS+=("CRITICAL: $CRIT_COUNT critical-severity security events in PG, last 24h")

echo
echo '--- Top 5 hostile source IPs (last 24h) ---'
sudo -u postgres psql -d eventhorizon -c "
SELECT source_ip, COUNT(*) AS hits, COUNT(DISTINCT event_type) AS distinct_types
FROM security_events
WHERE detected_at > NOW() - INTERVAL '24 hours'
GROUP BY source_ip
ORDER BY hits DESC
LIMIT 5;" 2>/dev/null

echo
echo '--- Open anomalies ---'
ANOM_COUNT=$(sudo -u postgres psql -d eventhorizon -At -c "SELECT COUNT(*) FROM anomalies WHERE resolved=FALSE;" 2>/dev/null)
echo "open_anomalies=${ANOM_COUNT:-?}"
if [ "${ANOM_COUNT:-0}" -gt 0 ]; then
  sudo -u postgres psql -d eventhorizon -c "
SELECT id, type, description, detected_at
FROM anomalies WHERE resolved=FALSE ORDER BY detected_at DESC LIMIT 5;" 2>/dev/null
fi

# ---------- 10. Frankfurt reachability ----------
echo
echo '----- 10. FRANKFURT REMOTE PROBES -----'
echo '--- WireGuard wg1 ---'
wg show wg1 2>/dev/null || echo '(wg1 interface not present)'

WG_HANDSHAKE=$(wg show wg1 2>/dev/null | grep -E 'latest handshake' | head -1)
echo "WG handshake: ${WG_HANDSHAKE:-(none)}"

echo
echo '--- ICMP ---'
if timeout 6 ping -c 3 -W 2 "$FRA_HOST" >/dev/null 2>&1; then
  echo "ping: OK"
else
  echo "ping: FAIL"
  ALERTS+=("WARN: Frankfurt unreachable via ICMP")
fi

echo
echo '--- SSH port (22) ---'
if timeout 5 nc -zv "$FRA_HOST" 22 2>&1 | grep -qE 'succeeded|open'; then
  echo "ssh port: OPEN"
else
  echo "ssh port: CLOSED or filtered (FRA SSH still down)"
fi

# ---------- 11. Disk pressure ----------
echo
echo '----- 11. DISK -----'
df -h / 2>/dev/null
df -h /mnt/eh-nvme-hot 2>/dev/null
df -h /mnt/eh-cold 2>/dev/null

NVME_PCT=$(df --output=pcent /mnt/eh-nvme-hot 2>/dev/null | tail -1 | tr -d ' %')
HDD_PCT=$(df --output=pcent /mnt/eh-cold 2>/dev/null | tail -1 | tr -d ' %')
[ "${NVME_PCT:-0}" -gt 85 ] && ALERTS+=("WARN: NVMe ${NVME_PCT}% — disk pressure approaching")
[ "${HDD_PCT:-0}" -gt 85 ] && ALERTS+=("WARN: HDD ${HDD_PCT}% — disk pressure approaching")

# ---------- 12. Recent journal errors ----------
echo
echo '----- 12. RECENT JOURNAL ERRORS (last 30 min, severity=err+) -----'
journalctl --since "30 min ago" -p err --no-pager 2>/dev/null | tail -10

# ---------- 13. Verdict ----------
echo
echo '======================================================================'
if [ "${#ALERTS[@]}" -eq 0 ]; then
  echo 'VERDICT: GREEN — no actionable issues found'
elif printf '%s\n' "${ALERTS[@]}" | grep -q '^CRITICAL'; then
  echo 'VERDICT: RED — critical issues require attention'
  printf '%s\n' "${ALERTS[@]}"
else
  echo 'VERDICT: YELLOW — minor flags, no immediate action'
  printf '%s\n' "${ALERTS[@]}"
fi
echo '======================================================================'
