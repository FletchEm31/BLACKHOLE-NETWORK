#!/usr/bin/env python3
"""
strategy_sector_rotation.py — BHN Strategy 8: SECTOR-ROTATION.

QC source: TheOmniscientParadox. Logic identical; plumbing swapped (QC
framework → Alpaca + trading_core).

Mechanic (verbatim from QC):
  - Daily rebalance, 5 minutes before market close
  - Score each non-safe ticker via sector_signal.score_ticker()
  - Sort by final_score DESC; pick the leader
  - Rotation decision tree (verbatim — see _decide_target()):
      no holding   → best if best_score > 0     else safe
      in safe (BIL)→ best if best_score > 0.02  else stay safe
      in risk      → best if best_score > current * 1.10 else
                     stay current unless current < -0.02 → safe
  - SPY 200MA defense (if SPY below 200MA AND target is not safe):
      prefer UUP if UUP > 0 and UUP > target_score
      else if target_score < 0: flee to safe
  - Vol-targeted position sizing:
      weight = min(1.0, target_vol / curr_vol)
        where curr_vol = std(returns_21d) * sqrt(252) and target_vol = 0.80
  - Drift-band rebalance: only call set_holdings if |current_weight -
    target_weight| > 5% — prevents over-trading when scores wobble
  - Remainder fill: if target_weight < 1.0 and target != safe and
    1.0 - target_weight > 0.10, hold the remainder in safe (BIL)

Capital: $5,000 per rules.json strat_8_sector_rotation block.
Account: dedicated Alpaca paper account (BHN-Paper-Strat8, env at
/etc/bhn-trading/strat8.env).

State (current_holding): derived from open paper_trades — if there's an
open trade for this strategy in a risk-asset, that's current_holding;
if only an open BIL trade, current_holding = safe; if none, None.

HORIZON SMS hooks (per operator spec):
  - State transition (NEUTRAL ↔ IN_ASSET ↔ different IN_ASSET)
  - Stop / target hit (5% trailing, 13.25% target per shared-risk spec)
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

import trading_core as tc
import nasdaq_risk as nr        # share the trailing-stop infrastructure
import sector_signal as ss


STRATEGY_ID = tc.StrategyId.SECTOR_ROTATION.value
logger = tc.get_logger(STRATEGY_ID)

# Operator-shared risk params for Strat 8 (long-side profit target)
PROFIT_TARGET_PCT = nr.PROFIT_TARGET_LONG_PCT   # 13.25%

# QC-source rotation thresholds
SAFE_TICKER = ss.SAFE_TICKER
TARGET_VOL = 0.80                  # 80% annualized
CONFIDENCE_THRESHOLD = 0.10        # 10% relative; best must beat current × 1.10
SAFE_EXIT_TRIGGER = 0.02           # current_score < -0.02 → flee to safe
SAFE_ENTRY_TRIGGER = 0.02          # if currently in safe, best must > 0.02 to leave
DRIFT_REBALANCE_THRESHOLD = 0.05   # only rebalance if |drift| > 5%
REMAINDER_FILL_THRESHOLD = 0.10    # if 1 - target_weight > 10%, fill with BIL

# Position-side metadata key
WATER_MARK_KEY = "water_mark"


# ─────────────────────────────────────────────────────────────────────────
# State derivation — derived from open paper_trades (no separate table)
# ─────────────────────────────────────────────────────────────────────────

class SectorState(str, Enum):
    FLAT = "flat"            # no open positions
    IN_SAFE = "in_safe"      # only BIL open
    IN_ASSET = "in_asset"    # risk asset (possibly + BIL remainder) open


def _derive_state() -> tuple[SectorState, Optional[str], list[dict]]:
    """Returns (state, current_risk_ticker_or_None, open_trades).
    current_risk_ticker is the risk asset (non-BIL) currently held, or None
    if the strategy is in safe or flat."""
    open_trades = tc.get_open_trades(STRATEGY_ID)
    if not open_trades:
        return SectorState.FLAT, None, []
    risk = [t for t in open_trades if t["ticker"] != SAFE_TICKER]
    if not risk:
        return SectorState.IN_SAFE, None, open_trades
    # Assume single risk position (matches QC's "current_holding" semantics);
    # if multiple, take the largest by qty × entry_price (reconciliation will
    # flag any oddity)
    risk.sort(key=lambda t: int(t["qty"]) * float(t["entry_price"]), reverse=True)
    return SectorState.IN_ASSET, risk[0]["ticker"], open_trades


def _alert_horizon(severity: str, message: str) -> None:
    try:
        tc._send_alert(severity=severity, message=message)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────
# Decision tree — verbatim from QC TheOmniscientParadox.Rebalance()
# ─────────────────────────────────────────────────────────────────────────

def _decide_target(signal: ss.SectorSignal, current_holding: Optional[str]) -> str:
    """Returns the ticker to hold this cycle. Mirrors QC's decision tree."""
    if signal.best_asset is None:
        return SAFE_TICKER

    best = signal.best_asset
    best_score = signal.best_score

    # Step 1: base decision
    if current_holding is None:
        target = best if best_score > 0 else SAFE_TICKER
    elif current_holding == SAFE_TICKER:
        target = best if best_score > SAFE_ENTRY_TRIGGER else SAFE_TICKER
    else:
        current_score = signal.scores.get(current_holding)
        current_score_val = current_score.final_score if current_score else -999
        if best_score > current_score_val * (1 + CONFIDENCE_THRESHOLD):
            target = best
        elif current_score_val < -SAFE_EXIT_TRIGGER:
            target = SAFE_TICKER
        else:
            target = current_holding

    # Step 2: SPY 200MA defense
    if signal.spy_trend is False and target != SAFE_TICKER:
        uup_score = signal.scores.get("UUP")
        uup_score_val = uup_score.final_score if uup_score else -999
        target_score = signal.scores.get(target)
        target_score_val = target_score.final_score if target_score else -999

        if uup_score_val > 0 and uup_score_val > target_score_val:
            target = "UUP"
        elif target_score_val < 0:
            target = SAFE_TICKER

    return target


