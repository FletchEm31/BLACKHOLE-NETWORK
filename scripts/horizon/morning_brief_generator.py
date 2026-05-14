#!/usr/bin/env python3
"""
morning_brief_generator.py — HORIZON daily financial intelligence brief.

Assembles a structured brief from market_regimes, market_daily, macro_daily,
market_sentiment, market_events, pattern_library, paper_trades, weather
tables. Emits as plain-text email via SMTP.

TRIGGERING MODEL (operator-controlled, NOT auto-daily):
  - SMS "BRIEF" or "MORNING BRIEF"   → HORIZON SMS handler invokes this script
  - n8n manual trigger button         → n8n workflow calls this script
  - Grafana P2+ overnight alert       → webhook → this script (only if
                                          operator_config.brief_auto_on_incident)
  - SMS "BRIEF DAILY 8AM"             → HORIZON writes operator_config.brief_schedule;
                                          --check-schedule mode reads it and fires when due

There is no bhn-morning-brief.timer. The .service unit exists for systemctl
start invocation by any of the above triggers.

CLI:
  python3 morning_brief_generator.py                          # assemble + send (manual / sms)
  python3 morning_brief_generator.py --reason sms             # tag trigger source in log
  python3 morning_brief_generator.py --no-send                # print to stdout, no email
  python3 morning_brief_generator.py --check-schedule         # respect operator_config.brief_schedule
                                                                # exit 0 silently if not due
  python3 morning_brief_generator.py --on-incident            # called by Grafana webhook;
                                                                # respects brief_auto_on_incident gate

Env (/etc/bhn-trading/env):
  PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PASSWORD
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_TO
"""
from __future__ import annotations

import argparse
import os
import smtplib
import sys
import time
from datetime import date, datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "trading"))
import trading_core as tc

# Same-package import — events_calendar pre-fetch
sys.path.insert(0, str(Path(__file__).resolve().parent))
import events_calendar  # noqa: E402


logger = tc.get_logger("horizon_morning_brief")


PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")


# ─────────────────────────────────────────────────────────────────────────
# PG queries — each returns simple Python data, no formatting yet
# ─────────────────────────────────────────────────────────────────────────

def fetch_latest_regime() -> Optional[dict]:
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, regime, spy_close, spy_vs_200ma, vix, yield_curve,
                       confidence_score, notes
                FROM market_regimes
                ORDER BY date DESC LIMIT 1
            """)
            r = cur.fetchone()
            if not r:
                return None
            return {
                "date": r[0], "regime": r[1], "spy_close": float(r[2]) if r[2] else None,
                "spy_vs_200ma": float(r[3]) if r[3] else None,
                "vix": float(r[4]) if r[4] else None,
                "yield_curve": float(r[5]) if r[5] else None,
                "confidence_score": float(r[6]) if r[6] else None,
                "notes": r[7],
            }


def fetch_spy_change_pct() -> Optional[float]:
    """Yesterday-to-today pct change for SPY from market_daily."""
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, close FROM market_daily
                WHERE ticker = 'SPY'
                ORDER BY date DESC LIMIT 2
            """)
            rows = cur.fetchall()
            if len(rows) < 2:
                return None
            today_close = float(rows[0][1])
            prior_close = float(rows[1][1])
            if prior_close == 0:
                return None
            return (today_close - prior_close) / prior_close


