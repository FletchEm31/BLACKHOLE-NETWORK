#!/usr/bin/env python3
"""
daily_summary.py — BHN trading framework end-of-day digest.

Runs once per trading day, after market close. For the target trading day
(default: today in US/Eastern), computes per-strategy + portfolio-wide
statistics, persists them to strategy_performance, and SMS-es the digest
to the operator.

Per-strategy aggregates:
- Trades opened (count, total notional)
- Trades closed (count, realized P&L $, realized P&L %)
- Win rate (closed trades only — wins / total closed)
- Avg win $, avg loss $, profit factor (gross wins / gross losses)
- Best trade, worst trade
- Turnover (sum of |entry_value| + |exit_value| for the day)
- Open positions carried (count + mark-to-market unrealized)

Portfolio level:
- Total realized P&L across all 5 strategies
- Total unrealized (open positions × current price)
- Total turnover dollars
- Active vs halted strategies
- System halt state (any incidents today?)
- vs. last-7-day average (alerts on outliers — e.g. -3σ realized day)

Output paths:
1. SMS to operator (compact ~1500 char digest) — default ON
2. JSON to stdout when --format json (for HORIZON consumption)
3. Always: insert/update strategy_performance row per strategy for the day

CLI:
    python3 daily_summary.py                  # today in ET, SMS + print
    python3 daily_summary.py --date 2026-05-12
    python3 daily_summary.py --no-sms         # print only
    python3 daily_summary.py --format json    # machine-readable
    python3 daily_summary.py --strategy strat_3_scalp  # single strategy only

Cron suggestion:
    15 13 * * 1-5  python3 /opt/bhn/trading/daily_summary.py
    # 13:15 PT = ~75min after NYSE close, gives time for late fills

Trading-day window: [target_date 00:00 ET, target_date+1 00:00 ET).
This avoids weird edge cases with after-hours trades and matches Alpaca's
own day-attribution.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

import requests
import psycopg2.extras

import trading_core as tc


logger = tc.get_logger("daily_summary")

ET = ZoneInfo("America/New_York")


# ─────────────────────────────────────────────────────────────────────────
# Date / window helpers
# ─────────────────────────────────────────────────────────────────────────

def parse_date_arg(arg: Optional[str]) -> date:
    if arg:
        return datetime.strptime(arg, "%Y-%m-%d").date()
    return datetime.now(ET).date()


def trading_day_window(target_date: date) -> tuple[datetime, datetime]:
    """Return [start, end) in UTC for the ET trading day."""
    start_et = datetime.combine(target_date, datetime.min.time(), tzinfo=ET)
    end_et = start_et + timedelta(days=1)
    return start_et.astimezone(timezone.utc), end_et.astimezone(timezone.utc)


# ─────────────────────────────────────────────────────────────────────────
# Aggregations
# ─────────────────────────────────────────────────────────────────────────

def fetch_closed_trades(strategy_id: Optional[str],
                         start_utc: datetime, end_utc: datetime) -> list[dict]:
    sql = """
        SELECT * FROM paper_trades
        WHERE status = 'closed'
          AND exit_time >= %s AND exit_time < %s
    """
    params: list = [start_utc, end_utc]
    if strategy_id:
        sql += " AND strategy_id = %s"
        params.append(strategy_id)
    with tc.get_pg_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]


def fetch_opened_trades(strategy_id: Optional[str],
                         start_utc: datetime, end_utc: datetime) -> list[dict]:
    sql = """
        SELECT * FROM paper_trades
        WHERE entry_time >= %s AND entry_time < %s
    """
    params: list = [start_utc, end_utc]
    if strategy_id:
        sql += " AND strategy_id = %s"
        params.append(strategy_id)
    with tc.get_pg_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]


def fetch_open_trades(strategy_id: Optional[str]) -> list[dict]:
    sql = "SELECT * FROM paper_trades WHERE status = 'open'"
    params: list = []
    if strategy_id:
        sql += " AND strategy_id = %s"
        params.append(strategy_id)
    with tc.get_pg_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]


def mark_to_market(trades: list[dict]) -> Decimal:
    """Mark open trades to last trade price. Decimal sum of unrealized P&L."""
    if not trades:
        return Decimal("0")
    alpaca = tc.get_alpaca()
    total = Decimal("0")
    for t in trades:
        try:
            price = Decimal(str(alpaca.get_latest_trade(t["ticker"]).price))
        except Exception as e:
            logger.debug(f"MTM lookup failed for {t['ticker']}: {e}")
            continue
        qty = Decimal(str(t["qty"]))
        entry = Decimal(str(t["entry_price"]))
        total += (price - entry) * qty
    return total


def aggregate_strategy(strategy_id: str, target_date: date,
                       start_utc: datetime, end_utc: datetime) -> dict:
    opened = fetch_opened_trades(strategy_id, start_utc, end_utc)
    closed = fetch_closed_trades(strategy_id, start_utc, end_utc)
    open_now = fetch_open_trades(strategy_id)

    realized_pnl = sum((Decimal(str(t["pnl_dollar"] or 0)) for t in closed), Decimal("0"))
    pnl_pcts = [float(t["pnl_pct"]) for t in closed if t.get("pnl_pct") is not None]
    wins = [t for t in closed if (t.get("pnl_dollar") or 0) > 0]
    losses = [t for t in closed if (t.get("pnl_dollar") or 0) < 0]
    flats = [t for t in closed if (t.get("pnl_dollar") or 0) == 0]

    gross_wins = sum((Decimal(str(t["pnl_dollar"])) for t in wins), Decimal("0"))
    gross_losses = sum((Decimal(str(t["pnl_dollar"])) for t in losses), Decimal("0"))
    profit_factor = (
        float(gross_wins / abs(gross_losses)) if gross_losses < 0 else
        (float("inf") if gross_wins > 0 else 0.0)
    )

    avg_win = float(gross_wins / len(wins)) if wins else 0.0
    avg_loss = float(gross_losses / len(losses)) if losses else 0.0

    best = max(closed, key=lambda t: t["pnl_dollar"] or 0, default=None)
    worst = min(closed, key=lambda t: t["pnl_dollar"] or 0, default=None)

    # Turnover = sum of notional traded (entries + exits)
    turnover = Decimal("0")
    for t in opened:
        turnover += Decimal(str(t["entry_price"])) * Decimal(str(t["qty"]))
    for t in closed:
        if t.get("exit_price"):
            turnover += Decimal(str(t["exit_price"])) * Decimal(str(t["qty"]))

    unrealized = mark_to_market(open_now)

    return {
        "strategy_id": strategy_id,
        "date": target_date.isoformat(),
        "trades_opened": len(opened),
        "trades_closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "flats": len(flats),
        "win_rate": (len(wins) / len(closed)) if closed else None,
        "realized_pnl_dollar": float(realized_pnl),
        "realized_pnl_pct_mean": (statistics.mean(pnl_pcts) if pnl_pcts else 0.0),
        "gross_wins_dollar": float(gross_wins),
        "gross_losses_dollar": float(gross_losses),
        "profit_factor": profit_factor,
        "avg_win_dollar": avg_win,
        "avg_loss_dollar": avg_loss,
        "best_trade": _trade_brief(best),
        "worst_trade": _trade_brief(worst),
        "turnover_dollar": float(turnover),
        "open_positions_carried": len(open_now),
        "unrealized_pnl_dollar": float(unrealized),
    }


def _trade_brief(t: Optional[dict]) -> Optional[dict]:
    if not t:
        return None
    return {
        "ticker": t["ticker"],
        "qty": int(t["qty"]),
        "pnl_dollar": float(t["pnl_dollar"] or 0),
        "pnl_pct": float(t["pnl_pct"] or 0),
        "exit_reason": t.get("exit_reason"),
    }


# ─────────────────────────────────────────────────────────────────────────
# 7-day baseline + outlier flagging
# ─────────────────────────────────────────────────────────────────────────

def fetch_7day_baseline(strategy_id: str, before_date: date) -> Optional[dict]:
    """Last 7 strategy_performance rows before target date. Returns mean +
    stddev of realized P&L for outlier detection."""
    with tc.get_pg_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT realized_pnl_dollar FROM strategy_performance
                WHERE strategy_id = %s AND date < %s
                ORDER BY date DESC LIMIT 7
                """,
                (strategy_id, before_date),
            )
            rows = [float(r["realized_pnl_dollar"]) for r in cur.fetchall()]
    if len(rows) < 3:
        return None
    return {
        "mean": statistics.mean(rows),
        "stdev": statistics.stdev(rows) if len(rows) >= 2 else 0.0,
        "n": len(rows),
    }


