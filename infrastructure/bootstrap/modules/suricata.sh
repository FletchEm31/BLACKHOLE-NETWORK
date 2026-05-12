#!/bin/bash
# infrastructure/bootstrap/modules/suricata.sh
#
# Sourced by bhn-node-bootstrap.sh. Provides:
#   setup_suricata
#
# Installs Suricata IDS. Heavyweight (~50k rules, ~100 Mbps inspected per vCPU
# under default ruleset) — opt-in via INSTALL_SURICATA=1 or auto-on for hub
# and scan node types (set in master).

setup_suricata() {
  log "Installing Suricata IDS"
  add-apt-repository -y ppa:oisf/suricata-stable >/dev/null 2>&1 || true
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq suricata

  # Bind to the default-route NIC
  sed -i "s/^\([[:space:]]*-[[:space:]]*interface:\).*/\1 ${NET_IFACE}/" \
    /etc/suricata/suricata.yaml || true

  # Pull rule sources
  suricata-update >/dev/null 2>&1 || warn "suricata-update failed (rules may be stale)"

  # Tune logrotate so eve.json doesn't fill the disk
  cat >/etc/logrotate.d/suricata <<'EOF'
/var/log/suricata/*.log /var/log/suricata/*.json {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    postrotate
        systemctl reload suricata >/dev/null 2>&1 || true
    endscript
}
EOF

  systemctl enable --now suricata >/dev/null 2>&1 || true
  ok "Suricata active on ${NET_IFACE}"
}
