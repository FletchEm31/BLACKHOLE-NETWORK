#!/bin/bash
# bhn-fail2ban-collector — snapshot fail2ban banned IPs → LA PG.
#
# Runs on every node via cron every 5 min. For each active jail, lists the
# currently banned IPs and INSERTs one row per (jail × IP × measurement).
# Detecting "new bans" is then a windowed query against the table.
#
# Reads PG DSN from /root/.bhn-fail2ban.env (mode 0600):
#   BHN_FAIL2BAN_PG_DSN='postgresql://log_shipper:<PW>@10.8.0.1/eventhorizon'
#
# Cron entry on each node (/etc/cron.d/bhn-fail2ban-collector):
#   */5 * * * * root /usr/local/sbin/bhn-fail2ban-collector.sh
#
# Exit: 0 success, 1 missing config, 2 PG failure, 3 fail2ban-client failure.

set -euo pipefail

ENV_FILE=/root/.bhn-fail2ban.env
INFO_FILE=/etc/eh-node-info.conf

[[ -r "$ENV_FILE" ]]  || { echo "bhn-fail2ban: missing $ENV_FILE" >&2; exit 1; }
[[ -r "$INFO_FILE" ]] || { echo "bhn-fail2ban: missing $INFO_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
# shellcheck disable=SC1090
. "$INFO_FILE"
[[ -n "${BHN_FAIL2BAN_PG_DSN:-}" ]] || { echo "bhn-fail2ban: BHN_FAIL2BAN_PG_DSN empty" >&2; exit 1; }
[[ -n "${NODE_NAME:-}" ]]           || { echo "bhn-fail2ban: NODE_NAME empty"           >&2; exit 1; }

command -v fail2ban-client >/dev/null || { echo "bhn-fail2ban: fail2ban-client not installed" >&2; exit 3; }
if ! systemctl is-active --quiet fail2ban 2>/dev/null; then
  echo "bhn-fail2ban: fail2ban service not active — skipping (exit 0)"; exit 0
fi

# List of jails (parses "Jail list:\t<a>, <b>, <c>")
jail_list=$(fail2ban-client status 2>/dev/null | awk -F':' '/Jail list/ {print $2}' | tr ',' '\n' | tr -d ' ')
[[ -z "$jail_list" ]] && exit 0

esc() { printf '%s' "$1" | sed "s/'/''/g"; }
node_esc="$(esc "$NODE_NAME")"

values=""
for jail in $jail_list; do
  status=$(fail2ban-client status "$jail" 2>/dev/null) || continue
  total_banned=$(echo "$status" | awk -F: '/Currently banned/ {gsub(/^ +| +$/,"",$2); print $2}')
  ips=$(echo "$status" | awk -F: '/Banned IP list/ {sub(/^[ \t]+/,"",$2); print $2}')
  [[ -z "$ips" || "$ips" == "" ]] && continue
  for ip in $ips; do
    [[ -z "$ip" ]] && continue
    values+="('$node_esc','$(esc "$jail")','$(esc "$ip")'::inet,${total_banned:-0},'{}'::jsonb),"
  done
done
values="${values%,}"
[[ -z "$values" ]] && exit 0

psql "$BHN_FAIL2BAN_PG_DSN" -v ON_ERROR_STOP=1 >/dev/null <<SQL \
  || { echo "bhn-fail2ban: PG insert failed" >&2; exit 2; }
INSERT INTO fail2ban_events (node_name, jail, banned_ip, banned_count_in_jail, raw_payload)
VALUES $values
ON CONFLICT (node_name, jail, banned_ip, measured_at) DO NOTHING;
SQL