def fetch_vix_percentile(window_days: int = 252) -> Optional[float]:
    """Percentile rank of today's VIX vs trailing window_days values, 0-100."""
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT vix FROM macro_daily
                WHERE vix IS NOT NULL
                ORDER BY date DESC LIMIT %s
            """, (window_days,))
            rows = [float(r[0]) for r in cur.fetchall()]
    if len(rows) < 30:
        return None
    today_vix = rows[0]
    history = rows  # includes today
    below = sum(1 for v in history if v < today_vix)
    return round(100.0 * below / len(history), 1)


def fetch_open_positions() -> list[dict]:
    """All open paper_trades + strategy_id + latest signal value per strategy."""
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT pt.strategy_id, pt.ticker, pt.qty, pt.side,
                       pt.entry_price, pt.entry_time,
                       pt.stop_loss, pt.trailing_stop_pct,
                       md.close AS latest_close,
                       (EXTRACT(EPOCH FROM (NOW() - pt.entry_time)) / 86400)::int AS days_held
                FROM paper_trades pt
                LEFT JOIN LATERAL (
                    SELECT close FROM market_daily
                    WHERE ticker = pt.ticker
                    ORDER BY date DESC LIMIT 1
                ) md ON TRUE
                WHERE pt.status = 'open'
                ORDER BY pt.strategy_id, pt.entry_time
            """)
            rows = []
            for r in cur.fetchall():
                entry = float(r[4])
                latest = float(r[8]) if r[8] else entry
                sign = 1.0 if r[3] == "buy" else -1.0
                pnl_pct = sign * (latest - entry) / entry if entry > 0 else 0.0
                rows.append({
                    "strategy_id": r[0], "ticker": r[1], "qty": int(r[2]), "side": r[3],
                    "entry_price": entry, "entry_time": r[5],
                    "stop_loss": float(r[6]) if r[6] else None,
                    "trailing_stop_pct": float(r[7]) if r[7] else None,
                    "latest_close": latest, "pnl_pct": pnl_pct,
                    "days_held": int(r[9]) if r[9] is not None else 0,
                })
            return rows


def fetch_latest_strategy_signal(strategy_id: str) -> Optional[dict]:
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT evaluated_at, ticker, action, reason, value
                FROM signals_log
                WHERE strategy_id = %s
                ORDER BY evaluated_at DESC LIMIT 1
            """, (strategy_id,))
            r = cur.fetchone()
            if not r:
                return None
            return {
                "evaluated_at": r[0], "ticker": r[1], "action": r[2],
                "reason": r[3], "value": float(r[4]) if r[4] is not None else None,
            }


def fetch_todays_signals() -> list[dict]:
    """Signals_log entries from the last 24h, across all strategies."""
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT strategy_id, ticker, action, reason, value, evaluated_at
                FROM signals_log
                WHERE evaluated_at > NOW() - INTERVAL '24 hours'
                ORDER BY evaluated_at DESC
                LIMIT 25
            """)
            return [{
                "strategy_id": r[0], "ticker": r[1], "action": r[2],
                "reason": r[3], "value": float(r[4]) if r[4] is not None else None,
                "evaluated_at": r[5],
            } for r in cur.fetchall()]


def fetch_active_patterns(top_k: int = 3) -> list[dict]:
    """Top active patterns by confidence × log(sample_size)."""
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT pattern_type, description, sample_size, win_rate, avg_return,
                       confidence_score, last_triggered_at, strategies_affected
                FROM pattern_library
                WHERE active = TRUE AND sample_size > 0
                ORDER BY (confidence_score * LN(GREATEST(sample_size, 1) + 1)) DESC
                LIMIT %s
            """, (top_k,))
            return [{
                "pattern_type": r[0], "description": r[1], "sample_size": int(r[2]),
                "win_rate": float(r[3]) if r[3] else None,
                "avg_return": float(r[4]) if r[4] else None,
                "confidence_score": float(r[5]) if r[5] else None,
                "last_triggered_at": r[6],
                "strategies_affected": list(r[7] or []),
            } for r in cur.fetchall()]


def fetch_todays_events() -> list[dict]:
    today_et = datetime.now(ET).date()
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT event_date, event_type, ticker, description, expected_impact
                FROM market_events
                WHERE event_date = %s
                ORDER BY expected_impact DESC, event_type
            """, (today_et,))
            return [{
                "event_date": r[0], "event_type": r[1], "ticker": r[2],
                "description": r[3], "expected_impact": r[4],
            } for r in cur.fetchall()]


