#!/usr/bin/env python3
"""
eh-log-shipper — push security events from a non-hub node into the hub
PostgreSQL `node_logs` table over the WG tunnel.

Sources shipped (MVP):
  - Suricata alerts (event_type=alert in /var/log/suricata/eve.json)
  - CrowdSec alerts (cscli alerts list -o json)

Reads identity from /etc/eh-node-info.conf and DSN from
/root/.eh-log-shipper.env (mode 0600). Maintains last-shipped timestamps
per source in /var/lib/eh-log-shipper/state.json so re-runs don't dup-ship.

Cron: every 5 minutes. Non-zero exit → cron mails / leaves stderr in log.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

INFO_FILE = Path("/etc/eh-node-info.conf")
ENV_FILE  = Path("/root/.eh-log-shipper.env")
STATE_DIR = Path("/var/lib/eh-log-shipper")
STATE_FILE = STATE_DIR / "state.json"
SURICATA_EVE = Path("/var/log/suricata/eve.json")

SOURCES = ("suricata", "crowdsec")


def die(msg: str, code: int = 1) -> None:
    print(f"eh-log-shipper: {msg}", file=sys.stderr)
    sys.exit(code)


def load_kv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip("'").strip('"')
    return out


def load_state() -> dict[str, str]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {s: "1970-01-01T00:00:00+00:00" for s in SOURCES}


def save_state(state: dict[str, str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True, indent=2))
    tmp.replace(STATE_FILE)


def parse_ts(s: str) -> datetime:
    # tolerate Suricata's microsecond precision and trailing TZ formats
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def collect_suricata(since: datetime, node: str) -> list[dict]:
    """Yield rows for node_logs from Suricata's eve.json since `since`."""
    rows: list[dict] = []
    if not SURICATA_EVE.exists():
        return rows
    try:
        with SURICATA_EVE.open("r", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or '"event_type":"alert"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("event_type") != "alert":
                    continue
                ts = parse_ts(e["timestamp"])
                if ts <= since:
                    continue
                a = e.get("alert", {})
                sev_num = a.get("severity")  # 1=major, 2=med, 3=minor
                sev = {1: "high", 2: "medium", 3: "low"}.get(sev_num, "info")
                rows.append({
                    "node_name": node,
                    "event_time": ts.isoformat(),
                    "source": "suricata",
                    "severity": sev,
                    "signature": a.get("signature"),
                    "src_ip": e.get("src_ip"),
                    "dst_ip": e.get("dest_ip"),
                    "proto": e.get("proto"),
                    "meta": {
                        "category": a.get("category"),
                        "signature_id": a.get("signature_id"),
                        "src_port": e.get("src_port"),
                        "dst_port": e.get("dest_port"),
                    },
                })
    except OSError:
        pass
    return rows


def collect_crowdsec(since: datetime, node: str) -> list[dict]:
    """Yield rows from CrowdSec alerts via `cscli alerts list -o json`."""
    rows: list[dict] = []
    try:
        # 6-min window with 5-min cron = 1m overlap; the state-file `since`
        # filter dedups within that overlap.
        out = subprocess.run(
            ["cscli", "alerts", "list", "-s", "6m", "-o", "json"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if out.returncode != 0:
            return rows
        for a in json.loads(out.stdout or "[]"):
            ts_str = a.get("created_at") or a.get("start_at")
            if not ts_str:
                continue
            ts = parse_ts(ts_str)
            if ts <= since:
                continue
            scenario = a.get("scenario") or ""
            src_value = (a.get("source") or {}).get("ip") or (a.get("source") or {}).get("value")
            severity = "high" if "ban" in (a.get("decisions") or [{}])[0].get("type", "") else "medium"
            rows.append({
                "node_name": node,
                "event_time": ts.isoformat(),
                "source": "crowdsec",
                "severity": severity,
                "signature": scenario,
                "src_ip": src_value,
                "dst_ip": None,
                "proto": None,
                "meta": {
                    "as_name":  (a.get("source") or {}).get("as_name"),
                    "country":  (a.get("source") or {}).get("cn"),
                    "events_count": a.get("events_count"),
                    "decisions": [d.get("type") for d in (a.get("decisions") or [])],
                },
            })
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    return rows


def insert_rows(dsn: str, rows: list[dict]) -> None:
    """Bulk INSERT via psql stdin — avoids psycopg2 dependency."""
    if not rows:
        return
    sql_lines = [
        "BEGIN;",
        "INSERT INTO node_logs (node_name, event_time, source, severity, "
        "signature, src_ip, dst_ip, proto, meta) VALUES",
    ]
    values = []
    for r in rows:
        # Escape single quotes by doubling
        def lit(v):
            if v is None:
                return "NULL"
            s = str(v).replace("'", "''")
            return f"'{s}'"
        meta_json = json.dumps(r["meta"]).replace("'", "''")
        values.append(
            f"({lit(r['node_name'])}, {lit(r['event_time'])}, {lit(r['source'])}, "
            f"{lit(r['severity'])}, {lit(r['signature'])}, {lit(r['src_ip'])}, "
            f"{lit(r['dst_ip'])}, {lit(r['proto'])}, '{meta_json}'::jsonb)"
        )
    sql_lines.append(",\n".join(values) + ";")
    sql_lines.append("COMMIT;")
    sql = "\n".join(sql_lines)

    proc = subprocess.run(
        ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-q"],
        input=sql, capture_output=True, text=True, timeout=30, check=False,
    )
    if proc.returncode != 0:
        die(f"psql insert failed: {proc.stderr.strip()}", code=2)


def main() -> int:
    if not INFO_FILE.is_file():
        die(f"missing {INFO_FILE}")
    if not ENV_FILE.is_file():
        die(f"missing {ENV_FILE}")

    info = load_kv(INFO_FILE)
    env  = load_kv(ENV_FILE)
    node = info.get("NODE_NAME")
    dsn  = env.get("EH_LOG_SHIPPER_DSN")
    if not node:
        die("NODE_NAME missing in /etc/eh-node-info.conf")
    if not dsn:
        die("EH_LOG_SHIPPER_DSN missing in /root/.eh-log-shipper.env")

    state = load_state()
    total = 0

    suri_since = parse_ts(state.get("suricata", "1970-01-01T00:00:00+00:00"))
    suri_rows = collect_suricata(suri_since, node)
    if suri_rows:
        insert_rows(dsn, suri_rows)
        state["suricata"] = max(r["event_time"] for r in suri_rows)
        total += len(suri_rows)

    cs_since = parse_ts(state.get("crowdsec", "1970-01-01T00:00:00+00:00"))
    cs_rows = collect_crowdsec(cs_since, node)
    if cs_rows:
        insert_rows(dsn, cs_rows)
        state["crowdsec"] = max(r["event_time"] for r in cs_rows)
        total += len(cs_rows)

    save_state(state)

    if total > 0:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        print(f"[{ts}] eh-log-shipper: {node} shipped {total} rows "
              f"(suricata={len(suri_rows)}, crowdsec={len(cs_rows)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