def outlier_flag(today_pnl: float, baseline: Optional[dict]) -> Optional[str]:
    if not baseline or baseline["stdev"] == 0:
        return None
    z = (today_pnl - baseline["mean"]) / baseline["stdev"]
    if z <= -3:
        return f"OUTLIER -3σ vs 7d baseline (z={z:.2f})"
    if z <= -2:
        return f"warning -2σ vs 7d baseline (z={z:.2f})"
    if z >= 3:
        return f"OUTLIER +3σ vs 7d baseline (z={z:.2f})"
    return None


# ─────────────────────────────────────────────────────────────────────────
# Persist to strategy_performance
# ─────────────────────────────────────────────────────────────────────────

def persist_performance_row(agg: dict) -> None:
    """Insert or update strategy_performance row for (strategy_id, date).
    UNIQUE constraint on (strategy_id, date) assumed per trading-schema.sql."""
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO strategy_performance
                    (strategy_id, date, trades_opened, trades_closed,
                     wins, losses, win_rate, realized_pnl_dollar,
                     realized_pnl_pct_mean, profit_factor,
                     avg_win_dollar, avg_loss_dollar,
                     turnover_dollar, open_positions_carried,
                     unrealized_pnl_dollar, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (strategy_id, date) DO UPDATE
                SET trades_opened          = EXCLUDED.trades_opened,
                    trades_closed          = EXCLUDED.trades_closed,
                    wins                   = EXCLUDED.wins,
                    losses                 = EXCLUDED.losses,
                    win_rate               = EXCLUDED.win_rate,
                    realized_pnl_dollar    = EXCLUDED.realized_pnl_dollar,
                    realized_pnl_pct_mean  = EXCLUDED.realized_pnl_pct_mean,
                    profit_factor          = EXCLUDED.profit_factor,
                    avg_win_dollar         = EXCLUDED.avg_win_dollar,
                    avg_loss_dollar        = EXCLUDED.avg_loss_dollar,
                    turnover_dollar        = EXCLUDED.turnover_dollar,
                    open_positions_carried = EXCLUDED.open_positions_carried,
                    unrealized_pnl_dollar  = EXCLUDED.unrealized_pnl_dollar,
                    metadata               = EXCLUDED.metadata
                """,
                (
                    agg["strategy_id"], agg["date"],
                    agg["trades_opened"], agg["trades_closed"],
                    agg["wins"], agg["losses"], agg["win_rate"],
                    agg["realized_pnl_dollar"], agg["realized_pnl_pct_mean"],
                    agg["profit_factor"] if agg["profit_factor"] != float("inf") else None,
                    agg["avg_win_dollar"], agg["avg_loss_dollar"],
                    agg["turnover_dollar"], agg["open_positions_carried"],
                    agg["unrealized_pnl_dollar"],
                    json.dumps({"best_trade": agg["best_trade"],
                                "worst_trade": agg["worst_trade"]}),
                ),
            )


# ─────────────────────────────────────────────────────────────────────────
# Halt state context (for digest)
# ─────────────────────────────────────────────────────────────────────────

def fetch_halt_incidents(start_utc: datetime, end_utc: datetime) -> list[dict]:
    with tc.get_pg_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT breaker_type, strategy_id, action, reason, triggered_by,
                       created_at
                FROM circuit_breaker_log
                WHERE created_at >= %s AND created_at < %s
                ORDER BY created_at
                """,
                (start_utc, end_utc),
            )
            return [dict(r) for r in cur.fetchall()]


