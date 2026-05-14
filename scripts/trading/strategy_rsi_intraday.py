#!/usr/bin/env python3
"""
strategy_rsi_intraday.py — BHN Strategy 13: BHN-RSI-INTRADAY.

Simple RSI-14 mean reversion on QQQ. Primary purpose: pipeline testing —
generate real paper_trades / signals_log / strategy_performance volume
quickly so HORIZON and downstream consumers exercise full end-to-end flow.

Signal (read from LA's market_daily.rsi_14, populated by horizon
market_data_collector at 16:30 ET daily):
  RSI < 30  →  buy QQQ at TARGET_HOLDING_FRACTION of allocation
  RSI > 70  →  sell QQQ, park 100% in JPST
  30 <= RSI <= 70 → hold current position, no transition

Risk (applies to QQQ position only; JPST parking is idle):
  3% trailing stop
  8% profit target
  5-day max hold (calendar days; exits to JPST regardless of signal)

Cadence: every 30 minutes during US market hours (09:30-16:00 ET, Mon-Fri).
The daily-bar RSI doesn't change intraday — the 30-min cycles primarily
serve risk-management (stop / target / max-hold). Signal flips happen at
most once per day (after the 16:30 ET market_data_collector run on LA
refreshes the rsi_14 value for QQQ).

Account: PA37PRN150AG (BHN-STRAT-SIGNALS, shared with strat_4 momentum).
Credentials in /etc/bhn-trading/strat13.env via STRAT13_ALPACA_KEY_ID +
STRAT13_ALPACA_SECRET. The rules.json[strat_13_rsi_intraday].broker
subblock points at those env var names.

Capital: $12,500 (50% of Account 3). Strat 4 holds the other 50%.

Reconciliation note: strat_4 trades a different universe (top-S&P-by-vol
SMA crossover) so position collision in the shared account is unlikely.
The reconcile_state() math sums qty per (ticker, account); divergent
universes produce no false-positive mismatches.

HORIZON SMS alerts fire on:
  - Every state transition (FLAT → IN_QQQ → PARKED_JPST and back)
  - Every stop / target / max-hold close (via the standard reason field
    in paper_trades.exit_reason).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import trading_core as tc


STRATEGY_ID = tc.StrategyId.RSI_INTRADAY.value
logger = tc.get_logger(STRATEGY_ID)

TARGET_HOLDING_FRACTION = Decimal("0.99")   # leave 1% cash for slippage

# Signal thresholds (operator spec)
RSI_BUY_THRESHOLD  = Decimal("30")
RSI_SELL_THRESHOLD = Decimal("70")

# Risk parameters (operator spec)
TRAILING_STOP_PCT = Decimal("3.0")
PROFIT_TARGET_PCT = Decimal("8.0")
MAX_HOLD_DAYS     = 5

TICKER       = "QQQ"
PARK_TICKER  = "JPST"


# ─────────────────────────────────────────────────────────────────────────
# Live price helper
# ─────────────────────────────────────────────────────────────────────────

def _get_live_price(ticker: str) -> Optional[Decimal]:
    try:
        bar = tc.get_strategy_alpaca(STRATEGY_ID).get_latest_trade(ticker)
        return Decimal(str(bar.price))
    except Exception as e:
        logger.warning(f"live price unavailable for {ticker}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────
# RSI source — read from LA's market_daily (refreshed at 16:30 ET)
# ─────────────────────────────────────────────────────────────────────────

def _get_latest_rsi(ticker: str = TICKER) -> Optional[tuple[Decimal, datetime.date]]:
    """Read the most recent rsi_14 + date for `ticker` from market_daily.
    Returns (rsi, date) or None if the column hasn't been populated yet
    (e.g. fresh deploy before market_data_collector first run).

    market_daily lives on LA's PG; this query runs against the
    eventhorizon DB via trading_core's shared connection pool (PG_HOST is
    set to 10.8.0.1 on NJ deployments)."""
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, rsi_14
                FROM market_daily
                WHERE ticker = %s AND rsi_14 IS NOT NULL
                ORDER BY date DESC
                LIMIT 1
            """, (ticker,))
            row = cur.fetchone()
            if not row:
                return None
            return Decimal(str(row[1])), row[0]


