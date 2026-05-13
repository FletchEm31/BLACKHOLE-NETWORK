#!/bin/bash
# bhn-proxy-log-collector — ship tinyproxy CONNECT events to LA PG.
#
# Runs on Hillsboro (tinyproxy host) every 5 min. Tails the trailing N lines
# of /var/log/tinyproxy/tinyproxy.log, parses CONNECT establishment lines,
# UPSERTs to proxy_request_logs. Dedup via UNIQUE(node, log_time, pid, dst).
#
# Reads PG DSN from /root/.bhn-proxy-log.env (mode 0600):
#   BHN_PROXY_LOG_PG_DSN='postgresql://log_shipper:<PW>@10.8.0.1/eventhorizon'
#
# Cron (Hillsboro only):
#   */5 * * * * root /usr/local/sbin/bhn-proxy-log-collector.sh

set -euo pipefail

ENV_FILE=/root/.bhn-proxy-log.env
INFO_FILE=/etc/eh-node-info.conf
LOG_FILE=${BHN_PROXY_LOG_PATH:-/var/log/tinyproxy/tinyproxy.log}
TAIL_LINES=${BHN_PROXY_LOG_TAIL:-5000}

[[ -r "$ENV_FILE" ]]  || { echo "bhn-proxy-log: missing $ENV_FILE" >&2; exit 1; }
[[ -r "$INFO_FILE" ]] || { echo "bhn-proxy-log: missing $INFO_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
# shellcheck disable=SC1090
. "$INFO_FILE"
[[ -n "${BHN_PROXY_LOG_PG_DSN:-}" ]] || { echo "bhn-proxy-log: BHN_PROXY_LOG_PG_DSN empty" >&2; exit 1; }
[[ -n "${NODE_NAME:-}" ]]            || { echo "bhn-proxy-log: NODE_NAME empty" >&2; exit 1; }
[[ -r "$LOG_FILE" ]]                 || { echo "bhn-proxy-log: $LOG_FILE not readable" >&2; exit 0; }

esc() { printf '%s' "$1" | sed "s/'/''/g"; }
node_esc="$(esc "$NODE_NAME")"

# Parse two line patterns from tinyproxy LogLevel=Info|Connect:
# A) CONNECT  May 13 22:00:01 [12345]: Connect (file descriptor 7): 10.8.0.1 [10.8.0.1] - returning request for "GET https://api.anthropic.com/...":
#    → captures pid, src_ip, dst_url
# B) CONNECT  May 13 22:00:01 [12345]: Established connection to "api.anthropic.com" using file descriptor 8.
#    → captures pid, dst_host (no src_ip here)
#
# We use pattern B as the canonical "successful CONNECT" event. src_ip we
# fill in by looking back through the most recent A-line with the matching
# pid (done inline in the awk pass).
#
# Year stamping: tinyproxy logs the short month+day but no year. We assume
# entries are from this year unless month+day > today's date, in which case
# previous year.

current_year=$(date +%Y)
this_md=$(date +'%m%d')

tail -n "$TAIL_LINES" "$LOG_FILE" | awk -v year="$current_year" -v this_md="$this_md" '
function mon2num(m) {
  if (m=="Jan") return "01"; if (m=="Feb") return "02"; if (m=="Mar") return "03"
  if (m=="Apr") return "04"; if (m=="May") return "05"; if (m=="Jun") return "06"
  if (m=="Jul") return "07"; if (m=="Aug") return "08"; if (m=="Sep") return "09"
  if (m=="Oct") return "10"; if (m=="Nov") return "11"; if (m=="Dec") return "12"
  return "01"
}
{
  # Common prefix: LEVEL<spaces>Mon DD HH:MM:SS [PID]: rest
  if (match($0, /^[A-Z]+[ \t]+([A-Z][a-z]+) +([0-9]+) +([0-9:]+) \[([0-9]+)\]: (.*)$/, m)) {
    mon=mon2num(m[1]); day=sprintf("%02d", m[2])
    md = mon day
    y = year
    if (md > this_md) y = year - 1   # log entry crossed year boundary
    ts = y "-" mon "-" day "T" m[3] "Z"
    pid = m[4]; rest = m[5]
    if (match(rest, /Connect \(file descriptor [0-9]+\): ([0-9.]+) /, sm)) {
      src_by_pid[pid] = sm[1]
    } else if (match(rest, /Established connection to "([^"]+)"/, dm)) {
      dst = dm[1]
      src = (pid in src_by_pid) ? src_by_pid[pid] : ""
      # split host:port if dst looks like host:443
      port = ""
      if (match(dst, /^(.*):([0-9]+)$/, pm)) { dst = pm[1]; port = pm[2] }
      gsub(/'\''/, "'\''" "'\''", dst)  # SQL-escape single quotes
      gsub(/'\''/, "'\''" "'\''", src)
      printf "%s\x1f%s\x1f%s\x1f%s\x1f%s\n", ts, pid, src, dst, port
    }
  }
}
' > /tmp/bhn-proxy-log-rows.$$

values=""
count=0
while IFS=$'\x1f' read -r ts pid src dst port; do
  [[ -z "$ts" || -z "$dst" ]] && continue
  src_lit="NULL"; [[ -n "$src" ]] && src_lit="'$src'::inet"
  port_lit="NULL"; [[ -n "$port" ]] && port_lit="$port"
  values+="('$ts'::timestamptz,'$node_esc',$pid,$src_lit,'$dst',$port_lit,NULL,NULL,NULL),"
  count=$((count + 1))
done < /tmp/bhn-proxy-log-rows.$$
rm -f /tmp/bhn-proxy-log-rows.$$
values="${values%,}"
[[ -z "$values" ]] && exit 0

psql "$BHN_PROXY_LOG_PG_DSN" -v ON_ERROR_STOP=1 >/dev/null <<SQL \
  || { echo "bhn-proxy-log: PG insert failed" >&2; exit 2; }
INSERT INTO proxy_request_logs (log_time, node_name, pid, src_ip, dst_host, dst_port, response_code, bytes_sent, raw_line)
VALUES $values
ON CONFLICT (node_name, log_time, pid, dst_host) DO NOTHING;
SQL
