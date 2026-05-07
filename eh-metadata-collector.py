#!/usr/bin/env python3
"""
EH metadata collector — runs every 5 minutes via /etc/crontab.

Polls `wg show wg0 dump` and reconciles the `sessions` table:
  - For each WG peer with a recent handshake (within ACTIVE_WINDOW seconds),
    UPDATE the latest open session row for that peer (refresh bytes_in/out)
    or INSERT a new open row if none exists / the existing one is stale.
  - For peers whose open row is stale (no recent handshake), set
    disconnected_at = NOW() so the row no longer counts as active.

Bug history: the original version of this script INSERTed a new row on
every poll for every active peer, never closing previous open rows —
because `ON CONFLICT DO NOTHING` matched no constraint. That left the
table with hundreds of phantom open sessions per actual user. Fixed
2026-05-07.

Reads the PG password from /root/.eh-metadata.env (chmod 600), not
hardcoded.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import psycopg2

ACTIVE_WINDOW = 180  # seconds — peer handshake counts as "alive" within this
EXIT_NODE = os.environ.get("EH_EXIT_NODE") or socket.gethostname().split("-")[2:3]
EXIT_NODE = EXIT_NODE[0] if isinstance(EXIT_NODE, list) and EXIT_NODE else "LA"


def load_env(path: str = "/root/.eh-metadata.env") -> dict:
    env = {}
    p = Path(path)
    if not p.exists():
        return env
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def connect_db():
    env = load_env()
    pw = env.get("EH_METADATA_PG_PASSWORD")
    if not pw:
        print("ERROR: EH_METADATA_PG_PASSWORD not set in /root/.eh-metadata.env", file=sys.stderr)
        sys.exit(1)
    return psycopg2.connect(
        dbname="eventhorizon", user="ehuser", password=pw, host="127.0.0.1",
    )


def get_wg_peers(interface: str = "wg0") -> list[dict]:
    """Returns list of {key, endpoint, bytes_in, bytes_out, last_handshake}."""
    result = subprocess.run(
        ["wg", "show", interface, "dump"],
        capture_output=True, text=True, check=True,
    )
    peers = []
    # First line is the interface itself; skip with [1:]
    for line in result.stdout.strip().split("\n")[1:]:
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        peers.append({
            "key": parts[0],  # full pubkey, no truncation
            "endpoint": parts[2] if parts[2] != "(none)" else None,
            "bytes_in": int(parts[5]),    # rx bytes
            "bytes_out": int(parts[6]) if len(parts) > 6 else 0,
            "last_handshake": int(parts[4]),
        })
    return peers


def reconcile(conn, peers: list[dict]) -> tuple[int, int, int]:
    """Returns (opened, updated, closed) counts."""
    now_ts = time.time()
    cur = conn.cursor()

    # Map: user_key -> (id, connected_at) for currently-open rows
    cur.execute(
        "SELECT id, user_key, connected_at FROM sessions WHERE disconnected_at IS NULL"
    )
    open_by_key: dict[str, tuple[int, "datetime.datetime"]] = {
        row[1]: (row[0], row[2]) for row in cur.fetchall()
    }

    active_keys: set[str] = set()
    opened = updated = closed = 0

    for peer in peers:
        key = peer["key"]
        if peer["last_handshake"] == 0:
            # never handshaken — ignore
            continue
        is_active = (now_ts - peer["last_handshake"]) < ACTIVE_WINDOW
        if not is_active:
            continue
        active_keys.add(key)

        if key in open_by_key:
            sid, _conn_at = open_by_key[key]
            cur.execute(
                "UPDATE sessions SET bytes_in = %s, bytes_out = %s WHERE id = %s",
                (peer["bytes_in"], peer["bytes_out"], sid),
            )
            updated += 1
        else:
            cur.execute(
                """INSERT INTO sessions (user_key, bytes_in, bytes_out, exit_node)
                   VALUES (%s, %s, %s, %s)""",
                (key, peer["bytes_in"], peer["bytes_out"], EXIT_NODE),
            )
            opened += 1

    # Close any open session whose peer is no longer active
    for key, (sid, _) in open_by_key.items():
        if key not in active_keys:
            cur.execute(
                "UPDATE sessions SET disconnected_at = NOW() WHERE id = %s", (sid,),
            )
            closed += 1

    conn.commit()
    return opened, updated, closed


def main():
    try:
        peers = get_wg_peers("wg0")
    except subprocess.CalledProcessError as e:
        print(f"ERROR: wg show failed: {e}", file=sys.stderr)
        sys.exit(1)
    conn = connect_db()
    opened, updated, closed = reconcile(conn, peers)
    conn.close()
    print(
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] peers={len(peers)} "
        f"opened={opened} updated={updated} closed={closed}"
    )


if __name__ == "__main__":
    main()