# ─────────────────────────────────────────────────────────────────────────
# Risk evaluation — trailing stop / profit target / max hold
# ─────────────────────────────────────────────────────────────────────────

def _evaluate_exit(trade: dict, current_price: Decimal) -> tuple[str, str]:
    """Returns (action, reason). action ∈ {hold, trailing_stop, target, time_exit}.

    Only QQQ positions are subject to exit logic. JPST parking is idle —
    we hold it until the next RSI<30 buy signal lifts us back to QQQ.
    """
    if trade["ticker"] != TICKER:
        return "hold", "park position; no risk gate"

    entry_price = Decimal(str(trade["entry_price"]))
    if entry_price <= 0:
        return "hold", "invalid entry_price"

    # Profit target
    pct_gain = (current_price - entry_price) / entry_price * Decimal("100")
    if pct_gain >= PROFIT_TARGET_PCT:
        return "target", f"profit_target hit: +{pct_gain:.2f}% >= {PROFIT_TARGET_PCT}%"

    # Trailing stop: track high-water mark from metadata; fall back to entry
    # if not present (first cycle after open). The stop is tracking the
    # high — we exit if we drop TRAILING_STOP_PCT below it.
    metadata = trade.get("metadata") or {}
    if isinstance(metadata, str):
        # JSONB returned as raw string in some drivers
        try:
            import json
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}
    water_mark = Decimal(str(metadata.get("water_mark", entry_price)))
    if current_price > water_mark:
        # Caller updates water_mark via metadata write; we recompute here in case
        water_mark = current_price
    drawdown_pct = (water_mark - current_price) / water_mark * Decimal("100")
    if drawdown_pct >= TRAILING_STOP_PCT:
        return "trailing_stop", (f"drawdown {drawdown_pct:.2f}% >= "
                                  f"{TRAILING_STOP_PCT}% from HWM ${water_mark}")

    # Max hold (calendar days since entry_time)
    entry_time = trade["entry_time"]
    if isinstance(entry_time, datetime):
        days_held = (datetime.now(timezone.utc) - entry_time).days
    else:
        # Some PG drivers hand back naive datetime
        try:
            days_held = (datetime.now(timezone.utc).replace(tzinfo=None) - entry_time).days
        except Exception:
            days_held = 0
    if days_held >= MAX_HOLD_DAYS:
        return "time_exit", f"held {days_held}d >= {MAX_HOLD_DAYS}d max"

    return "hold", "no exit trigger"


