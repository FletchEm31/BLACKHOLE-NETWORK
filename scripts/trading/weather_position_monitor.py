#!/usr/bin/env python3
"""
weather_position_monitor.py — WeatherBHN automated stop-loss monitor.

Runs every 60 seconds via bhn-weather-position-monitor.timer.
Reads open Kalshi positions + current market prices, evaluates 4 stop-loss
triggers, and logs exits to weather_position_exits.

STOP_LOSS_DRY_RUN=true (default): writes to weather_position_exits with
notes='DRY_RUN', sends no SMS, places no Kalshi orders.

Set STOP_LOSS_DRY_RUN=false ONLY with operator approval after 7-day dry-run
review per WEATHERBHN_STOP_LOSS_SPEC.md implementation step 8.

Trigger types:
  PROB_SHIFT    — implied probability moved against us >= STOP_LOSS_PROB_SHIFT
  DOLLAR_LOSS   — dollar loss per position >= STOP_LOSS_DOLLAR
  FORECAST_HARD — NWS forecast shifted >= STOP_LOSS_FORECAST_HARD_LIMIT_F
  FORECAST_SOFT — NWS forecast shifted >= STOP_LOSS_FORECAST_SHIFT_F (flag only)
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from typing import Optional

import requests

import trading_core as tc


logger = tc.get_logger("strat_9_weather_position_monitor")

# ─────────────────────────────────────────────────────────────────────────
# Config from env (strat9.env)
# ─────────────────────────────────────────────────────────────────────────

def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key, "")
    if not v:
        return default
    return v.lower() in ("true", "1", "yes")


STOP_LOSS_ENABLED          = _env_bool("STOP_LOSS_ENABLED", True)
STOP_LOSS_DRY_RUN          = _env_bool("STOP_LOSS_DRY_RUN", True)   # safe default
STOP_LOSS_PROB_SHIFT       = _env_float("STOP_LOSS_PROB_SHIFT", 0.20)
STOP_LOSS_DOLLAR           = _env_float("STOP_LOSS_DOLLAR", 2.00)
STOP_LOSS_FORECAST_SHIFT_F = _env_float("STOP_LOSS_FORECAST_SHIFT_F", 2.0)
STOP_LOSS_FORECAST_HARD_F  = _env_float("STOP_LOSS_FORECAST_HARD_LIMIT_F", 4.0)
STOP_LOSS_TAIL_NO_THRESH   = _env_float("STOP_LOSS_TAIL_NO_THRESHOLD", 0.05)


@dataclass
class Position:
    contract_ticker:    str
    side:               str   # 'yes' | 'no'
    contracts:          int
    avg_price:          Optional[float]   # entry cost per contract [0,1] fraction
    city:               Optional[str]
    station_code:       Optional[str]
    bucket_floor:       Optional[float]
    bucket_cap:         Optional[float]
    target_date:        Optional[date]
    # Derived at evaluation time
    entry_implied_prob: Optional[float]   # P(YES) at entry
    current_yes_mid:    Optional[float]   # current P(YES) from market
    current_yes_bid:    Optional[float]
    nws_forecast_now:   Optional[float]   # latest NWS tmax_f forecast
    nws_forecast_entry: Optional[float]   # NWS tmax_f forecast at entry date (earliest run)


# ─────────────────────────────────────────────────────────────────────────
# DB queries
# ─────────────────────────────────────────────────────────────────────────

def _load_open_positions(conn) -> list[Position]:
    """Return all positions where the latest snapshot has contracts > 0."""
    with conn.cursor() as cur:
        cur.execute("""
            WITH latest AS (
                SELECT DISTINCT ON (contract_ticker)
                    contract_ticker, side, contracts, avg_price, captured_at
                FROM kalshi_positions
                ORDER BY contract_ticker, captured_at DESC
            )
            SELECT
                l.contract_ticker,
                l.side,
                l.contracts,
                l.avg_price,
                c.city,
                c.station_code,
                c.bucket_floor,
                c.bucket_cap,
                c.target_date
            FROM latest l
            LEFT JOIN weather_kalshi_contract_catalog c
                ON c.market_ticker = l.contract_ticker
            WHERE l.contracts > 0
        """)
        rows = cur.fetchall()

    positions = []
    for row in rows:
        (ticker, side, contracts, avg_price,
         city, station_code, bucket_floor, bucket_cap, target_date) = row

        avg_f = float(avg_price) if avg_price is not None else None
        # avg_price may arrive as cents (int-like) if API returns 1-99.
        # Normalize to [0,1] fraction so comparisons are consistent with yes_mid.
        if avg_f is not None and avg_f > 1.0:
            avg_f = avg_f / 100.0

        # Entry implied probability = P(YES) at entry
        if avg_f is not None:
            entry_impl = avg_f if side == "yes" else (1.0 - avg_f)
        else:
            entry_impl = None

        positions.append(Position(
            contract_ticker    = ticker,
            side               = side,
            contracts          = int(contracts),
            avg_price          = avg_f,
            city               = city,
            station_code       = station_code,
            bucket_floor       = float(bucket_floor) if bucket_floor is not None else None,
            bucket_cap         = float(bucket_cap)   if bucket_cap   is not None else None,
            target_date        = target_date,
            entry_implied_prob = entry_impl,
            current_yes_mid    = None,
            current_yes_bid    = None,
            nws_forecast_now   = None,
            nws_forecast_entry = None,
        ))
    return positions


def _enrich_market_prices(conn, positions: list[Position]) -> None:
    """Populate current_yes_mid and current_yes_bid from latest bronze snapshots."""
    if not positions:
        return
    tickers = [p.contract_ticker for p in positions]
    with conn.cursor() as cur:
        cur.execute("""
            WITH latest AS (
                SELECT DISTINCT ON (market_ticker)
                    market_ticker, yes_mid, yes_bid, yes_ask
                FROM weather_bronze_kalshi_market_snapshots
                WHERE market_ticker = ANY(%s)
                ORDER BY market_ticker, retrieved_at DESC
            )
            SELECT market_ticker, yes_mid, yes_bid FROM latest
        """, (tickers,))
        price_map = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

    for p in positions:
        prices = price_map.get(p.contract_ticker)
        if prices:
            mid, bid = prices
            p.current_yes_mid = float(mid) if mid is not None else None
            p.current_yes_bid = float(bid) if bid is not None else None
            # Normalize if somehow > 1
            if p.current_yes_mid and p.current_yes_mid > 1.0:
                p.current_yes_mid /= 100.0
            if p.current_yes_bid and p.current_yes_bid > 1.0:
                p.current_yes_bid /= 100.0


def _enrich_nws_forecasts(conn, positions: list[Position]) -> None:
    """Populate nws_forecast_now and nws_forecast_entry per position."""
    if not positions:
        return
    # Gather (station_code, target_date) pairs with open positions
    pairs = list({
        (p.station_code, p.target_date)
        for p in positions
        if p.station_code and p.target_date
    })
    if not pairs:
        return

    with conn.cursor() as cur:
        # Latest NWS forecast per (station, date)
        cur.execute("""
            WITH latest AS (
                SELECT DISTINCT ON (station_code, target_date)
                    station_code, target_date, tmax_f, forecast_run_time
                FROM weather_bronze_nws_forecast_snapshots
                WHERE (station_code, target_date) = ANY(%s::record[])
                ORDER BY station_code, target_date, forecast_run_time DESC
            )
            SELECT station_code, target_date, tmax_f FROM latest
        """, (pairs,))
        latest_map = {(r[0], r[1]): float(r[2]) for r in cur.fetchall() if r[2] is not None}

        # Earliest NWS forecast per (station, date) — proxy for "entry" forecast
        cur.execute("""
            WITH earliest AS (
                SELECT DISTINCT ON (station_code, target_date)
                    station_code, target_date, tmax_f
                FROM weather_bronze_nws_forecast_snapshots
                WHERE (station_code, target_date) = ANY(%s::record[])
                ORDER BY station_code, target_date, forecast_run_time ASC
            )
            SELECT station_code, target_date, tmax_f FROM earliest
        """, (pairs,))
        earliest_map = {(r[0], r[1]): float(r[2]) for r in cur.fetchall() if r[2] is not None}

    for p in positions:
        key = (p.station_code, p.target_date)
        p.nws_forecast_now   = latest_map.get(key)
        p.nws_forecast_entry = earliest_map.get(key)


# ─────────────────────────────────────────────────────────────────────────
# Stop-loss trigger evaluation
# ─────────────────────────────────────────────────────────────────────────

def _hours_to_settlement(target_date: Optional[date]) -> Optional[float]:
    """Rough hours until market settlement (midnight ET = 05:00 UTC next day)."""
    if target_date is None:
        return None
    settlement_utc = datetime(
        target_date.year, target_date.month, target_date.day,
        tzinfo=timezone.utc
    ) + timedelta(days=1, hours=5)   # midnight ET ≈ 05:00 UTC
    delta = settlement_utc - datetime.now(timezone.utc)
    return max(0.0, delta.total_seconds() / 3600.0)


def _effective_thresholds(p: Position) -> tuple[float, float]:
    """Return (prob_shift_threshold, dollar_loss_threshold) after applying
    tail-no relaxation and time-based tightening."""
    prob_thresh   = STOP_LOSS_PROB_SHIFT
    dollar_thresh = STOP_LOSS_DOLLAR

    # Tail No exception: relaxed thresholds for positions where entry P(YES) < threshold
    is_tail_no = (
        p.side == "no"
        and p.entry_implied_prob is not None
        and p.entry_implied_prob < STOP_LOSS_TAIL_NO_THRESH
    )
    if is_tail_no:
        prob_thresh   = 0.40
        dollar_thresh = 5.00

    # Time-based tightening (not applied to tail No until < 30 min)
    hours_left = _hours_to_settlement(p.target_date)
    if hours_left is not None and not (is_tail_no and hours_left > 0.5):
        if 2.0 <= hours_left <= 6.0:
            prob_thresh = min(prob_thresh, 0.15)
        elif 1.0 <= hours_left < 2.0:
            prob_thresh = min(prob_thresh, 0.10)
        elif hours_left < 1.0:
            # < 1 hour: no auto-exits — let ride to settlement
            prob_thresh = float("inf")

    return prob_thresh, dollar_thresh


def evaluate_triggers(p: Position) -> Optional[dict]:
    """Return a trigger dict if any stop-loss condition fires, else None.

    Returns dict with keys: trigger_type, prob_shift, dollar_loss,
    forecast_shift_f, notes (for DRY_RUN)."""
    if p.entry_implied_prob is None:
        return None

    prob_thresh, dollar_thresh = _effective_thresholds(p)

    # Trigger 4 < 1hr: no exits
    if prob_thresh == float("inf"):
        return None

    current_prob = p.current_yes_mid  # current P(YES)
    if current_prob is None:
        return None

    # Trigger 1: Implied probability shift
    if p.side == "yes":
        prob_shift = p.entry_implied_prob - current_prob   # positive = moved against YES holder
    else:
        prob_shift = current_prob - p.entry_implied_prob   # positive = moved against NO holder

    if prob_shift >= prob_thresh:
        return {
            "trigger_type":   "PROB_SHIFT",
            "prob_shift":     round(prob_shift, 4),
            "dollar_loss":    _dollar_loss(p),
            "forecast_shift_f": _forecast_shift(p),
        }

    # Trigger 2: Dollar loss
    dollar_loss = _dollar_loss(p)
    if dollar_loss is not None and dollar_loss >= dollar_thresh:
        return {
            "trigger_type":   "DOLLAR_LOSS",
            "prob_shift":     round(prob_shift, 4),
            "dollar_loss":    round(dollar_loss, 2),
            "forecast_shift_f": _forecast_shift(p),
        }

    # Trigger 3: Forecast hard exit
    fcast_shift = _forecast_shift(p)
    if fcast_shift is not None and fcast_shift >= STOP_LOSS_FORECAST_HARD_F:
        return {
            "trigger_type":   "FORECAST_HARD",
            "prob_shift":     round(prob_shift, 4),
            "dollar_loss":    dollar_loss,
            "forecast_shift_f": round(fcast_shift, 1),
        }

    # Trigger 3 soft: flag for review but don't exit
    if fcast_shift is not None and fcast_shift >= STOP_LOSS_FORECAST_SHIFT_F:
        logger.warning(
            f"{p.contract_ticker}: FORECAST soft alert — NWS shifted "
            f"{fcast_shift:.1f}°F (threshold={STOP_LOSS_FORECAST_SHIFT_F}°F). "
            f"Monitoring — no exit yet."
        )

    return None


def _dollar_loss(p: Position) -> Optional[float]:
    """Dollar loss for a position given current market price."""
    if p.avg_price is None or p.current_yes_mid is None or p.contracts <= 0:
        return None
    # Entry cost per contract vs current value per contract
    if p.side == "yes":
        entry_price_per   = p.avg_price
        current_price_per = p.current_yes_mid
    else:
        entry_price_per   = 1.0 - p.avg_price
        current_price_per = 1.0 - p.current_yes_mid
    # Each contract pays $1 at settlement; cost is price per contract
    loss = (entry_price_per - current_price_per) * p.contracts
    return round(loss, 4)


def _forecast_shift(p: Position) -> Optional[float]:
    if p.nws_forecast_now is None or p.nws_forecast_entry is None:
        return None
    return round(abs(p.nws_forecast_now - p.nws_forecast_entry), 1)


# ─────────────────────────────────────────────────────────────────────────
# Exit logging + order placement
# ─────────────────────────────────────────────────────────────────────────

def _log_exit(conn, p: Position, trigger: dict, dry_run: bool) -> None:
    """Write a row to weather_position_exits."""
    fcast_now   = p.nws_forecast_now
    fcast_entry = p.nws_forecast_entry

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO weather_position_exits (
                market_ticker, city, contract_side, bucket_floor, bucket_cap,
                trigger_type, entry_price, contracts,
                entry_implied_prob, exit_implied_prob, prob_shift,
                dollar_loss, forecast_at_entry, forecast_at_exit, forecast_shift_f,
                notes
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s
            )
        """, (
            p.contract_ticker,
            p.city or "UNKNOWN",
            p.side,
            p.bucket_floor,
            p.bucket_cap,
            trigger["trigger_type"],
            p.avg_price,
            p.contracts,
            p.entry_implied_prob,
            p.current_yes_mid,
            trigger.get("prob_shift"),
            trigger.get("dollar_loss"),
            fcast_entry,
            fcast_now,
            trigger.get("forecast_shift_f"),
            "DRY_RUN" if dry_run else None,
        ))


