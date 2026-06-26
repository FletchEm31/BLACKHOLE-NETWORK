#!/bin/bash
# bhn-resource-collector — snapshot CPU/RAM/load/disk → LA PG.
#
# Runs on every node via cron every 5 min. Reads /proc files + df, no
# external deps beyond awk/sed/df. CPU% computed as a 2s sample diff
# of /proc/stat so it reflects the moment, not since-boot.
#
# Reads PG DSN from /root/.bhn-resource.env (mode 0600):
#   BHN_RESOURCE_PG_DSN='postgresql://log_shipper:<PW>@<BHN_WG_LA_IP>/eventhorizon'
#
# Cron entry on each node (/etc/cron.d/bhn-resource-collector):
#   */5 * * * * root /usr/local/sbin/bhn-resource-collector.sh
#
# Exit: 0 success, 1 missing config, 2 PG failure.

set -euo pipefail

ENV_FILE=/root/.bhn-resource.env
INFO_FILE=/etc/eh-node-info.conf

[[ -r "$ENV_FILE" ]]  || { echo "bhn-resource: missing $ENV_FILE" >&2; exit 1; }
[[ -r "$INFO_FILE" ]] || { echo "bhn-resource: missing $INFO_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
# shellcheck disable=SC1090
. "$INFO_FILE"
[[ -n "${BHN_RESOURCE_PG_DSN:-}" ]] || { echo "bhn-resource: BHN_RESOURCE_PG_DSN empty" >&2; exit 1; }
[[ -n "${NODE_NAME:-}" ]]           || { echo "bhn-resource: NODE_NAME empty"           >&2; exit 1; }

# CPU% via /proc/stat diff over 2 seconds.
read_cpu() { awk '/^cpu / {print $2+$3+$4+$5+$6+$7+$8, $5}' /proc/stat; }
read t1_total t1_idle <<< "$(read_cpu)"; sleep 2; read t2_total t2_idle <<< "$(read_cpu)"
dt=$((t2_total - t1_total)); didle=$((t2_idle - t1_idle))
if [[ $dt -gt 0 ]]; then
  cpu_pct=$(awk "BEGIN {printf \"%.2f\", (1 - $didle / $dt) * 100}")
else
  cpu_pct="NULL"
fi

cpu_count=$(grep -c ^processor /proc/cpuinfo)
read load1 load5 load15 _ < /proc/loadavg
read uptime_s _ < /proc/uptime; uptime_s=${uptime_s%.*}
proc_count=$(ls /proc/ | grep -c '^[0-9]')

mt=$(awk '/^MemTotal:/     {print $2}' /proc/meminfo)
ma=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
st=$(awk '/^SwapTotal:/    {print $2}' /proc/meminfo)
sf=$(awk '/^SwapFree:/     {print $2}' /proc/meminfo)
mem_total_mb=$((mt / 1024))
mem_avail_mb=$((ma / 1024))
mem_used_mb=$((mem_total_mb - mem_avail_mb))
swap_total_mb=$((st / 1024))
swap_used_mb=$(((st - sf) / 1024))

esc() { printf '%s' "$1" | sed "s/'/''/g"; }
node_esc="$(esc "$NODE_NAME")"

# Disk rows from df (skip tmpfs/devtmpfs/squashfs)
disk_values=""
while read -r fs total used _ pct mp; do
  [[ -z "$mp" ]] && continue
  pct=${pct%\%}
  disk_values+="('$node_esc','$(esc "$fs")','$(esc "$mp")',$total,$used,$pct),"
done < <(df -kP -x tmpfs -x devtmpfs -x squashfs -x overlay 2>/dev/null | tail -n +2)
disk_values="${disk_values%,}"

# Single transaction
psql "$BHN_RESOURCE_PG_DSN" -v ON_ERROR_STOP=1 >/dev/null <<SQL \
  || { echo "bhn-resource: PG insert failed" >&2; exit 2; }
BEGIN;
INSERT INTO node_resource_stats (node_name, cpu_pct, cpu_count, load_1m, load_5m, load_15m,
                                 mem_total_mb, mem_used_mb, mem_available_mb,
                                 swap_total_mb, swap_used_mb, uptime_s, proc_count)
VALUES ('$node_esc', $cpu_pct, $cpu_count, $load1, $load5, $load15,
        $mem_total_mb, $mem_used_mb, $mem_avail_mb,
        $swap_total_mb, $swap_used_mb, $uptime_s, $proc_count);
${disk_values:+INSERT INTO node_disk_stats (node_name, filesystem, mount_point, total_kb, used_kb, used_pct) VALUES $disk_values;}
COMMIT;
SQL