def fetch_current_halt_state() -> bool:
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT halted FROM trading_strategies WHERE id='system'")
            row = cur.fetchone()
            return bool(row[0]) if row else False


# ─────────────────────────────────────────────────────────────────────────
# Formatting
# ─────────────────────────────────────────────────────────────────────────

def format_sms(target_date: date, portfolio: dict, per_strategy: list[dict],
               incidents: list[dict], currently_halted: bool) -> str:
    lines = [f"BHN EOD {target_date.isoformat()}"]
    sign = "+" if portfolio["realized_pnl_dollar"] >= 0 else ""
    lines.append(
        f"Realized: {sign}${portfolio['realized_pnl_dollar']:,.0f} | "
        f"Unrealized: ${portfolio['unrealized_pnl_dollar']:,.0f}"
    )
    lines.append(
        f"Trades: {portfolio['trades_closed']} closed "
        f"({portfolio['wins']}W/{portfolio['losses']}L) | "
        f"Turnover: ${portfolio['turnover_dollar']:,.0f}"
    )
    lines.append("")
    for agg in per_strategy:
        sign = "+" if agg["realized_pnl_dollar"] >= 0 else ""
        wr = f"{agg['win_rate']*100:.0f}%" if agg["win_rate"] is not None else "—"
        flag = ""
        if agg.get("outlier"):
            flag = f" ⚠ {agg['outlier']}"
        lines.append(
            f"{agg['strategy_id'].replace('strat_', '').replace('_', ' '):15s} "
            f"{sign}${agg['realized_pnl_dollar']:>+7.0f} "
            f"({agg['trades_closed']}t, {wr}){flag}"
        )
    if incidents:
        lines.append("")
        lines.append(f"Incidents: {len(incidents)} breaker event(s)")
        for inc in incidents[:3]:
            lines.append(f"  {inc['breaker_type']}: {(inc.get('reason') or '')[:60]}")
    if currently_halted:
        lines.append("")
        lines.append("⚠ SYSTEM CURRENTLY HALTED")
    return "\n".join(lines)


