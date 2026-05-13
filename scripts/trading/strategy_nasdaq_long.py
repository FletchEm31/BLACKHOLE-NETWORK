#!/usr/bin/env python3
"""
strategy_nasdaq_long.py — BHN Strategy 6: NASDAQ-LONG.

QC source: Two_2Algorithm. Logic identical; plumbing replaced (QC framework
calls → Alpaca + trading_core).

Mechanic (verbatim from QC):
  - Weekly Monday rebalance, 10 min after market open
  - Rank QLD/PSQ/QID by their intercept when regressing SQQQ returns
    against ETF returns over an 18-day window (see nasdaq_signal.py)
  - If QLD has the highest intercept → hold 99% QLD (single name)
  - Otherwise → hold 99% JPST (park in cash equivalent)
  - Always invested in one of the two; no FLAT state in steady operation

Operator-applied risk overrides (from the 'shared across all three' spec):
  - 5% trailing stop (overrides QC's 7.35%)
  - 13.25% profit target (matches QC)
  - 60-day max hold (operator spec; QC has no calendar limit)

Capital: $5,000 per rules.json strat_6_nasdaq_long block.
Account: dedicated Alpaca paper account (BHN-Paper-Strat6, credentials in
/etc/bhn-trading/strat6.env via STRAT6_ALPACA_KEY_ID + STRAT6_ALPACA_SECRET).

Cadence design (single cron-runnable script — operator can wire a weekly
Monday timer OR a daily one, the script self-detects):
  - Every run: risk check on open positions (trailing stop / target /
    max-hold) — closes if any fires + parks in JPST.
  - Monday runs (Time.weekday == 0): also recompute regression signal +
    rebalance to QLD or JPST per leader.
  - Non-Monday runs: skip signal recompute, just maintenance.

HORIZON SMS alerts fire on:
  - Every state transition (FLAT/IN_POSITION/PARKED)
  - Every stop / target / max-hold close (handled by nasdaq_risk.close_with_alert)
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import trading_core as tc
import nasdaq_signal as ns
import nasdaq_risk as nr
import nasdaq_state as nst


STRATEGY_ID = tc.StrategyId.NASDAQ_LONG.value
logger = tc.get_logger(STRATEGY_ID)

# Per QC: self.SetHoldings(symbol, 0.99). We mirror the 99% fraction.
TARGET_HOLDING_FRACTION = Decimal("0.99")

# Risk tickers used by derive_state — QLD is the only risk position for this strategy.
RISK_TICKERS = ("QLD",)


def _get_live_price(ticker: str) -> Optional[Decimal]:
    """Latest-trade price via Alpaca. None on failure."""
    try:
        bar = tc.get_alpaca().get_latest_trade(ticker)
        return Decimal(str(bar.price))
    except Exception as e:
        logger.warning(f"live price unavailable for {ticker}: {e}")
        return None


def _is_signal_day() -> bool:
    """Mondays only (matches QC's WeekStart(SPY) schedule)."""
    # weekday(): Mon=0, Sun=6. UTC date is fine — Monday-in-NY is also Monday-in-UTC.
    return datetime.now(timezone.utc).weekday() == 0


def _risk_check_open_trades() -> tuple[int, int]:
    """Evaluate trailing stops / profit targets / max-hold on every open trade.
    Returns (n_evaluated, n_closed)."""
    open_trades = tc.get_open_trades(STRATEGY_ID)
    if not open_trades:
        return 0, 0
    closed = 0
    for trade in open_trades:
        price = _get_live_price(trade["ticker"])
        if price is None:
            continue
        result = nr.evaluate_trade(
            trade=trade,
            current_price=price,
            profit_target_pct=nr.PROFIT_TARGET_LONG_PCT,
        )
        logger.info(f"risk-check {trade['ticker']} {trade['side']}: {result.detail}")
        if result.action == "hold":
            continue
        # Place the actual close order
        alpaca = tc.get_alpaca()
        try:
            order = alpaca.submit_order(
                symbol=trade["ticker"], qty=int(trade["qty"]),
                side="sell" if trade["side"] == "buy" else "buy",
                type="market", time_in_force="day",
            )
            fill = Decimal(str(order.filled_avg_price or price))
            nr.close_with_alert(
                trade=trade, exit_price=fill, result=result,
                strategy_id=STRATEGY_ID, alpaca_order_id=order.id,
            )
            closed += 1
        except Exception as e:
            logger.error(f"failed to close {trade['ticker']} on {result.action}: {e}")
    return len(open_trades), closed


def _liquidate_all() -> None:
    """Close every open paper_trade for this strategy with reason='manual' (rebalance).
    Used before transitioning between QLD and JPST."""
    open_trades = tc.get_open_trades(STRATEGY_ID)
    alpaca = tc.get_alpaca()
    for trade in open_trades:
        price = _get_live_price(trade["ticker"]) or Decimal(str(trade["entry_price"]))
        try:
            order = alpaca.submit_order(
                symbol=trade["ticker"], qty=int(trade["qty"]),
                side="sell" if trade["side"] == "buy" else "buy",
                type="market", time_in_force="day",
            )
            fill = Decimal(str(order.filled_avg_price or price))
            tc.close_trade(
                trade_id=trade["id"], exit_price=fill,
                exit_reason=tc.ExitReason.MANUAL,
                alpaca_order_id_exit=order.id,
            )
            logger.info(f"liquidated {trade['ticker']} qty={trade['qty']} @ {fill}")
        except Exception as e:
            logger.error(f"liquidate failed for {trade['ticker']}: {e}")


def _open_position(ticker: str, allocation: Decimal, signal: ns.NasdaqSignal,
                    state_before: nst.NasdaqState) -> Optional[int]:
    """Place a market order for `ticker` sized to fill TARGET_HOLDING_FRACTION
    of the strategy's allocation. Returns the new trade_id or None on failure."""
    price = _get_live_price(ticker)
    if price is None:
        logger.error(f"cannot open {ticker}: no live price")
        return None
    target_dollars = allocation * TARGET_HOLDING_FRACTION
    qty = int(target_dollars / price)
    if qty < 1:
        logger.warning(f"skip {ticker}: position size <1 share at ${price}")
        return None

    # Log the audit signal first so trade row links back to it
    sig_metadata = {
        "leader":           signal.leader,
        "leader_score":     signal.leader_score,
        "runner_up":        signal.runner_up,
        "runner_up_score":  signal.runner_up_score,
        "rankings":         [{"ticker": r.ticker, "intercept": r.intercept,
                              "slope": r.slope, "n": r.n_observations}
                              for r in signal.rankings],
        "spy_close":        signal.spy_close,
        "spy_ma_200":       signal.spy_ma_200,
        "spy_above_ma":     signal.spy_above_ma,
        "state_before":     state_before.value,
        "reason":           signal.reason,
    }
    signal_id = tc.log_signal(
        STRATEGY_ID, ticker, tc.Action.BUY,
        reason=signal.reason, value=signal.leader_score,
        acted_on=True, raw_payload=sig_metadata,
    )

    try:
        order = tc.place_order(
            strategy_id=STRATEGY_ID, ticker=ticker, side=tc.Action.BUY,
            qty=qty, order_type="market", signal_id=signal_id,
            metadata={
                **sig_metadata,
                "water_mark":   str(price),  # initial HWM = entry price
                "entry_state":  state_before.value,
                "target_holding_fraction": str(TARGET_HOLDING_FRACTION),
            },
        )
        logger.info(f"OPEN {ticker} {qty}@${price} signal_id={signal_id} "
                    f"order={order['alpaca_order_id']}")
        return order["trade_id"]
    except Exception as e:
        logger.error(f"place_order refused for {ticker}: {e}")
        with tc.get_pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE signals_log SET acted_on=false WHERE id=%s",
                            (signal_id,))
        return None