def fetch_upcoming_events(days: int = 7) -> list[dict]:
    today_et = datetime.now(ET).date()
    end = today_et + timedelta(days=days)
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT event_date, event_type, ticker, description, expected_impact
                FROM market_events
                WHERE event_date > %s AND event_date <= %s
                ORDER BY event_date, expected_impact DESC
                LIMIT 20
            """, (today_et, end))
            return [{
                "event_date": r[0], "event_type": r[1], "ticker": r[2],
                "description": r[3], "expected_impact": r[4],
            } for r in cur.fetchall()]


def fetch_weather_status() -> dict:
    """Compute simple 30d forecast accuracy per Kalshi city. Returns dict
    with per-city accuracy + last-update timestamp."""
    out: dict = {"cities": {}, "last_forecast_at": None}
    try:
        with tc.get_pg_conn() as conn:
            with conn.cursor() as cur:
                # Last forecast write (any city, any model)
                cur.execute("SELECT MAX(generated_at) FROM weather_forecasts")
                row = cur.fetchone()
                if row and row[0]:
                    out["last_forecast_at"] = row[0]

                # 30d accuracy: % of tmax_f forecasts within 5°F of observation
                # Joins forecasts (lead 24h) to observations on same (station, day, variable)
                cur.execute("""
                    SELECT f.station_code,
                           COUNT(*) AS n,
                           AVG(CASE WHEN ABS(f.predicted_value - o.observed_value) <= 5 THEN 1.0 ELSE 0.0 END) AS pct_within_5f
                    FROM weather_forecasts f
                    JOIN weather_observations o
                      ON o.station_code = f.station_code
                     AND o.variable = f.variable
                     AND o.observed_at::date = f.target_date
                    WHERE f.variable = 'tmax_f'
                      AND f.lead_time_hours BETWEEN 12 AND 36
                      AND f.target_date >= CURRENT_DATE - INTERVAL '30 days'
                      AND f.target_date <  CURRENT_DATE
                    GROUP BY f.station_code
                """)
                for r in cur.fetchall():
                    station, n, pct = r
                    out["cities"][station] = {"n": int(n), "pct_within_5f": float(pct) if pct else None}
    except Exception as e:
        logger.warning(f"weather status compute failed: {e}")
    return out


def fetch_strat8_top_score() -> Optional[dict]:
    """Latest Strat 8 sector-rotation signal value (its computed final_score)."""
    sig = fetch_latest_strategy_signal("strat_8_sector_rotation")
    return sig


def fetch_sentiment() -> Optional[dict]:
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, fear_greed_index, fear_greed_label, put_call_ratio,
                       insider_buy_sell_ratio, aaii_bull_pct, aaii_bear_pct
                FROM market_sentiment
                ORDER BY date DESC LIMIT 1
            """)
            r = cur.fetchone()
            if not r:
                return None
            return {
                "date": r[0],
                "fear_greed_index": int(r[1]) if r[1] is not None else None,
                "fear_greed_label": r[2],
                "put_call_ratio": float(r[3]) if r[3] else None,
                "insider_buy_sell_ratio": float(r[4]) if r[4] else None,
                "aaii_bull_pct": float(r[5]) if r[5] else None,
                "aaii_bear_pct": float(r[6]) if r[6] else None,
            }


# ─────────────────────────────────────────────────────────────────────────
# Formatting
# ─────────────────────────────────────────────────────────────────────────

REGIME_GLYPH: dict[str, str] = {
    "BULL_CALM":      "🟢",
    "BULL_VOLATILE":  "🟡",
    "BULL_STRESSED":  "🟠",
    "BEAR_PANIC":     "🔴",
    "BEAR_GRIND":     "⚫",
}


def _fmt_pct(v: Optional[float], digits: int = 1) -> str:
    if v is None:
        return "?"
    return f"{v*100:+.{digits}f}%"


