#!/bin/bash
# infrastructure/bootstrap/modules/ssh-hardening.sh
#
# Sourced by bhn-node-bootstrap.sh. Provides:
#   setup_ssh_hardening
#
# Behavior:
#   - Regenerates SSH host keys (critical for snapshot-based deployments)
#   - PermitRootLogin = prohibit-password (key-only)
#   - Disables PasswordAuthentication in any /etc/ssh/sshd_config.d override
#   - Pre-loads admin pubkeys (from ADMIN_PUBKEYS_FILE env, or hardcoded fallback)
#   - Restarts sshd
#
# Idempotent: re-running is safe.

setup_ssh_hardening() {
  log "SSH hardening"

  # Regenerate host keys (snapshot-deployed nodes inherit keys → MITM risk)
  if [[ ! -f /etc/eh-node-info.conf ]]; then
    rm -f /etc/ssh/ssh_host_*
    DEBIAN_FRONTEND=noninteractive dpkg-reconfigure openssh-server >/dev/null 2>&1
    ok "SSH host keys regenerated"
  fi

  # Key-only root login
  sed -i 's/^#*PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config

  # Disable password auth in any conf.d overrides (cloud-init common offender)
  for f in /etc/ssh/sshd_config.d/*.conf; do
    [[ -f "$f" ]] && sed -i 's/^PasswordAuthentication yes/PasswordAuthentication no/' "$f"
  done

  # Pre-load admin SSH keys
  mkdir -p /root/.ssh && chmod 700 /root/.ssh
  touch /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys

  if [[ -n "${ADMIN_PUBKEYS_FILE:-}" ]] && [[ -f "$ADMIN_PUBKEYS_FILE" ]]; then
    while IFS= read -r line; do
      [[ -z "$line" ]] && continue
      [[ "$line" =~ ^# ]] && continue
      grep -qF "$line" /root/.ssh/authorized_keys || echo "$line" >>/root/.ssh/authorized_keys
    done <"$ADMIN_PUBKEYS_FILE"
    ok "Admin pubkeys loaded from $ADMIN_PUBKEYS_FILE"
  else
    # Hardcoded fallback — operator's primary key
    local fletch_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKi16AATgU6NMKiTPBi4XVRt/BZr1/jrEPG0F6qPTecw fletch-desktop"
    grep -qF "$fletch_key" /root/.ssh/authorized_keys || echo "$fletch_key" >>/root/.ssh/authorized_keys
    ok "Default admin pubkey loaded (fletch-desktop)"
  fi

  systemctl restart sshd
  ok "sshd restarted with hardened config"
}
