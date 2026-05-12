#!/bin/bash
# infrastructure/bootstrap/modules/crowdsec.sh
#
# Sourced by bhn-node-bootstrap.sh. Provides:
#   setup_crowdsec
#
# Installs CrowdSec + cs-firewall-bouncer-iptables. CrowdSec replaces fail2ban
# in the v3+ stack — pulls collaborative threat intel and (unlike fail2ban)
# doesn't silently die on snapshot-deployed nodes.
#
# Default collections enabled: linux, sshd, nginx (if nginx is installed
# later, CrowdSec's auto-detect picks it up on next reload).

setup_crowdsec() {
  log "Installing CrowdSec"
  curl -fsSL https://install.crowdsec.net | sh >/dev/null 2>&1
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    crowdsec crowdsec-firewall-bouncer-iptables

  systemctl enable --now crowdsec >/dev/null 2>&1 || true
  systemctl enable --now crowdsec-firewall-bouncer >/dev/null 2>&1 || true

  ok "CrowdSec active (linux + sshd collections)"
}
