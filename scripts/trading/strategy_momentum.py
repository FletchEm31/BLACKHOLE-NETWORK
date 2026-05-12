#!/usr/bin/env python3
"""
strategy_momentum.py — BHN Strategy 4: Momentum Trend Following.

50/200 SMA crossover with volume confirmation on S&P 500 high-volume names.
Golden cross (50-SMA crosses above 200-SMA, volume >1.5x 20-day avg) = BUY.
Death cross (50-SMA crosses below 200-SMA) = SELL.
8% trailing stop below highest close since entry, evaluated each cycle.
Equal-weight position sizing, max 5 positions.

Cadence: daily post-market (17:00 ET via cron — strategy is NOT polling-driven).
Capital allocation: $20,000 from trading_strategies row.

Data source: Alpaca daily bars (no FMP dependency for SMA computation — Alpaca's
free paper IEX feed includes daily bars going back years for S&P liquid names).

Configuration via rules.json `strat_4_momentum` block. Hardcoded DEFAULTS below
match the operator-stated spec; rules.json overrides any field.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal
from typing import Any, Optional

import trading_core as tc


STRATEGY_ID = tc.StrategyId.MOMENTUM.value
logger = tc.get_logger(STRATEGY_ID)

# ─── Hardcoded defaults (overridden by rules.json strat_4_momentum block) ──
DEFAULTS = {
    "universe": [
        # Tech (high-volume mega-caps + key names)
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
        "AVGO", "ORCL", "ADBE", "CRM", "AMD", "INTC", "CSCO",
        # Financials
        "JPM", "BAC", "WFC", "GS", "MS", "BLK", "C", "V", "MA",
        # Energy
        "XOM", "CVX", "COP",
        # Healthcare
        "JNJ", "PFE", "UNH", "ABBV", "MRK", "LLY",
        # Consumer
        "WMT", "COST", "HD", "MCD", "KO", "PEP", "NKE", "DIS",
        "PG", "T", "VZ", "PM",
    ],
    "signals": {
        "fast_period": 50,
        "slow_period": 200,
        "volume_confirm_multiplier": 1.5,
        "volume_avg_period": 20,
    },
    "position_limits": {
        "max_positions": 5,
        "trailing_stop_pct": 0.08,  # 8% below highest close since entry
    },
}


# ─────────────────────────────────────────────────────────────────────────
# Bar fetching
# ─────────────────────────────────────────────────────────────────────────

def fetch_daily_bars(ticker: str, lookback_days: int = 230) -> Optional[list[dict]]:
    """
    Fetch ~lookback_days of daily bars for `ticker` from Alpaca.
    Returns list of dicts: [{close: Decimal, volume: int, date: date}, ...]
    sorted oldest-first. Returns None on failure.

    Need 201+ bars to compute both today's and yesterday's 200-SMA (so we
    can detect the cross event). 230 gives margin for weekends + holidays.
    """
    alpaca = tc.get_alpaca()
    end = datetime.now(timezone.utc).date()
    # Calendar lookback — Alpaca returns only trading days regardless
    start = end - timedelta(days=int(lookback_days * 1.5))

    try:
        # Alpaca SDK: get_bars(symbol, timeframe, start=ISO, end=ISO, ...).
        # TimeFrame.Day = daily bars. Returns iterable of Bar objects.
        from alpaca_trade_api import TimeFrame
        bars_iter = alpaca.get_bars(
            ticker, TimeFrame.Day,
            start=start.isoformat(), end=end.isoformat(),
            adjustment="all",
        )
        bars = [{
            "close": Decimal(str(b.c)),
            "volume": int(b.v),
            "date": b.t.date() if hasattr(b.t, "date") else date.fromisoformat(str(b.t)[:10]),
        } for b in bars_iter]
        if len(bars) < 201:
            logger.debug(f"{ticker}: only {len(bars)} bars — insufficient for 200-SMA")
            return None
        return bars
    except Exception as e:
        logger.debug(f"{ticker}: bar fetch failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────
# Signal computation
# ─────────────────────────────────────────────────────────────────────────

def sma(closes: list[Decimal], period: int, offset: int = 0) -> Optional[Decimal]:
    """Simple moving average of last `period` closes, ending `offset` bars back."""
    end = len(closes) - offset
    start = end - period
    if start < 0:
        return None
    window = closes[start:end]
    return sum(window) / Decimal(period)


def compute_metrics(bars: list[dict], signals_cfg: dict) -> Optional[dict]:
    """Return {sma_fast_today, sma_fast_prev, sma_slow_today, sma_slow_prev,
    vol_today, vol_avg, today_close} or None if insufficient data."""
    closes = [b["close"] for b in bars]
    fast = int(signals_cfg["fast_period"])
    slow = int(signals_cfg["slow_period"])
    vol_period = int(signals_cfg["volume_avg_period"])

    sma_fast_today = sma(closes, fast, offset=0)
    sma_fast_prev = sma(closes, fast, offset=1)
    sma_slow_today = sma(closes, slow, offset=0)
    sma_slow_prev = sma(closes, slow, offset=1)
    if None in (sma_fast_today, sma_fast_prev, sma_slow_today, sma_slow_prev):
        return None

    vol_today = bars[-1]["volume"]
    vol_window = bars[-vol_period - 1:-1]  # exclude today; 20-day avg up to yesterday
    if len(vol_window) < vol_period:
        return None
    vol_avg = sum(b["volume"] for b in vol_window) / vol_period

    return {
        "sma_fast_today": sma_fast_today,
        "sma_fast_prev": sma_fast_prev,
        "sma_slow_today": sma_slow_today,
        "sma_slow_prev": sma_slow_prev,
        "vol_today": vol_today,
        "vol_avg": vol_avg,
        "today_close": bars[-1]["close"],
    }


def is_golden_cross(m: dict) -> bool:
    return m["sma_fast_prev"] <= m["sma_slow_prev"] and m["sma_fast_today"] > m["sma_slow_today"]


def is_death_cross(m: dict) -> bool:
    return m["sma_fast_prev"] >= m["sma_slow_prev"] and m["sma_fast_today"] < m["sma_slow_today"]


def has_volume_confirm(m: dict, multiplier: float) -> bool:
    return m["vol_today"] >= m["vol_avg"] * multiplier


# ─────────────────────────────────────────────────────────────────────────
# Position sizing
# ─────────────────────────────────────────────────────────────────────────

def compute_position_size(allocation: Decimal, max_positions: int,
                          open_count: int, price: Decimal) -> int:
    """Equal weight. Returns 0 if at position limit or price <= 0."""
    if open_count >= max_positions or price <= 0:
        return 0
    per_position = allocation / Decimal(max_positions)
    shares = per_position / price
    return int(shares)


# ─────────────────────────────────────────────────────────────────────────
# Exit logic — death cross + trailing stop
# ─────────────────────────────────────────────────────────────────────────

def process_exits(rules: dict) -> None:
    """
    For each open position:
      - Death cross detected on this ticker's latest bars → exit
      - Trailing stop hit (today_close <= highest_close_since_entry * (1 - pct)) → exit
    Trailing-stop tracking: updates paper_trades.metadata.highest_close_since_entry
    in place each cycle so future runs use the latest peak.
    """
    open_trades = tc.get_open_trades(STRATEGY_ID)
    if not open_trades:
        return

    signals_cfg = rules["signals"]
    trailing_pct = Decimal(str(rules["position_limits"]["trailing_stop_pct"]))

    for t in open_trades:
        ticker = t["ticker"]
        trade_id = t["id"]

        bars = fetch_daily_bars(ticker)
        if not bars:
            logger.warning(f"Exit check {ticker}: bars unavailable, skipping cycle")
            continue
        today_close = bars[-1]["close"]

        # 1. Death cross
        metrics = compute_metrics(bars, signals_cfg)
        if metrics and is_death_cross(metrics):
            _exit_position(t, ticker, today_close, tc.ExitReason.MANUAL,
                           f"death cross: SMA50({metrics['sma_fast_today']:.2f}) "
                           f"crossed below SMA200({metrics['sma_slow_today']:.2f})")
            continue

        # 2. Trailing stop — first compute highest close since entry
        entry_time = t["entry_time"]
        if entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)
        entry_date = entry_time.date()
        bars_since_entry = [b for b in bars if b["date"] >= entry_date]
        if not bars_since_entry:
            continue
        highest_since_entry = max(b["close"] for b in bars_since_entry)

        # Stored peak in metadata (may be higher than what current bars show
        # if there's a gap; take the larger of the two)
        meta = t.get("metadata") or {}
        prev_peak = Decimal(str(meta.get("highest_close_since_entry", "0")))
        peak = max(highest_since_entry, prev_peak)

        # Update if new peak
        if peak > prev_peak:
            meta["highest_close_since_entry"] = str(peak)
            _update_metadata(trade_id, meta)

        # Trailing stop level
        stop_level = peak * (Decimal("1") - trailing_pct)
        if today_close <= stop_level:
            _exit_position(t, ticker, today_close, tc.ExitReason.TRAILING_STOP,
                           f"close ${today_close} ≤ trailing stop ${stop_level:.2f} "
                           f"(peak ${peak:.2f}, {trailing_pct*100:.0f}% trail)")


def _exit_position(trade: dict, ticker: str, price: Decimal,
                   reason: tc.ExitReason, reason_str: str) -> None:
    """Place sell order + close paper_trade."""
    qty = int(trade["qty"])
    try:
        alpaca = tc.get_alpaca()
        order = alpaca.submit_order(symbol=ticker, qty=qty, side="sell",
                                    type="market", time_in_force="day")
        fill = Decimal(str(order.filled_avg_price or price))
        result = tc.close_trade(
            trade_id=trade["id"],
            exit_price=fill,
            exit_reason=reason,
            alpaca_order_id_exit=order.id,
        )
        logger.info(f"EXIT {ticker} {qty}@${fill} ({reason.value}: {reason_str}) "
                    f"P&L=${result['pnl_dollar']} ({result['pnl_pct']:.2f}%)")
    except Exception as e:
        logger.error(f"Failed to close {ticker} (trade_id={trade['id']}): {e}")


def _update_metadata(trade_id: int, metadata: dict) -> None:
    import json
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE paper_trades SET metadata = %s::jsonb WHERE id = %s",
                (json.dumps(metadata), trade_id),
            )


# ─────────────────────────────────────────────────────────────────────────
# Entry logic — scan universe for golden crosses
# ─────────────────────────────────────────────────────────────────────────

def process_entries(rules: dict, allocation: Decimal) -> None:
    universe = rules.get("universe", DEFAULTS["universe"])
    signals_cfg = rules["signals"]
    max_positions = int(rules["position_limits"]["max_positions"])
    vol_mult = float(signals_cfg["volume_confirm_multiplier"])

    open_trades = tc.get_open_trades(STRATEGY_ID)
    open_tickers = {t["ticker"] for t in open_trades}
    open_count = len(open_trades)
    if open_count >= max_positions:
        logger.info(f"At position limit ({open_count}/{max_positions}); no new entries")
        return

    candidates: list[tuple[str, dict]] = []
    for ticker in universe:
        if ticker in open_tickers:
            continue
        bars = fetch_daily_bars(ticker)
        if not bars:
            continue
        metrics = compute_metrics(bars, signals_cfg)
        if not metrics:
            continue
        if not is_golden_cross(metrics):
            continue
        if not has_volume_confirm(metrics, vol_mult):
            # Log signal but don't act
            tc.log_signal(
                STRATEGY_ID, ticker, tc.Action.BUY,
                reason="golden cross but volume not confirmed",
                value=float(metrics["vol_today"] / metrics["vol_avg"]),
                acted_on=False,
                raw_payload={"sma_fast": str(metrics["sma_fast_today"]),
                             "sma_slow": str(metrics["sma_slow_today"]),
                             "vol_ratio": str(metrics["vol_today"] / metrics["vol_avg"])},
            )
            continue
        candidates.append((ticker, metrics))

    if not candidates:
        logger.info("No golden-cross candidates this cycle")
        return

    # Sort by volume ratio (stronger confirmation first)
    candidates.sort(
        key=lambda x: x[1]["vol_today"] / x[1]["vol_avg"],
        reverse=True,
    )

    slots_available = max_positions - open_count
    logger.info(f"Found {len(candidates)} golden-cross candidates; taking top {slots_available}")

    for ticker, metrics in candidates[:slots_available]:
        price = metrics["today_close"]
        qty = compute_position_size(allocation, max_positions, open_count, price)
        if qty < 1:
            logger.info(f"Skip {ticker}: position size <1 share at ${price}")
            continue

        vol_ratio = metrics["vol_today"] / metrics["vol_avg"]
        signal_id = tc.log_signal(
            STRATEGY_ID, ticker, tc.Action.BUY,
            reason=f"golden cross + {vol_ratio:.2f}x volume",
            value=float(vol_ratio),
            acted_on=True,
            raw_payload={
                "sma_fast_today": str(metrics["sma_fast_today"]),
                "sma_fast_prev":  str(metrics["sma_fast_prev"]),
                "sma_slow_today": str(metrics["sma_slow_today"]),
                "sma_slow_prev":  str(metrics["sma_slow_prev"]),
                "vol_today":      metrics["vol_today"],
                "vol_avg":        float(metrics["vol_avg"]),
                "vol_ratio":      float(vol_ratio),
                "entry_price":    str(price),
            },
        )

        try:
            order = tc.place_order(
                strategy_id=STRATEGY_ID,
                ticker=ticker,
                side=tc.Action.BUY,
                qty=qty,
                order_type="market",
                signal_id=signal_id,
                trailing_stop_pct=Decimal(str(rules["position_limits"]["trailing_stop_pct"])) * 100,
                metadata={
                    "vol_ratio": float(vol_ratio),
                    "sma_fast_today": str(metrics["sma_fast_today"]),
                    "sma_slow_today": str(metrics["sma_slow_today"]),
                    "highest_close_since_entry": str(price),  # seed for trailing stop
                },
            )
            logger.info(f"BUY {ticker} {qty}@${price} (vol={vol_ratio:.2f}x, "
                        f"order={order['alpaca_order_id']})")
            open_count += 1
        except RuntimeError as e:
            logger.warning(f"BUY refused for {ticker}: {e}")
            with tc.get_pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE signals_log SET acted_on=false WHERE id=%s",
                                (signal_id,))


# ─────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────

def main() -> int:
    allowed, reason = tc.should_run(STRATEGY_ID)
    if not allowed:
        logger.info(f"Skipping run: {reason}")
        return 0

    rules = tc.get_strategy_rules(STRATEGY_ID) or {}
    rules = {**DEFAULTS, **rules}
    for k in ("signals", "position_limits"):
        rules[k] = {**DEFAULTS[k], **rules.get(k, {})}

    allocation = tc.get_rls_capital_allocation(STRATEGY_ID)
    if allocation <= 0:
        logger.warning(f"Allocation is ${allocation} — refusing to run")
        return 1

    logger.info(f"=== {STRATEGY_ID} cycle start (allocation=${allocation}, "
                f"universe={len(rules['universe'])} tickers) ===")

    with tc.pg_advisory_lock(abs(hash(STRATEGY_ID)) % (2**31)):
        try:
            with tc.get_pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE trading_strategies SET last_run_at = NOW() WHERE id = %s",
                        (STRATEGY_ID,),
                    )

            # Exits first — death cross + trailing stop
            process_exits(rules)

            # Then entries — golden cross + volume confirm
            process_entries(rules, allocation)

        except Exception:
            logger.exception(f"{STRATEGY_ID} cycle failed")
            tc.update_strategy_status(STRATEGY_ID, "error",
                                      "exception in main loop (see log)")
            return 1

    logger.info(f"=== {STRATEGY_ID} cycle end ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
