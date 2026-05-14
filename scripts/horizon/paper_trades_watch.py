#!/usr/bin/env python3
"""
paper_trades_watch.py — periodic monitor of BHN paper trading activity.

Fired by bhn-paper-trades-watch.timer at:
  09:35 America/New_York  (5 min after market open; catches strat_13 first cycle)
  15:58 America/New_York  (3 min after strat_8 rebalance at 15:55 ET)

Each run:
  1. Queries LA PG for recent paper_trades (last 6h) + signals_log activity
     + last_run_at on active strategies
  2. SSHes to NJ and tails the strategy logs (rsi-intraday, sector-rotation,
     nasdaq-long) for the most recent 30 lines each
  3. Composes a plain-text summary email + sends via SMTP

Standalone — does NOT import trading_core (the env-loader's required-vars
list is trading-specific and we don't need it for read-only monitoring).
Uses psycopg2 + smtplib directly.

Env (/etc/bhn-trading/env):
  PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PASSWORD
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_TO

CLI:
  python3 paper_trades_watch.py                # run, send email
  python3 paper_trades_watch.py --no-send      # print to stdout instead
  python3 paper_trades_watch.py --window-hours 12   # widen lookback window
"""
from __future__ import annotations

import argparse
import logging
import os
import smtplib
import subprocess
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras


logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
log = logging.getLogger("paper_trades_watch")


ET = ZoneInfo("America/New_York")

# Strategies the operator currently cares about; matches the post-2026-05-14
# restructure active set. Adjust if the active set changes.
ACTIVE_STRATEGIES = (
    "strat_3_mean_reversion",
    "strat_4_momentum",
    "strat_6_nasdaq_long",
    "strat_7_nasdaq_short",   # paused per restructure but tracked for completeness
    "strat_8_sector_rotation",
    "strat_13_rsi_intraday",
)

# Strategy log filenames on NJ — bhn-strategy@.service writes one per name
# (with hyphens). The systemd template substitutes %i literally.
NJ_LOG_FILES = (
    "strategy-rsi-intraday.log",
    "strategy-sector-rotation.log",
    "strategy-nasdaq-long.log",
    "strategy-nasdaq-short.log",
    "strategy-mean-reversion.log",
    "strategy-momentum.log",
)


def pg_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=os.environ["PG_HOST"],
        port=os.environ.get("PG_PORT", "5432"),
        dbname=os.environ["PG_DB"],
        user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"],
        connect_timeout=10,
    )


# ─────────────────────────────────────────────────────────────────────────
# PG queries
# ─────────────────────────────────────────────────────────────────────────

def fetch_recent_trades(window_hours: int) -> list[dict]:
    with pg_conn() as c:
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, strategy_id, ticker, side, qty, entry_price,
                       status, entry_time, exit_time, exit_price, exit_reason,
                       pnl_pct, pnl_dollar
                FROM paper_trades
                WHERE entry_time > NOW() - (%s || ' hours')::interval
                   OR (exit_time IS NOT NULL AND exit_time > NOW() - (%s || ' hours')::interval)
                ORDER BY entry_time DESC
                LIMIT 50
            """, (window_hours, window_hours))
            return [dict(r) for r in cur.fetchall()]


def fetch_signal_counts(window_hours: int) -> list[dict]:
    with pg_conn() as c:
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT strategy_id,
                       COUNT(*) AS signals,
                       COUNT(*) FILTER (WHERE acted_on) AS acted_on,
                       MAX(evaluated_at) AS most_recent
                FROM signals_log
                WHERE evaluated_at > NOW() - (%s || ' hours')::interval
                GROUP BY strategy_id
                ORDER BY strategy_id
            """, (window_hours,))
            return [dict(r) for r in cur.fetchall()]


