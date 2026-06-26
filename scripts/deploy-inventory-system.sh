#!/bin/bash
# deploy-inventory-system.sh
# Deploys bhn-inventory-collector.sh to LA, NJ, and Hillsboro.
# Run this FROM LA (ssh root@<BHN_WG_LA_IP>) after applying the SQL schema.
#
# Prerequisites:
#   1. SQL schema applied on LA:
#        sudo -u postgres pg_dump eventhorizon > /mnt/eh-nvme-hot/backups/pre-node-inventory-$(date +%Y%m%d-%H%M).sql
#        sudo -u postgres psql -d eventhorizon -f sql/node-inventory-schema.sql
#   2. BHN_EHUSER_PG_PASS exported in your shell (ehuser password from Proton Pass: EH-Postgres-ehuser-2026-05-08)
#
# Usage:
#   export BHN_EHUSER_PG_PASS='<password>'
#   bash scripts/deploy-inventory-system.sh

set -euo pipefail

: "${BHN_EHUSER_PG_PASS:?Set BHN_EHUSER_PG_PASS to the ehuser PostgreSQL password}"

SCRIPT_SRC="$(cd "$(dirname "$0")" && pwd)/bhn-inventory-collector.sh"
REMOTE_SCRIPT=/usr/local/bin/bhn-inventory-collector.sh
CRON_FILE=/etc/cron.d/bhn-inventory
PG_HOST=<BHN_WG_LA_IP>
PG_DB=eventhorizon
PG_USER=ehuser

# Node definitions: "label|host|port|node_name"
declare -a NODES=(
    "LA|<BHN_WG_LA_IP>|22|BHN-LOSANGELES-US1"
    "NJ|<BHN_WG_NJ_IP>|2222|BHN-NEWJERSEY-US2"
    "Hillsboro|<BHN_WG_HIL_IP>|22|BHN-HILLSBORO-US3"
)

SSH_KEY=/root/.ssh/id_ed25519
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"

log() { echo "==> $*"; }

deploy_node() {
    local label="$1" host="$2" port="$3" node_name="$4"
    local ssh_cmd="ssh $SSH_OPTS -i $SSH_KEY -p $port root@$host"
    local scp_cmd="scp $SSH_OPTS -i $SSH_KEY -P $port"
    local dsn="postgresql://${PG_USER}:${BHN_EHUSER_PG_PASS}@${PG_HOST}:5432/${PG_DB}"

    log "[$label] Deploying collector script to $host:$port"
    $scp_cmd "$SCRIPT_SRC" "root@${host}:${REMOTE_SCRIPT}"
    $ssh_cmd "chmod 750 ${REMOTE_SCRIPT}"

    log "[$label] Writing env file"
    $ssh_cmd "cat > /root/.bhn-inventory.env <<'EOF'
# bhn-inventory-collector DSN — mode 0600
BHN_INVENTORY_PG_DSN=${dsn}
EOF
chmod 600 /root/.bhn-inventory.env"

    log "[$label] Installing cron job (every 30 min)"
    $ssh_cmd "cat > ${CRON_FILE} <<'EOF'
# BHN node inventory collector — runs every 30 minutes
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

*/30 * * * * root ${REMOTE_SCRIPT} >> /var/log/bhn-inventory-collector.log 2>&1
EOF
chmod 644 ${CRON_FILE}"

    log "[$label] Running collector now"
    $ssh_cmd "${REMOTE_SCRIPT}" && log "[$label] First run succeeded" \
        || log "[$label] WARNING: first run failed — check /var/log/bhn-inventory-collector.log"
}

for node_entry in "${NODES[@]}"; do
    IFS='|' read -r label host port node_name <<< "$node_entry"
    deploy_node "$label" "$host" "$port" "$node_name"
    echo
done

log "Deployment complete. Verify with:"
log "  sudo -u postgres psql -d eventhorizon -c 'SELECT node_name, service_type, COUNT(*) FROM node_services GROUP BY 1,2 ORDER BY 1,2;'"
log "  sudo -u postgres psql -d eventhorizon -c 'SELECT node_name, COUNT(*) FROM node_ports GROUP BY 1 ORDER BY 1;'"
log "  sudo -u postgres psql -d eventhorizon -c 'SELECT node_name, COUNT(*) FROM node_packages GROUP BY 1 ORDER BY 1;'"
