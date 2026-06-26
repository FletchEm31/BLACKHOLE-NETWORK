#!/bin/bash
# bhn-iptables-collector — snapshot iptables packet/byte counters → LA PG.
#
# Runs on every node via cron every 5 min. iptables -L -n -v -x prints
# counters per rule per chain; we parse and INSERT one row per (node,
# chain, rule_idx, measured_at). Cumulative counters — Grafana / queries
# compute deltas with LAG().
#
# Reads PG DSN from /root/.bhn-iptables.env:
#   BHN_IPTABLES_PG_DSN='postgresql://log_shipper:<PW>@<BHN_WG_LA_IP>/eventhorizon'
#
# Cron (every node):
#   */5 * * * * root /usr/local/sbin/bhn-iptables-collector.sh

set -euo pipefail

ENV_FILE=/root/.bhn-iptables.env
INFO_FILE=/etc/eh-node-info.conf

[[ -r "$ENV_FILE" ]]  || { echo "bhn-iptables: missing $ENV_FILE" >&2; exit 1; }
[[ -r "$INFO_FILE" ]] || { echo "bhn-iptables: missing $INFO_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
# shellcheck disable=SC1090
. "$INFO_FILE"
[[ -n "${BHN_IPTABLES_PG_DSN:-}" ]] || { echo "bhn-iptables: BHN_IPTABLES_PG_DSN empty" >&2; exit 1; }
[[ -n "${NODE_NAME:-}" ]]           || { echo "bhn-iptables: NODE_NAME empty" >&2; exit 1; }

esc() { printf '%s' "$1" | sed "s/'/''/g"; }
node_esc="$(esc "$NODE_NAME")"

# `iptables -L -n -v -x` output sections per chain look like:
#   Chain INPUT (policy ACCEPT 0 packets, 0 bytes)
#   num   pkts bytes target     prot opt in     out     source       destination
#       1   123 9876 ufw-...    all  --  *      *       0.0.0.0/0    0.0.0.0/0
output=$(iptables -L -n -v -x --line-numbers 2>/dev/null || true)
[[ -z "$output" ]] && { echo "bhn-iptables: iptables -L returned nothing" >&2; exit 0; }

values=""
current_chain=""
while IFS= read -r line; do
  if [[ "$line" =~ ^Chain[[:space:]]+([^ ]+) ]]; then
    current_chain="${BASH_REMATCH[1]}"
    continue
  fi
  # Skip headers + blank lines
  [[ -z "$line" ]] && continue
  [[ "$line" =~ ^[[:space:]]*num ]] && continue
  [[ -z "$current_chain" ]] && continue
  # Rule line: num pkts bytes target proto opt in out source destination [extras]
  read -r num pkts bytes target proto opt in_if out_if src dst rest <<< "$line"
  [[ "$num" =~ ^[0-9]+$ ]] || continue
  rule_spec=$(esc "$line")
  values+="('$node_esc','$(esc "$current_chain")',$num,${pkts:-0},${bytes:-0},'$(esc "$target")','$(esc "$proto")','$(esc "$in_if")','$(esc "$out_if")','$(esc "$src")','$(esc "$dst")','$rule_spec'),"
done <<< "$output"
values="${values%,}"
[[ -z "$values" ]] && exit 0

psql "$BHN_IPTABLES_PG_DSN" -v ON_ERROR_STOP=1 >/dev/null <<SQL \
  || { echo "bhn-iptables: PG insert failed" >&2; exit 2; }
INSERT INTO iptables_stats (node_name, chain, rule_idx, packets, bytes, target, proto, in_iface, out_iface, source_spec, dest_spec, rule_spec)
VALUES $values;
SQL
