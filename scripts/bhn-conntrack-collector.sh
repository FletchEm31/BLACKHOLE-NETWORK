#!/bin/bash
# bhn-conntrack-collector — snapshot kernel conntrack to LA PG.
#
# Reads /proc/net/nf_conntrack (cheap, no conntrack-tools dep) and emits
# one row per connection. Volume can be HIGH — eh-purge deletes rows
# older than 14 days to keep table size bounded.
#
# Reads PG DSN from /root/.bhn-conntrack.env:
#   BHN_CONNTRACK_PG_DSN='postgresql://log_shipper:<PW>@10.8.0.1/eventhorizon'
#
# Cron (every node):
#   */5 * * * * root /usr/local/sbin/bhn-conntrack-collector.sh
#
# Exit: 0 success, 1 missing config, 2 PG failure.

set -euo pipefail

ENV_FILE=/root/.bhn-conntrack.env
INFO_FILE=/etc/eh-node-info.conf

[[ -r "$ENV_FILE" ]]  || { echo "bhn-conntrack: missing $ENV_FILE" >&2; exit 1; }
[[ -r "$INFO_FILE" ]] || { echo "bhn-conntrack: missing $INFO_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
# shellcheck disable=SC1090
. "$INFO_FILE"
[[ -n "${BHN_CONNTRACK_PG_DSN:-}" ]] || { echo "bhn-conntrack: BHN_CONNTRACK_PG_DSN empty" >&2; exit 1; }
[[ -n "${NODE_NAME:-}" ]]            || { echo "bhn-conntrack: NODE_NAME empty" >&2; exit 1; }

CONNTRACK_FILE=/proc/net/nf_conntrack
[[ -r "$CONNTRACK_FILE" ]] || { echo "bhn-conntrack: $CONNTRACK_FILE not readable (kernel module loaded?)" >&2; exit 0; }

esc() { printf '%s' "$1" | sed "s/'/''/g"; }
node_esc="$(esc "$NODE_NAME")"

# /proc/net/nf_conntrack format (space-separated, varies by proto):
# ipv4 2 tcp 6 431999 ESTABLISHED src=10.8.0.1 dst=10.8.0.4 sport=22 dport=64978
#       packets=12 bytes=2048 src=10.8.0.4 dst=10.8.0.1 sport=64978 dport=22 packets=10 bytes=1024 [ASSURED] mark=0 use=1
#
# We extract: proto, state, src/dst/sport/dport from the FIRST direction tuple,
# plus packets/bytes from each direction.
values=""
count=0
while read -r line; do
  proto=$(echo "$line" | awk '{print $3}')
  [[ "$proto" =~ ^(tcp|udp|icmp|icmpv6)$ ]] || continue
  state=""
  if [[ "$proto" == "tcp" ]]; then
    state=$(echo "$line" | awk '{print $6}')
  fi
  # Original-direction src/dst/sport/dport (first occurrence)
  src=$(echo "$line"   | grep -oE 'src=[0-9a-f.:]+' | head -1 | cut -d= -f2)
  dst=$(echo "$line"   | grep -oE 'dst=[0-9a-f.:]+' | head -1 | cut -d= -f2)
  sport=$(echo "$line" | grep -oE 'sport=[0-9]+'    | head -1 | cut -d= -f2)
  dport=$(echo "$line" | grep -oE 'dport=[0-9]+'    | head -1 | cut -d= -f2)
  # packets/bytes — two occurrences (orig + reply); awk picks them in order
  read -r po po2 < <(echo "$line" | grep -oE 'packets=[0-9]+' | cut -d= -f2 | tr '\n' ' ')
  read -r bo bo2 < <(echo "$line" | grep -oE 'bytes=[0-9]+'   | cut -d= -f2 | tr '\n' ' ')
  [[ -z "$src" || -z "$dst" ]] && continue
  state_lit="NULL"; [[ -n "$state" ]] && state_lit="'$(esc "$state")'"
  sport_lit="NULL"; [[ -n "$sport" ]] && sport_lit="$sport"
  dport_lit="NULL"; [[ -n "$dport" ]] && dport_lit="$dport"
  values+="('$node_esc','$proto',$state_lit,'$src'::inet,$sport_lit,'$dst'::inet,$dport_lit,${bo:-0},${bo2:-0},${po:-0},${po2:-0}),"
  count=$((count + 1))
  # Hard cap to avoid runaway INSERTs if the conntrack table is enormous.
  [[ $count -ge 5000 ]] && break
done < "$CONNTRACK_FILE"
values="${values%,}"
[[ -z "$values" ]] && exit 0

psql "$BHN_CONNTRACK_PG_DSN" -v ON_ERROR_STOP=1 >/dev/null <<SQL \
  || { echo "bhn-conntrack: PG insert failed" >&2; exit 2; }
INSERT INTO connection_snapshots (node_name, proto, state, src_ip, src_port, dst_ip, dst_port,
                                  bytes_orig, bytes_reply, packets_orig, packets_reply)
VALUES $values;
SQL
