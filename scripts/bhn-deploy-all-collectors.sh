#!/bin/bash
# bhn-deploy-all-collectors — master deploy/test/status for internal collectors.
#
# Runs FROM LA. Internal (no-API-key) collectors only. For each node it:
#   1. ships the collector to /usr/local/sbin/   (scp + chmod 0755)
#   2. writes /root/.bhn-<short>.env             (PG DSN only, 0600)
#   3. writes /etc/cron.d/<basename>             (schedule from script header)
#   4. runs one test execution
#   5. verifies ≥1 row landed in the destination table (delta count)
#   6. prints a pass/no-data/fail line per (node, collector)
#
# Modes:
#   deploy   full install + verify
#   test     test-run + verify (assumes already deployed)
#   status   probe script/env/cron presence per (node, collector)
#
# Node selection flags (mutually exclusive — pick one or neither):
#   --nodes A,B,C        only operate on the listed nodes
#   --skip-nodes A,B     operate on every node EXCEPT the listed ones
# Examples:
#   ./bhn-deploy-all-collectors.sh deploy --nodes LA,Frankfurt,NJ
#   ./bhn-deploy-all-collectors.sh deploy --skip-nodes Hillsboro
#   ./bhn-deploy-all-collectors.sh status
# Valid node names: LA Frankfurt Hillsboro NJ (case-sensitive).
#
# Topology (set by operator, kept in sync with infrastructure/docs/):
#   LA          local            vnstat iptables docker-stats pg-stats
#                                n8n-stats fail2ban dns-log wg-stats
#                                conntrack resource-stats
#   Frankfurt   ssh frankfurt    vnstat iptables fail2ban dns-log
#                                conntrack resource-stats tor-stats
#   Hillsboro   ssh hillsboro    vnstat iptables fail2ban dns-log
#                                conntrack resource-stats tor-stats proxy-log
#   NJ          ssh -p 2222 root@<BHN_WG_NJ_IP>  vnstat iptables fail2ban dns-log
#                                          conntrack resource-stats
#               (NJ runs sshd on port 2222 — alias `nj` is not used, the script
#                dials the WG tunnel IP + explicit port directly so it works
#                regardless of operator ~/.ssh/config state.)
#
# Note on DSN: spec forces every collector to use log_shipper for simplicity.
# tor-stats and wg-stats schemas were originally provisioned around n8n_user;
# if log_shipper lacks INSERT on tor_relay_stats / wg_peer_stats those will
# surface as FAIL (exit=2 on remote). Fix on LA with a GRANT, not in this script.

set -uo pipefail

usage() {
    echo "Usage: $0 {deploy|test|status} [--nodes A,B,C | --skip-nodes A,B]" >&2
    echo "  Valid node names: LA Frankfurt Hillsboro NJ" >&2
}

MODE="${1:-}"
case "$MODE" in
    deploy|test|status) shift ;;
    *) usage; exit 1 ;;
esac

INCLUDE_NODES=""
SKIP_NODES=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --nodes)        INCLUDE_NODES="${2:-}"; shift 2 || { usage; exit 1; } ;;
        --nodes=*)      INCLUDE_NODES="${1#--nodes=}"; shift ;;
        --skip-nodes)   SKIP_NODES="${2:-}"; shift 2 || { usage; exit 1; } ;;
        --skip-nodes=*) SKIP_NODES="${1#--skip-nodes=}"; shift ;;
        -h|--help)      usage; exit 0 ;;
        *)              echo "Unknown argument: $1" >&2; usage; exit 1 ;;
    esac
done

if [[ -n "$INCLUDE_NODES" && -n "$SKIP_NODES" ]]; then
    echo "Error: --nodes and --skip-nodes are mutually exclusive." >&2
    exit 1
fi

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
LA_PG_DSN="postgresql://log_shipper:BHN-LogShipper-2026@<BHN_WG_LA_IP>/eventhorizon"

