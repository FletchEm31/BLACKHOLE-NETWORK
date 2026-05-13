#!/usr/bin/env python3
"""
nasdaq_risk.py — Shared risk management for Strats 6 (NASDAQ-LONG) + 7 (NASDAQ-SHORT).

Replaces QC's CompositeRiskManagementModel (MaximumUnrealizedProfitPercentPerSecurity
+ TrailingStopRiskManagementModel) with explicit per-trade monitoring against
Alpaca live prices.

Operator-shared risk parameters:
  - 5% TRAILING stop loss
    * longs: track highest close since entry (HWM); stop fires when
             current ≤ HWM × (1 - 5%)
    * shorts: track LOWEST close since entry (LWM); stop fires when
              current ≥ LWM × (1 + 5%)
  - Profit target: 13.25% long, 15% short
  - Max hold: 60 days (hard limit, both directions)

High-water / low-water marks live in paper_trades.metadata['water_mark'] and
get updated each risk-check cycle. This makes the trailing logic auditable
(every cycle's HWM/LWM is in PG) and stateless across process restarts —
no separate state file needed.

HORIZON SMS hooks fire on stop, target, or max-hold via tc._send_alert.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import trading_core as tc


STOP_LOSS_PCT = Decimal("0.05")             # 5% trailing
PROFIT_TARGET_LONG_PCT = Decimal("0.1325")  # 13.25%
PROFIT_TARGET_SHORT_PCT = Decimal("0.15")   # 15%
MAX_HOLD_DAYS = 60                          # hard limit per operator spec


@dataclass
class RiskCheckResult:
    trade_id: int
    ticker: str
    side: str
    entry_price: Decimal
    current_price: Decimal
    water_mark: Decimal           # current HWM (long) or LWM (short)
    stop_level: Decimal           # current trailing stop trigger
    pnl_pct: Decimal              # vs entry
    action: str                   # "hold" | "stop_loss" | "profit_target" | "max_hold"
    detail: str


def _alert_horizon(severity: str, message: str) -> None:
    try:
        tc._send_alert(severity=severity, message=message)
    except Exception:
        pass


def _get_water_mark(trade: dict) -> Decimal:
    """Read paper_trades.metadata['water_mark']. Initialize to entry_price if absent."""
    meta = trade.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    wm = meta.get("water_mark")
    if wm is not None:
        return Decimal(str(wm))
    return Decimal(str(trade["entry_price"]))


def _persist_water_mark(trade_id: int, water_mark: Decimal) -> None:
    """UPSERT paper_trades.metadata['water_mark']. Uses jsonb_set so we don't
    clobber other metadata fields (entry_pe, regression_scores, etc)."""
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE paper_trades
                SET metadata = jsonb_set(
                    COALESCE(metadata, '{}'::jsonb),
                    '{water_mark}',
                    to_jsonb(%s::numeric)
                )
                WHERE id = %s
                """,
                (str(water_mark), trade_id),
            )


