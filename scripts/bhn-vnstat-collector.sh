#!/bin/bash
# bhn-vnstat-collector — ship vnstat hour/day/month bandwidth totals to LA PG.
#
# Runs on every node via cron every 15 min. Calls `vnstat --json` per known
# interface, parses out the hour/day/month aggregates, and UPSERTs one row
# per (node, interface, period_type, period_start) into node_bandwidth_stats.
# The UNIQUE constraint handles re-runs cleanly — same bucket key updates
# with the latest rx/tx as vnstat continues accumulating in that bucket.
#
# Reads PG DSN from /root/.bhn-vnstat.env (mode 0600):
#   BHN_VNSTAT_PG_DSN='postgresql://log_shipper:<PW>@10.8.0.1/eventhorizon'
#
# Cron entry on each node (/etc/cron.d/bhn-vnstat-collector):
#   */15 * * * * root /usr/local/sbin/bhn-vnstat-collector.sh
#
# Pre-deploy on each node:
#   apt-get install -y vnstat   # enables the vnstatd daemon
#   systemctl enable --now vnstatd
#
# Exit: 0 success, 1 missing config, 2 PG failure, 3 vnstat failure.

set -euo pipefail

ENV_FILE=/root/.bhn-vnstat.env
INFO_FILE=/etc/eh-node-info.conf

[[ -r "$ENV_FILE" ]]  || { echo "bhn-vnstat: missing $ENV_FILE" >&2; exit 1; }
[[ -r "$INFO_FILE" ]] || { echo "bhn-vnstat: missing $INFO_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
# shellcheck disable=SC1090
. "$INFO_FILE"
[[ -n "${BHN_VNSTAT_PG_DSN:-}" ]] || { echo "bhn-vnstat: BHN_VNSTAT_PG_DSN empty" >&2; exit 1; }
[[ -n "${NODE_NAME:-}" ]]         || { echo "bhn-vnstat: NODE_NAME empty"         >&2; exit 1; }

command -v vnstat >/dev/null || { echo "bhn-vnstat: vnstat not installed" >&2; exit 3; }
command -v jq     >/dev/null || { echo "bhn-vnstat: jq not installed"     >&2; exit 3; }

# Snapshot all interfaces vnstat knows about at once.
snapshot=$(vnstat --json) || { echo "bhn-vnstat: vnstat --json failed" >&2; exit 3; }

# Build a multi-row INSERT … ON CONFLICT DO UPDATE for all (iface × period × bucket) combos.
# jq emits one TSV line per row: iface\tperiod\tperiod_start_iso\trx\ttx\traw_json
rows=$(echo "$snapshot" | jq -r '
  .interfaces[] |
  . as $iface |
  (
    ($iface.traffic.hour  // []) | map({p:"hour",  d:.date, t:.time, rx:.rx, tx:.tx})
    + ($iface.traffic.day   // []) | map({p:"day",   d:.date,         rx:.rx, tx:.tx})
    + ($iface.traffic.month // []) | map({p:"month", d:.date,         rx:.rx, tx:.tx})
    + ($iface.traffic.top   // []) | map({p:"top",   d:.date,         rx:.rx, tx:.tx})
  )[] |
  [
    $iface.name,
    .p,
    (
      if .p == "hour" then
        (.d | "\(.year)-\(.month|tostring|@text)-\(.day|tostring|@text)") + "T" + (.t | "\(.hour|tostring|@text):\(.minute|tostring|@text):00Z")
      else
        (.d | "\(.year)-\(.month|tostring|@text)-\((.day // 1)|tostring|@text)") + "T00:00:00Z"
      end
    ),
    (.rx | tostring),
    (.tx | tostring)
  ] | @tsv
' 2>/dev/null || true)

[[ -z "$rows" ]] && { echo "bhn-vnstat: no parseable rows from vnstat --json"; exit 0; }

esc() { printf '%s' "$1" | sed "s/'/''/g"; }

values=""
while IFS=$'\t' read -r iface period period_start rx tx; do
  [[ -z "$iface" || -z "$period" || -z "$period_start" || -z "$rx" || -z "$tx" ]] && continue
  values+="('$(esc "$NODE_NAME")','$(esc "$iface")','$(esc "$period")','$(esc "$period_start")'::timestamptz,$rx,$tx,'{}'::jsonb),"
done <<< "$rows"
values="${values%,}"
[[ -z "$values" ]] && exit 0

psql "$BHN_VNSTAT_PG_DSN" -v ON_ERROR_STOP=1 >/dev/null <<SQL \
  || { echo "bhn-vnstat: PG insert failed" >&2; exit 2; }
INSERT INTO node_bandwidth_stats (node_name, interface, period_type, period_start, rx_bytes, tx_bytes, raw_payload)
VALUES $values
ON CONFLICT (node_name, interface, period_type, period_start)
DO UPDATE SET rx_bytes = EXCLUDED.rx_bytes, tx_bytes = EXCLUDED.tx_bytes, measured_at = NOW();
SQL
