#!/usr/bin/env python3
"""
bhn-horizon-weekly-report — generate the weekly BHN report.

Sunday 08:00 PT. Output: a long-form text/markdown report covering the
trailing 7 days across security, trading, bandwidth, Tor relay performance,
and anomalies. n8n wires this in:
  - Cron node (Sunday 08:00 PT)
  - Execute Command node (this script) → captures stdout
  - Send Email node (to horizon@eventhorizonvpn.com)
  - Twilio SMS node — first 1500 chars to operator

Config /etc/bhn/horizon-weekly.env:
  BHN_WEEKLY_PG_DSN='postgresql://agent_reader:<PW>@10.8.0.1/eventhorizon'
"""
from __future__ import annotations
import os, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import psycopg2


def log(msg): print(f"bhn-horizon-weekly-report: {msg}", file=sys.stderr)


def load_env(p='/etc/bhn/horizon-weekly.env') -> dict:
    out = {}
    if not Path(p).is_file(): return out
    for ln in Path(p).read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith('#') or '=' not in ln: continue
        k, v = ln.split('=', 1); out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def section(title: str, body: str) -> str:
    return f"\n## {title}\n\n{body}\n"


def main():
    env = load_env()
    dsn = env.get('BHN_WEEKLY_PG_DSN', '')
    if not dsn: log("no PG DSN — skipping"); return 0

    out = [f"# BHN weekly report — week ending {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"]

    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        # --- Security 7d ---
        cur.execute("""
            SELECT node_name, source, SUM(alert_count) AS total,
                   SUM(severity_critical) AS crit, SUM(severity_high) AS high
            FROM node_logs_summary
            WHERE window_start > NOW() - INTERVAL '7 days'
            GROUP BY node_name, source
            ORDER BY total DESC NULLS LAST
        """)
        rows = cur.fetchall()
        body = "| Node | Source | Total | Critical | High |\n|---|---|---|---|---|\n"
        body += "\n".join(f"| {n} | {s} | {t} | {c} | {h} |" for n, s, t, c, h in rows) if rows else "(no events)"
        out.append(section("Security events (last 7 days)", body))

        # --- CrowdSec decisions ---
        cur.execute("""
            SELECT node_name, COUNT(DISTINCT decision_id) AS unique_decisions,
                   COUNT(DISTINCT value) AS unique_targets
            FROM crowdsec_decisions
            WHERE measured_at > NOW() - INTERVAL '7 days'
            GROUP BY node_name
        """)
        rows = cur.fetchall()
        body = "\n".join(f"- {n}: {d} unique decisions, {t} unique targets" for n, d, t in rows) if rows else "(none)"
        out.append(section("CrowdSec activity (last 7 days)", body))

        # --- Trading 7d ---
        cur.execute("""
            SELECT strategy_id,
                   SUM(realized_pnl_usd)   AS pnl,
                   SUM(trades_opened)      AS opens,
                   SUM(trades_closed)      AS closes,
                   AVG(win_rate_pct)       AS winrate
            FROM strategy_performance
            WHERE date > CURRENT_DATE - INTERVAL '7 days'
            GROUP BY strategy_id
            ORDER BY pnl DESC NULLS LAST
        """)
        rows = cur.fetchall()
        body = "| Strategy | 7d P&L | Opens | Closes | Avg WinRate |\n|---|---|---|---|---|\n"
        body += "\n".join(
            f"| {sid} | ${float(p or 0):+.2f} | {o or 0} | {c or 0} | {float(w or 0):.1f}% |"
            for sid, p, o, c, w in rows
        ) if rows else "(no trading data)"
        out.append(section("Trading performance (last 7 days)", body))

        # --- Bandwidth 7d ---
        cur.execute("""
            SELECT node_name, SUM(rx_bytes + tx_bytes) / 1024 / 1024 / 1024 AS total_gb
            FROM node_bandwidth_stats
            WHERE period_type = 'day'
              AND period_start > NOW() - INTERVAL '7 days'
            GROUP BY node_name
            ORDER BY total_gb DESC NULLS LAST
        """)
        rows = cur.fetchall()
        body = "\n".join(f"- {n}: {float(g or 0):.1f} GB" for n, g in rows) if rows else "(no bandwidth data)"
        out.append(section("Bandwidth (last 7 days)", body))

        # --- Tor relay 7d ---
        cur.execute("""
            SELECT node, MAX(bytes_read + bytes_written) / 1024 / 1024 / 1024 AS gb_used,
                   MAX(uptime_seconds) / 86400 AS uptime_days
            FROM tor_relay_stats
            WHERE measured_at > NOW() - INTERVAL '7 days'
            GROUP BY node
            ORDER BY gb_used DESC NULLS LAST
        """)
        rows = cur.fetchall()
        body = "| Relay | Cycle GB | Uptime (d) |\n|---|---|---|\n"
        body += "\n".join(
            f"| {n} | {float(g or 0):.1f} | {float(u or 0):.1f} |"
            for n, g, u in rows
        ) if rows else "(no relay data)"
        out.append(section("Tor relay performance (last 7 days)", body))

        # --- Trading incidents (circuit breakers + reconciliation mismatches) ---
        cur.execute("""
            SELECT breaker_type, COUNT(*) AS n,
                   ARRAY_AGG(DISTINCT strategy_id) AS strategies
            FROM circuit_breaker_log
            WHERE tripped_at > NOW() - INTERVAL '7 days'
            GROUP BY breaker_type
        """)
        rows = cur.fetchall()
        body = "\n".join(f"- {bt}: {n} (strategies: {', '.join(s)})" for bt, n, s in rows) if rows else "(no breaker trips)"
        out.append(section("Trading incidents (last 7 days)", body))

    print("\n".join(out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