def _fmt_money(v: Optional[float]) -> str:
    if v is None:
        return "?"
    return f"${v:,.2f}"


def format_brief(regime: Optional[dict], spy_change: Optional[float],
                  vix_pctile: Optional[float], positions: list[dict],
                  signals: list[dict], patterns: list[dict],
                  events_today: list[dict], events_upcoming: list[dict],
                  weather: dict, sentiment: Optional[dict],
                  strat8: Optional[dict]) -> str:
    today_pt = datetime.now(PT).date().isoformat()
    parts: list[str] = []
    parts.append(f"Subject: BHN Morning Brief — {today_pt}\n")

    # ── Market regime ──
    if regime:
        glyph = REGIME_GLYPH.get(regime["regime"], "")
        parts.append(f"MARKET REGIME: {regime['regime']} {glyph}".rstrip())
        spy_str = _fmt_money(regime["spy_close"])
        spy_chg = f" ({_fmt_pct(spy_change)})" if spy_change is not None else ""
        spy_vs = _fmt_pct(regime["spy_vs_200ma"]) if regime["spy_vs_200ma"] is not None else "?"
        parts.append(f"SPY: {spy_str}{spy_chg} — {spy_vs} vs 200MA")
        vix_extra = f" (percentile: {vix_pctile}th)" if vix_pctile is not None else ""
        vix_band = "Low" if regime["vix"] and regime["vix"] < 15 else \
                   "Elevated" if regime["vix"] and regime["vix"] < 25 else "High"
        parts.append(f"VIX: {regime['vix']:.1f} — {vix_band}{vix_extra}"
                      if regime["vix"] is not None else "VIX: ?")
        if regime["yield_curve"] is not None:
            curve_label = "Inverted" if regime["yield_curve"] < 0 else "Normal"
            parts.append(f"Yield Curve: {regime['yield_curve']:+.2f}% — {curve_label}")
        if regime["confidence_score"] is not None:
            parts.append(f"Regime confidence: {regime['confidence_score']:.2f}")
        parts.append("")

    # ── Your positions (grouped by strategy_id) ──
    parts.append("YOUR POSITIONS:")
    if not positions:
        parts.append("  No open positions across any strategy.")
    else:
        by_strat: dict[str, list[dict]] = {}
        for p in positions:
            by_strat.setdefault(p["strategy_id"], []).append(p)
        for sid, lst in sorted(by_strat.items()):
            label = sid.replace("strat_", "Strat ").replace("_", " ").title()
            entries = []
            for p in lst:
                entries.append(f"{p['ticker']} (day {p['days_held']}) "
                                f"{_fmt_pct(p['pnl_pct'])}")
            parts.append(f"  {label}: " + ", ".join(entries))
        # Note Strat 8 momentum score if available
        if strat8 and strat8.get("value") is not None:
            parts.append(f"  Strat 8 sector momentum score: {strat8['value']:.3f} "
                          f"(last evaluated {strat8['evaluated_at']:%Y-%m-%d %H:%M})")
    parts.append("")

    # ── Today's signals ──
    parts.append("TODAY'S SIGNALS:")
    if not signals:
        parts.append("  No signals fired in the last 24h.")
    else:
        # Show top 8 most-recent
        for s in signals[:8]:
            v_str = f" v={s['value']:.3f}" if s["value"] is not None else ""
            parts.append(f"  {s['strategy_id']}: {s['action'].upper()} {s['ticker']}"
                          f"{v_str} — {s['reason'] or '(no reason)'}")
    parts.append("")

    # ── Active patterns ──
    parts.append("PATTERNS ACTIVE:")
    if not patterns:
        parts.append("  No active patterns. Pattern detector requires >= 63 trading "
                      "days of market_daily per ticker — likely still in scaffold mode.")
    else:
        for p in patterns:
            wr = f"win rate {p['win_rate']*100:.0f}%" if p["win_rate"] is not None else ""
            ar = f"avg return {p['avg_return']*100:+.1f}%" if p["avg_return"] is not None else ""
            parts.append(f"  - {p['description'] or p['pattern_type']} "
                          f"({wr}, {p['sample_size']} instances{', ' + ar if ar else ''})")
    parts.append("")

    # ── Today's events ──
    parts.append("TODAY'S EVENTS:")
    if not events_today:
        parts.append("  No scheduled events.")
    else:
        for e in events_today:
            impact_tag = ""
            if e["expected_impact"] == "high":
                impact_tag = " (HIGH IMPACT)"
            tk = f" [{e['ticker']}]" if e["ticker"] else ""
            parts.append(f"  {e['event_type']}{tk}: {e['description']}{impact_tag}")
    parts.append("")

    # ── Upcoming events (next 7 days) ──
    if events_upcoming:
        parts.append("UPCOMING (next 7 days):")
        for e in events_upcoming[:10]:
            tk = f" [{e['ticker']}]" if e["ticker"] else ""
            parts.append(f"  {e['event_date']}: {e['event_type']}{tk} "
                          f"({e['expected_impact']})")
        parts.append("")

    # ── Weather model status ──
    parts.append("WEATHER MODEL:")
    if weather.get("last_forecast_at"):
        parts.append(f"  Last forecast: {weather['last_forecast_at']:%Y-%m-%d %H:%M UTC}")
    else:
        parts.append("  No forecast data available.")
    if weather["cities"]:
        for city, stats in weather["cities"].items():
            pct = stats.get("pct_within_5f")
            pct_str = f"{pct*100:.0f}%" if pct is not None else "?"
            parts.append(f"  {city}: accuracy (±5°F, 30d) = {pct_str} "
                          f"over {stats['n']} forecasts")
    parts.append("  Active bets: none — Kalshi auth not yet wired (Phase 1 paper-only).")
    parts.append("")

    # ── Sentiment ──
    if sentiment:
        parts.append("SENTIMENT:")
        if sentiment["fear_greed_index"] is not None:
            parts.append(f"  Fear & Greed: {sentiment['fear_greed_index']} "
                          f"({sentiment['fear_greed_label']})")
        if sentiment["put_call_ratio"] is not None:
            parts.append(f"  Put/Call ratio: {sentiment['put_call_ratio']:.2f}")
        if sentiment["insider_buy_sell_ratio"] is not None:
            parts.append(f"  Insider buy/sell (5d $-weighted): "
                          f"{sentiment['insider_buy_sell_ratio']:.2f}")
        if sentiment["aaii_bull_pct"] is not None:
            parts.append(f"  AAII: bull {sentiment['aaii_bull_pct']:.1f}% / "
                          f"bear {sentiment['aaii_bear_pct']:.1f}%")
        parts.append("")

    # ── Recommendation (deterministic templating from facts above) ──
    parts.append("RECOMMENDATION:")
    parts.append(_build_recommendation(regime, events_today, positions))
    parts.append("")
    parts.append(f"— HORIZON {datetime.now(PT):%H:%M} PT")
    return "\n".join(parts)


