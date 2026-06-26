#!/bin/bash
# bhn-inventory-collector.sh
# Collects Docker containers, BHN-relevant systemd services, listening ports,
# and key package versions from this node. Writes results to the eventhorizon
# PostgreSQL database on LA (<BHN_WG_LA_IP>) via the WireGuard tunnel.
#
# Cron: every 30 minutes (see /etc/cron.d/bhn-inventory)
# Idempotent — uses upsert for services/packages; DELETE+INSERT for ports.
#
# Required files on each node:
#   /etc/bhn-node-info.conf  — must export NODE_NAME
#   /root/.bhn-inventory.env — must export BHN_INVENTORY_PG_DSN
#     Example DSN: postgresql://ehuser:PASSWORD@<BHN_WG_LA_IP>:5432/eventhorizon
#
# Exit: 0 success, 1 config error, 2 PG error

set -euo pipefail

INFO=/etc/bhn-node-info.conf
ENV_FILE=/root/.bhn-inventory.env

[[ -r "$INFO" ]]     || { echo "bhn-inventory-collector: missing $INFO" >&2; exit 1; }
[[ -r "$ENV_FILE" ]] || { echo "bhn-inventory-collector: missing $ENV_FILE" >&2; exit 1; }

# shellcheck disable=SC1090
. "$INFO"
# shellcheck disable=SC1090
. "$ENV_FILE"

[[ -n "${NODE_NAME:-}" ]]             || { echo "bhn-inventory-collector: NODE_NAME empty" >&2; exit 1; }
[[ -n "${BHN_INVENTORY_PG_DSN:-}" ]] || { echo "bhn-inventory-collector: BHN_INVENTORY_PG_DSN empty" >&2; exit 1; }

# Sanitize node name for embedding in SQL (no single quotes)
NODE_SAFE="${NODE_NAME//\'/\'\'}"

SQL_FILE=$(mktemp /tmp/bhn-inventory-XXXXXX.sql)
trap 'rm -f "$SQL_FILE"' EXIT

log() { echo "[$(date -u +%H:%M:%S)] bhn-inventory[$NODE_NAME] $*"; }

log "Starting collection"

# ===== 1. Systemd services =====
# Track services matching BHN-relevant patterns regardless of state.
# Any service matching these patterns is worth monitoring.
BHN_PATTERNS=(
    "grafana"
    "postgresql"
    "docker"
    "crowdsec"
    "fail2ban"
    "suricata"
    "netdata"
    "dnscrypt-proxy"
    "tinyproxy"
    "shadowsocks"
    "wg-quick"
    "bhn-"
    "eh-embed"
    "n8n"
    "wallos"
    "unbound"
    "tor"
    "redis"
)

declare -A SVC_STATUS

while IFS= read -r line; do
    # systemctl list-units --all: "● svc.service  loaded active running  Desc"
    svc=$(echo "$line" | awk '{print $1}' | sed 's/●//' | tr -d ' ')
    [[ "$svc" == *.service ]] || continue
    svc="${svc%.service}"
    # active_state = field 3, sub_state = field 4
    active=$(echo "$line" | awk '{print $3}')
    sub=$(echo "$line" | awk '{print $4}')

    # Check if this service name matches any BHN pattern
    relevant=0
    for pat in "${BHN_PATTERNS[@]}"; do
        if [[ "$svc" == *"$pat"* ]]; then
            relevant=1
            break
        fi
    done
    [[ $relevant -eq 0 ]] && continue

    # Normalise status: prefer sub-state; promote 'failed' from active state
    status="$sub"
    [[ "$active" == "failed" ]] && status="failed"
    [[ -z "$status" || "$status" == "-" ]] && status="$active"

    SVC_STATUS["$svc"]="$status"
done < <(systemctl list-units --type=service --all --no-legend --no-pager 2>/dev/null || true)

# Emit upsert SQL for each service
for svc_name in "${!SVC_STATUS[@]}"; do
    svc_safe="${svc_name//\'/\'\'}"
    st_safe="${SVC_STATUS[$svc_name]//\'/\'\'}"
    cat >> "$SQL_FILE" <<SQL
INSERT INTO node_services (node_name, service_name, service_type, status, collected_at)
VALUES ('${NODE_SAFE}', '${svc_safe}', 'systemd', '${st_safe}', NOW())
ON CONFLICT (node_name, service_name, service_type)
DO UPDATE SET status = EXCLUDED.status, collected_at = EXCLUDED.collected_at;
SQL
done

log "Systemd: ${#SVC_STATUS[@]} services matched"