def fetch_strategy_status() -> list[dict]:
    with pg_conn() as c:
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, status, capital_allocation, last_run_at,
                       last_status_change_at, last_status_change_reason
                FROM trading_strategies
                WHERE id = ANY(%s)
                ORDER BY id
            """, (list(ACTIVE_STRATEGIES),))
            return [dict(r) for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────────────
# NJ log tails via SSH
# ─────────────────────────────────────────────────────────────────────────

def tail_nj_log(filename: str, lines: int = 30) -> str:
    """Best-effort tail. Returns log content or an error string. Never raises."""
    path = f"/var/log/bhn-trading/{filename}"
    try:
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             "nj", f"tail -n {lines} {path} 2>&1 || echo '(no log yet — strategy has not fired)'"],
            capture_output=True, timeout=30,
        )
        text = r.stdout.decode("utf-8", "replace").rstrip()
        if r.returncode != 0:
            stderr = r.stderr.decode("utf-8", "replace")[:200]
            return f"(ssh nj rc={r.returncode}: {stderr})"
        return text or "(empty)"
    except subprocess.TimeoutExpired:
        return "(ssh nj timeout)"
    except Exception as e:
        return f"(ssh nj error: {e})"


# ─────────────────────────────────────────────────────────────────────────
# Format summary
# ─────────────────────────────────────────────────────────────────────────

def format_summary(window_hours: int,
                    trades: list[dict],
                    signals: list[dict],
                    statuses: list[dict],
                    logs: dict[str, str]) -> tuple[str, str]:
    """Returns (subject, body)."""
    now_et = datetime.now(ET)
    now_utc = datetime.now(timezone.utc)
    parts: list[str] = []
    parts.append(f"BHN PAPER-TRADES WATCH — {now_et:%Y-%m-%d %H:%M ET} ({now_utc:%H:%M UTC})")
    parts.append(f"Window: last {window_hours}h")
    parts.append("")

    # ── trades ──
    parts.append(f"PAPER TRADES (last {window_hours}h, {len(trades)} rows):")
    if not trades:
        parts.append("  (no trades in window)")
    else:
        for t in trades:
            ts = t["entry_time"].astimezone(ET) if t["entry_time"] else None
            ts_str = ts.strftime("%m-%d %H:%M") if ts else "—"
            status = t["status"]
            pnl = f" pnl={t['pnl_pct']:+.2f}%" if t["pnl_pct"] is not None else ""
            exit_info = f" exit={t['exit_reason']}" if t["exit_reason"] else ""
            parts.append(f"  #{t['id']:>4}  {ts_str}  {t['strategy_id']:30s}  "
                          f"{t['side']:4s} {t['ticker']:6s} qty={t['qty']:>4} "
                          f"@ ${t['entry_price']}  [{status}]{pnl}{exit_info}")
    parts.append("")

    # ── signals ──
    parts.append(f"SIGNALS (last {window_hours}h, by strategy):")
    if not signals:
        parts.append("  (no signals in window)")
    else:
        for s in signals:
            mr = s["most_recent"].astimezone(ET) if s["most_recent"] else None
            mr_str = mr.strftime("%m-%d %H:%M ET") if mr else "—"
            parts.append(f"  {s['strategy_id']:30s}  total={s['signals']:>3}  "
                          f"acted_on={s['acted_on']:>3}  last={mr_str}")
    parts.append("")

    # ── strategy status ──
    parts.append("STRATEGY STATUS:")
    for st in statuses:
        lr = st["last_run_at"].astimezone(ET) if st["last_run_at"] else None
        lr_str = lr.strftime("%m-%d %H:%M ET") if lr else "(never)"
        parts.append(f"  {st['id']:30s}  status={st['status']:8s}  "
                      f"alloc=${float(st['capital_allocation']):>10.2f}  last_run={lr_str}")
    parts.append("")

    # ── NJ log tails (only show ones that have content) ──
    parts.append("NJ STRATEGY LOGS (tail 30 each, only non-empty):")
    for fname, content in logs.items():
        if "(no log yet" in content or content == "(empty)":
            parts.append(f"  --- {fname}: {content} ---")
            continue
        parts.append(f"  --- {fname} ---")
        for ln in content.splitlines()[-30:]:
            parts.append(f"    {ln}")
        parts.append("")

    subject = (f"BHN watch {now_et:%m-%d %H:%M ET}: "
               f"{len(trades)} trades / {sum(s['signals'] for s in signals)} signals")
    return subject, "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────
# Email send
# ─────────────────────────────────────────────────────────────────────────

def send_email(subject: str, body: str) -> bool:
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    pwd  = os.environ.get("SMTP_PASSWORD")
    sender = os.environ.get("SMTP_FROM", user)
    to     = os.environ.get("SMTP_TO", "hayden.harper92@proton.me")

    if not all((host, user, pwd, sender, to)):
        log.error("SMTP credentials incomplete — set SMTP_HOST/PORT/USER/PASSWORD/FROM/TO")
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = to

    try:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            s.login(user, pwd)
            s.send_message(msg)
        log.info(f"email sent to {to}: {subject!r}")
        return True
    except Exception as e:
        log.error(f"SMTP send failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description="BHN paper-trades watch monitor")
    p.add_argument("--window-hours", type=int, default=6,
                    help="Lookback window for trades/signals (default 6)")
    p.add_argument("--no-send", action="store_true",
                    help="Print to stdout instead of emailing")
    args = p.parse_args()

    log.info(f"=== watch cycle start (window={args.window_hours}h, send={not args.no_send}) ===")
    try:
        trades   = fetch_recent_trades(args.window_hours)
        signals  = fetch_signal_counts(args.window_hours)
        statuses = fetch_strategy_status()
        logs     = {fn: tail_nj_log(fn) for fn in NJ_LOG_FILES}

        subject, body = format_summary(args.window_hours, trades, signals, statuses, logs)

        if args.no_send:
            print(f"Subject: {subject}\n")
            print(body)
            return 0

        ok = send_email(subject, body)
        return 0 if ok else 1
    except Exception:
        log.exception("watch cycle failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