def _do_signal_cycle(allocation: Decimal) -> None:
    """Monday rebalance: compute signal, transition state if needed."""
    state_before, _ = nst.derive_state(STRATEGY_ID, RISK_TICKERS)
    signal = ns.evaluate_long_signal()
    target = signal.target_ticker  # 'QLD' or 'JPST' per QC

    logger.info(f"signal cycle: state={state_before.value}, "
                f"target={target}, reason={signal.reason}")

    # What ticker (if any) are we currently holding?
    open_trades = tc.get_open_trades(STRATEGY_ID)
    current_ticker = open_trades[0]["ticker"] if open_trades else None

    if current_ticker == target:
        logger.info(f"already holding {target} — no rebalance needed")
        return

    # Transitioning. Liquidate first (always pass through cash position),
    # then open new.
    if open_trades:
        logger.info(f"transitioning {current_ticker} → {target}; liquidating")
        _liquidate_all()

    # Open the new position
    new_trade_id = _open_position(target, allocation, signal, state_before)
    if new_trade_id is None:
        logger.error(f"failed to open {target} — strategy will retry next cycle")
        return

    state_after = (nst.NasdaqState.IN_POSITION if target == "QLD"
                   else nst.NasdaqState.PARKED)
    nst.announce_transition(
        strategy_id=STRATEGY_ID, old=state_before, new=state_after,
        reason=signal.reason,
    )


def main() -> int:
    allowed, reason = tc.should_run(STRATEGY_ID)
    if not allowed:
        logger.info(f"Skipping run: {reason}")
        return 0

    allocation = tc.get_rls_capital_allocation(STRATEGY_ID)
    if allocation <= 0:
        logger.warning(f"Allocation is ${allocation} — refusing to run")
        return 1

    logger.info(f"=== {STRATEGY_ID} cycle start (allocation=${allocation}) ===")

    with tc.pg_advisory_lock(abs(hash(STRATEGY_ID)) % (2**31)):
        try:
            with tc.get_pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE trading_strategies SET last_run_at = NOW() WHERE id = %s",
                        (STRATEGY_ID,),
                    )

            # Always: risk-check existing positions
            n_eval, n_closed = _risk_check_open_trades()
            logger.info(f"risk check: {n_eval} evaluated, {n_closed} closed")

            # If any positions closed (stop/target/max-hold), park in JPST.
            # QC's OnData hook does this; we trigger explicitly after closes.
            if n_closed > 0:
                state_now, _ = nst.derive_state(STRATEGY_ID, RISK_TICKERS)
                if state_now == nst.NasdaqState.FLAT:
                    # Park immediately (QC behavior — "always invested")
                    park_signal = ns.NasdaqSignal(
                        fires=True, direction="park", target_ticker="JPST",
                        leader=None, leader_score=None,
                        runner_up=None, runner_up_score=None,
                        rankings=[],
                        reason="post-exit auto-park in JPST",
                    )
                    _open_position("JPST", allocation, park_signal, state_now)
                    nst.announce_transition(
                        strategy_id=STRATEGY_ID,
                        old=state_now, new=nst.NasdaqState.PARKED,
                        reason="post-exit auto-park",
                    )

            # Monday: signal recompute + rebalance
            if _is_signal_day():
                _do_signal_cycle(allocation)
            else:
                logger.info("non-Monday cycle — maintenance-only, no signal recompute")

        except Exception:
            logger.exception(f"{STRATEGY_ID} cycle failed")
            tc.update_strategy_status(STRATEGY_ID, "error",
                                      "exception in main loop (see log)")
            return 1

    logger.info(f"=== {STRATEGY_ID} cycle end ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
