#!/bin/bash
# bhn-docker-stats-collector — snapshot docker stats → LA PG.
#
# Runs on any node with Docker installed via cron every 5 min.
# `docker stats --no-stream --format '{{json .}}'` emits one JSON line per
# running container with friendly-formatted numbers; we parse them back to
# raw bytes / percentages and INSERT one row per container.
#
# Reads PG DSN from /root/.bhn-docker-stats.env:
#   BHN_DOCKER_STATS_PG_DSN='postgresql://log_shipper:<PW>@10.8.0.1/eventhorizon'
#
# Cron (any node running Docker):
#   */5 * * * * root /usr/local/sbin/bhn-docker-stats-collector.sh

set -euo pipefail

ENV_FILE=/root/.bhn-docker-stats.env
INFO_FILE=/etc/eh-node-info.conf

[[ -r "$ENV_FILE" ]]  || { echo "bhn-docker-stats: missing $ENV_FILE" >&2; exit 1; }
[[ -r "$INFO_FILE" ]] || { echo "bhn-docker-stats: missing $INFO_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
# shellcheck disable=SC1090
. "$INFO_FILE"
[[ -n "${BHN_DOCKER_STATS_PG_DSN:-}" ]] || { echo "bhn-docker-stats: BHN_DOCKER_STATS_PG_DSN empty" >&2; exit 1; }
[[ -n "${NODE_NAME:-}" ]]               || { echo "bhn-docker-stats: NODE_NAME empty" >&2; exit 1; }

command -v docker >/dev/null || { echo "bhn-docker-stats: docker not installed — skipping" >&2; exit 0; }
command -v jq     >/dev/null || { echo "bhn-docker-stats: jq not installed" >&2; exit 3; }

stats=$(docker stats --no-stream --format '{{json .}}' 2>/dev/null || true)
[[ -z "$stats" ]] && exit 0

# `docker stats` emits human-friendly strings like "12.34%", "123.4MiB / 1.234GiB",
# "1.23kB / 4.56MB". Convert those to numeric.
# Helpers (awk inline)
to_bytes='
function to_b(v,    n,u) {
  n=v; sub(/[a-zA-Z]+$/,"",n)
  u=v; sub(/^[0-9.]+/,"",u)
  if (u=="B")              return n
  if (u=="kB" || u=="KiB") return n*1024
  if (u=="MB" || u=="MiB") return n*1024*1024
  if (u=="GB" || u=="GiB") return n*1024*1024*1024
  if (u=="TB" || u=="TiB") return n*1024*1024*1024*1024
  return n
}'

esc() { printf '%s' "$1" | sed "s/'/''/g"; }
node_esc="$(esc "$NODE_NAME")"

# Get image per container via separate docker call (docker stats doesn't include image)
declare -A image_for
while IFS=$'\t' read -r cname img; do
  image_for[$cname]="$img"
done < <(docker ps --format '{{.Names}}\t{{.Image}}' 2>/dev/null)

values=""
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  name=$(echo "$line" | jq -r '.Name')
  cid=$(echo "$line"  | jq -r '.ID')
  cpu=$(echo "$line"  | jq -r '.CPUPerc' | tr -d '%')
  memp=$(echo "$line" | jq -r '.MemPerc' | tr -d '%')
  memusage=$(echo "$line" | jq -r '.MemUsage')   # "12.34MiB / 1.234GiB"
  netio=$(echo "$line"    | jq -r '.NetIO')      # "1.23kB / 4.56MB"
  blockio=$(echo "$line"  | jq -r '.BlockIO')    # "0B / 0B"
  pids=$(echo "$line"     | jq -r '.PIDs')

  read -r mem_used mem_limit <<< "$(echo "$memusage" | awk -F' / ' "$to_bytes {printf \"%.0f %.0f\", to_b(\$1), to_b(\$2)}")"
  read -r net_rx net_tx <<< "$(echo "$netio"   | awk -F' / ' "$to_bytes {printf \"%.0f %.0f\", to_b(\$1), to_b(\$2)}")"
  read -r blk_r blk_w   <<< "$(echo "$blockio" | awk -F' / ' "$to_bytes {printf \"%.0f %.0f\", to_b(\$1), to_b(\$2)}")"

  mem_used_mb=$(awk "BEGIN{printf \"%.2f\", $mem_used/1024/1024}")
  mem_limit_mb=$(awk "BEGIN{printf \"%.2f\", $mem_limit/1024/1024}")
  image="${image_for[$name]:-unknown}"

  values+="('$node_esc','$(esc "$name")','$(esc "$cid")','$(esc "$image")',${cpu:-NULL},$mem_used_mb,$mem_limit_mb,${memp:-NULL},${net_rx:-0},${net_tx:-0},${blk_r:-0},${blk_w:-0},${pids:-0}),"
done <<< "$stats"
values="${values%,}"
[[ -z "$values" ]] && exit 0

psql "$BHN_DOCKER_STATS_PG_DSN" -v ON_ERROR_STOP=1 >/dev/null <<SQL \
  || { echo "bhn-docker-stats: PG insert failed" >&2; exit 2; }
INSERT INTO container_stats (node_name, container_name, container_id, image, cpu_pct, mem_used_mb, mem_limit_mb, mem_pct, net_rx_bytes, net_tx_bytes, block_read_bytes, block_write_bytes, pids)
VALUES $values;
SQL
