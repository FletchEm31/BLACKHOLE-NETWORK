#!/usr/bin/env python3
"""
nasdaq_state.py — State machine for Strats 6 (NASDAQ-LONG) + 7 (NASDAQ-SHORT).

Three states (operator-referenced as "States 1, 2, 3"; mapped here to named
constants for code clarity):

  FLAT     (1) — no open positions for this strategy
  IN_POSITION (2) — strategy currently holds the risk position
                   (QLD for Strat 6; short QQQ/SPY for Strat 7)
  PARKED   (3) — strategy holds JPST instead of the risk position
                 (Strat 6 only — when leader != QLD, QC code parks 99% in JPST)

Transitions fire HORIZON SMS alerts (per operator spec: "SMS alert on every
state change") via trading_core's _send_alert webhook.

State is derived from `paper_trades` table at evaluation time — no separate
state table. The strategy's open trades, filtered by ticker, determine the
state. This keeps PG as the single source of truth.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

import trading_core as tc


class NasdaqState(str, Enum):
    FLAT = "flat"
    IN_POSITION = "in_position"
    PARKED = "parked"


PARK_TICKER = "JPST"


def derive_state(strategy_id: str, risk_tickers: tuple[str, ...]) -> tuple[NasdaqState, list[dict]]:
    """Look at open paper_trades for `strategy_id`. Returns (state, open_trades_list).
    risk_tickers = the non-park tickers a position in this strategy uses.
    For Strat 6: ('QLD',). For Strat 7: ('QQQ', 'SPY') — both sides of the short."""
    open_trades = tc.get_open_trades(strategy_id)
    if not open_trades:
        return NasdaqState.FLAT, []

    has_risk = any(t["ticker"] in risk_tickers for t in open_trades)
    has_park = any(t["ticker"] == PARK_TICKER for t in open_trades)

    if has_risk:
        return NasdaqState.IN_POSITION, open_trades
    if has_park:
        return NasdaqState.PARKED, open_trades
    # Unknown ticker open — treat as IN_POSITION conservatively; the reconciliation
    # daemon will flag the mismatch separately
    return NasdaqState.IN_POSITION, open_trades


def _alert_horizon(severity: str, message: str) -> None:
    try:
        tc._send_alert(severity=severity, message=message)
    except Exception:
        pass


def announce_transition(strategy_id: str, old: NasdaqState, new: NasdaqState,
                          reason: str) -> None:
    """Emit a HORIZON SMS for a state change. No-op if old == new."""
    if old == new:
        return
    _alert_horizon(
        severity="info",
        message=f"BHN {strategy_id} state {old.value} → {new.value}: {reason}",
    )
