#!/bin/bash
# bhn-crowdsec-collector — snapshot active CrowdSec decisions to LA PG.
#
# Runs on every node via cron every 5 min. `cscli decisions list -o json`
# returns the currently active set (bans, captchas, throttles). One row per
# decision per measurement. Operator can `SELECT … WHERE measured_at = MAX`
# for "current state" and the whole table is the history.
#
# Reads PG DSN from /root/.bhn-crowdsec.env (mode 0600):
#   BHN_CROWDSEC_PG_DSN='postgresql://log_shipper:<PW>@<BHN_WG_LA_IP>/eventhorizon'
#
# Cron entry on each node (/etc/cron.d/bhn-crowdsec-collector):
#   */5 * * * * root /usr/local/sbin/bhn-crowdsec-collector.sh
#
# Exit: 0 success, 1 missing config, 2 PG failure, 3 cscli failure.

set -euo pipefail

ENV_FILE=/root/.bhn-crowdsec.env
INFO_FILE=/etc/eh-node-info.conf

[[ -r "$ENV_FILE" ]]  || { echo "bhn-crowdsec: missing $ENV_FILE" >&2; exit 1; }
[[ -r "$INFO_FILE" ]] || { echo "bhn-crowdsec: missing $INFO_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
# shellcheck disable=SC1090
. "$INFO_FILE"
[[ -n "${BHN_CROWDSEC_PG_DSN:-}" ]] || { echo "bhn-crowdsec: BHN_CROWDSEC_PG_DSN empty" >&2; exit 1; }
[[ -n "${NODE_NAME:-}" ]]           || { echo "bhn-crowdsec: NODE_NAME empty" >&2; exit 1; }

command -v cscli >/dev/null || { echo "bhn-crowdsec: cscli not installed" >&2; exit 3; }
command -v jq    >/dev/null || { echo "bhn-crowdsec: jq not installed"    >&2; exit 3; }

# cscli decisions list -o json returns [] when there are no decisions.
decisions=$(cscli decisions list -o json 2>/dev/null) || { echo "bhn-crowdsec: cscli failed" >&2; exit 3; }
[[ "$(echo "$decisions" | jq 'length')" == "0" ]] && exit 0

esc() { printf '%s' "$1" | sed "s/'/''/g"; }

# Each decision shape (CrowdSec API): {id, origin, type, scope, value, scenario, duration, ...}
rows=$(echo "$decisions" | jq -r '.[] |
  [
    (.id      | tostring),
    (.origin   // ""),
    (.scenario // ""),
    (.type     // ""),
    (.value    // ""),
    (.scope    // ""),
    (.duration // ""),
    (. | tojson)
  ] | @tsv')

values=""
while IFS=$'\t' read -r did origin scenario dtype dvalue scope duration raw; do
  [[ -z "$did" ]] && continue
  # Duration is like "23h59m45s" — parse to seconds via date arithmetic for portability.
  duration_s="NULL"
  if [[ -n "$duration" ]]; then
    secs=$(echo "$duration" | awk '
      { t=0; s=$0
        if (match(s, /([0-9]+)h/, m)) t += m[1]*3600
        if (match(s, /([0-9]+)m/, m)) t += m[1]*60
        if (match(s, /([0-9]+)s/, m)) t += m[1]
        print t
      }')
    [[ -n "$secs" && "$secs" -gt 0 ]] && duration_s="$secs"
  fi
  if [[ "$duration_s" != "NULL" ]]; then
    expires="NOW() + INTERVAL '$duration_s seconds'"
  else
    expires="NULL"
  fi
  values+="('$(esc "$NODE_NAME")',$did,'$(esc "$origin")','$(esc "$scenario")','$(esc "$dtype")','$(esc "$dvalue")','$(esc "$scope")',$duration_s,$expires,'$(esc "$raw")'::jsonb),"
done <<< "$rows"
values="${values%,}"
[[ -z "$values" ]] && exit 0

psql "$BHN_CROWDSEC_PG_DSN" -v ON_ERROR_STOP=1 >/dev/null <<SQL \
  || { echo "bhn-crowdsec: PG insert failed" >&2; exit 2; }
INSERT INTO crowdsec_decisions (node_name, decision_id, origin, scenario, type, value, scope, duration_s, expires_at, raw_payload)
VALUES $values
ON CONFLICT (node_name, decision_id, measured_at) DO NOTHING;
SQL