# ----- node + collector topology --------------------------------------------
declare -A NODE_SSH=(
    [LA]=""                  # empty target = run locally
    [Frankfurt]="frankfurt"  # SSH config alias on LA (~/.ssh/config)
    [Hillsboro]="hillsboro"  # SSH config alias on LA
    [NJ]="root@<BHN_WG_NJ_IP>"     # explicit user@tunnel-IP — port set in NODE_SSH_PORT
)
# Per-node SSH/SCP port override. Unset/empty = default 22.
# NJ runs sshd on 2222 (Vultr template hardening); we set it here so the
# script works even without an ~/.ssh/config entry for nj.
declare -A NODE_SSH_PORT=(
    [NJ]="2222"
)
NODE_ORDER=(LA Frankfurt Hillsboro NJ)

# Apply --nodes / --skip-nodes filtering. CSV input, case-sensitive,
# unknown names abort before any work begins.
apply_node_filter() {
    local csv="$1" mode="$2"          # mode = include | skip
    local -A wanted=()
    local IFS_SAVE="$IFS"
    IFS=',' read -ra parts <<<"$csv"
    IFS="$IFS_SAVE"
    local p
    for p in "${parts[@]}"; do
        p="${p#"${p%%[![:space:]]*}"}"
        p="${p%"${p##*[![:space:]]}"}"
        [[ -z "$p" ]] && continue
        if [[ -z "${NODE_SSH[$p]+x}" ]]; then
            echo "Error: unknown node '$p' (valid: ${NODE_ORDER[*]})" >&2
            exit 1
        fi
        wanted["$p"]=1
    done
    local filtered=() n
    for n in "${NODE_ORDER[@]}"; do
        if [[ "$mode" == include ]]; then
            [[ -n "${wanted[$n]:-}" ]] && filtered+=("$n")
        else
            [[ -z "${wanted[$n]:-}" ]] && filtered+=("$n")
        fi
    done
    if [[ ${#filtered[@]} -eq 0 ]]; then
        echo "Error: node filter selected zero nodes — nothing to do." >&2
        exit 1
    fi
    NODE_ORDER=("${filtered[@]}")
}

if [[ -n "$INCLUDE_NODES" ]]; then
    apply_node_filter "$INCLUDE_NODES" include
elif [[ -n "$SKIP_NODES" ]]; then
    apply_node_filter "$SKIP_NODES" skip
fi

declare -A NODE_COLLECTORS=(
    [LA]="vnstat iptables docker-stats pg-stats n8n-stats fail2ban dns-log wg-stats conntrack resource-stats"
    [Frankfurt]="vnstat iptables fail2ban dns-log conntrack resource-stats tor-stats"
    [Hillsboro]="vnstat iptables fail2ban dns-log conntrack resource-stats tor-stats proxy-log"
    [NJ]="vnstat iptables fail2ban dns-log conntrack resource-stats"
)

# script | env_file | env_var | target_table | node_col | cron
# node_col blank = LA-only collectors that don't filter rows by node_name.
declare -A COLLECTOR_META=(
    [vnstat]="bhn-vnstat-collector.sh|/root/.bhn-vnstat.env|BHN_VNSTAT_PG_DSN|node_bandwidth_stats|node_name|*/15 * * * *"
    [iptables]="bhn-iptables-collector.sh|/root/.bhn-iptables.env|BHN_IPTABLES_PG_DSN|iptables_stats|node_name|*/5 * * * *"
    [docker-stats]="bhn-docker-stats-collector.sh|/root/.bhn-docker-stats.env|BHN_DOCKER_STATS_PG_DSN|container_stats|node_name|*/5 * * * *"
    [pg-stats]="bhn-pg-stats-collector.sh|/root/.bhn-pg-stats.env|BHN_PG_STATS_PG_DSN|pg_activity_snapshots||*/5 * * * *"
    [n8n-stats]="bhn-n8n-stats-collector.sh|/root/.bhn-n8n-stats.env|BHN_N8N_STATS_PG_DSN|n8n_execution_stats||*/5 * * * *"
    [fail2ban]="bhn-fail2ban-collector.sh|/root/.bhn-fail2ban.env|BHN_FAIL2BAN_PG_DSN|fail2ban_events|node_name|*/5 * * * *"
    [dns-log]="bhn-dns-log-collector.sh|/root/.bhn-dns-log.env|BHN_DNS_LOG_PG_DSN|dns_query_log|node_name|*/5 * * * *"
    [wg-stats]="bhn-wg-stats.sh|/root/.bhn-wg-stats.env|BHN_WG_STATS_PG_DSN|wg_peer_stats||*/5 * * * *"
    [conntrack]="bhn-conntrack-collector.sh|/root/.bhn-conntrack.env|BHN_CONNTRACK_PG_DSN|connection_snapshots|node_name|*/5 * * * *"
    [resource-stats]="bhn-resource-collector.sh|/root/.bhn-resource.env|BHN_RESOURCE_PG_DSN|node_resource_stats|node_name|*/5 * * * *"
    [tor-stats]="bhn-tor-stats.sh|/root/.bhn-tor-stats.env|BHN_TOR_STATS_PG_DSN|tor_relay_stats|node|*/5 * * * *"
    [proxy-log]="bhn-proxy-log-collector.sh|/root/.bhn-proxy-log.env|BHN_PROXY_LOG_PG_DSN|proxy_request_logs|node_name|*/5 * * * *"
)

# ----- summary counters -----------------------------------------------------
TOTAL_PASS=0
TOTAL_NODATA=0
TOTAL_FAIL=0
SUMMARY=()

record() {
    local node="$1" short="$2" status="$3" detail="$4"
    SUMMARY+=("$(printf '%-10s  %-14s  %-7s  %s' "$node" "$short" "$status" "$detail")")
    case "$status" in
        PASS)    TOTAL_PASS=$((TOTAL_PASS+1)) ;;
        NO-DATA) TOTAL_NODATA=$((TOTAL_NODATA+1)) ;;
        FAIL)    TOTAL_FAIL=$((TOTAL_FAIL+1)) ;;
    esac
}

split_meta() {
    IFS='|' read -r SCRIPT_FILE ENV_FILE ENV_VAR TABLE NODE_COL CRON <<<"${COLLECTOR_META[$1]}"
}

# Cross-check COLLECTOR_META env-var names against the actual collector source.
# Each collector reads its OWN env var (BHN_RESOURCE_PG_DSN, BHN_IPTABLES_PG_DSN,
# etc.) — writing the wrong name silently breaks the cron because the script
# can't find its DSN. This guard catches drift where a collector was renamed or
# its env-var changed without updating the meta table here.
verify_collector_env_vars() {
    local mismatches=() short
    for short in "${!COLLECTOR_META[@]}"; do
        local script_file env_file env_var rest
        IFS='|' read -r script_file env_file env_var rest <<<"${COLLECTOR_META[$short]}"
        local src="$SCRIPTS_DIR/$script_file"
        if [[ ! -f "$src" ]]; then
            mismatches+=("$short: source $script_file missing in $SCRIPTS_DIR")
            continue
        fi
        if ! grep -wq "$env_var" "$src"; then
            mismatches+=("$short: meta declares $env_var but $script_file never references it")
        fi
    done
    if (( ${#mismatches[@]} > 0 )); then
        echo "ERROR: COLLECTOR_META / collector-source env-var drift:" >&2
        printf '  - %s\n' "${mismatches[@]}" >&2
        echo "Fix the meta table or the collector script before re-running." >&2
        exit 3
    fi
}

# ----- node helpers ---------------------------------------------------------
node_exec() {
    local node="$1"; shift
    local target="${NODE_SSH[$node]}"
    if [[ -z "$target" ]]; then
        bash -c "$*"
    else
        local -a args=(-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3)
        local port="${NODE_SSH_PORT[$node]:-}"
        [[ -n "$port" ]] && args+=(-p "$port")
        ssh "${args[@]}" "$target" "$*"
    fi
}

node_send_file() {
    local node="$1" src="$2" dst="$3" mode="$4"
    local target="${NODE_SSH[$node]}"
    if [[ -z "$target" ]]; then
        install -m "$mode" "$src" "$dst"
    else
        local -a scp_args=(-q -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3)
        local -a ssh_args=(-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3)
        local port="${NODE_SSH_PORT[$node]:-}"
        if [[ -n "$port" ]]; then
            scp_args+=(-P "$port")
            ssh_args+=(-p "$port")
        fi
        scp "${scp_args[@]}" "$src" "$target:$dst" \
          && ssh "${ssh_args[@]}" "$target" "chmod $mode '$dst'"
    fi
}

# Write content to a remote/local file with explicit mode. Uses a tight umask
# so the file is never world-readable between create and chmod.
node_write_file() {
    local node="$1" dst="$2" mode="$3" content="$4"
    local target="${NODE_SSH[$node]}"
    if [[ -z "$target" ]]; then
        ( umask 077 && printf '%s' "$content" > "$dst" ) && chmod "$mode" "$dst"
    else
        local -a args=(-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3)
        local port="${NODE_SSH_PORT[$node]:-}"
        [[ -n "$port" ]] && args+=(-p "$port")
        printf '%s' "$content" | ssh "${args[@]}" "$target" \
            "umask 077 && cat > '$dst' && chmod $mode '$dst'"
    fi
}

pg_q() { psql -At "$LA_PG_DSN" -c "$1" 2>/dev/null; }

# ----- per-collector operations ---------------------------------------------
deploy_one() {
    local node="$1" short="$2"
    split_meta "$short"

    local src="$SCRIPTS_DIR/$SCRIPT_FILE"
    if [[ ! -f "$src" ]]; then
        record "$node" "$short" "FAIL" "source missing: $src"
        return 1
    fi

    if ! node_send_file "$node" "$src" "/usr/local/sbin/$SCRIPT_FILE" 0755; then
        record "$node" "$short" "FAIL" "scp/install failed"
        return 1
    fi

    # Per-collector env var — NOT shared. Each collector script sources its
    # own /root/.bhn-<name>.env and reads its specific var name. We write the
    # var name straight from COLLECTOR_META so the cron will find its DSN.
    local env_content
    env_content="$(printf "%s='%s'\n" "$ENV_VAR" "$LA_PG_DSN")"
    echo "    [$node/$short] $ENV_FILE  ←  $ENV_VAR=<log_shipper DSN>"
    if ! node_write_file "$node" "$ENV_FILE" 0600 "$env_content"; then
        record "$node" "$short" "FAIL" "env write failed"
        return 1
    fi

    local cron_base="${SCRIPT_FILE%.sh}"
    local cron_content
    cron_content="$(printf 'SHELL=/bin/bash\nPATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n%s root /usr/local/sbin/%s\n' "$CRON" "$SCRIPT_FILE")"
    if ! node_write_file "$node" "/etc/cron.d/$cron_base" 0644 "$cron_content"; then
        record "$node" "$short" "FAIL" "cron write failed"
        return 1
    fi
    return 0
}

verify_run() {
    local node="$1" short="$2"
    split_meta "$short"

    local node_name="" where_clause="TRUE"
    if [[ -n "$NODE_COL" ]]; then
        node_name="$(node_exec "$node" '. /etc/eh-node-info.conf 2>/dev/null && printf %s "$NODE_NAME"' 2>/dev/null)"
        if [[ -z "$node_name" ]]; then
            record "$node" "$short" "FAIL" "remote /etc/eh-node-info.conf missing or NODE_NAME empty"
            return 1
        fi
        local esc; esc="$(printf %s "$node_name" | sed "s/'/''/g")"
        where_clause="$NODE_COL = '$esc'"
    fi

    local before after rc=0
    before="$(pg_q "SELECT COUNT(*) FROM $TABLE WHERE $where_clause;")"
    if [[ -z "$before" ]]; then
        record "$node" "$short" "FAIL" "PG pre-count failed (table=$TABLE)"
        return 1
    fi

    if ! node_exec "$node" "/usr/local/sbin/$SCRIPT_FILE" >/dev/null 2>&1; then
        rc=$?
        record "$node" "$short" "FAIL" "exit=$rc on remote (script=$SCRIPT_FILE)"
        return 1
    fi

    after="$(pg_q "SELECT COUNT(*) FROM $TABLE WHERE $where_clause;")"
    if [[ -z "$after" ]]; then
        record "$node" "$short" "FAIL" "PG post-count failed (table=$TABLE)"
        return 1
    fi

    local delta=$((after - before))
    if (( delta >= 1 )); then
        record "$node" "$short" "PASS" "+$delta row(s) → $TABLE"
        return 0
    else
        record "$node" "$short" "NO-DATA" "exit=0, 0 new rows in $TABLE (no events to ship?)"
        return 0
    fi
}

status_one() {
    local node="$1" short="$2"
    split_meta "$short"
    local cron_base="${SCRIPT_FILE%.sh}"
    local probe rc=0
    probe="$(node_exec "$node" "
        if [ -x /usr/local/sbin/$SCRIPT_FILE ]; then SC=yes; else SC=no; fi
        if [ -r '$ENV_FILE' ];                      then EN=yes; else EN=no; fi
        if [ -f /etc/cron.d/$cron_base ];           then CR=yes; else CR=no; fi
        printf '%s|%s|%s' \$SC \$EN \$CR
    ")" || rc=$?
    if (( rc != 0 )); then
        record "$node" "$short" "FAIL" "ssh probe failed (rc=$rc)"
        return 1
    fi
    local SC EN CR
    IFS='|' read -r SC EN CR <<<"$probe"
    if [[ "$SC" == yes && "$EN" == yes && "$CR" == yes ]]; then
        record "$node" "$short" "PASS" "script=$SC env=$EN cron=$CR"
    else
        record "$node" "$short" "FAIL" "script=$SC env=$EN cron=$CR"
    fi
}

# ----- preflight ------------------------------------------------------------
echo "BHN deploy-all-collectors — mode=$MODE"
echo "Source: $SCRIPTS_DIR"
echo

verify_collector_env_vars

for node in "${NODE_ORDER[@]}"; do
    target="${NODE_SSH[$node]}"
    if [[ -z "$target" ]]; then
        echo "  $node: localhost ok"
    else
        probe_args=(-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3)
        probe_port="${NODE_SSH_PORT[$node]:-}"
        [[ -n "$probe_port" ]] && probe_args+=(-p "$probe_port")
        probe_label="$target${probe_port:+ (port $probe_port)}"
        if ssh "${probe_args[@]}" "$target" "echo ok" >/dev/null 2>&1; then
            echo "  $node: ssh $probe_label ok"
        else
            echo "  $node: ssh $probe_label FAILED — will be marked unreachable"
            NODE_SSH[$node]="__SKIP__"
        fi
    fi
done

if ! pg_q "SELECT 1;" >/dev/null; then
    echo
    echo "ERROR: cannot reach LA PG with log_shipper DSN — aborting."
    exit 2
fi
echo

# ----- main loop ------------------------------------------------------------
for node in "${NODE_ORDER[@]}"; do
    if [[ "${NODE_SSH[$node]}" == "__SKIP__" ]]; then
        for short in ${NODE_COLLECTORS[$node]}; do
            record "$node" "$short" "FAIL" "node unreachable (preflight)"
        done
        continue
    fi

    echo "================== $node =================="
    for short in ${NODE_COLLECTORS[$node]}; do
        case "$MODE" in
            deploy)
                if deploy_one "$node" "$short"; then
                    verify_run "$node" "$short"
                fi
                ;;
            test)
                verify_run "$node" "$short"
                ;;
            status)
                status_one "$node" "$short"
                ;;
        esac
    done
    echo
done

# ----- summary --------------------------------------------------------------
echo "===================== SUMMARY ====================="
printf '%-10s  %-14s  %-7s  %s\n' "NODE" "COLLECTOR" "STATE" "DETAIL"
printf '%-10s  %-14s  %-7s  %s\n' "----" "---------" "-----" "------"
for line in "${SUMMARY[@]}"; do printf '%s\n' "$line"; done
echo
echo "Pass: $TOTAL_PASS    No-data: $TOTAL_NODATA    Fail: $TOTAL_FAIL"

exit $(( TOTAL_FAIL > 0 ? 1 : 0 ))