def _send_sms(message: str) -> bool:
    """Send Twilio SMS. Returns True on success."""
    sid       = os.environ.get("TWILIO_ACCOUNT_SID")
    token     = os.environ.get("TWILIO_AUTH_TOKEN")
    sender    = os.environ.get("TWILIO_FROM_NUMBER")
    recipient = os.environ.get("TWILIO_OPERATOR_NUMBER")
    if not all([sid, token, sender, recipient]):
        logger.warning("Twilio env vars missing — skipping SMS")
        return False
    try:
        resp = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            auth=(sid, token),
            data={"From": sender, "To": recipient, "Body": message[:1500]},
            timeout=10,
        )
        ok = resp.status_code in (200, 201)
        if not ok:
            logger.warning(f"SMS failed: {resp.status_code} {resp.text[:200]}")
        return ok
    except Exception as e:
        logger.warning(f"SMS exception: {e}")
        return False


def _place_exit_order(p: Position) -> Optional[str]:
    """Place a limit sell order. Returns order_id or None on failure.

    Tries limit at current_bid - 1c first. Does NOT wait for fill here —
    fill monitoring is handled by the next monitor cycle checking kalshi_positions.
    """
    try:
        from kalshi_client import KalshiClient
        client = KalshiClient()

        if p.current_yes_bid is None:
            logger.warning(f"{p.contract_ticker}: no current bid — cannot place limit order")
            return None

        # Limit price = current_bid - 1 cent. Floor at 1c.
        limit_price_fraction = max(0.01, p.current_yes_bid - 0.01)
        limit_price_cents    = max(1, round(limit_price_fraction * 100))

        order_result = client.place_order(
            ticker          = p.contract_ticker,
            side            = p.side,
            count           = p.contracts,
            price           = limit_price_cents,
            order_type      = "limit",
            action          = "sell",
            client_order_id = str(uuid.uuid4()),
            time_in_force   = "GTC",
        )
        order_id = (order_result.get("order") or {}).get("order_id")
        logger.info(
            f"{p.contract_ticker}: limit sell placed — "
            f"{p.contracts} {p.side} @ {limit_price_cents}c (order_id={order_id})"
        )
        return order_id

    except Exception as e:
        logger.warning(f"{p.contract_ticker}: place_exit_order failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────
# Main monitoring cycle
# ─────────────────────────────────────────────────────────────────────────

def run_cycle() -> int:
    """One monitoring cycle. Returns count of positions where stop-loss triggered."""
    if not STOP_LOSS_ENABLED:
        logger.debug("STOP_LOSS_ENABLED=false — skipping cycle")
        return 0

    triggered = 0

    with tc.get_pg_conn() as conn:
        positions = _load_open_positions(conn)

        if not positions:
            logger.debug("position-monitor: no open positions")
            return 0

        logger.info(f"position-monitor: evaluating {len(positions)} open positions")

        _enrich_market_prices(conn, positions)
        _enrich_nws_forecasts(conn, positions)

        for p in positions:
            trigger = evaluate_triggers(p)
            if trigger is None:
                continue

            triggered += 1
            log_msg = (
                f"STOP LOSS TRIGGERED [{trigger['trigger_type']}]: "
                f"{p.contract_ticker} | {p.side.upper()} x{p.contracts} | "
                f"entry_prob={p.entry_implied_prob:.2%} "
                f"current_prob={p.current_yes_mid:.2%} "
                f"shift={trigger.get('prob_shift', 0):.2%} "
                f"dollar_loss=${trigger.get('dollar_loss') or 0:.2f}"
            )

            if STOP_LOSS_DRY_RUN:
                logger.info(f"[DRY-RUN] {log_msg}")
                _log_exit(conn, p, trigger, dry_run=True)
                continue

            # Live mode
            logger.warning(log_msg)
            _log_exit(conn, p, trigger, dry_run=False)
            _send_sms(
                f"BHN STOP LOSS: {p.city or p.contract_ticker} "
                f"{p.side.upper()} x{p.contracts}\n"
                f"Trigger: {trigger['trigger_type']}\n"
                f"Shift: {trigger.get('prob_shift', 0):.1%} "
                f"Loss: ${trigger.get('dollar_loss') or 0:.2f}"
            )
            _place_exit_order(p)

    if triggered:
        logger.info(
            f"position-monitor: {triggered} stop-loss trigger(s) "
            f"{'[DRY-RUN]' if STOP_LOSS_DRY_RUN else '[LIVE]'}"
        )
    return triggered


def main() -> int:
    logger.info(
        f"=== position-monitor cycle start "
        f"(dry_run={STOP_LOSS_DRY_RUN}, "
        f"prob_shift={STOP_LOSS_PROB_SHIFT}, "
        f"dollar={STOP_LOSS_DOLLAR}) ==="
    )
    n = run_cycle()
    logger.info(f"=== position-monitor cycle end (triggered={n}) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
