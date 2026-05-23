#!/bin/bash
# bhn-security-events-collector.sh
# Populates security_events on LA from local log files:
#   - /var/log/ufw.log          (UFW BLOCK lines  -> ufw_block)
#   - /var/log/auth.log         (sshd failures    -> ssh_failure)
#   - /var/log/fail2ban.log     (Ban lines        -> fail2ban)
#
# Batched inserts (200 rows per psql call), idempotency via a state file
# (last-seen timestamp per source) at /var/lib/bhn-security-events/state.json -
# so re-running won't reprocess rows already ingested.
#
# Two failure modes that killed an earlier iteration under set -euo pipefail:
#   1. ON CONFLICT DO NOTHING on a table without a UNIQUE constraint - psql
#      raises an error, set -e fires, script exits silently. Fixed by simply
#      removing the clause; idempotency comes from the state file below, and
#      backfill duplicates are acceptable on security_events.
#   2. grep -oE returning 1 on no-match (e.g. UFW log line with "PROTO=41"
#      against /PROTO=[A-Z]+/) - pipefail propagates the failure, set -e
#      fires. Fixed by appending "|| true" to every grep-driven extraction
#      so a missing field just yields an empty variable rather than killing
#      the run.
#
# Deploy:
#   /usr/local/sbin/bhn-security-events-collector.sh   (chmod 0700, root)
#   /etc/cron.d/bhn-security-events                    (*/5 cadence)
#
# Env file (NOT in repo - holds DSN with password):
#   /root/.bhn-security-events.env
#     BHN_SEC_PG_DSN='postgresql://ehuser:<PASSWORD>@127.0.0.1/eventhorizon'

set -euo pipefail

ENV_FILE=/root/.bhn-security-events.env
STATE_FILE=/var/lib/bhn-security-events/state.json
STATE_DIR=$(dirname "$STATE_FILE")

[[ -r "$ENV_FILE" ]] || { echo "bhn-sec: missing $ENV_FILE" >&2; exit 1; }
. "$ENV_FILE"
[[ -n "${BHN_SEC_PG_DSN:-}" ]] || { echo "bhn-sec: BHN_SEC_PG_DSN empty" >&2; exit 1; }

mkdir -p "$STATE_DIR"

load_since() {
    local src="$1"
    [[ -f "$STATE_FILE" ]] && python3 -c "
import json
d=json.load(open('$STATE_FILE'))
print(d.get('$src','1970-01-01T00:00:00'))
" 2>/dev/null || echo "1970-01-01T00:00:00"
}

save_since() {
    local src="$1" ts="$2"
    python3 -c "
import json,os
f='$STATE_FILE'
d=json.load(open(f)) if os.path.exists(f) else {}
d['$src']='$ts'
json.dump(d,open(f,'w'))
" 2>/dev/null || true
}

esc() { echo "${1//\'/\'\'}"; }

flush_batch() {
    local values="$1"
    [[ -z "$values" ]] && return 0
    values="${values%,}"
    psql "$BHN_SEC_PG_DSN" -v ON_ERROR_STOP=1 -q <<SQL
INSERT INTO security_events (detected_at, source_ip, event_type, description, action_taken, severity)
VALUES $values;
SQL
}

total=0
BATCH_SIZE=200

