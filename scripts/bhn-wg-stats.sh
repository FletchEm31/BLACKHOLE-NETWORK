#!/bin/bash
# bhn-wg-stats — sample WireGuard hub peer bandwidth + handshake state.
#
# Runs on LA hub via cron every 5 min. Parses `wg show wg0 dump` and
# INSERTs one row per peer into wg_peer_stats. Peer labels are derived
# from the tunnel IP via a hardcoded map (mirrors STATUS.md's WG peer
# registry — keep them in sync when peers are added/removed).
#
# Reads PG DSN from /root/.bhn-wg-stats.env (mode 0600):
#   BHN_WG_STATS_PG_DSN='postgresql://n8n_user:<PW>@<BHN_WG_LA_IP>/eventhorizon'
#
# Cron: every 5 minutes. Missing 3 consecutive runs → Grafana "stats
# pipeline stalled" alert (no-data on wg_peer_stats).
#
# Exit: 0 success, 1 missing config, 2 PG failure, 3 wg show failure.

set -euo pipefail

ENV_FILE=/root/.bhn-wg-stats.env
WG_IF=wg0

[[ -r "$ENV_FILE" ]] || { echo "bhn-wg-stats: missing $ENV_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
[[ -n "${BHN_WG_STATS_PG_DSN:-}" ]] || { echo "bhn-wg-stats: BHN_WG_STATS_PG_DSN empty" >&2; exit 1; }

# Map tunnel IP → human label. Update when a peer is added.
label_for_ip() {
    case "$1" in
        <BHN_WG_PEER_IP>) echo "Phone" ;;
        <BHN_WG_OPC_IP>) echo "PC" ;;
        <BHN_WG_NJ_IP>) echo "NJ" ;;
        <BHN_WG_PEER_IP>) echo "Fletch-Laptop-Split" ;;
        <BHN_WG_HIL_IP>) echo "Hillsboro" ;;
        <BHN_WG_FRA_IP>) echo "Frankfurt" ;;
        <BHN_WG_PEER_IP>) echo "Fletch-Laptop-Full" ;;
        <BHN_WG_PEER_IP>) echo "Fletch-Laptop-Full2" ;;
        *)        echo "Unknown-$1" ;;
    esac
}

# `wg show <iface> dump` output (tab-separated):
#   Line 1 (interface):  privkey  pubkey  listen_port  fwmark
#   Lines 2..N (peers):  pubkey  preshared_key  endpoint  allowed_ips  latest_handshake  rx_bytes  tx_bytes  persistent_keepalive
wg_output=$(wg show "$WG_IF" dump) || { echo "bhn-wg-stats: wg show failed" >&2; exit 3; }

sql_values=""
peer_count=0
while IFS=$'\t' read -r f1 f2 f3 f4 f5 f6 f7 f8; do
    # Peer rows have allowed_ips matching /32 (we only label single-host peers).
    # Interface row has only 4 fields, so f5..f8 will be empty — skip.
    [[ -z "${f4:-}" ]] && continue
    [[ "$f4" =~ ^[0-9.]+/32$ ]] || continue

    pubkey="$f1"
    endpoint="$f3"
    peer_ip="${f4%/32}"
    handshake="$f5"
    rx="$f6"
    tx="$f7"

    label=$(label_for_ip "$peer_ip")

    # Handshake: 0 = never; otherwise unix epoch.
    now_epoch=$(date +%s)
    if [[ "$handshake" == "0" ]]; then
        handshake_lit="NULL"
        age_lit="NULL"
        stale_lit="NULL"
    else
        handshake_lit="to_timestamp($handshake)"
        age=$(( now_epoch - handshake ))
        age_lit="$age"
        # Per wg-peer-stats-health-extension: 180s = stale threshold (3 min).
        if [[ $age -gt 180 ]]; then stale_lit="TRUE"; else stale_lit="FALSE"; fi
    fi

    # Endpoint may be "(none)" pre-handshake.
    if [[ "$endpoint" == "(none)" || -z "$endpoint" ]]; then
        endpoint_lit="NULL"
    else
        endpoint_lit="'${endpoint//\'/\'\'}'"
    fi

    # pubkey, peer_ip, label are all from controlled output — single-quote escape just in case.
    sql_values+="('${peer_ip//\'/\'\'}', '${label//\'/\'\'}', '${pubkey//\'/\'\'}', $rx, $tx, $handshake_lit, $endpoint_lit, $age_lit, $stale_lit),"
    peer_count=$((peer_count + 1))
done <<< "$wg_output"

[[ $peer_count -eq 0 ]] && { echo "bhn-wg-stats: no peer rows parsed from wg show" >&2; exit 0; }

# Strip trailing comma
sql_values="${sql_values%,}"

psql "$BHN_WG_STATS_PG_DSN" -v ON_ERROR_STOP=1 >/dev/null <<SQL \
  || { echo "bhn-wg-stats: PG insert failed" >&2; exit 2; }
INSERT INTO wg_peer_stats (peer_ip, peer_label, peer_pubkey, bytes_received, bytes_sent, latest_handshake, endpoint, handshake_age_seconds, is_stale)
VALUES $sql_values;
SQL