def format_text(target_date: date, portfolio: dict, per_strategy: list[dict],
                incidents: list[dict], currently_halted: bool) -> str:
    sep = "=" * 70
    lines = [
        sep,
        f"BHN Trading Framework — Daily Summary for {target_date.isoformat()}",
        sep,
        "",
        "PORTFOLIO",
        f"  Realized P&L:       ${portfolio['realized_pnl_dollar']:>+12,.2f}",
        f"  Unrealized P&L:     ${portfolio['unrealized_pnl_dollar']:>+12,.2f}",
        f"  Total turnover:     ${portfolio['turnover_dollar']:>12,.2f}",
        f"  Trades opened:       {portfolio['trades_opened']:>12}",
        f"  Trades closed:       {portfolio['trades_closed']:>12}",
        f"  Wins/Losses/Flats:   {portfolio['wins']}/{portfolio['losses']}/{portfolio['flats']}",
        f"  Win rate:            "
        f"{(portfolio['win_rate']*100):.1f}%" if portfolio['win_rate'] is not None else "  Win rate:            n/a",
        f"  Open positions:      {portfolio['open_positions_carried']}",
        f"  System halted:       {currently_halted}",
        "",
        sep,
        "PER-STRATEGY",
        sep,
    ]
    for agg in per_strategy:
        lines.append("")
        lines.append(f"{agg['strategy_id']}:")
        lines.append(f"  Trades opened/closed: {agg['trades_opened']} / {agg['trades_closed']}")
        if agg["win_rate"] is not None:
            lines.append(f"  Win rate:             {agg['win_rate']*100:.1f}% "
                         f"({agg['wins']}W / {agg['losses']}L / {agg['flats']}F)")
        lines.append(f"  Realized P&L:         ${agg['realized_pnl_dollar']:>+10,.2f} "
                     f"({agg['realized_pnl_pct_mean']:>+.2f}% avg per trade)")
        if agg["profit_factor"] != float("inf"):
            lines.append(f"  Profit factor:        {agg['profit_factor']:.2f}")
        lines.append(f"  Avg win / avg loss:   ${agg['avg_win_dollar']:>+.2f} / "
                     f"${agg['avg_loss_dollar']:>+.2f}")
        lines.append(f"  Turnover:             ${agg['turnover_dollar']:,.2f}")
        lines.append(f"  Open carried:         {agg['open_positions_carried']} "
                     f"(unrealized ${agg['unrealized_pnl_dollar']:>+,.2f})")
        if agg["best_trade"]:
            b = agg["best_trade"]
            lines.append(f"  Best:                 {b['ticker']} "
                         f"${b['pnl_dollar']:>+.2f} ({b['pnl_pct']:+.2f}%) "
                         f"[{b['exit_reason']}]")
        if agg["worst_trade"]:
            w = agg["worst_trade"]
            lines.append(f"  Worst:                {w['ticker']} "
                         f"${w['pnl_dollar']:>+.2f} ({w['pnl_pct']:+.2f}%) "
                         f"[{w['exit_reason']}]")
        if agg.get("outlier"):
            lines.append(f"  ⚠ {agg['outlier']}")

    if incidents:
        lines.append("")
        lines.append(sep)
        lines.append("CIRCUIT BREAKER EVENTS TODAY")
        lines.append(sep)
        for inc in incidents:
            ts = inc["created_at"].astimezone(ET).strftime("%H:%M ET")
            lines.append(f"  {ts}  {inc['breaker_type']:20s} "
                         f"({inc.get('triggered_by', '?')}): "
                         f"{(inc.get('reason') or '')[:80]}")

    lines.append("")
    lines.append(sep)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────
# Twilio SMS (mirrors killswitch — both call /etc/bhn/trading.env vars)
# ─────────────────────────────────────────────────────────────────────────

