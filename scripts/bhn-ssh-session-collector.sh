#!/bin/bash
# bhn-ssh-session-collector — ship SSH sessions + auditd-tracked commands → LA PG.
#
# Two sources:
#   1. `last -F` — wtmp parse for session boundaries (login_at, logout_at,
#      user, tty, source_ip)
#   2. `ausearch -k bhn_ssh_cmd -ts <since>` — auditd execve events
#      (requires infrastructure/audit/bhn-ssh-audit.rules deployed)
#
# Reads PG DSN from /root/.bhn-ssh-sessions.env:
#   BHN_SSH_PG_DSN='postgresql://log_shipper:<PW>@10.8.0.1/eventhorizon'
#
# Cron (every node):
#   */5 * * * * root /usr/local/sbin/bhn-ssh-session-collector.sh

set -uo pipefail

ENV_FILE=/root/.bhn-ssh-sessions.env
INFO_FILE=/etc/eh-node-info.conf
STATE_DIR=/var/lib/bhn-ssh-collector

[[ -r "$ENV_FILE" ]]  || { echo "bhn-ssh: missing $ENV_FILE" >&2; exit 1; }
[[ -r "$INFO_FILE" ]] || { echo "bhn-ssh: missing $INFO_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
# shellcheck disable=SC1090
. "$INFO_FILE"
[[ -n "${BHN_SSH_PG_DSN:-}" ]] || { echo "bhn-ssh: BHN_SSH_PG_DSN empty" >&2; exit 1; }
[[ -n "${NODE_NAME:-}" ]]      || { echo "bhn-ssh: NODE_NAME empty"      >&2; exit 1; }

mkdir -p "$STATE_DIR"
LAST_CMD_TS_FILE="$STATE_DIR/last_cmd_ts"

esc() { printf '%s' "$1" | sed "s/'/''/g"; }
node_esc="$(esc "$NODE_NAME")"

# ─── Sessions from `last -F` ──────────────────────────────────────────
# `last -F` output (one line per session):
#   root  pts/0    1.2.3.4   Mon May 13 14:23:45 2026 - Mon May 13 14:45:12 2026  (00:21)
#   root  pts/0    1.2.3.4   Mon May 13 14:23:45 2026   still logged in
#
# Parse with awk: user is $1, tty $2, ip $3, then "Mon Day HH:MM:SS YYYY" x2.

values=""
count=0
while IFS= read -r line; do
  # Skip wtmp markers (reboot, etc.) — we only want user sessions.
  [[ "$line" =~ ^(reboot|wtmp|shutdown) ]] && continue
  # Pull user/tty/source — first 3 fields; source might be a hostname or IP.
  user=$(echo "$line" | awk '{print $1}')
  tty=$(echo "$line"  | awk '{print $2}')
  source=$(echo "$line" | awk '{print $3}')
  [[ -z "$user" ]] && continue
  # Login timestamp: fields 4-8 ("Mon May 13 14:23:45 2026")
  login_raw=$(echo "$line" | awk '{print $4, $5, $6, $7, $8}')
  login_at=$(date -d "$login_raw" --iso-8601=seconds 2>/dev/null || echo "")
  [[ -z "$login_at" ]] && continue
  # Logout: "still logged in" → NULL, else fields after " - "
  if echo "$line" | grep -q "still logged in"; then
    logout_lit="NULL"
    duration_lit="NULL"
  else
    logout_raw=$(echo "$line" | sed -E 's/.* - ([A-Za-z]+ [A-Za-z]+ +[0-9]+ [0-9:]+ [0-9]+).*/\1/')
    logout_at=$(date -d "$logout_raw" --iso-8601=seconds 2>/dev/null || echo "")
    if [[ -n "$logout_at" ]]; then
      logout_lit="'$logout_at'::timestamptz"
      duration_lit=$(( $(date -d "$logout_at" +%s) - $(date -d "$login_at" +%s) ))
    else
      logout_lit="NULL"; duration_lit="NULL"
    fi
  fi
  # source IP: try to parse as IP; fall back to NULL
  if [[ "$source" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || [[ "$source" =~ ^[0-9a-f:]+$ ]]; then
    src_lit="'$source'::inet"
  else
    src_lit="NULL"
  fi
  values+="('$node_esc','$(esc "$user")',$src_lit,'$(esc "$tty")','$login_at'::timestamptz,$logout_lit,$duration_lit,'$(esc "$line")'),"
  count=$((count + 1))
done < <(last -F -n 200 2>/dev/null || true)

values="${values%,}"
if [[ -n "$values" ]]; then
  psql "$BHN_SSH_PG_DSN" -v ON_ERROR_STOP=1 >/dev/null <<SQL \
    || { echo "bhn-ssh: sessions insert failed" >&2; exit 2; }
INSERT INTO ssh_sessions (node_name, user_name, source_ip, tty, login_at, logout_at, duration_s, raw_line)
VALUES $values
ON CONFLICT (node_name, user_name, source_ip, login_at) DO UPDATE SET
  logout_at = COALESCE(EXCLUDED.logout_at, ssh_sessions.logout_at),
  duration_s = COALESCE(EXCLUDED.duration_s, ssh_sessions.duration_s);
SQL
fi

# ─── Commands from auditd ─────────────────────────────────────────────
# Only run if auditd is available; otherwise skip silently.
if ! command -v ausearch >/dev/null; then
  exit 0
fi

# Pull commands since last cycle (or last 10 min if no state)
since_iso="10 minutes ago"
[[ -f "$LAST_CMD_TS_FILE" ]] && since_iso=$(cat "$LAST_CMD_TS_FILE")
new_high=$(date --iso-8601=seconds)

# ausearch -k bhn_ssh_cmd -ts "$since_iso" --format text
# Each event is multi-line; we use --raw + python-style oneline parsing via awk
cmd_values=""
while IFS= read -r line; do
  # Each "type=EXECVE" line has key=value pairs separated by spaces.
  type=$(echo "$line" | grep -oE 'type=[A-Z_]+' | head -1 | cut -d= -f2)
  [[ "$type" != "SYSCALL" && "$type" != "EXECVE" ]] && continue
  ts=$(echo "$line" | grep -oE 'msg=audit\([0-9.]+:[0-9]+\)' | sed 's/.*(\([0-9.]*\):.*/\1/')
  [[ -z "$ts" ]] && continue
  command_time=$(date -d "@${ts%.*}" --iso-8601=seconds 2>/dev/null) || continue
  ses=$(echo "$line" | grep -oE 'ses=[0-9]+' | cut -d= -f2)
  auid=$(echo "$line" | grep -oE 'auid=[0-9]+' | cut -d= -f2)
  uid=$(echo "$line"  | grep -oE ' uid=[0-9]+' | head -1 | tr -dc '0-9')
  exe=$(echo "$line"  | grep -oE 'exe="[^"]*"' | head -1 | sed 's/exe="\(.*\)"/\1/')
  args=$(echo "$line" | grep -oE 'a[0-9]+="[^"]*"' | sed 's/a[0-9]*="\(.*\)"/\1/' | tr '\n' ' ')
  cwd=$(echo "$line"  | grep -oE 'cwd="[^"]*"' | head -1 | sed 's/cwd="\(.*\)"/\1/')
  [[ -z "$exe" ]] && continue
  cmd_values+="('$node_esc',${ses:-NULL},${auid:-NULL},${uid:-NULL},'$command_time'::timestamptz,'$(esc "$exe")','$(esc "$args")','$(esc "$cwd")','$(esc "$line")'),"
done < <(ausearch -k bhn_ssh_cmd -ts "$since_iso" --raw 2>/dev/null || true)

cmd_values="${cmd_values%,}"
if [[ -n "$cmd_values" ]]; then
  psql "$BHN_SSH_PG_DSN" -v ON_ERROR_STOP=1 >/dev/null <<SQL \
    || { echo "bhn-ssh: commands insert failed" >&2; exit 2; }
INSERT INTO ssh_commands (node_name, ses_id, auid, uid, command_time, executable, args, cwd, raw_line)
VALUES $cmd_values;
SQL
  echo "$new_high" > "$LAST_CMD_TS_FILE"
fi