def evaluate_trade(trade: dict, current_price: Decimal,
                    profit_target_pct: Decimal,
                    persist_water_mark: bool = True) -> RiskCheckResult:
    """Evaluate one open trade. Updates the water mark in PG if the price moved
    favorably. Returns the action to take; caller is responsible for placing
    the actual close order."""
    entry_price = Decimal(str(trade["entry_price"]))
    side = trade["side"]
    ticker = trade["ticker"]
    trade_id = trade["id"]
    entry_time = trade["entry_time"]
    if entry_time.tzinfo is None:
        entry_time = entry_time.replace(tzinfo=timezone.utc)

    wm = _get_water_mark(trade)

    # Update water mark if price moved further in our favor
    if side == "buy":
        if current_price > wm:
            wm = current_price
            if persist_water_mark:
                _persist_water_mark(trade_id, wm)
        stop_level = wm * (Decimal("1") - STOP_LOSS_PCT)
        pnl_pct = (current_price - entry_price) / entry_price
    elif side == "sell":
        if current_price < wm:
            wm = current_price
            if persist_water_mark:
                _persist_water_mark(trade_id, wm)
        stop_level = wm * (Decimal("1") + STOP_LOSS_PCT)
        pnl_pct = (entry_price - current_price) / entry_price
    else:
        return RiskCheckResult(
            trade_id=trade_id, ticker=ticker, side=side,
            entry_price=entry_price, current_price=current_price,
            water_mark=wm, stop_level=Decimal("0"),
            pnl_pct=Decimal("0"), action="hold",
            detail=f"unknown side '{side}'",
        )

    # Max hold check
    age_days = (datetime.now(timezone.utc) - entry_time).days
    if age_days >= MAX_HOLD_DAYS:
        return RiskCheckResult(
            trade_id=trade_id, ticker=ticker, side=side,
            entry_price=entry_price, current_price=current_price,
            water_mark=wm, stop_level=stop_level, pnl_pct=pnl_pct,
            action="max_hold",
            detail=(f"{ticker} {side} held {age_days}d ≥ {MAX_HOLD_DAYS}d max — "
                    f"closing at pnl {pnl_pct:+.2%}"),
        )

    # Trailing stop check
    if side == "buy" and current_price <= stop_level:
        return RiskCheckResult(
            trade_id=trade_id, ticker=ticker, side=side,
            entry_price=entry_price, current_price=current_price,
            water_mark=wm, stop_level=stop_level, pnl_pct=pnl_pct,
            action="stop_loss",
            detail=(f"{ticker} long trailing stop: current {current_price} ≤ "
                    f"stop {stop_level:.4f} (HWM {wm} × {1-STOP_LOSS_PCT:.2f}), "
                    f"pnl {pnl_pct:+.2%}"),
        )
    if side == "sell" and current_price >= stop_level:
        return RiskCheckResult(
            trade_id=trade_id, ticker=ticker, side=side,
            entry_price=entry_price, current_price=current_price,
            water_mark=wm, stop_level=stop_level, pnl_pct=pnl_pct,
            action="stop_loss",
            detail=(f"{ticker} short trailing stop: current {current_price} ≥ "
                    f"stop {stop_level:.4f} (LWM {wm} × {1+STOP_LOSS_PCT:.2f}), "
                    f"pnl {pnl_pct:+.2%}"),
        )

    # Profit target check
    if pnl_pct >= profit_target_pct:
        return RiskCheckResult(
            trade_id=trade_id, ticker=ticker, side=side,
            entry_price=entry_price, current_price=current_price,
            water_mark=wm, stop_level=stop_level, pnl_pct=pnl_pct,
            action="profit_target",
            detail=(f"{ticker} {side} target hit: pnl {pnl_pct:+.2%} ≥ "
                    f"+{profit_target_pct:.2%}"),
        )

    return RiskCheckResult(
        trade_id=trade_id, ticker=ticker, side=side,
        entry_price=entry_price, current_price=current_price,
        water_mark=wm, stop_level=stop_level, pnl_pct=pnl_pct,
        action="hold",
        detail=(f"{ticker} pnl {pnl_pct:+.2%}, HWM/LWM {wm}, stop {stop_level:.4f}"),
    )


def close_with_alert(trade: dict, exit_price: Decimal, result: RiskCheckResult,
                      strategy_id: str, alpaca_order_id: Optional[str] = None) -> dict:
    """Close the trade in PG + fire a HORIZON SMS describing the trigger."""
    if result.action == "stop_loss":
        exit_reason = tc.ExitReason.TRAILING_STOP
        severity = "warning"
    elif result.action == "profit_target":
        exit_reason = tc.ExitReason.TARGET
        severity = "info"
    elif result.action == "max_hold":
        exit_reason = tc.ExitReason.TIME_EXIT
        severity = "info"
    else:
        exit_reason = tc.ExitReason.MANUAL
        severity = "info"

    closed = tc.close_trade(
        trade_id=trade["id"],
        exit_price=exit_price,
        exit_reason=exit_reason,
        alpaca_order_id_exit=alpaca_order_id,
    )
    _alert_horizon(
        severity=severity,
        message=(f"BHN {strategy_id}: {result.detail}. "
                 f"P&L ${closed['pnl_dollar']} ({closed['pnl_pct']:.2f}%) "
                 f"over {closed['duration_seconds']//3600}h"),
    )
    return closed
