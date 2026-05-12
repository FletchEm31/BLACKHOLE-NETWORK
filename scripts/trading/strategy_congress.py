#!/usr/bin/env python3
"""
strategy_congress.py — BHN Strategy 1: Congress Trade Following.

Based on Quiver Quantitative's Congress Buys Strategy (34.97% historical CAGR).
Polls Quiver API every 15 min for fresh congressional disclosures, filters for
purchases >$10k transacted within the last 48h, weights by transaction size +
member seniority + committee/sector relevance, and places paper-trade buys
into Alpaca (gated by trading_core's circuit breakers + live-mode flags).

Configuration via rules.json `strat_1_congress` block. Hardcoded defaults
below match the operator-stated spec; rules.json overrides any field.

Exit logic: 30-day hold OR 15% stop loss. Both evaluated each run.
Position size: 10% of portfolio max per position; equal-weight bucketing within
that cap. Capital allocation: $20,000 from trading_strategies row.

systemd timer: every 15 min.
Run: `python3 strategy_congress.py` (no args; reads env + rules.json).
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

import requests

import trading_core as tc


STRATEGY_ID = tc.StrategyId.CONGRESS.value
logger = tc.get_logger(STRATEGY_ID)

# ─── Hardcoded defaults (overridden by rules.json strat_1_congress block) ──
DEFAULTS = {
    "poll_interval_seconds": 900,        # 15 min
    "filters": {
        "min_transaction_usd": 10_000,
        "max_days_after_disclosure": 2,  # buy within 48h
        "purchases_only": True,
    },
    "weighting": {
        # 1.0 = pure log(amount); seniority + committee multipliers
        # applied if data is available
        "use_seniority": True,
        "use_committee_relevance": True,
    },
    "position_limits": {
        "max_position_pct_of_portfolio": 0.10,  # 10%
        "stop_loss_pct": 0.15,                  # 15%
        "hold_days": 30,
    },
    # Quiver API
    "quiver_endpoint": "https://api.quiverquant.com/beta/live/congresstrading",
}

# Member seniority (years served — Quiver doesn't expose this directly; we
# hardcode a short reference. Update via rules.json or supplement with a
# congress.gov sync workflow later).
# Values: years served as of 2026. Higher = more seniority = signal boost.
SENIORITY_HINTS = {
    "Nancy Pelosi": 38, "Mitch McConnell": 41, "Bernie Sanders": 35,
    "Elizabeth Warren": 13, "Tommy Tuberville": 5, "Dan Crenshaw": 7,
    "Pat Toomey": 22, "Richard Burr": 30, "Diane Feinstein": 30,
    # Default for unknowns: 10 (median House tenure)
}


# ─────────────────────────────────────────────────────────────────────────
# Quiver API
# ─────────────────────────────────────────────────────────────────────────

def fetch_recent_disclosures(quiver_key: str, endpoint: str) -> list[dict]:
    """
    Pull recent congressional trades from Quiver. Quiver paginates; the
    /beta/live/congresstrading endpoint returns the most-recent ~500 trades
    which is plenty for a 15-min polling cycle.
    """
    if not quiver_key:
        raise RuntimeError("QUIVER_API_KEY not set in env — strategy_congress cannot run")

    headers = {"Authorization": f"Token {quiver_key}", "Accept": "application/json"}
    for attempt in range(3):
        try:
            resp = requests.get(endpoint, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                logger.warning(f"Quiver returned non-list: {type(data)} — skipping cycle")
                return []
            logger.info(f"Quiver: fetched {len(data)} recent disclosures")
            return data
        except requests.RequestException as e:
            logger.warning(f"Quiver fetch attempt {attempt+1}/3 failed: {e}")
            time.sleep(2 ** attempt)
    logger.error("Quiver fetch failed after 3 attempts — skipping this cycle")
    return []


def parse_disclosure(raw: dict) -> Optional[dict]:
    """
    Normalize one Quiver disclosure record. Returns None on unparseable rows
    so we skip them rather than crashing the cycle.
    """
    try:
        ticker = raw.get("Ticker", "").strip().upper()
        if not ticker or not ticker.isalpha():
            return None

        txn_type = raw.get("Transaction", "").strip().lower()
        # Quiver uses "Purchase", "Sale", "Exchange" — we only want purchases
        if "purchase" not in txn_type:
            return None

        # Amount: Quiver gives a "Range" string like "$1,000 - $15,000" plus
        # sometimes "Amount" as an int. Use Amount if present, else parse the
        # midpoint of Range.
        amount = raw.get("Amount")
        if amount is None:
            range_str = raw.get("Range", "")
            amount = _parse_range_midpoint(range_str)
        if amount is None:
            return None
        amount = int(amount)

        txn_date_str = raw.get("TransactionDate") or raw.get("Traded")
        if not txn_date_str:
            return None
        txn_date = datetime.fromisoformat(txn_date_str.replace("Z", "+00:00"))
        if txn_date.tzinfo is None:
            txn_date = txn_date.replace(tzinfo=timezone.utc)

        return {
            "ticker": ticker,
            "amount_usd": amount,
            "transaction_date": txn_date,
            "member": raw.get("Representative") or raw.get("Senator") or "Unknown",
            "report_date": raw.get("ReportDate"),
            "raw": raw,
        }
    except (ValueError, KeyError, TypeError) as e:
        logger.debug(f"Skipping unparseable disclosure: {e} | raw={raw}")
        return None


def _parse_range_midpoint(range_str: str) -> Optional[int]:
    """'$1,000,001 - $5,000,000' → 3,000,000 (midpoint)."""
    if not range_str:
        return None
    try:
        parts = range_str.replace("$", "").replace(",", "").split("-")
        if len(parts) != 2:
            return None
        low = float(parts[0].strip())
        high = float(parts[1].strip())
        return int((low + high) / 2)
    except (ValueError, IndexError):
        return None


# ─────────────────────────────────────────────────────────────────────────
# Filtering
# ─────────────────────────────────────────────────────────────────────────

def filter_disclosures(disclosures: list[dict], filters: dict) -> list[dict]:
    min_amount = filters["min_transaction_usd"]
    max_age_days = filters["max_days_after_disclosure"]
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_age_days)

    out = []
    for d in disclosures:
        if d["amount_usd"] < min_amount:
            continue
        if d["transaction_date"] < cutoff:
            continue
        out.append(d)
    return out


# ─────────────────────────────────────────────────────────────────────────
# Weighting
# ─────────────────────────────────────────────────────────────────────────

def compute_weight(disclosure: dict, weighting: dict) -> Decimal:
    """
    Combined weight = log10(amount) × seniority_multiplier × committee_multiplier.
    Higher weight = more conviction; used to break ties when multiple
    signals fire same cycle.
    """
    amount = max(disclosure["amount_usd"], 1)
    base = Decimal(str(math.log10(amount)))

    multiplier = Decimal("1.0")
    if weighting.get("use_seniority"):
        years = SENIORITY_HINTS.get(disclosure["member"], 10)
        # Seniority multiplier: log-scaled, capped at 1.5x for 40+ years
        seniority_mult = Decimal(str(1 + math.log10(max(years, 1)) / 4))
        multiplier *= seniority_mult

    # Committee relevance not implemented yet (requires committee → sector
    # mapping + member committee assignments — needs a separate data source).
    # Stub at 1.0 for now.

    return base * multiplier


# ─────────────────────────────────────────────────────────────────────────
# Position sizing
# ─────────────────────────────────────────────────────────────────────────

def compute_position_size(allocation: Decimal, max_pct: Decimal,
                          price: Decimal, weight: Decimal) -> int:
    """
    Position size = min(max_pct × allocation, weight-scaled portion).
    Returns whole shares. 0 if price <= 0 or sizing below 1 share.
    """
    if price <= 0:
        return 0
    max_dollars = allocation * max_pct
    # For v1, ignore weight in sizing — use flat max-pct cap per position.
    # weight is logged in signal metadata for analysis but doesn't yet
    # gradient position size (could in v2 with backtested optimization).
    shares = max_dollars / price
    return int(shares.quantize(Decimal("1"), rounding=tc.ROUND_HALF_UP)) \
        if hasattr(tc, "ROUND_HALF_UP") else int(shares)


# ─────────────────────────────────────────────────────────────────────────
# Entry logic
# ─────────────────────────────────────────────────────────────────────────

def process_new_signals(disclosures: list[dict], rules: dict, allocation: Decimal) -> None:
    """
    For each filtered disclosure, evaluate entry: check existing position,
    fetch current price, log signal, place buy.
    """
    open_trades = tc.get_open_trades(STRATEGY_ID)
    open_tickers = {t["ticker"] for t in open_trades}

    position_limits = rules.get("position_limits", DEFAULTS["position_limits"])
    weighting = rules.get("weighting", DEFAULTS["weighting"])
    max_pct = Decimal(str(position_limits["max_position_pct_of_portfolio"]))
    stop_loss_pct = Decimal(str(position_limits["stop_loss_pct"]))

    alpaca = tc.get_alpaca()

    for d in disclosures:
        ticker = d["ticker"]
        if ticker in open_tickers:
            tc.log_signal(STRATEGY_ID, ticker, tc.Action.HOLD,
                          reason="already holding", value=d["amount_usd"],
                          raw_payload=d["raw"])
            continue

        weight = compute_weight(d, weighting)

        # Get current price
        try:
            bar = alpaca.get_latest_trade(ticker)
            price = Decimal(str(bar.price))
        except Exception as e:
            logger.info(f"Skip {ticker}: can't get price ({e})")
            tc.log_signal(STRATEGY_ID, ticker, tc.Action.HOLD,
                          reason=f"no_price: {e}", value=d["amount_usd"],
                          raw_payload=d["raw"])
            continue

        qty = compute_position_size(allocation, max_pct, price, weight)
        if qty < 1:
            logger.info(f"Skip {ticker}: position size <1 share at ${price}")
            continue

        # Log + place
        signal_id = tc.log_signal(
            STRATEGY_ID, ticker, tc.Action.BUY,
            reason=f"congress buy by {d['member']}, ${d['amount_usd']:,}",
            value=float(weight),
            acted_on=True,
            raw_payload={**d["raw"], "weight": str(weight), "shares": qty,
                         "entry_price": str(price)},
        )

        try:
            stop_loss = price * (Decimal("1") - stop_loss_pct)
            order = tc.place_order(
                strategy_id=STRATEGY_ID,
                ticker=ticker,
                side=tc.Action.BUY,
                qty=qty,
                order_type="market",
                signal_id=signal_id,
                stop_loss=stop_loss,
                metadata={"member": d["member"], "weight": str(weight),
                          "disclosed_amount_usd": d["amount_usd"]},
            )
            logger.info(f"BUY {ticker} {qty}@${price} (member={d['member']}, "
                        f"weight={weight:.2f}, order={order['alpaca_order_id']})")
        except RuntimeError as e:
            logger.warning(f"BUY refused for {ticker}: {e}")
            # signal_id was logged with acted_on=true; update to false on refusal
            with tc.get_pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE signals_log SET acted_on=false WHERE id=%s",
                                (signal_id,))


# ─────────────────────────────────────────────────────────────────────────
# Exit logic
# ─────────────────────────────────────────────────────────────────────────

def process_exits(rules: dict) -> None:
    """
    For each open position:
      - 30-day hold expiry (or rules.json `hold_days`) → time exit
      - 15% stop loss (or rules.json `stop_loss_pct`) → stop loss
    """
    position_limits = rules.get("position_limits", DEFAULTS["position_limits"])
    hold_days = int(position_limits["hold_days"])
    stop_loss_pct = Decimal(str(position_limits["stop_loss_pct"]))

    open_trades = tc.get_open_trades(STRATEGY_ID)
    if not open_trades:
        return

    alpaca = tc.get_alpaca()
    now = datetime.now(timezone.utc)

    for t in open_trades:
        ticker = t["ticker"]
        entry_time = t["entry_time"]
        if entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)
        entry_price = Decimal(str(t["entry_price"]))
        qty = int(t["qty"])

        # Time exit
        age = now - entry_time
        if age >= timedelta(days=hold_days):
            _close(t, ticker, qty, tc.ExitReason.TIME_EXIT,
                   reason_str=f"hold_days={hold_days} expired (age={age.days}d)")
            continue

        # Stop loss
        try:
            bar = alpaca.get_latest_trade(ticker)
            price = Decimal(str(bar.price))
        except Exception as e:
            logger.warning(f"Can't price {ticker} for exit check: {e}")
            continue

        stop_price = entry_price * (Decimal("1") - stop_loss_pct)
        if price <= stop_price:
            _close(t, ticker, qty, tc.ExitReason.STOP_LOSS,
                   reason_str=f"price ${price} ≤ stop ${stop_price}")
            continue


def _close(trade: dict, ticker: str, qty: int,
           exit_reason: tc.ExitReason, reason_str: str) -> None:
    """Place sell order + close paper_trade row."""
    try:
        alpaca = tc.get_alpaca()
        order = alpaca.submit_order(symbol=ticker, qty=qty, side="sell",
                                    type="market", time_in_force="day")
        # Use the order's filled price if available, else current bar price
        fill_price = Decimal(str(order.filled_avg_price or alpaca.get_latest_trade(ticker).price))
        result = tc.close_trade(
            trade_id=trade["id"],
            exit_price=fill_price,
            exit_reason=exit_reason,
            alpaca_order_id_exit=order.id,
        )
        logger.info(f"EXIT {ticker} {qty}@${fill_price} ({exit_reason.value}: {reason_str}) "
                    f"P&L=${result['pnl_dollar']} ({result['pnl_pct']:.2f}%)")
    except Exception as e:
        logger.error(f"Failed to close {ticker} (trade_id={trade['id']}): {e}")


# ─────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────

def main() -> int:
    # Gate
    allowed, reason = tc.should_run(STRATEGY_ID)
    if not allowed:
        logger.info(f"Skipping run: {reason}")
        return 0

    rules = tc.get_strategy_rules(STRATEGY_ID) or DEFAULTS
    # Deep-merge rules over DEFAULTS for any missing keys
    rules = {**DEFAULTS, **rules}
    for k in ("filters", "weighting", "position_limits"):
        rules[k] = {**DEFAULTS[k], **rules.get(k, {})}

    allocation = tc.get_rls_capital_allocation(STRATEGY_ID)
    if allocation <= 0:
        logger.warning(f"Allocation is ${allocation} — refusing to run")
        return 1

    env = tc._load_env()
    quiver_key = env.get("quiver_key")
    if not quiver_key:
        logger.error("QUIVER_API_KEY not in env — strategy disabled until key added")
        tc.update_strategy_status(STRATEGY_ID, "error",
                                  "QUIVER_API_KEY not configured")
        return 1

    logger.info(f"=== {STRATEGY_ID} cycle start (allocation=${allocation}) ===")

    with tc.pg_advisory_lock(abs(hash(STRATEGY_ID)) % (2**31)):
        try:
            # Update last_run_at
            with tc.get_pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE trading_strategies SET last_run_at = NOW() WHERE id = %s",
                        (STRATEGY_ID,),
                    )

            # Phase 1: exits (so we free up capacity before new entries)
            process_exits(rules)

            # Phase 2: poll Quiver + signal generation + entry
            raw_disclosures = fetch_recent_disclosures(quiver_key, rules["quiver_endpoint"])
            parsed = [d for d in (parse_disclosure(r) for r in raw_disclosures) if d]
            filtered = filter_disclosures(parsed, rules["filters"])

            logger.info(f"Disclosures: raw={len(raw_disclosures)} parsed={len(parsed)} "
                        f"after_filters={len(filtered)}")

            if filtered:
                process_new_signals(filtered, rules, allocation)

        except Exception:
            logger.exception(f"{STRATEGY_ID} cycle failed")
            tc.update_strategy_status(STRATEGY_ID, "error",
                                      "exception in main loop (see log)")
            return 1

    logger.info(f"=== {STRATEGY_ID} cycle end ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