def send_sms(message: str) -> bool:
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    sender = os.environ.get("TWILIO_FROM_NUMBER")
    recipient = os.environ.get("TWILIO_OPERATOR_NUMBER")
    if not all([sid, token, sender, recipient]):
        logger.warning("Twilio env vars missing — skipping SMS notification")
        return False
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    try:
        resp = requests.post(
            url, auth=(sid, token),
            data={"From": sender, "To": recipient, "Body": message[:1500]},
            timeout=10,
        )
        if resp.status_code in (200, 201):
            logger.info(f"SMS sent: {message[:80]}...")
            return True
        logger.warning(f"Twilio API returned {resp.status_code}: {resp.text[:200]}")
        return False
    except requests.RequestException as e:
        logger.warning(f"Twilio request failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────

def build_summary(target_date: date, strategy_filter: Optional[str]) -> dict:
    start_utc, end_utc = trading_day_window(target_date)
    logger.info(f"Window: {start_utc.isoformat()} → {end_utc.isoformat()}")

    strategies = [s.value for s in tc.StrategyId
                  if s.value != "system"]
    if strategy_filter:
        strategies = [s for s in strategies if s == strategy_filter]
        if not strategies:
            raise ValueError(f"Unknown strategy id: {strategy_filter}")

    per_strategy: list[dict] = []
    for sid in strategies:
        agg = aggregate_strategy(sid, target_date, start_utc, end_utc)
        baseline = fetch_7day_baseline(sid, target_date)
        flag = outlier_flag(agg["realized_pnl_dollar"], baseline)
        if flag:
            agg["outlier"] = flag
        per_strategy.append(agg)

    # Portfolio rollup
    portfolio = {
        "trades_opened": sum(a["trades_opened"] for a in per_strategy),
        "trades_closed": sum(a["trades_closed"] for a in per_strategy),
        "wins": sum(a["wins"] for a in per_strategy),
        "losses": sum(a["losses"] for a in per_strategy),
        "flats": sum(a["flats"] for a in per_strategy),
        "realized_pnl_dollar": sum(a["realized_pnl_dollar"] for a in per_strategy),
        "unrealized_pnl_dollar": sum(a["unrealized_pnl_dollar"] for a in per_strategy),
        "turnover_dollar": sum(a["turnover_dollar"] for a in per_strategy),
        "open_positions_carried": sum(a["open_positions_carried"] for a in per_strategy),
    }
    portfolio["win_rate"] = (
        portfolio["wins"] / portfolio["trades_closed"]
        if portfolio["trades_closed"] else None
    )

    incidents = fetch_halt_incidents(start_utc, end_utc)
    currently_halted = fetch_current_halt_state()

    return {
        "target_date": target_date.isoformat(),
        "portfolio": portfolio,
        "per_strategy": per_strategy,
        "incidents": [
            {**i,
             "created_at": i["created_at"].isoformat() if i.get("created_at") else None}
            for i in incidents
        ],
        "currently_halted": currently_halted,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="BHN trading EOD digest")
    parser.add_argument("--date", help="Trading day in YYYY-MM-DD (default: today ET)")
    parser.add_argument("--no-sms", action="store_true",
                        help="Skip Twilio SMS, print only")
    parser.add_argument("--no-persist", action="store_true",
                        help="Don't write to strategy_performance (dry run)")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--strategy", help="Limit to one strategy id")
    args = parser.parse_args()

    target_date = parse_date_arg(args.date)
    logger.info(f"=== EOD summary for {target_date} ===")

    summary = build_summary(target_date, args.strategy)

    # Persist per-strategy rows
    if not args.no_persist:
        for agg in summary["per_strategy"]:
            try:
                persist_performance_row(agg)
            except Exception as e:
                logger.error(f"Persist failed for {agg['strategy_id']}: {e}")

    # Output
    if args.format == "json":
        print(json.dumps(summary, default=str, indent=2))
    else:
        print(format_text(
            target_date,
            summary["portfolio"], summary["per_strategy"],
            [{**i, "created_at": datetime.fromisoformat(i["created_at"])}
             for i in summary["incidents"] if i.get("created_at")],
            summary["currently_halted"],
        ))

    # SMS
    if not args.no_sms:
        sms_body = format_sms(
            target_date,
            summary["portfolio"], summary["per_strategy"],
            [{**i, "created_at": datetime.fromisoformat(i["created_at"])}
             for i in summary["incidents"] if i.get("created_at")],
            summary["currently_halted"],
        )
        send_sms(sms_body)

    return 0


if __name__ == "__main__":
    sys.exit(main())
