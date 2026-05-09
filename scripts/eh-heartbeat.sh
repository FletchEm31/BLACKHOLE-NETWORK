#!/bin/bash
# eh-heartbeat — report this node alive to the hub's nodes registry.
#
# Reads identity from /etc/eh-node-info.conf and PG DSN from
# /root/.eh-heartbeat.env (mode 0600). Updates nodes.last_seen on every run.
# Sets status='online' if the row is currently 'bootstrapping' or 'degraded' —
# does NOT clobber a deliberate 'decommissioned'.
#
# Cron: every 5 minutes. Three consecutive misses = stale (Grafana alert).
#
# Exit: 0 on success, 1 on missing config, 2 on PG failure.

set -euo pipefail

INFO=/etc/eh-node-info.conf
ENV_FILE=/root/.eh-heartbeat.env

[[ -r "$INFO" ]]     || { echo "eh-heartbeat: missing $INFO" >&2; exit 1; }
[[ -r "$ENV_FILE" ]] || { echo "eh-heartbeat: missing $ENV_FILE" >&2; exit 1; }

# shellcheck disable=SC1090
. "$INFO"
# shellcheck disable=SC1090
. "$ENV_FILE"

[[ -n "${NODE_NAME:-}" ]]              || { echo "eh-heartbeat: NODE_NAME empty"; exit 1; }
[[ -n "${EH_HEARTBEAT_PG_DSN:-}" ]]    || { echo "eh-heartbeat: EH_HEARTBEAT_PG_DSN empty"; exit 1; }

# Parameterized via psql `-v` (psql variable substitution) to avoid SQL
# injection from a tampered /etc/eh-node-info.conf. Note: `-v` substitution
# only works via stdin/`-f` — `-c` bypasses it. Hence the heredoc.
psql "$EH_HEARTBEAT_PG_DSN" \
    -v ON_ERROR_STOP=1 \
    -v node="$NODE_NAME" \
    >/dev/null <<'SQL' \
  || { echo "eh-heartbeat: PG update failed" >&2; exit 2; }
UPDATE nodes
SET last_seen = NOW(),
    status   = CASE
                 WHEN status IN ('bootstrapping','degraded') THEN 'online'
                 ELSE status
               END
WHERE name = :'node';
SQL
