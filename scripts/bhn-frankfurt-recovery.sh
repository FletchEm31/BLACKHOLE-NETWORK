#!/bin/bash
# BHN (Blackhole Network) — Frankfurt recovery diagnostics + SSH key setup.
#
# Phase 1 (always runs): diagnose Frankfurt reachability from LA.
# Phase 2 (if SSH works): generate dedicated key on LA, install on FRA,
#   verify the nightly diagnostic can use it.
#
# Run on LA. Pipe output to a log:
#   bash /tmp/eh-frankfurt-recovery.sh 2>&1 | tee /tmp/fra-recovery.log

set -uo pipefail

FRA_HOST=192.248.187.208
FRA_USER=root
FRA_KEY=/root/.ssh/eh_frankfurt   # path the orchestrator expects

echo '======================================================================'
echo "FRANKFURT RECOVERY DIAGNOSTIC — $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo '======================================================================'

# ---------- Phase 1: reachability matrix ----------
echo
echo '----- WireGuard tunnel state (wg1) -----'
wg show wg1 2>/dev/null || echo '(no wg1 interface)'

echo
echo '----- ICMP ping (3 packets, 2s timeout) -----'
PING_OK='no'
if timeout 8 ping -c 3 -W 2 "$FRA_HOST" >/dev/null 2>&1; then
  PING_OK='yes'
  echo 'PING: OK'
else
  echo 'PING: FAIL'
fi

echo
echo '----- TCP port 22 reachable -----'
PORT22_OK='no'
if timeout 5 nc -zv "$FRA_HOST" 22 2>&1 | grep -q -E 'succeeded|open'; then
  PORT22_OK='yes'
  echo 'PORT 22: OPEN'
else
  echo 'PORT 22: CLOSED or filtered'
fi

echo
echo '----- SSH handshake (existing default key) -----'
SSH_OK='no'
if timeout 8 ssh -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$FRA_USER@$FRA_HOST" 'echo FRA_OK; uname -r; uptime' 2>&1; then
  SSH_OK='yes'
fi

echo
echo '======================================================================'
echo 'REACHABILITY SUMMARY'
echo '======================================================================'
printf '  WireGuard tunnel: %s\n' "$(wg show wg1 2>/dev/null | grep -E 'latest handshake' | head -1 || echo '(no handshake info)')"
printf '  ICMP ping:        %s\n' "$PING_OK"
printf '  SSH port 22:      %s\n' "$PORT22_OK"
printf '  SSH handshake:    %s\n' "$SSH_OK"

# ---------- Decide ----------
echo
if [ "$SSH_OK" = 'no' ]; then
  echo '======================================================================'
  echo 'FRANKFURT NOT REACHABLE — STOPPING HERE'
  echo '======================================================================'
  echo
  echo 'Likely diagnosis based on what failed:'
  if [ "$PING_OK" = 'no' ] && [ "$PORT22_OK" = 'no' ]; then
    echo '  All checks failed. Host appears down or completely firewalled.'
    echo '  Action: log in to Vultr web console, check instance status,'
    echo '          reboot via console if hung, inspect serial console for'
    echo '          boot errors.'
  elif [ "$PING_OK" = 'yes' ] && [ "$PORT22_OK" = 'no' ]; then
    echo '  Host responds to ping, but port 22 is closed.'
    echo '  Action: Vultr web console -> log in -> check sshd status'
    echo '            (systemctl status sshd) and UFW (ufw status).'
    echo '          Likely a sshd config error or UFW rule blocking your egress IP.'
  elif [ "$PING_OK" = 'yes' ] && [ "$PORT22_OK" = 'yes' ]; then
    echo '  Host responds to ping AND port 22 is open, but SSH handshake fails.'
    echo '  Action: SSH key may have been removed from authorized_keys, OR'
    echo '          known_hosts has a stale entry. Check both.'
  fi
  exit 1
fi

# ---------- Phase 2: key setup (only if SSH works) ----------
echo '======================================================================'
echo 'FRANKFURT REACHABLE — PROCEEDING WITH KEY SETUP'
echo '======================================================================'

# Generate dedicated key for orchestrator use, if not already present
if [ -f "$FRA_KEY" ]; then
  echo "Key already exists at $FRA_KEY — reusing"
else
  echo "Generating new ed25519 key at $FRA_KEY"
  ssh-keygen -t ed25519 -f "$FRA_KEY" -C "LA->FRA nightly diagnostic ($(date +%Y-%m-%d))" -N ''
fi

ls -la "$FRA_KEY" "$FRA_KEY.pub"

# Install pub on Frankfurt (idempotent — won't double-add if already present)
PUBKEY=$(cat "$FRA_KEY.pub")
echo
echo 'Installing public key on Frankfurt'
ssh -o BatchMode=yes "$FRA_USER@$FRA_HOST" "
  mkdir -p ~/.ssh
  chmod 700 ~/.ssh
  touch ~/.ssh/authorized_keys
  chmod 600 ~/.ssh/authorized_keys
  if grep -qF '$PUBKEY' ~/.ssh/authorized_keys; then
    echo 'pub key already present — no change'
  else
    echo '$PUBKEY' >> ~/.ssh/authorized_keys
    echo 'pub key appended to authorized_keys'
  fi
"

# Verify the orchestrator's exact SSH invocation works
echo
echo '----- Test: orchestrator SSH invocation with new key -----'
if timeout 10 ssh -i "$FRA_KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$FRA_USER@$FRA_HOST" 'echo "ORCHESTRATOR-KEY-OK"; hostname; uname -r' ; then
  echo 'PASS — orchestrator can reach Frankfurt with the new key'
else
  echo 'FAIL — key install completed but SSH with -i flag does not work'
  echo 'Action: check that the new pub key landed in ~/.ssh/authorized_keys on FRA'
  exit 2
fi

echo
echo '======================================================================'
echo 'FRANKFURT KEY SETUP COMPLETE'
echo '======================================================================'
echo
echo 'Next nightly diagnostic run (09:00 UTC) will include Frankfurt section.'
echo 'You can manually trigger sooner if you want to verify:'
echo '  /opt/eh-diagnostics/eh-nightly-diagnostic.sh'
exit 0
