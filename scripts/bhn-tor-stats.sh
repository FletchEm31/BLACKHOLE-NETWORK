#!/bin/bash
# bhn-tor-stats — sample Tor relay state on this node.
#
# Runs on each Tor relay node via cron every 5 min. Reads /var/lib/tor/state
# via `docker exec bhn-tor-relay`, parses bandwidth caps from torrc,
# computes uptime from container State.StartedAt, INSERTs to LA PG
# over the WG tunnel.
#
# v1 limitations:
#   - circuits_built stays NULL — requires ControlSocket enabled in torrc
#     (currently `ControlSocket 0` across all three relays). Deferred.
#   - bytes_read / bytes_written = AccountingBytes*InInterval from state
#     (cumulative since current accounting cycle started — reset monthly
#     on `AccountingStart month 1 00:00`).
#   - Rate/burst are constants parsed from torrc — they only change on
#     torrc edit + rebuild.
#
# Reads PG DSN from /root/.bhn-tor-stats.env (mode 0600):
#   BHN_TOR_STATS_PG_DSN='postgresql://n8n_user:<PW>@<BHN_WG_LA_IP>/eventhorizon'
#
# Exit: 0 success, 1 missing config, 2 PG failure, 3 docker/exec failure.

set -euo pipefail

ENV_FILE=/root/.bhn-tor-stats.env
CONTAINER=bhn-tor-relay

[[ -r "$ENV_FILE" ]] || { echo "bhn-tor-stats: missing $ENV_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
[[ -n "${BHN_TOR_STATS_PG_DSN:-}" ]] || { echo "bhn-tor-stats: BHN_TOR_STATS_PG_DSN empty" >&2; exit 1; }

# Verify container is running.
running=$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || echo "false")
[[ "$running" == "true" ]] || { echo "bhn-tor-stats: container $CONTAINER not running" >&2; exit 3; }

# Pull state file once.
state=$(docker exec "$CONTAINER" cat /var/lib/tor/state 2>/dev/null) \
    || { echo "bhn-tor-stats: failed reading /var/lib/tor/state" >&2; exit 3; }

# Accounting bytes (may be absent if accounting just started or AccountingMax unset)
bytes_read=$(echo "$state"    | awk '/^AccountingBytesReadInInterval/    {print $2}')
bytes_written=$(echo "$state" | awk '/^AccountingBytesWrittenInInterval/ {print $2}')
[[ -z "$bytes_read"    ]] && bytes_read="NULL"
[[ -z "$bytes_written" ]] && bytes_written="NULL"

# Nickname + fingerprint from the fingerprint file ("Nickname FINGERPRINT")
fp_line=$(docker exec "$CONTAINER" cat /var/lib/tor/fingerprint 2>/dev/null || echo "")
nickname=$(echo "$fp_line" | awk '{print $1}')
fingerprint=$(echo "$fp_line" | awk '{print $2}')
[[ -z "$nickname" ]] && { echo "bhn-tor-stats: empty nickname from fingerprint file" >&2; exit 3; }

# Bandwidth rate/burst from torrc — convert KB/MB/GB suffix to bytes/sec
parse_bw() {
    awk -v key="$1" '$1 == key {
        val = $2; unit = $3
        if      (unit == "KB") val *= 1024
        else if (unit == "MB") val *= 1024 * 1024
        else if (unit == "GB") val *= 1024 * 1024 * 1024
        print val
        exit
    }' <<< "$2"
}
torrc=$(docker exec "$CONTAINER" cat /etc/tor/torrc 2>/dev/null || echo "")
rate=$(parse_bw RelayBandwidthRate  "$torrc")
burst=$(parse_bw RelayBandwidthBurst "$torrc")
[[ -z "$rate" ]]  && rate="NULL"
[[ -z "$burst" ]] && burst="NULL"

# Uptime
start_iso=$(docker inspect -f '{{.State.StartedAt}}' "$CONTAINER")
start_epoch=$(date -d "$start_iso" +%s 2>/dev/null || echo "0")
now_epoch=$(date +%s)
if [[ "$start_epoch" -gt 0 ]]; then
    uptime_s=$((now_epoch - start_epoch))
else
    uptime_s="NULL"
fi

# Raw payload — preserve unparsed context for offline analysis
raw_json=$(jq -cn \
    --arg fp "$fingerprint" --arg nick "$nickname" \
    --arg start "$start_iso" \
    --arg br "$bytes_read" --arg bw "$bytes_written" \
    --arg rate "$rate" --arg burst "$burst" \
    '{fingerprint: $fp, nickname: $nick, container_started_at: $start,
      bytes_read_raw: $br, bytes_written_raw: $bw,
      rate_bps: $rate, burst_bps: $burst}')

esc() { printf '%s' "$1" | sed "s/'/''/g"; }

psql "$BHN_TOR_STATS_PG_DSN" -v ON_ERROR_STOP=1 >/dev/null <<SQL \
  || { echo "bhn-tor-stats: PG insert failed" >&2; exit 2; }
INSERT INTO tor_relay_stats (node, bytes_read, bytes_written, circuits_built,
                             relay_bandwidth_rate, relay_bandwidth_burst,
                             uptime_seconds, fingerprint, raw_payload)
VALUES ('$(esc "$nickname")', $bytes_read, $bytes_written, NULL,
        $rate, $burst, $uptime_s, '$(esc "$fingerprint")', '$(esc "$raw_json")'::jsonb);
SQL