def _decide_weight(signal: ss.SectorSignal, target: str) -> Decimal:
    """Vol-targeted weight. For safe asset: 100%. For risk asset: min(1.0,
    target_vol / curr_vol_annualized). Matches QC verbatim."""
    if target == SAFE_TICKER:
        return Decimal("1.0")
    score = signal.scores.get(target)
    if score is None or score.vol_annualized <= 0:
        return Decimal("1.0")
    weight = TARGET_VOL / score.vol_annualized
    return Decimal(str(min(1.0, weight)))


# ─────────────────────────────────────────────────────────────────────────
# Order placement
# ─────────────────────────────────────────────────────────────────────────

def _get_live_price(ticker: str) -> Optional[Decimal]:
    try:
        bar = tc.get_alpaca().get_latest_trade(ticker)
        return Decimal(str(bar.price))
    except Exception as e:
        logger.warning(f"live price unavailable for {ticker}: {e}")
        return None


def _close_trade(trade: dict, reason: tc.ExitReason) -> bool:
    """Submit a market close + record in paper_trades. Returns True on success."""
    ticker = trade["ticker"]
    price = _get_live_price(ticker) or Decimal(str(trade["entry_price"]))
    alpaca = tc.get_alpaca()
    try:
        order = alpaca.submit_order(
            symbol=ticker, qty=int(trade["qty"]),
            side="sell" if trade["side"] == "buy" else "buy",
            type="market", time_in_force="day",
        )
        fill = Decimal(str(order.filled_avg_price or price))
        tc.close_trade(
            trade_id=trade["id"], exit_price=fill, exit_reason=reason,
            alpaca_order_id_exit=order.id,
        )
        logger.info(f"CLOSE {ticker} qty={trade['qty']} @ {fill} reason={reason.value}")
        return True
    except Exception as e:
        logger.error(f"close failed for {ticker}: {e}")
        return False


