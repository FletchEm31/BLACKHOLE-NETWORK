#!/bin/bash
# bhn-node-offline-recover — best-effort non-destructive probe of offline nodes.
#
# Runs on LA via cron every 5 min. Queries the nodes table for any node whose
# last_seen is older than 10 min, then for each:
#   1. SSH ping (8s timeout) via ~/.ssh/config alias if present, else by tunnel IP
#   2. If SSH succeeds: check `wg show` + `systemctl is-active eh-heartbeat.service`
#   3. If WG handshake is stale but heartbeat is up: log and POST to n8n webhook
#   4. If SSH fails entirely: log + POST to n8n webhook
#
# Does NOT auto-restart services — that's operator's call. Output goes to:
#   - stdout/stderr (cron logs)
#   - POST to webhook in $BHN_RECOVER_WEBHOOK_URL (n8n receives, fires SMS)
#
# Reads PG DSN + webhook from /root/.bhn-node-offline-recover.env:
#   BHN_RECOVER_PG_DSN='postgresql://ehuser:<PW>@<BHN_WG_LA_IP>/eventhorizon'
#   BHN_RECOVER_WEBHOOK_URL='http://<BHN_WG_LA_IP>:5678/webhook/<TOKEN>/node-offline'
#
# Cron (LA): */5 * * * * root /usr/local/sbin/bhn-node-offline-recover.sh

set -uo pipefail

ENV_FILE=/root/.bhn-node-offline-recover.env
[[ -r "$ENV_FILE" ]] || { echo "bhn-node-recover: missing $ENV_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
[[ -n "${BHN_RECOVER_PG_DSN:-}" ]]      || { echo "bhn-node-recover: BHN_RECOVER_PG_DSN empty" >&2; exit 1; }

# Pull offline nodes (last_seen > 10 min)
offline=$(psql "$BHN_RECOVER_PG_DSN" -At -F '|' -v ON_ERROR_STOP=1 <<'SQL'
SELECT name, tunnel_ip, public_ip, EXTRACT(EPOCH FROM (NOW() - last_seen))::int AS stale_seconds
FROM nodes
WHERE status NOT IN ('decommissioned')
  AND (last_seen IS NULL OR last_seen < NOW() - INTERVAL '10 minutes');
SQL
)
[[ -z "$offline" ]] && exit 0

report() {
  local node="$1" outcome="$2" detail="$3"
  echo "[bhn-node-recover] $node: $outcome — $detail"
  if [[ -n "${BHN_RECOVER_WEBHOOK_URL:-}" ]]; then
    payload=$(printf '{"node":"%s","outcome":"%s","detail":"%s","ts":"%s"}' \
      "$node" "$outcome" "$detail" "$(date -u +%FT%TZ)")
    curl -fsS --max-time 6 -H 'Content-Type: application/json' \
      -d "$payload" "$BHN_RECOVER_WEBHOOK_URL" >/dev/null 2>&1 \
      || echo "[bhn-node-recover] webhook POST failed for $node"
  fi
}

while IFS='|' read -r node tunnel_ip public_ip stale_s; do
  [[ -z "$node" ]] && continue
  target="${tunnel_ip:-$public_ip}"
  [[ -z "$target" ]] && { report "$node" "no-address" "no tunnel_ip or public_ip in nodes table"; continue; }

  # SSH probe (8s connect, 5s exec). Use BatchMode so it never prompts.
  if ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new \
         "$target" 'true' 2>/dev/null; then
    # SSH up — check WG handshake age + heartbeat service
    wg_handshake_age=$(ssh -o BatchMode=yes -o ConnectTimeout=5 "$target" \
      "wg show 2>/dev/null | awk '/latest handshake:/ {found=1} END {print found}'" 2>/dev/null)
    heartbeat_active=$(ssh -o BatchMode=yes -o ConnectTimeout=5 "$target" \
      "systemctl is-active eh-heartbeat.service 2>/dev/null || true" 2>/dev/null)
    report "$node" "ssh-reachable" "stale=${stale_s}s heartbeat=$heartbeat_active wg_peer_with_handshake=$wg_handshake_age"
  else
    report "$node" "ssh-unreachable" "stale=${stale_s}s target=$target — likely host down, ISP issue, or WG tunnel broken"
  fi
done <<< "$offline"