# -- 1. UFW blocks ---------------------------------
UFW_LOG=/var/log/ufw.log
if [[ -f "$UFW_LOG" ]]; then
    since=$(load_since ufw)
    values=""; latest=""; count=0
    while IFS= read -r line; do
        [[ "$line" != *"[UFW BLOCK]"* ]] && continue
        raw_ts=$(echo "$line" | awk '{print $1" "$2" "$3}')
        ts=$(date -d "$raw_ts" --iso-8601=seconds 2>/dev/null) || continue
        [[ "$ts" > "$since" ]] || continue
        src_ip=$(echo "$line" | grep -oE 'SRC=[0-9.]+' | head -1 | cut -d= -f2 || true)
        dst_port=$(echo "$line" | grep -oE 'DPT=[0-9]+' | head -1 | cut -d= -f2 || true)
        proto=$(echo "$line" | grep -oE 'PROTO=[A-Z]+' | head -1 | cut -d= -f2 || true)
        desc="UFW blocked ${proto:-?} from ${src_ip:-?} to port ${dst_port:-?}"
        values+="('$ts'::timestamptz,$([ -n "$src_ip" ] && echo "'$src_ip'::inet" || echo "NULL"),'ufw_block','$(esc "$desc")','blocked','low'),"
        latest="$ts"; count=$((count+1)); total=$((total+1))
        if [[ $count -ge $BATCH_SIZE ]]; then
            flush_batch "$values"
            values=""; count=0
        fi
    done < "$UFW_LOG"
    flush_batch "$values"
    [[ -n "$latest" ]] && save_since ufw "$latest"
fi

# -- 2. SSH failures -------------------------------
AUTH_LOG=/var/log/auth.log
if [[ -f "$AUTH_LOG" ]]; then
    since=$(load_since ssh)
    values=""; latest=""; count=0
    while IFS= read -r line; do
        [[ "$line" != *"sshd"* ]] && continue
        [[ "$line" != *"Failed password"* ]] && [[ "$line" != *"Invalid user"* ]] && continue
        raw_ts=$(echo "$line" | awk '{print $1" "$2" "$3}')
        ts=$(date -d "$raw_ts" --iso-8601=seconds 2>/dev/null) || continue
        [[ "$ts" > "$since" ]] || continue
        src_ip=$(echo "$line" | grep -oE 'from [0-9.]+' | head -1 | awk '{print $2}' || true)
        user=$(echo "$line" | grep -oE '(for invalid user |for )[a-zA-Z0-9_-]+' | head -1 | awk '{print $NF}' || true)
        desc="SSH failure for user '${user:-?}' from ${src_ip:-?}"
        values+="('$ts'::timestamptz,$([ -n "$src_ip" ] && echo "'$src_ip'::inet" || echo "NULL"),'ssh_failure','$(esc "$desc")','logged','medium'),"
        latest="$ts"; count=$((count+1)); total=$((total+1))
        if [[ $count -ge $BATCH_SIZE ]]; then
            flush_batch "$values"
            values=""; count=0
        fi
    done < "$AUTH_LOG"
    flush_batch "$values"
    [[ -n "$latest" ]] && save_since ssh "$latest"
fi

# -- 3. fail2ban -----------------------------------
F2B_LOG=/var/log/fail2ban.log
if [[ -f "$F2B_LOG" ]]; then
    since=$(load_since fail2ban)
    values=""; latest=""; count=0
    while IFS= read -r line; do
        [[ "$line" != *"Ban "* ]] && continue
        raw_ts=$(echo "$line" | awk '{print $1" "$2}')
        ts=$(date -d "$raw_ts" --iso-8601=seconds 2>/dev/null) || continue
        [[ "$ts" > "$since" ]] || continue
        src_ip=$(echo "$line" | grep -oE 'Ban [0-9.]+' | awk '{print $2}' || true)
        jail=$(echo "$line" | grep -oE '\[[a-zA-Z0-9_-]+\]' | head -1 | tr -d '[]' || true)
        desc="fail2ban banned ${src_ip:-?} in jail ${jail:-?}"
        values+="('$ts'::timestamptz,$([ -n "$src_ip" ] && echo "'$src_ip'::inet" || echo "NULL"),'fail2ban','$(esc "$desc")','banned','high'),"
        latest="$ts"; count=$((count+1)); total=$((total+1))
        if [[ $count -ge $BATCH_SIZE ]]; then
            flush_batch "$values"
            values=""; count=0
        fi
    done < "$F2B_LOG"
    flush_batch "$values"
    [[ -n "$latest" ]] && save_since fail2ban "$latest"
fi

[[ $total -gt 0 ]] && echo "[$(date -u --iso-8601=seconds)] bhn-security-events: inserted $total rows"
exit 0
