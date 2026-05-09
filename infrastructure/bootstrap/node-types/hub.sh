#!/bin/bash
# infrastructure/bootstrap/node-types/hub.sh
#
# Sourced by eh-node-bootstrap.sh after modules. Defines:
#   node_type_install
#
# Hub composition: encrypted storage tiers, WG hub, dnscrypt-proxy, Shadowsocks,
# CrowdSec, Suricata, PostgreSQL on encrypted NVMe, Grafana, n8n (VPN-only),
# backup pipeline. Hub is the single pane of glass for the whole network.

node_type_install() {
  log "Composing hub-type install"

  # ─── 1. Encrypted block storage (optional but expected for production) ─
  if [[ -n "${ATTACH_NVME:-}" ]]; then
    setup_encrypted_storage "$ATTACH_NVME" "eh-nvme" "/mnt/eh-nvme-hot" \
      "postgres" "pcap" "logs" "grafana"
  else
    warn "ATTACH_NVME not set — running hub without hot tier (PG on system disk)"
  fi
  if [[ -n "${ATTACH_HDD:-}" ]]; then
    setup_encrypted_storage "$ATTACH_HDD" "eh-hdd" "/mnt/eh-hdd-cold" \
      "archives/postgres" "archives/pcap" "archives/logs" "snapshots" "reports"
  else
    warn "ATTACH_HDD not set — backup pipeline will fail (cold tier missing)"
  fi

  # ─── 2. Core services ─────────────────────────────────────────
  setup_dnscrypt
  setup_wireguard_hub "$WG_INTERFACE" "$HUB_WG_PORT"
  setup_shadowsocks "$SS_PORT" "$SS_PASSWORD"
  setup_crowdsec
  [[ "$INSTALL_SURICATA" == "1" ]] && setup_suricata

  # ─── 3. PostgreSQL on encrypted NVMe ──────────────────────────
  log "Installing PostgreSQL"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql postgresql-contrib

  if mountpoint -q /mnt/eh-nvme-hot 2>/dev/null; then
    log "Migrating PostgreSQL data dir to encrypted NVMe"
    systemctl stop postgresql
    if [[ ! -d /mnt/eh-nvme-hot/postgres/base ]]; then
      rsync -aHAX /var/lib/postgresql/14/main/ /mnt/eh-nvme-hot/postgres/ 2>/dev/null || true
      chown -R postgres:postgres /mnt/eh-nvme-hot/postgres
      chmod 700 /mnt/eh-nvme-hot/postgres
    fi
    [[ -d /var/lib/postgresql/14/main && ! -d /var/lib/postgresql/14/main.OLD ]] \
      && mv /var/lib/postgresql/14/main /var/lib/postgresql/14/main.OLD
    sed -i "s|^data_directory.*|data_directory = '/mnt/eh-nvme-hot/postgres'|" \
      /etc/postgresql/14/main/postgresql.conf
    # Listen on the WG tunnel IP for VPN-only client access
    sed -i "s|^#*listen_addresses.*|listen_addresses = 'localhost,${TUNNEL_IP}'|" \
      /etc/postgresql/14/main/postgresql.conf
    systemctl start postgresql
    ok "PostgreSQL active on encrypted NVMe (listening on ${TUNNEL_IP})"
  else
    systemctl start postgresql
    ok "PostgreSQL active (default location — no NVMe attached)"
  fi

  # Apply nodes-table schema (idempotent)
  if [[ -f "${BOOTSTRAP_DIR}/../../sql/nodes-schema.sql" ]]; then
    sudo -u postgres psql -d eventhorizon -f "${BOOTSTRAP_DIR}/../../sql/nodes-schema.sql" \
      >/dev/null 2>&1 \
      && ok "nodes-schema.sql applied" \
      || warn "nodes-schema.sql not applied (DB 'eventhorizon' may not exist yet)"
  fi

  # ─── 4. Grafana (VPN-only) ────────────────────────────────────
  log "Installing Grafana"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq apt-transport-https
  mkdir -p /etc/apt/keyrings
  curl -fsSL https://apt.grafana.com/gpg.key | gpg --dearmor -o /etc/apt/keyrings/grafana.gpg
  echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" \
    >/etc/apt/sources.list.d/grafana.list
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq grafana

  # Bind Grafana to the tunnel IP only — VPN-only access
  sed -i "s|^;http_addr =.*|http_addr = ${TUNNEL_IP}|" /etc/grafana/grafana.ini
  sed -i "s|^http_addr =.*|http_addr = ${TUNNEL_IP}|" /etc/grafana/grafana.ini

  # Move Grafana state to encrypted NVMe if available
  if mountpoint -q /mnt/eh-nvme-hot 2>/dev/null; then
    systemctl stop grafana-server 2>/dev/null || true
    rsync -aHAX /var/lib/grafana/ /mnt/eh-nvme-hot/grafana/ 2>/dev/null || true
    chown -R grafana:grafana /mnt/eh-nvme-hot/grafana
    sed -i "s|^;data =.*|data = /mnt/eh-nvme-hot/grafana|" /etc/grafana/grafana.ini
    sed -i "s|^data =.*|data = /mnt/eh-nvme-hot/grafana|" /etc/grafana/grafana.ini
  fi

  systemctl enable --now grafana-server >/dev/null
  ok "Grafana active on http://${TUNNEL_IP}:3000"

  # ─── 5. n8n (VPN-only, mirrors Grafana posture) ───────────────
  log "Installing Node.js + n8n"
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null 2>&1
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs
  npm install -g n8n >/dev/null 2>&1

  cat >/etc/systemd/system/n8n.service <<EOF
[Unit]
Description=n8n workflow automation
After=network.target wg-quick@${WG_INTERFACE}.service postgresql.service
Requires=wg-quick@${WG_INTERFACE}.service

[Service]
Type=simple
User=root
Environment=N8N_SECURE_COOKIE=false
Environment=N8N_HOST=${TUNNEL_IP}
Environment=N8N_PROTOCOL=http
Environment=N8N_PORT=5678
Environment=N8N_LISTEN_ADDRESS=${TUNNEL_IP}
Environment=WEBHOOK_URL=http://${TUNNEL_IP}:5678/
ExecStart=/usr/bin/n8n start
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now n8n >/dev/null
  ok "n8n active on http://${TUNNEL_IP}:5678 (VPN-only)"

  # ─── 6. Backup pipeline ───────────────────────────────────────
  setup_backup_pipeline
}
