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
# Topology (set by operator, kept in sync with infrastructure/docs/):
#   LA          local            vnstat iptables docker-stats pg-stats
#                                n8n-stats fail2ban dns-log wg-stats
#                                conntrack resource-stats
#   Frankfurt   ssh frankfurt    vnstat iptables fail2ban dns-log
#                                conntrack resource-stats tor-stats
#   Hillsboro   ssh hillsboro    vnstat iptables fail2ban dns-log
#                                conntrack resource-stats tor-stats proxy-log
#   NJ          ssh nj  (-p2222) vnstat iptables fail2ban dns-log
#                                conntrack resource-stats
#
# Note on DSN: spec forces every collector to use log_shipper for simplicity.
# tor-stats and wg-stats schemas were originally provisioned around n8n_user;
# if log_shipper lacks INSERT on tor_relay_stats / wg_peer_stats those will
# surface as FAIL (exit=2 on remote). Fix on LA with a GRANT, not in this script.

set -uo pipefail

MODE="${1:-}"
case "$MODE" in
    deploy|test|status) ;;
    *) echo "Usage: $0 {deploy|test|status}" >&2; exit 1 ;;
esac

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
LA_PG_DSN="postgresql://log_shipper:BHN-LogShipper-2026@10.8.0.1/eventhorizon"

# ----- node + collector topology --------------------------------------------
declare -A NODE_SSH=(
    [LA]=""                # empty target = run locally
    [Frankfurt]="frankfurt"
    [Hillsboro]="hillsboro"
    [NJ]="nj"
)
NODE_ORDER=(LA Frankfurt Hillsboro NJ)

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

# ----- node helpers ---------------------------------------------------------
node_exec() {
    local node="$1"; shift
    local target="${NODE_SSH[$node]}"
    if [[ -z "$target" ]]; then
        bash -c "$*"
    else
        ssh -o BatchMode=yes -o ConnectTimeout=10 "$target" "$*"
    fi
}

node_send_file() {
    local node="$1" src="$2" dst="$3" mode="$4"
    local target="${NODE_SSH[$node]}"
    if [[ -z "$target" ]]; then
        install -m "$mode" "$src" "$dst"
    else
        scp -q -o BatchMode=yes -o ConnectTimeout=10 "$src" "$target:$dst" \
          && ssh -o BatchMode=yes -o ConnectTimeout=10 "$target" "chmod $mode '$dst'"
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
        printf '%s' "$content" | ssh -o BatchMode=yes -o ConnectTimeout=10 "$target" \
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

    local env_content
    env_content="$(printf "%s='%s'\n" "$ENV_VAR" "$LA_PG_DSN")"
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

for node in "${NODE_ORDER[@]}"; do
    target="${NODE_SSH[$node]}"
    if [[ -z "$target" ]]; then
        echo "  $node: localhost ok"
    else
        if ssh -o BatchMode=yes -o ConnectTimeout=10 "$target" "echo ok" >/dev/null 2>&1; then
            echo "  $node: ssh $target ok"
        else
            echo "  $node: ssh $target FAILED — will be marked unreachable"
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
