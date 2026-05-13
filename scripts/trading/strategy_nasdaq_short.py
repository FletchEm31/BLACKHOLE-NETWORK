#!/usr/bin/env python3
"""
strategy_nasdaq_short.py — BHN Strategy 7: NASDAQ-SHORT.

OPERATOR-SPEC, NO QC SOURCE. Designed as the bear-market hedge complement
to Strat 6 (NASDAQ-LONG). Shares the regression signal infrastructure
(nasdaq_signal.py) and trailing-stop infrastructure (nasdaq_risk.py).

Entry conditions (ALL must be true):
  1. PSQ or QID wins the 18-day regression vs SQQQ (same calc as Strat 6,
     opposite signal). Handled by nasdaq_signal.evaluate_short_signal().
  2. SPY has been below its 200-day MA for ≥5 consecutive trading days.
  3. SPY has NOT had ≥3 consecutive closes above its 200MA within the
     last 20 trading days (no recent failed bear rejection).

Position when entered:
  - Short QQQ @ 30% of allocation
  - Short SPY @ 20% of allocation
  - JPST     @ 50% of allocation (cash sleeve — earns yield on the
    half-margined balance)
  REQUIRES margin-enabled Alpaca account.

Exits (any ONE triggers):
  E1. SPY reclaims 200MA for ≥3 consecutive days → close all shorts → NEUTRAL
  E2. QLD wins regression again (bear regime done) → close all shorts → NEUTRAL
  E3. 5% trailing stop on either short leg → close that leg only
  E4. 15% profit target on either short leg → close that leg only
  E5. 60-day max-hold hard limit on any short position

State transitions (operator-enforced):
  Never directly short → long or long → short. Always pass through
  NEUTRAL (100% JPST) — Strat 7's state machine only knows NEUTRAL and
  IN_POSITION. The "neutral" pass-through prevents whipsaw during
  regime transitions.

Cadence (matches Strat 6 — single cron-runnable file):
  - Every run: risk check on open trades (trailing stop / target / max-hold).
    Per-leg closes (E3/E4/E5).
  - Monday runs: signal-based checks (E1, E2) on existing position, then
    if NEUTRAL → entry signal evaluation. Open all 3 legs if entry fires.
  - Non-Monday: maintenance only.

Capital: $5,000 per rules.json strat_7_nasdaq_short.
Account: dedicated margin-enabled Alpaca paper account (BHN-Paper-Strat7,
keys in /etc/bhn-trading/strat7.env via STRAT7_ALPACA_KEY_ID + SECRET).

DEPLOY POLICY (operator spec): start with enabled=false. Activate only
AFTER Strat 6 has been validated in paper trading.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

import trading_core as tc
import nasdaq_signal as ns
import nasdaq_risk as nr


STRATEGY_ID = tc.StrategyId.NASDAQ_SHORT.value
logger = tc.get_logger(STRATEGY_ID)

# Position split — operator spec
DEFAULT_SHORT_QQQ_PCT = Decimal("0.30")
DEFAULT_SHORT_SPY_PCT = Decimal("0.20")
DEFAULT_JPST_PCT      = Decimal("0.50")

SHORT_LEG_TICKERS = ("QQQ", "SPY")     # the two short legs
PARK_TICKER = "JPST"
RISK_TICKERS = ("QQQ", "SPY")          # for state derivation (any open short = IN_POSITION)

# Exit-condition thresholds
SPY_RECLAIM_STREAK_REQUIRED = 3        # E1: 3 consecutive above-MA days exits the strategy


class Strat7State(str, Enum):
    NEUTRAL = "neutral"          # 100% JPST (or FLAT — initialization)
    IN_POSITION = "in_position"  # short QQQ + short SPY + JPST simultaneously


def _alert_horizon(severity: str, message: str) -> None:
    try:
        tc._send_alert(severity=severity, message=message)
    except Exception:
        pass


def _get_live_price(ticker: str) -> Optional[Decimal]:
    try:
        bar = tc.get_alpaca().get_latest_trade(ticker)
        return Decimal(str(bar.price))
    except Exception as e:
        logger.warning(f"live price unavailable for {ticker}: {e}")
        return None


def _derive_state() -> tuple[Strat7State, list[dict]]:
    open_trades = tc.get_open_trades(STRATEGY_ID)
    has_short = any(t["ticker"] in SHORT_LEG_TICKERS and t["side"] == "sell"
                    for t in open_trades)
    return (Strat7State.IN_POSITION if has_short else Strat7State.NEUTRAL,
            open_trades)


def _is_signal_day() -> bool:
    """Mondays only — matches Strat 6's weekly cadence."""
    return datetime.now(timezone.utc).weekday() == 0


