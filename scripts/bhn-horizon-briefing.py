#!/usr/bin/env python3
"""
bhn-horizon-briefing — generate the daily morning briefing text.

Pure-Python composer that queries LA PG for the data points HORIZON's morning
SMS needs, formats them into a compact SMS-friendly digest, prints to stdout.

n8n wires this in: Cron node (08:00 PT = 15:00 UTC during DST) → Execute Command
node calling this script → Twilio SMS node (operator number).

Sections:
  1. Network health        — count of online/stale nodes (nodes table)
  2. Security events 24h   — alerts by severity (node_logs_summary)
  3. Trading P&L summary   — yesterday's realized P&L (strategy_performance)
  4. Top news headlines    — most-recent alpaca_news (top 3)
  5. Weather               — from external_data table (if available)
  6. Anomalies detected    — any current Grafana-alert state (alerts firing)

Output is plain text, ~1500 chars max (single SMS = 160 chars, multi-part OK).

Cron (n8n schedule node OR direct cron):
  0 8 * * *  Pacific/Los_Angeles   /usr/local/sbin/bhn-horizon-briefing.py

Direct cron in UTC: convert based on DST. Better: schedule via n8n which
handles timezone.

Config /etc/bhn/horizon-briefing.env:
  BHN_BRIEFING_PG_DSN='postgresql://agent_reader:<PW>@10.8.0.1/eventhorizon'
"""
from __future__ import annotations
import os, sys, textwrap
from datetime import datetime, timezone
from pathlib import Path
import psycopg2

ENV_FILES = ['/etc/bhn/horizon-briefing.env', '/root/.bhn-horizon-briefing.env']


def log(msg): print(f"bhn-horizon-briefing: {msg}", file=sys.stderr)


def load_env() -> dict:
    for p in ENV_FILES:
        if Path(p).is_file():
            out = {}
            for ln in Path(p).read_text().splitlines():
                ln = ln.strip()
                if not ln or ln.startswith('#') or '=' not in ln: continue
                k, v = ln.split('=', 1); out[k.strip()] = v.strip().strip('"').strip("'")
            return out
    return {}


def fetch_section(cur, sql: str, params=None) -> list:
    try:
        cur.execute(sql, params or ())
        return cur.fetchall()
    except Exception as e:
        log(f"query failed: {e}"); return []


def compose(cur) -> str:
    out: list[str] = []
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    out.append(f"BHN morning briefing — {today}")

    # 1. Network health
    rows = fetch_section(cur, """
        SELECT COUNT(*) FILTER (WHERE last_seen >= NOW() - INTERVAL '15 min')  AS online,
               COUNT(*) FILTER (WHERE last_seen <  NOW() - INTERVAL '15 min'
                                   OR last_seen IS NULL)                       AS stale
        FROM nodes WHERE status NOT IN ('decommissioned')
    """)
    if rows:
        on, st = rows[0]
        out.append(f"Network: {on} online, {st} stale")

    # 2. Security events 24h
    rows = fetch_section(cur, """
        SELECT source, SUM(alert_count), SUM(severity_critical), SUM(severity_high)
        FROM node_logs_summary
        WHERE window_start > NOW() - INTERVAL '24 hours'
        GROUP BY source ORDER BY source
    """)
    if rows:
        sec_lines = []
        for src, tot, crit, high in rows:
            sec_lines.append(f"  {src}: {tot or 0} (crit {crit or 0}, high {high or 0})")
        out.append("Security 24h:\n" + "\n".join(sec_lines))

    # 3. Trading P&L
    rows = fetch_section(cur, """
        SELECT strategy_id, realized_pnl_usd, trades_closed
        FROM strategy_performance
        WHERE date = CURRENT_DATE - INTERVAL '1 day'
        ORDER BY strategy_id
    """)
    if rows:
        pnl_lines = []
        total = 0.0
        for sid, pnl, n in rows:
            p = float(pnl or 0); total += p
            pnl_lines.append(f"  {sid}: ${p:+.2f} ({n or 0} closed)")
        pnl_lines.append(f"  TOTAL: ${total:+.2f}")
        out.append("Trading yesterday:\n" + "\n".join(pnl_lines))

    # 4. Top 3 news headlines
    rows = fetch_section(cur, """
        SELECT headline FROM alpaca_news
        WHERE created_at > NOW() - INTERVAL '12 hours'
        ORDER BY created_at DESC LIMIT 3
    """)
    if rows:
        news = "\n".join("  · " + (h or '')[:120] for (h,) in rows)
        out.append("Top news:\n" + news)

    # 5. Weather — best-effort, depends on external_data shape
    rows = fetch_section(cur, """
        SELECT metadata FROM external_data
        WHERE source = 'openweathermap'
          AND captured_at > NOW() - INTERVAL '6 hours'
        ORDER BY captured_at DESC LIMIT 1
    """)
    if rows:
        meta = rows[0][0] or {}
        try:
            temp = meta.get('main', {}).get('temp')
            desc = (meta.get('weather') or [{}])[0].get('description', '')
            if temp is not None:
                out.append(f"Weather: {temp}°F, {desc}")
        except Exception: pass

    # 6. Anomalies — Tor relays approaching cap, WG peers stale, etc.
    rows = fetch_section(cur, """
        SELECT node, (bytes_read + bytes_written) / 1024 / 1024 / 1024 AS gb
        FROM (
          SELECT DISTINCT ON (node) node, bytes_read, bytes_written
          FROM tor_relay_stats
          WHERE measured_at > NOW() - INTERVAL '1 hour'
          ORDER BY node, measured_at DESC
        ) latest
        WHERE bytes_read IS NOT NULL AND bytes_written IS NOT NULL
    """)
    if rows:
        anomalies = []
        for node, gb in rows:
            cap_gb = 1500 if node == 'BHNFornaxEU1' else 750
            pct = (float(gb or 0) / cap_gb) * 100 if cap_gb else 0
            if pct > 70:
                anomalies.append(f"  {node}: {gb:.1f}/{cap_gb}GB ({pct:.0f}%)")
        if anomalies:
            out.append("Tor relay accounting:\n" + "\n".join(anomalies))

    return "\n\n".join(out)


def main():
    env = load_env()
    dsn = env.get('BHN_BRIEFING_PG_DSN', '')
    if not dsn:
        log("no PG DSN configured — skipping"); return 0
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        text = compose(cur)
    print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
