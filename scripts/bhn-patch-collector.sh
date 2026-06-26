#!/bin/bash
# bhn-patch-collector — daily snapshot of pending apt updates → LA PG.
#
# Counts total pending packages + security pending + reads
# /var/run/reboot-required to surface kernel-pending reboots.
#
# Reads PG DSN from /root/.bhn-patch.env (mode 0600):
#   BHN_PATCH_PG_DSN='postgresql://log_shipper:<PW>@<BHN_WG_LA_IP>/eventhorizon'
#
# Cron (every node, daily — heavy apt update is once/day on the script):
#   0 5 * * * root /usr/local/sbin/bhn-patch-collector.sh
#
# Exit: 0 success, 1 missing config, 2 PG failure.

set -euo pipefail

ENV_FILE=/root/.bhn-patch.env
INFO_FILE=/etc/eh-node-info.conf

[[ -r "$ENV_FILE" ]]  || { echo "bhn-patch: missing $ENV_FILE" >&2; exit 1; }
[[ -r "$INFO_FILE" ]] || { echo "bhn-patch: missing $INFO_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
# shellcheck disable=SC1090
. "$INFO_FILE"
[[ -n "${BHN_PATCH_PG_DSN:-}" ]] || { echo "bhn-patch: BHN_PATCH_PG_DSN empty" >&2; exit 1; }
[[ -n "${NODE_NAME:-}" ]]        || { echo "bhn-patch: NODE_NAME empty" >&2; exit 1; }

# Refresh package index (quiet). Honors any global apt proxy config.
DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1 || true

# apt-get -s upgrade prints "Inst <pkg> ..." per upgradable package.
upgradable=$(apt-get -s upgrade 2>/dev/null | grep '^Inst ' || true)
pending_total=$(echo "$upgradable" | grep -c '^Inst ' || true)

# Security-only count — packages whose source line includes the security suite.
pending_security=$(echo "$upgradable" | grep -ciE '\((.*-security|.*Debian-Security)' || true)

reboot_required="false"
[[ -f /var/run/reboot-required ]] && reboot_required="true"

# Build a JSON array of {name, current, candidate, is_security}
pkg_json="[]"
if [[ -n "$upgradable" ]]; then
  pkg_json=$(echo "$upgradable" | awk '
    BEGIN { print "[" }
    {
      # Inst <name> [<current_ver>] (<candidate_ver> <repo>...)
      name=$2
      cur=""
      if ($3 ~ /^\[/) { cur=$3; gsub(/[\[\]]/,"",cur) }
      cand=""
      for (i=1;i<=NF;i++) if ($i ~ /^\(/) { cand=$i; sub(/^\(/,"",cand); break }
      is_sec="false"
      if (match($0, /\((.*-security|.*Debian-Security)/)) is_sec="true"
      if (NR>1) printf ","
      printf "{\"name\":\"%s\",\"current\":\"%s\",\"candidate\":\"%s\",\"is_security\":%s}", name, cur, cand, is_sec
    }
    END { print "]" }
  ')
fi

last_update="NULL"
if [[ -f /var/lib/apt/periodic/update-success-stamp ]]; then
  ts=$(stat -c %Y /var/lib/apt/periodic/update-success-stamp)
  last_update="to_timestamp($ts)"
fi

esc() { printf '%s' "$1" | sed "s/'/''/g"; }

psql "$BHN_PATCH_PG_DSN" -v ON_ERROR_STOP=1 >/dev/null <<SQL \
  || { echo "bhn-patch: PG insert failed" >&2; exit 2; }
INSERT INTO node_patch_status (node_name, pending_total, pending_security, reboot_required, pkg_list, last_apt_update_at)
VALUES ('$(esc "$NODE_NAME")', ${pending_total:-0}, ${pending_security:-0}, $reboot_required,
        '$(esc "$pkg_json")'::jsonb, $last_update);
SQL