def _update_water_mark(trade_id: int, new_high: Decimal) -> None:
    """Persist a fresh high-water mark into paper_trades.metadata for the
    next cycle's trailing-stop reference."""
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE paper_trades
                SET metadata = COALESCE(metadata, '{}'::jsonb) ||
                                jsonb_build_object('water_mark', %s::text)
                WHERE id = %s
            """, (str(new_high), trade_id))


def _close_trade(trade: dict, exit_price: Decimal, exit_reason: tc.ExitReason,
                  alpaca_order_id: Optional[str]) -> dict:
    """Wrapper around tc.close_trade — keeps logging consistent with other
    strategies."""
    result = tc.close_trade(
        trade_id=trade["id"], exit_price=exit_price, exit_reason=exit_reason,
        alpaca_order_id_exit=alpaca_order_id,
    )
    logger.info(f"CLOSED {trade['ticker']} qty={trade['qty']} entry=${trade['entry_price']} "
                f"exit=${exit_price} reason={exit_reason.value} "
                f"pnl_pct={result['pnl_pct']:.2f}% pnl_$={result['pnl_dollar']:.2f}")
    return result


def _risk_check_open_trades() -> tuple[int, int]:
    """Trailing stop / profit target / max-hold on every open trade.
    Updates water_mark on the way for any QQQ trade still above its prior HWM.
    Returns (n_evaluated, n_closed)."""
    open_trades = tc.get_open_trades(STRATEGY_ID)
    if not open_trades:
        return 0, 0

    alpaca = tc.get_strategy_alpaca(STRATEGY_ID)
    closed = 0
    for trade in open_trades:
        price = _get_live_price(trade["ticker"])
        if price is None:
            continue

        # Update HWM first if we're at a new high
        if trade["ticker"] == TICKER:
            metadata = trade.get("metadata") or {}
            if isinstance(metadata, str):
                import json
                try: metadata = json.loads(metadata)
                except Exception: metadata = {}
            prior_hwm = Decimal(str(metadata.get("water_mark", trade["entry_price"])))
            if price > prior_hwm:
                _update_water_mark(trade["id"], price)

        action, reason = _evaluate_exit(trade, price)
        logger.info(f"risk-check {trade['ticker']} @ ${price}: {action} — {reason}")
        if action == "hold":
            continue

        exit_reason_map = {
            "trailing_stop": tc.ExitReason.TRAILING_STOP,
            "target":        tc.ExitReason.TARGET,
            "time_exit":     tc.ExitReason.TIME_EXIT,
        }
        try:
            order = alpaca.submit_order(
                symbol=trade["ticker"], qty=int(trade["qty"]),
                side="sell" if trade["side"] == "buy" else "buy",
                type="market", time_in_force="day",
            )
            fill = Decimal(str(order.filled_avg_price or price))
            _close_trade(trade, exit_price=fill,
                          exit_reason=exit_reason_map[action],
                          alpaca_order_id=order.id)
            closed += 1
        except Exception as e:
            logger.error(f"failed to close {trade['ticker']} on {action}: {e}")

    return len(open_trades), closed


# ─────────────────────────────────────────────────────────────────────────
# Order placement helpers
# ─────────────────────────────────────────────────────────────────────────

def _liquidate_all(reason: tc.ExitReason = tc.ExitReason.MANUAL) -> None:
    """Close every open paper_trade for this strategy — used before a state
    transition (e.g. JPST → QQQ flip on RSI signal)."""
    open_trades = tc.get_open_trades(STRATEGY_ID)
    if not open_trades:
        return
    alpaca = tc.get_strategy_alpaca(STRATEGY_ID)
    for trade in open_trades:
        price = _get_live_price(trade["ticker"]) or Decimal(str(trade["entry_price"]))
        try:
            order = alpaca.submit_order(
                symbol=trade["ticker"], qty=int(trade["qty"]),
                side="sell" if trade["side"] == "buy" else "buy",
                type="market", time_in_force="day",
            )
            fill = Decimal(str(order.filled_avg_price or price))
            _close_trade(trade, exit_price=fill, exit_reason=reason,
                          alpaca_order_id=order.id)
        except Exception as e:
            logger.error(f"liquidate failed for {trade['ticker']}: {e}")


def _open_position(ticker: str, allocation: Decimal, signal_metadata: dict,
                    action: tc.Action = tc.Action.BUY) -> Optional[int]:
    """Open a market order sized to TARGET_HOLDING_FRACTION of allocation."""
    price = _get_live_price(ticker)
    if price is None:
        logger.error(f"cannot open {ticker}: no live price")
        return None

    target_dollars = allocation * TARGET_HOLDING_FRACTION
    qty = int(target_dollars / price)
    if qty < 1:
        logger.warning(f"skip {ticker}: position size <1 share at ${price}")
        return None

    signal_id = tc.log_signal(
        STRATEGY_ID, ticker, action,
        reason=signal_metadata.get("reason", ""),
        value=signal_metadata.get("rsi"),
        acted_on=True, raw_payload=signal_metadata,
    )
    try:
        order = tc.place_order(
            strategy_id=STRATEGY_ID, ticker=ticker, side=action,
            qty=qty, order_type="market", signal_id=signal_id,
            metadata={
                **signal_metadata,
                "water_mark": str(price),
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


# ─────────────────────────────────────────────────────────────────────────
# Signal cycle — RSI threshold check + state transition
# ─────────────────────────────────────────────────────────────────────────

def _current_state() -> str:
    """Return 'IN_QQQ', 'PARKED_JPST', or 'FLAT' based on open trades."""
    open_trades = tc.get_open_trades(STRATEGY_ID)
    if not open_trades:
        return "FLAT"
    held = {t["ticker"] for t in open_trades}
    if TICKER in held:
        return "IN_QQQ"
    if PARK_TICKER in held:
        return "PARKED_JPST"
    # Unexpected ticker — return FLAT so the signal cycle re-evaluates from
    # a clean state. Reconciliation will surface the orphan position.
    logger.warning(f"open trades hold unexpected ticker(s) {held}; treating as FLAT")
    return "FLAT"


def _do_signal_cycle(allocation: Decimal) -> None:
    """Read RSI and act:
        FLAT          + RSI<30 → buy QQQ
        FLAT          + RSI>70 → park JPST (defensive — unusual at deploy time)
        FLAT          + 30..70 → no-op (wait for clearer signal)
        IN_QQQ        + RSI>70 → liquidate QQQ, park JPST
        IN_QQQ        + RSI<30 → already in target; no-op
        PARKED_JPST   + RSI<30 → liquidate JPST, buy QQQ
        PARKED_JPST   + RSI>70 → already in target; no-op
        any           + 30..70 → hold current position
    """
    rsi_pair = _get_latest_rsi(TICKER)
    if rsi_pair is None:
        logger.warning(f"no RSI available for {TICKER} in market_daily — skipping cycle. "
                       f"Run scripts/horizon/market_data_collector.py on LA if not yet populated.")
        return

    rsi, rsi_date = rsi_pair
    state = _current_state()
    logger.info(f"signal cycle: state={state} RSI({TICKER},{rsi_date})={rsi:.2f}")

    sig_metadata = {
        "rsi":               float(rsi),
        "rsi_date":          rsi_date.isoformat(),
        "buy_threshold":     float(RSI_BUY_THRESHOLD),
        "sell_threshold":    float(RSI_SELL_THRESHOLD),
        "state_before":      state,
    }

    if rsi < RSI_BUY_THRESHOLD:
        # Want to be in QQQ
        if state == "IN_QQQ":
            logger.info(f"RSI {rsi:.2f} below {RSI_BUY_THRESHOLD} and already in QQQ — hold")
            return
        if state == "PARKED_JPST":
            logger.info(f"RSI {rsi:.2f} crossed below {RSI_BUY_THRESHOLD} — flipping JPST → QQQ")
            _liquidate_all(reason=tc.ExitReason.MANUAL)
        sig_metadata["reason"] = f"RSI {rsi:.2f} < {RSI_BUY_THRESHOLD}"
        _open_position(TICKER, allocation, sig_metadata, action=tc.Action.BUY)
        return

    if rsi > RSI_SELL_THRESHOLD:
        # Want to be parked
        if state == "PARKED_JPST":
            logger.info(f"RSI {rsi:.2f} above {RSI_SELL_THRESHOLD} and already parked — hold")
            return
        if state == "IN_QQQ":
            logger.info(f"RSI {rsi:.2f} crossed above {RSI_SELL_THRESHOLD} — flipping QQQ → JPST")
            _liquidate_all(reason=tc.ExitReason.MANUAL)
        sig_metadata["reason"] = f"RSI {rsi:.2f} > {RSI_SELL_THRESHOLD}"
        _open_position(PARK_TICKER, allocation, sig_metadata, action=tc.Action.BUY)
        return

    logger.info(f"RSI {rsi:.2f} in neutral zone [{RSI_BUY_THRESHOLD}..{RSI_SELL_THRESHOLD}] — hold")


# ─────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────

def main() -> int:
    allowed, reason = tc.should_run(STRATEGY_ID, requires_market_open=False)
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

            # 1. Risk check on open trades (trailing stop / target / max-hold)
            n_eval, n_closed = _risk_check_open_trades()
            logger.info(f"risk check: {n_eval} evaluated, {n_closed} closed")

            # 2. If risk close left us FLAT, auto-park in JPST so capital is
            #    always somewhere yielding (matches the broader BHN "no
            #    idle cash" preference) — except don't open JPST if we're
            #    currently above the sell threshold and JPST is the target
            #    anyway (signal cycle below handles that).
            if n_closed > 0:
                state_now = _current_state()
                if state_now == "FLAT":
                    logger.info("post-exit auto-park into JPST")
                    _open_position(
                        PARK_TICKER, allocation,
                        {"reason": "post-exit auto-park",
                         "state_before": "FLAT",
                         "rsi": None},
                        action=tc.Action.BUY,
                    )

            # 3. Signal cycle — RSI-driven state transitions
            _do_signal_cycle(allocation)

        except Exception:
            logger.exception(f"{STRATEGY_ID} cycle failed")
            tc.update_strategy_status(STRATEGY_ID, "error",
                                       "exception in main loop (see log)")
            return 1

    logger.info(f"=== {STRATEGY_ID} cycle end ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
