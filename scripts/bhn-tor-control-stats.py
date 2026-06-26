#!/usr/bin/env python3
"""
bhn-tor-control-stats — query Tor control port via the BHN relay container,
write circuit_count + traffic counters + accounting remaining + flags →
tor_relay_stats (extension columns from tor-relay-stats-control-port-extension.sql).

Runs on each relay node via cron every 5 min. Uses `docker exec bhn-tor-relay`
to run a tiny python control-protocol client against the local ControlSocket
(/var/lib/tor/control). No extra deps in the host — everything runs inside
the container which already has python3 + tor.

Reads PG DSN from /root/.bhn-tor-control-stats.env:
  BHN_TOR_CTRL_PG_DSN='postgresql://log_shipper:<PW>@<BHN_WG_LA_IP>/eventhorizon'

Cron (each relay node):
  */5 * * * *  root  /usr/local/sbin/bhn-tor-control-stats.py

Prereq: torrc has ControlSocket + CookieAuthentication enabled (see the
per-relay torrc files in infrastructure/services/tor-relay*/), and the
container has been rebuilt + restarted to pick up the torrc change.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import psycopg2

CONTAINER = 'bhn-tor-relay'


def log(msg): print(f"bhn-tor-control-stats: {msg}", file=sys.stderr)


def load_env(p):
    out = {}
    if not Path(p).is_file(): return out
    for ln in Path(p).read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith('#') or '=' not in ln: continue
        k, v = ln.split('=', 1); out[k.strip()] = v.strip().strip('"').strip("'")
    return out


# In-container helper script. Reads the cookie, AUTHENTICATEs, fires multiple
# GETINFO commands, prints results as JSON to stdout.
INCONTAINER_SCRIPT = r'''
import os, socket, json
COOKIE = open("/var/lib/tor/control.authcookie", "rb").read().hex()
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect("/var/lib/tor/control")
def send(cmd):
    s.sendall((cmd + "\r\n").encode())
def recv_resp():
    buf = b""
    while True:
        chunk = s.recv(4096)
        if not chunk: break
        buf += chunk
        # Tor terminates each response with "250 OK\r\n" or single-line "250-..." then "250 OK\r\n"
        if b"\r\n250 OK\r\n" in buf or buf.endswith(b"250 OK\r\n"):
            break
    return buf.decode(errors="replace")
send(f"AUTHENTICATE {COOKIE}")
recv_resp()
out = {}
for key, cmd in [
    ("version",              "GETINFO version"),
    ("circuit_status",       "GETINFO circuit-status"),
    ("traffic_read",         "GETINFO traffic/read"),
    ("traffic_written",      "GETINFO traffic/written"),
    ("accounting_bytes_left","GETINFO accounting/bytes-left"),
    ("accounting_max",       "GETINFO accounting/interval-end"),
    ("flags",                "GETINFO ns/id/$" + open("/var/lib/tor/fingerprint").read().split()[1]),
]:
    send(cmd)
    out[key] = recv_resp()
s.close()
print(json.dumps(out))
'''


def parse_int_after(line: str, key: str) -> int | None:
    """Extract integer following '250-key=' or '250 key=' pattern."""
    import re
    m = re.search(rf'{re.escape(key)}=(\d+)', line)
    return int(m.group(1)) if m else None


def parse_circuit_count(circ_blob: str) -> int:
    """circuit-status returns one circuit per line (250+circuit-status= ...)."""
    # Lines like "250+circuit-status=\r\n<circuits>\r\n.\r\n250 OK"
    # Count non-empty lines between the '+' header and the trailing '.'
    lines = [ln for ln in circ_blob.splitlines() if ln.startswith(('1', '2', '3', '4', '5', '6', '7', '8', '9'))
             and not ln.startswith(('250', '550'))]
    return len(lines)


def parse_flags_from_ns(ns_blob: str) -> list[str]:
    """ns response has 's' line with flags: 's Fast Guard HSDir Running Stable V2Dir Valid'"""
    for ln in ns_blob.splitlines():
        ln = ln.strip()
        if ln.startswith('s ') or ln.startswith('s\t'):
            return ln[2:].split()
    return []


def main():
    env = load_env('/root/.bhn-tor-control-stats.env')
    if not env: log("missing /root/.bhn-tor-control-stats.env — skipping"); return 0
    dsn = env.get('BHN_TOR_CTRL_PG_DSN', '')
    if not dsn: log("BHN_TOR_CTRL_PG_DSN missing — skipping"); return 0

    # Verify container is up
    rc = subprocess.run(['docker', 'inspect', '-f', '{{.State.Running}}', CONTAINER],
                        capture_output=True, text=True)
    if rc.returncode != 0 or rc.stdout.strip() != 'true':
        log(f"container {CONTAINER} not running"); return 0

    # Run the helper inside the container
    rc = subprocess.run(
        ['docker', 'exec', CONTAINER, 'python3', '-c', INCONTAINER_SCRIPT],
        capture_output=True, text=True, timeout=30
    )
    if rc.returncode != 0:
        log(f"docker exec failed: {rc.stderr[:300]}"); return 3
    try:
        data = json.loads(rc.stdout)
    except Exception as e:
        log(f"json parse failed: {e} stdout={rc.stdout[:300]}"); return 3

    # Pull fingerprint + nickname from inside the container (same as bhn-tor-stats.sh)
    fp_rc = subprocess.run(['docker', 'exec', CONTAINER, 'cat', '/var/lib/tor/fingerprint'],
                           capture_output=True, text=True)
    if fp_rc.returncode != 0: log("fingerprint read failed"); return 3
    parts = fp_rc.stdout.strip().split()
    nickname = parts[0] if parts else ''
    fingerprint = parts[1] if len(parts) > 1 else ''

    circuit_count = parse_circuit_count(data.get('circuit_status', ''))
    traffic_read   = parse_int_after(data.get('traffic_read', ''), 'traffic/read')
    traffic_written = parse_int_after(data.get('traffic_written', ''), 'traffic/written')
    accounting_left = parse_int_after(data.get('accounting_bytes_left', ''), 'accounting/bytes-left')
    flags = parse_flags_from_ns(data.get('flags', ''))

    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO tor_relay_stats
                (node, fingerprint, circuit_count, accounting_bytes_remaining,
                 traffic_read_bytes, traffic_written_bytes, flags, raw_payload, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'control_port')
        """, (nickname, fingerprint, circuit_count, accounting_left,
              traffic_read, traffic_written, flags, json.dumps(data)))
    log(f"snapshot inserted: circuits={circuit_count} flags={flags}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
