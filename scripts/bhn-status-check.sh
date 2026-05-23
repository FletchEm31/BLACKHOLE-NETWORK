#!/bin/bash
# BHN (Blackhole Network) — quick diagnostic checkup. Host services + network + hostile activity.
# Run on LA. Output structured for paste-back review.

echo '======================================================================'
echo "BHN STATUS CHECK — $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo '======================================================================'

echo
echo '===== KERNEL & UPTIME ====='
uname -r
uptime

echo
echo '===== CORE SERVICES ====='
for svc in postgresql wg-quick@wg0 wg-quick@wg1 docker eh-embed dnscrypt-proxy fail2ban suricata crowdsec ufw; do
  printf '%-22s %s\n' "$svc" "$(systemctl is-active $svc 2>/dev/null)"
done

echo
echo '===== N8N CONTAINER ====='
docker ps --filter name=n8n --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

echo
echo '===== WIREGUARD wg0 (CLIENTS) ====='
wg show wg0 2>/dev/null | head -40

echo
echo '===== WIREGUARD wg1 (FRANKFURT) ====='
wg show wg1 2>/dev/null | head -10

echo
echo '===== UFW STATUS ====='
ufw status verbose 2>/dev/null | head -25

echo
echo '===== FAIL2BAN STATUS ====='
fail2ban-client status 2>/dev/null
echo '--- sshd jail ---'
fail2ban-client status sshd 2>/dev/null | grep -E 'Currently|Total|Banned'
echo '--- recidive jail ---'
fail2ban-client status recidive 2>/dev/null | grep -E 'Currently|Total|Banned'

echo
echo '===== CROWDSEC ACTIVE DECISIONS (top 10) ====='
cscli decisions list -o table 2>/dev/null | head -20

echo
echo '===== SURICATA EVENT VOLUME (last 24h) ====='
SURI_LOG="/var/log/suricata/eve.json"
if [ -f "$SURI_LOG" ]; then
  echo "Total alerts (24h): $(grep -c '"event_type":"alert"' $SURI_LOG 2>/dev/null || echo n/a)"
  echo "Distinct source IPs (24h): $(grep '"event_type":"alert"' $SURI_LOG 2>/dev/null | jq -r '.src_ip' 2>/dev/null | sort -u | wc -l)"
fi

echo
echo '===== DISK ====='
df -h / 2>/dev/null
df -h /mnt/eh-nvme-hot 2>/dev/null
df -h /mnt/eh-cold 2>/dev/null

echo
echo '===== MEMORY & LOAD ====='
free -h | head -3
cat /proc/loadavg

echo
echo '===== TOP CONNECTIONS RIGHT NOW (active TCP) ====='
ss -tn state established 2>/dev/null | awk '{print $4, $5}' | sort | uniq -c | sort -rn | head -10

echo
echo '===== ACTIVE WG SESSIONS (last 24h, EH PG) ====='
sudo -u postgres psql -d eventhorizon -At -F $'\t' -c "
SELECT user_key,
       to_char(connected_at, 'MM-DD HH24:MI') AS connected,
       CASE WHEN disconnected_at IS NULL THEN 'OPEN' ELSE to_char(disconnected_at, 'MM-DD HH24:MI') END AS disconnected,
       ROUND(bytes_in/1e9::numeric, 2) AS gb_in,
       ROUND(bytes_out/1e9::numeric, 2) AS gb_out,
       exit_node
FROM sessions
WHERE connected_at > NOW() - INTERVAL '24 hours'
ORDER BY connected_at DESC
LIMIT 10;" 2>/dev/null

echo
echo '===== SECURITY EVENTS (last 1h, by severity x type) ====='
sudo -u postgres psql -d eventhorizon -c "
SELECT severity, event_type, COUNT(*)
FROM security_events
WHERE detected_at > NOW() - INTERVAL '1 hour'
GROUP BY severity, event_type
ORDER BY 1 DESC, 3 DESC;" 2>/dev/null

echo
echo '===== TOP HOSTILE SOURCE IPS (last 1h) ====='
sudo -u postgres psql -d eventhorizon -c "
SELECT source_ip, COUNT(*) AS hits, MIN(detected_at) AS first_seen, MAX(detected_at) AS last_seen
FROM security_events
WHERE detected_at > NOW() - INTERVAL '1 hour'
GROUP BY source_ip
ORDER BY hits DESC
LIMIT 5;" 2>/dev/null

echo
echo '===== OPEN ANOMALIES ====='
sudo -u postgres psql -d eventhorizon -c "
SELECT id, type, description, detected_at
FROM anomalies
WHERE resolved = FALSE
ORDER BY detected_at DESC
LIMIT 5;" 2>/dev/null

echo
echo '===== LATEST PULSE REPORT ====='
sudo -u postgres psql -d eventhorizon -c "
SELECT to_char(generated_at, 'YYYY-MM-DD HH24:MI') AS generated,
       sessions_active, sessions_new,
       ROUND(bytes_in/1e9::numeric, 2) AS gb_in,
       ROUND(bytes_out/1e9::numeric, 2) AS gb_out,
       events_total, events_high, events_critical,
       anomalies_open, important
FROM pulse_reports
ORDER BY generated_at DESC
LIMIT 1;" 2>/dev/null
echo '--- Latest summary ---'
sudo -u postgres psql -d eventhorizon -At -c "SELECT summary FROM pulse_reports ORDER BY generated_at DESC LIMIT 1;" 2>/dev/null

echo
echo '===== FRANKFURT REACHABILITY ====='
echo '--- ICMP ping (over WG tunnel; Vultr blocks public-IP TCP between regions) ---'
timeout 6 ping -c 3 -W 2 10.9.0.2 2>&1 | tail -5
echo '--- WG tunnel handshake ---'
wg show wg1 2>/dev/null | grep -E 'latest handshake|transfer'
echo '--- SSH port reachable (port 2222 via tunnel) ---'
timeout 5 nc -zv 10.9.0.2 2222 2>&1

echo
echo '===== RECENT JOURNAL ERRORS (last 30 min) ====='
journalctl --since "30 min ago" -p err --no-pager 2>/dev/null | tail -15

echo
echo '======================================================================'
echo "STATUS CHECK COMPLETE — $(date '+%H:%M:%S')"
echo '======================================================================'