def _build_recommendation(regime: Optional[dict], events_today: list[dict],
                            positions: list[dict]) -> str:
    """Templated recommendation. Pulls facts from regime + high-impact events
    + open positions. No LLM call."""
    lines: list[str] = []

    if regime:
        regime_name = regime["regime"]
        if regime_name in ("BULL_CALM", "BULL_VOLATILE"):
            lines.append("  Regime is favorable for current positioning.")
        elif regime_name == "BULL_STRESSED":
            lines.append("  Bull trend intact but volatility elevated — "
                          "watch position sizing, not directional exposure.")
        elif regime_name == "BEAR_PANIC":
            lines.append("  BEAR_PANIC regime — defensive bias warranted. "
                          "Mean reversion + cash heavy.")
        elif regime_name == "BEAR_GRIND":
            lines.append("  Grinding bear — short bias acceptable, momentum strategies degraded.")

    high_impact = [e for e in events_today if e["expected_impact"] == "high"]
    if high_impact:
        names = ", ".join(e["event_type"] for e in high_impact)
        lines.append(f"  Today's HIGH-IMPACT events: {names}. "
                      f"Expect intraday vol; defer fresh entries until post-print.")

    # Position-aware risk note: nearest trailing stop
    nearest_stop_pct = None
    for p in positions:
        if p["trailing_stop_pct"] and p["latest_close"]:
            stop_distance = p["trailing_stop_pct"] / 100.0
            nearest_stop_pct = min(nearest_stop_pct or 1.0, stop_distance)
    if nearest_stop_pct is not None:
        lines.append(f"  Tightest trailing stop is {nearest_stop_pct*100:.1f}% "
                      f"below entry — a same-percent SPY drop would trigger.")

    if not lines:
        lines.append("  No specific recommendation — insufficient data or quiet day.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────
# Email send
# ─────────────────────────────────────────────────────────────────────────

def send_email(body: str) -> bool:
    """Send the brief via SMTP. Returns True on success."""
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    pwd  = os.environ.get("SMTP_PASSWORD")
    sender = os.environ.get("SMTP_FROM", user)
    to     = os.environ.get("SMTP_TO", "hayden.harper92@proton.me")

    if not all((host, user, pwd, sender, to)):
        logger.error("SMTP credentials incomplete — set SMTP_HOST/PORT/USER/PASSWORD/FROM/TO "
                     "in /etc/bhn-trading/env.")
        return False

    # Subject is the first line of the body ("Subject: ..."); strip it for body.
    lines = body.split("\n", 1)
    subject_line = lines[0]
    subject = subject_line.replace("Subject:", "").strip() if subject_line.lower().startswith("subject:") \
              else f"BHN Morning Brief — {date.today():%Y-%m-%d}"
    payload = lines[1] if len(lines) > 1 and subject_line.lower().startswith("subject:") else body

    msg = MIMEText(payload, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to

    try:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            s.login(user, pwd)
            s.send_message(msg)
        logger.info(f"email sent to {to}")
        return True
    except Exception as e:
        logger.error(f"SMTP send failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────
# operator_config helpers
# ─────────────────────────────────────────────────────────────────────────

def record_brief_sent(trigger_reason: str) -> None:
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE operator_config
                SET last_brief_sent_at = NOW(), updated_at = NOW()
                WHERE id = 1
            """)
    logger.info(f"operator_config.last_brief_sent_at updated (trigger={trigger_reason})")


def fetch_operator_config() -> dict:
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT brief_schedule, brief_auto_on_incident, last_brief_sent_at
                FROM operator_config WHERE id = 1
            """)
            r = cur.fetchone()
            if not r:
                return {"brief_schedule": None, "brief_auto_on_incident": True,
                        "last_brief_sent_at": None}
            return {
                "brief_schedule": r[0],
                "brief_auto_on_incident": bool(r[1]) if r[1] is not None else True,
                "last_brief_sent_at": r[2],
            }


def schedule_says_fire_now(brief_schedule: Optional[str],
                            now: Optional[datetime] = None,
                            window_minutes: int = 5) -> bool:
    """Returns True if brief_schedule matches the current local time within
    window_minutes. Format: "HH:MM TZ" e.g. "08:00 PT" or "08:00 ET"."""
    if not brief_schedule:
        return False
    try:
        time_part, tz_part = brief_schedule.strip().rsplit(" ", 1)
        hh_str, mm_str = time_part.split(":")
        hh, mm = int(hh_str), int(mm_str)
        tz = PT if tz_part.upper() in ("PT", "PST", "PDT") else \
             ET if tz_part.upper() in ("ET", "EST", "EDT") else \
             ZoneInfo(tz_part)
    except (ValueError, KeyError):
        logger.warning(f"could not parse brief_schedule={brief_schedule!r}")
        return False
    n = now or datetime.now(tz)
    n_local = n.astimezone(tz)
    target = n_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    delta_min = abs((n_local - target).total_seconds()) / 60.0
    return delta_min <= window_minutes


def already_sent_today(last_sent: Optional[datetime]) -> bool:
    if last_sent is None:
        return False
    today_pt = datetime.now(PT).date()
    return last_sent.astimezone(PT).date() == today_pt


# ─────────────────────────────────────────────────────────────────────────
# Assembly + send pipeline
# ─────────────────────────────────────────────────────────────────────────

def assemble_brief() -> str:
    # Refresh events_calendar first so today's row is current
    try:
        events_calendar.refresh_events(horizon_days=14, sources=("fomc", "macro", "opex"))
    except Exception as e:
        logger.warning(f"events_calendar refresh failed (continuing): {e}")

    regime          = fetch_latest_regime()
    spy_change      = fetch_spy_change_pct()
    vix_pctile      = fetch_vix_percentile()
    positions       = fetch_open_positions()
    signals         = fetch_todays_signals()
    patterns        = fetch_active_patterns()
    events_today    = fetch_todays_events()
    events_upcoming = fetch_upcoming_events(days=7)
    weather         = fetch_weather_status()
    sentiment       = fetch_sentiment()
    strat8          = fetch_strat8_top_score()

    return format_brief(regime, spy_change, vix_pctile, positions, signals,
                          patterns, events_today, events_upcoming, weather,
                          sentiment, strat8)


def run(reason: str, send: bool = True, check_schedule: bool = False,
         on_incident: bool = False) -> int:
    cfg = fetch_operator_config()

    if check_schedule:
        if not schedule_says_fire_now(cfg["brief_schedule"]):
            logger.info(f"--check-schedule: brief_schedule={cfg['brief_schedule']!r} "
                        f"does not match now — exiting silently")
            return 0
        if already_sent_today(cfg["last_brief_sent_at"]):
            logger.info("--check-schedule: brief already sent today — skipping")
            return 0
        reason = "scheduled"

    if on_incident:
        if not cfg["brief_auto_on_incident"]:
            logger.info("--on-incident: brief_auto_on_incident=FALSE — skipping")
            return 0
        if already_sent_today(cfg["last_brief_sent_at"]):
            logger.info("--on-incident: brief already sent today — skipping")
            return 0
        reason = "incident"

    body = assemble_brief()

    if not send:
        sys.stdout.write(body)
        sys.stdout.write("\n")
        sys.stdout.flush()
        return 0

    ok = send_email(body)
    if ok:
        record_brief_sent(reason)
        return 0
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="HORIZON morning brief generator")
    parser.add_argument("--reason", default="manual",
                        help="Trigger label for the log: manual / sms / scheduled / incident")
    parser.add_argument("--no-send", action="store_true",
                        help="Print brief to stdout, do not email")
    parser.add_argument("--check-schedule", action="store_true",
                        help="Respect operator_config.brief_schedule; exit silently if not due")
    parser.add_argument("--on-incident", action="store_true",
                        help="Grafana webhook path; respects brief_auto_on_incident gate")
    args = parser.parse_args()

    logger.info(f"=== morning-brief start (reason={args.reason}, "
                f"no_send={args.no_send}, check_schedule={args.check_schedule}, "
                f"on_incident={args.on_incident}) ===")
    try:
        rc = run(reason=args.reason, send=not args.no_send,
                  check_schedule=args.check_schedule, on_incident=args.on_incident)
    except Exception:
        logger.exception("morning-brief failed")
        return 1
    logger.info(f"=== morning-brief end (rc={rc}) ===")
    return rc


if __name__ == "__main__":
    sys.exit(main())