def _open_trade(ticker: str, dollars: Decimal, allocation: Decimal,
                 signal: ss.SectorSignal, target_weight: Decimal,
                 state_before: SectorState) -> Optional[int]:
    """Open a market buy for the given dollar amount. Returns trade_id."""
    price = _get_live_price(ticker)
    if price is None:
        return None
    qty = int(dollars / price)
    if qty < 1:
        logger.warning(f"skip {ticker}: dollar size ${dollars} < 1 share at ${price}")
        return None

    score = signal.scores.get(ticker)
    sig_metadata = {
        "rebalance_target":     ticker,
        "target_weight":        str(target_weight),
        "dollars_intended":     str(dollars),
        "best_asset":           signal.best_asset,
        "best_score":           signal.best_score,
        "sorted_assets":        signal.sorted_assets,
        "spy_close":            signal.spy_close,
        "spy_sma_200":          signal.spy_sma_200,
        "spy_trend":            signal.spy_trend,
        "state_before":         state_before.value,
        "score_breakdown":      ({
            "roc_fast":      score.roc_fast,
            "roc_med":       score.roc_med,
            "roc_slow":      score.roc_slow,
            "vol_21":        score.vol_21,
            "vol_annualized": score.vol_annualized,
            "rsi_14":        score.rsi_14,
            "sma_50":        score.sma_50,
            "trend_score":   score.trend_score,
            "rsi_penalty":   score.rsi_penalty,
            "risk_adj_mom":  score.risk_adj_mom,
            "final_score":   score.final_score,
        }) if score else None,
    }
    signal_id = tc.log_signal(
        STRATEGY_ID, ticker, tc.Action.BUY,
        reason=f"sector rebalance → {ticker} (weight {target_weight})",
        value=score.final_score if score else None,
        acted_on=True, raw_payload=sig_metadata,
    )
    try:
        order = tc.place_order(
            strategy_id=STRATEGY_ID, ticker=ticker, side=tc.Action.BUY,
            qty=qty, order_type="market", signal_id=signal_id,
            metadata={**sig_metadata, WATER_MARK_KEY: str(price)},
        )
        logger.info(f"OPEN {ticker} {qty}@${price} (target_weight={target_weight}) "
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
# Risk check — trailing stop + profit target on every open position
# ─────────────────────────────────────────────────────────────────────────

def _risk_check_open_trades() -> int:
    """Run trailing stop / target / max-hold (here: no max_hold per spec) checks
    on each open trade. Returns count of trades closed."""
    open_trades = tc.get_open_trades(STRATEGY_ID)
    if not open_trades:
        return 0
    closed = 0
    for trade in open_trades:
        price = _get_live_price(trade["ticker"])
        if price is None:
            continue
        result = nr.evaluate_trade(
            trade=trade, current_price=price,
            profit_target_pct=PROFIT_TARGET_PCT,
        )
        if result.action == "hold":
            continue
        # Strat 8 doesn't enforce max-hold per operator spec ("none — signal driven")
        if result.action == "max_hold":
            logger.info(f"{trade['ticker']}: max_hold path skipped per strat_8 spec")
            continue
        # Close via market order
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
    return closed


# ─────────────────────────────────────────────────────────────────────────
# Rebalance — verbatim port of TheOmniscientParadox.Rebalance() execution
# ─────────────────────────────────────────────────────────────────────────

def _do_rebalance(allocation: Decimal) -> None:
    """Compute signal, decide target, size, liquidate non-target holdings,
    set holdings to drift-tolerant target weight, fill remainder with safe."""
    state_before, current_holding, open_trades = _derive_state()
    signal = ss.evaluate_sector_signal()

    if not signal.scores:
        logger.warning("no scoring data — skipping rebalance")
        return

    target_asset = _decide_target(signal, current_holding)
    target_weight = _decide_weight(signal, target_asset)

    logger.info(
        f"rebalance: state={state_before.value} current={current_holding} "
        f"best={signal.best_asset}({signal.best_score:+.4f}) "
        f"spy_trend={signal.spy_trend} → target={target_asset} "
        f"weight={target_weight}"
    )

    # Liquidate everything not in {target_asset, safe-if-remainder-fill}
    target_dollars = allocation * target_weight
    remainder = Decimal("1.0") - target_weight
    fill_remainder = (target_asset != SAFE_TICKER and remainder > Decimal(str(REMAINDER_FILL_THRESHOLD)))

    keep_tickers = {target_asset}
    if fill_remainder:
        keep_tickers.add(SAFE_TICKER)

    for trade in open_trades:
        if trade["ticker"] not in keep_tickers:
            _close_trade(trade, tc.ExitReason.MANUAL)

    # Refresh open trades after liquidations
    remaining_open = tc.get_open_trades(STRATEGY_ID)
    remaining_by_ticker = {t["ticker"]: t for t in remaining_open}

    # Determine current weight of target asset (for drift-band check)
    # Approximated: dollar value of current target position / allocation
    current_target_dollars = Decimal("0")
    if target_asset in remaining_by_ticker:
        ot = remaining_by_ticker[target_asset]
        live = _get_live_price(target_asset)
        if live is not None:
            current_target_dollars = Decimal(str(ot["qty"])) * live
    current_target_weight = (
        current_target_dollars / allocation if allocation > 0 else Decimal("0")
    )

    drift = abs(current_target_weight - target_weight)
    logger.info(f"target {target_asset}: current_w={current_target_weight:.3f} "
                f"target_w={target_weight} drift={drift:.3f}")

    if target_asset not in remaining_by_ticker:
        # No existing position in target → open one
        _open_trade(target_asset, target_dollars, allocation, signal,
                    target_weight, state_before)
    elif drift > Decimal(str(DRIFT_REBALANCE_THRESHOLD)):
        # Existing position but drifted too far → close + reopen at right size
        _close_trade(remaining_by_ticker[target_asset], tc.ExitReason.MANUAL)
        _open_trade(target_asset, target_dollars, allocation, signal,
                    target_weight, state_before)
    else:
        logger.info(f"drift {drift:.3f} ≤ {DRIFT_REBALANCE_THRESHOLD} — no rebalance needed")

    # Fill remainder with BIL if applicable
    if fill_remainder:
        remainder_dollars = allocation * remainder
        # Check if BIL position is already correct-sized
        if SAFE_TICKER in remaining_by_ticker:
            ot = remaining_by_ticker[SAFE_TICKER]
            live = _get_live_price(SAFE_TICKER)
            if live is not None:
                cur_safe_dollars = Decimal(str(ot["qty"])) * live
                safe_drift = abs(cur_safe_dollars - remainder_dollars) / allocation
                if safe_drift > Decimal(str(DRIFT_REBALANCE_THRESHOLD)):
                    _close_trade(ot, tc.ExitReason.MANUAL)
                    _open_trade(SAFE_TICKER, remainder_dollars, allocation,
                                signal, remainder, state_before)
        else:
            _open_trade(SAFE_TICKER, remainder_dollars, allocation,
                        signal, remainder, state_before)

    # Final state announce
    state_after, new_holding, _ = _derive_state()
    if state_before != state_after or current_holding != new_holding:
        _alert_horizon(
            severity="info",
            message=(f"BHN {STRATEGY_ID}: {state_before.value}/{current_holding} "
                     f"→ {state_after.value}/{new_holding} (score={signal.best_score:+.4f})"),
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

            # Trailing stops + targets first
            n_closed = _risk_check_open_trades()
            logger.info(f"risk check: {n_closed} positions closed")

            # Then the rotation rebalance (QC daily 5min-before-close cadence)
            _do_rebalance(allocation)

        except Exception:
            logger.exception(f"{STRATEGY_ID} cycle failed")
            tc.update_strategy_status(STRATEGY_ID, "error",
                                      "exception in main loop (see log)")
            return 1

    logger.info(f"=== {STRATEGY_ID} cycle end ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