# ─────────────────────────────────────────────────────────────────────────
# E1 + E2: signal-based exits (checked on Mondays before entry decision)
# ─────────────────────────────────────────────────────────────────────────

def _spy_reclaimed_for_streak(spy: dict, streak_required: int) -> bool:
    """E1: has SPY been above its 200MA for `streak_required` consecutive days?
    spy_200ma_state() returns max_above_streak_recent — we check that's ≥ N."""
    if not spy:
        return False
    return spy.get("max_above_streak_recent", 0) >= streak_required and spy.get("above_ma") is True


def _qld_wins_again(scores: list) -> bool:
    """E2: did QLD reclaim the #1 spot in the regression ranking?"""
    if not scores:
        return False
    return scores[0].ticker == ns.LONG_LEADER


# ─────────────────────────────────────────────────────────────────────────
# Risk check — trailing stops + profit targets + max-hold per leg
# ─────────────────────────────────────────────────────────────────────────

def _risk_check_open_trades() -> int:
    """Per-leg E3/E4/E5 checks via shared nasdaq_risk module. Returns count closed."""
    open_trades = tc.get_open_trades(STRATEGY_ID)
    if not open_trades:
        return 0
    closed = 0
    alpaca = tc.get_alpaca()
    for trade in open_trades:
        # JPST sleeve isn't risk-managed (cash equivalent, no stop/target sense)
        if trade["ticker"] == PARK_TICKER:
            continue
        price = _get_live_price(trade["ticker"])
        if price is None:
            continue
        result = nr.evaluate_trade(
            trade=trade, current_price=price,
            profit_target_pct=nr.PROFIT_TARGET_SHORT_PCT,
        )
        logger.info(f"risk-check {trade['ticker']} {trade['side']}: {result.detail}")
        if result.action == "hold":
            continue
        try:
            order = alpaca.submit_order(
                symbol=trade["ticker"], qty=int(trade["qty"]),
                side="buy" if trade["side"] == "sell" else "sell",
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
    return closed


# ─────────────────────────────────────────────────────────────────────────
# Position open / close
# ─────────────────────────────────────────────────────────────────────────

def _close_all_shorts(state_before: Strat7State, reason: tc.ExitReason,
                       reason_str: str) -> int:
    """Close every open short leg + any JPST sleeve. Used by E1/E2 exits."""
    open_trades = tc.get_open_trades(STRATEGY_ID)
    if not open_trades:
        return 0
    closed = 0
    alpaca = tc.get_alpaca()
    for trade in open_trades:
        price = _get_live_price(trade["ticker"]) or Decimal(str(trade["entry_price"]))
        try:
            order = alpaca.submit_order(
                symbol=trade["ticker"], qty=int(trade["qty"]),
                side="buy" if trade["side"] == "sell" else "sell",
                type="market", time_in_force="day",
            )
            fill = Decimal(str(order.filled_avg_price or price))
            tc.close_trade(
                trade_id=trade["id"], exit_price=fill, exit_reason=reason,
                alpaca_order_id_exit=order.id,
            )
            closed += 1
            logger.info(f"CLOSE {trade['ticker']} qty={trade['qty']} @ {fill} ({reason_str})")
        except Exception as e:
            logger.error(f"close-all failed for {trade['ticker']}: {e}")
    if closed > 0:
        _alert_horizon(
            severity="info",
            message=f"BHN {STRATEGY_ID}: closed {closed} legs ({reason_str}) → NEUTRAL",
        )
    return closed


def _open_leg(ticker: str, side: tc.Action, dollars: Decimal,
               signal: ns.NasdaqSignal) -> Optional[int]:
    """Open one leg of the short position. ticker + side are passed explicitly
    so the same helper handles short_QQQ (sell), short_SPY (sell), and the
    JPST sleeve (buy)."""
    price = _get_live_price(ticker)
    if price is None:
        logger.error(f"cannot open {ticker}: no live price")
        return None
    qty = int(dollars / price)
    if qty < 1:
        logger.warning(f"skip {ticker}: dollar size ${dollars} < 1 share at ${price}")
        return None

    sig_metadata = {
        "leader":          signal.leader,
        "leader_score":    signal.leader_score,
        "runner_up":       signal.runner_up,
        "runner_up_score": signal.runner_up_score,
        "spy_close":       signal.spy_close,
        "spy_ma_200":      signal.spy_ma_200,
        "spy_above_ma":    signal.spy_above_ma,
        "spy_days_below_streak": signal.spy_days_below_ma_streak,
        "reason":          signal.reason,
        "leg":             "short_qqq" if ticker == "QQQ" else (
                            "short_spy" if ticker == "SPY" else "jpst_sleeve"),
    }
    signal_id = tc.log_signal(
        STRATEGY_ID, ticker, side,
        reason=signal.reason, value=signal.leader_score,
        acted_on=True, raw_payload=sig_metadata,
    )
    try:
        order = tc.place_order(
            strategy_id=STRATEGY_ID, ticker=ticker, side=side, qty=qty,
            order_type="market", signal_id=signal_id,
            metadata={
                **sig_metadata, "water_mark": str(price),
                "dollars_intended": str(dollars),
            },
        )
        logger.info(f"OPEN {ticker} {side.value} {qty}@${price} "
                    f"order={order['alpaca_order_id']}")
        return order["trade_id"]
    except Exception as e:
        logger.error(f"place_order refused for {ticker}: {e}")
        with tc.get_pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE signals_log SET acted_on=false WHERE id=%s",
                            (signal_id,))
        return None