# ===== 2. Docker containers =====
if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
    docker_count=0
    while IFS=$'\t' read -r name image status; do
        [[ -z "$name" ]] && continue
        name_safe="${name//\'/\'\'}"
        image_safe="${image//\'/\'\'}"
        status_safe="${status//\'/\'\'}"
        cat >> "$SQL_FILE" <<SQL
INSERT INTO node_services (node_name, service_name, service_type, status, image, collected_at)
VALUES ('${NODE_SAFE}', '${name_safe}', 'docker', '${status_safe}', '${image_safe}', NOW())
ON CONFLICT (node_name, service_name, service_type)
DO UPDATE SET status = EXCLUDED.status, image = EXCLUDED.image, collected_at = EXCLUDED.collected_at;
SQL
        (( docker_count++ )) || true
    done < <(docker ps -a --format $'{{.Names}}\t{{.Image}}\t{{.Status}}' 2>/dev/null || true)
    log "Docker: $docker_count containers"
else
    log "Docker: not available on this node — skipping"
fi

# ===== 3. Listening ports (DELETE + fresh INSERT) =====
# Wipe and reinsert every run so disappeared ports don't linger.
cat >> "$SQL_FILE" <<SQL
DELETE FROM node_ports WHERE node_name = '${NODE_SAFE}';
SQL

port_count=0
parse_ports() {
    local proto="$1"
    # ss -tlnp / -ulnp output: State Recv-Q Send-Q  LocalAddr:Port  PeerAddr:Port  [Process]
    while IFS= read -r line; do
        [[ "$line" =~ ^(tcp|udp|LISTEN|UNCONN) ]] || continue
        local_field=$(echo "$line" | awk '{print $5}')
        proc_raw=$(echo "$line" | grep -oP 'comm="\K[^"]+' || true)

        # Parse addr:port — handle IPv6 [::]:port and IPv4 a.b.c.d:port and *:port
        if [[ "$local_field" =~ ^\[(.+)\]:([0-9]+)$ ]]; then
            addr="${BASH_REMATCH[1]}"
            port="${BASH_REMATCH[2]}"
        elif [[ "$local_field" =~ ^(.+):([0-9]+)$ ]]; then
            addr="${BASH_REMATCH[1]}"
            port="${BASH_REMATCH[2]}"
        else
            continue
        fi

        [[ "$port" =~ ^[0-9]+$ ]] || continue
        (( port >= 1 && port <= 65535 )) || continue

        addr_safe="${addr//\'/\'\'}"
        proc_safe="${proc_raw//\'/\'\'}"
        null_proc="NULLIF('${proc_safe}','')"

        cat >> "$SQL_FILE" <<SQL
INSERT INTO node_ports (node_name, protocol, address, port, process_name, collected_at)
VALUES ('${NODE_SAFE}', '${proto}', '${addr_safe}', ${port}, ${null_proc}, NOW())
ON CONFLICT DO NOTHING;
SQL
        (( port_count++ )) || true
    done < <(ss -${proto:0:1}lnp 2>/dev/null | tail -n +2 || true)
}

parse_ports tcp
parse_ports udp

log "Ports: $port_count listening entries"

# ===== 4. Key package versions =====
PACKAGES=(
    docker.io docker-ce
    postgresql-14 postgresql-16
    grafana
    python3
    crowdsec
    fail2ban
    suricata
    netdata
    dnscrypt-proxy
    tinyproxy
    shadowsocks-libev
    wireguard-tools
    unbound
    tor
    curl jq
)

pkg_count=0
for pkg in "${PACKAGES[@]}"; do
    version=$(dpkg-query -W -f='${Version}' "$pkg" 2>/dev/null || true)
    [[ -z "$version" ]] && continue
    pkg_safe="${pkg//\'/\'\'}"
    ver_safe="${version//\'/\'\'}"
    cat >> "$SQL_FILE" <<SQL
INSERT INTO node_packages (node_name, package_name, version, collected_at)
VALUES ('${NODE_SAFE}', '${pkg_safe}', '${ver_safe}', NOW())
ON CONFLICT (node_name, package_name)
DO UPDATE SET version = EXCLUDED.version, collected_at = EXCLUDED.collected_at;
SQL
    (( pkg_count++ )) || true
done

log "Packages: $pkg_count versions collected"

# ===== Execute SQL batch =====
psql "$BHN_INVENTORY_PG_DSN" \
    -v ON_ERROR_STOP=1 \
    -f "$SQL_FILE" \
    >/dev/null \
    || { echo "bhn-inventory-collector: PG write failed" >&2; exit 2; }

log "Collection complete — SQL batch executed"
