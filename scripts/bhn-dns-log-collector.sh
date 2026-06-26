#!/bin/bash
# bhn-dns-log-collector — ship dnscrypt-proxy TSV query log → LA PG.
#
# Prereq: enable query log in /etc/dnscrypt-proxy/dnscrypt-proxy.toml:
#   [query_log]
#     file = '/var/log/dnscrypt-proxy/query.log'
#     format = 'tsv'
#     ignored_qtypes = ['DNSKEY', 'NS']
# Then: systemctl restart dnscrypt-proxy.
#
# Reads PG DSN from /root/.bhn-dns-log.env:
#   BHN_DNS_LOG_PG_DSN='postgresql://log_shipper:<PW>@<BHN_WG_LA_IP>/eventhorizon'
#
# Cron (every node running dnscrypt-proxy):
#   */5 * * * * root /usr/local/sbin/bhn-dns-log-collector.sh

set -euo pipefail

ENV_FILE=/root/.bhn-dns-log.env
INFO_FILE=/etc/eh-node-info.conf
LOG_FILE=${BHN_DNS_LOG_PATH:-/var/log/dnscrypt-proxy/query.log}
TAIL_LINES=${BHN_DNS_LOG_TAIL:-10000}

[[ -r "$ENV_FILE" ]]  || { echo "bhn-dns-log: missing $ENV_FILE" >&2; exit 1; }
[[ -r "$INFO_FILE" ]] || { echo "bhn-dns-log: missing $INFO_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
# shellcheck disable=SC1090
. "$INFO_FILE"
[[ -n "${BHN_DNS_LOG_PG_DSN:-}" ]] || { echo "bhn-dns-log: BHN_DNS_LOG_PG_DSN empty" >&2; exit 1; }
[[ -n "${NODE_NAME:-}" ]]          || { echo "bhn-dns-log: NODE_NAME empty" >&2; exit 1; }
[[ -r "$LOG_FILE" ]]               || { echo "bhn-dns-log: $LOG_FILE not readable (enabled in dnscrypt-proxy.toml?)" >&2; exit 0; }

esc() { printf '%s' "$1" | sed "s/'/''/g"; }
node_esc="$(esc "$NODE_NAME")"

# dnscrypt-proxy TSV format:
#   [2026-05-13 14:23:45]<TAB><BHN_WG_LA_IP><TAB>example.com<TAB>A<TAB>PASS<TAB>0ms<TAB>cloudflare
# Some versions wrap the timestamp in [..], some don't; some include qclass column.
# Handle both by stripping brackets.

values=""
count=0
while IFS=$'\t' read -r ts client qname qtype status resp_time resolver _rest; do
  [[ -z "$ts" || -z "$qname" ]] && continue
  ts="${ts#[}"; ts="${ts%]}"
  # Convert "0ms" → 0; "12.3ms" → 12 (round down)
  rt_lit="NULL"
  if [[ -n "$resp_time" ]]; then
    rt_num="${resp_time%ms}"
    rt_num="${rt_num%.*}"
    [[ "$rt_num" =~ ^[0-9]+$ ]] && rt_lit="$rt_num"
  fi
  client_lit="NULL"
  [[ -n "$client" ]] && client_lit="'$client'::inet"
  values+="('${ts/ /T}Z'::timestamptz,'$node_esc',$client_lit,'$(esc "$qname")','$(esc "$qtype")','$(esc "$status")',$rt_lit,'$(esc "$resolver")'),"
  count=$((count + 1))
done < <(tail -n "$TAIL_LINES" "$LOG_FILE")
values="${values%,}"
[[ -z "$values" ]] && exit 0

psql "$BHN_DNS_LOG_PG_DSN" -v ON_ERROR_STOP=1 >/dev/null <<SQL \
  || { echo "bhn-dns-log: PG insert failed" >&2; exit 2; }
INSERT INTO dns_query_log (log_time, node_name, client_ip, qname, qtype, status, response_time_ms, resolver)
VALUES $values
ON CONFLICT (node_name, log_time, client_ip, qname, qtype) DO NOTHING;
SQL