def _enter_short_position(allocation: Decimal, signal: ns.NasdaqSignal) -> int:
    """Open all three legs: short QQQ (30%), short SPY (20%), JPST (50%).
    Returns count of legs successfully opened."""
    # Read split fractions from rules.json if overridden; else defaults
    rules_block = tc.get_strategy_rules(STRATEGY_ID) or {}
    split = rules_block.get("position_split") or {}
    short_qqq_pct = Decimal(str(split.get("short_qqq_pct", DEFAULT_SHORT_QQQ_PCT)))
    short_spy_pct = Decimal(str(split.get("short_spy_pct", DEFAULT_SHORT_SPY_PCT)))
    jpst_pct      = Decimal(str(split.get("jpst_pct", DEFAULT_JPST_PCT)))

    opened = 0
    if _open_leg("QQQ", tc.Action.SELL, allocation * short_qqq_pct, signal) is not None:
        opened += 1
    if _open_leg("SPY", tc.Action.SELL, allocation * short_spy_pct, signal) is not None:
        opened += 1
    if _open_leg(PARK_TICKER, tc.Action.BUY, allocation * jpst_pct, signal) is not None:
        opened += 1

    if opened > 0:
        _alert_horizon(
            severity="info",
            message=(f"BHN {STRATEGY_ID}: SHORT entered ({opened}/3 legs). "
                     f"Leader={signal.leader} score={signal.leader_score:+.6f}; "
                     f"SPY below 200MA {signal.spy_days_below_ma_streak}d"),
        )
    return opened


# ─────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────

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

            state_before, _ = _derive_state()

            # Per-cycle: E3 / E4 / E5 risk checks (trailing stop / target / max-hold)
            n_closed = _risk_check_open_trades()
            logger.info(f"per-leg risk check: {n_closed} legs closed")

            # Monday-only: E1 (SPY reclaim) + E2 (QLD wins) signal-based exits,
            # then entry evaluation if NEUTRAL.
            if _is_signal_day():
                scores = ns.compute_scores()
                spy = ns.spy_200ma_state()

                # E1 + E2 only meaningful if we currently hold shorts
                state_now, _ = _derive_state()
                if state_now == Strat7State.IN_POSITION:
                    if _spy_reclaimed_for_streak(spy, SPY_RECLAIM_STREAK_REQUIRED):
                        _close_all_shorts(state_now, tc.ExitReason.MANUAL,
                                           f"E1: SPY reclaimed 200MA for "
                                           f"{spy.get('max_above_streak_recent', 0)}d ≥ "
                                           f"{SPY_RECLAIM_STREAK_REQUIRED}d")
                    elif _qld_wins_again(scores):
                        _close_all_shorts(state_now, tc.ExitReason.MANUAL,
                                           f"E2: QLD reclaimed #1 rank "
                                           f"(intercept {scores[0].intercept:+.6f})")

                # If NEUTRAL after all exits, evaluate entry
                state_now, _ = _derive_state()
                if state_now == Strat7State.NEUTRAL:
                    short_signal = ns.evaluate_short_signal(scores=scores, spy=spy)
                    logger.info(f"entry eval: fires={short_signal.fires} "
                                f"reason={short_signal.reason}")
                    if short_signal.fires:
                        opened = _enter_short_position(allocation, short_signal)
                        if opened == 0:
                            logger.error("entry fired but 0 legs opened — strategy "
                                          "remains NEUTRAL, retry next Monday")
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
