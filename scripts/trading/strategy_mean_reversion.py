#!/usr/bin/env python3
"""
strategy_mean_reversion.py — BHN Strategy 3: Mean Reversion Scalp.

Intraday Bollinger Band reversion on liquid large-cap names.

Entry: 5-min close prints below the 20-period lower Bollinger Band
       (price - SMA20 < -2σ), volume on the entry bar ≥ 1.2× recent
       average. Z-score ranks candidates when more than one fires.

Exit:  whichever comes first —
       - price tags the 20-period SMA (middle band → take profit)
       - 2% stop from entry fill
       - 4-hour max hold
       - 15 minutes before market close (EOD flatten — never carry a
         scalp position overnight)

Universe: ~50 hardcoded liquid large-caps (price > $10, avg volume > 1M).
Capital allocation: $20,000. Equal-weight $2k per position, max 5
concurrent. Cadence: every 5 min during regular market hours.

This strategy is the only one in the framework that's allowed to trade
multiple times per name per day — a fade-the-overshoot pattern can fire
again after exit. Per-strategy daily turnover cap enforced by the
breaker daemon if turnover dollars exceed 6× allocation.

Math is vanilla Python (no pandas / numpy needed) — same approach as
strategy_momentum.py. Bollinger computation rolls over the last 20 bars
returned from Alpaca's get_bars endpoint at 5Min timeframe.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import trading_core as tc


STRATEGY_ID = tc.StrategyId.MEAN_REVERSION.value
logger = tc.get_logger(STRATEGY_ID)


# ─── Hardcoded defaults (overridden by rules.json strat_3_mean_reversion block) ────
DEFAULTS = {
    "bollinger": {
        "period":      20,          # 5-min bars in lookback window
        "stddev":      2.0,         # standard deviations for band width
        "timeframe":   "5Min",      # Alpaca bar size
        "lookback_bars": 40,        # fetch ≥ 2× period so first calc is clean
    },
    "entry_filters": {
        "min_price":           10.0,
        "min_avg_volume":      1_000_000,    # shares/day historical avg
        "volume_confirm_mult": 1.2,           # bar volume vs trailing avg
        "min_z_score":         2.0,           # 2.0σ below mean for entry
    },
    "position_limits": {
        "size_per_signal":   2000,
        "max_positions":     5,
        "max_hold_minutes":  240,            # 4h
        "stop_loss_pct":     0.02,           # 2%
        "eod_flatten_minutes_before_close": 15,
    },
}

# Universe — 50 high-volume large/mid-caps, sector-diverse, all > $10 + > 1M ADV
UNIVERSE = [
    # Tech / megacap
    "AAPL", "MSFT", "GOOGL", "META", "AMZN", "NVDA", "AMD", "TSLA",
    "ORCL", "CRM", "ADBE", "INTC", "QCOM", "AVGO", "NFLX",
    # Financials
    "JPM", "BAC", "WFC", "GS", "MS", "C", "V", "MA", "AXP",
    # Industrials / energy
    "CAT", "GE", "BA", "UPS", "FDX", "DE", "XOM", "CVX", "COP",
    # Consumer / retail
    "WMT", "HD", "LOW", "TGT", "COST", "NKE", "MCD", "SBUX", "KO", "PEP",
    # Healthcare
    "JNJ", "PFE", "UNH", "LLY", "ABBV", "MRK",
    # Other liquid scalp candidates
    "DIS", "BAC",  # duplicate filtered below
]
UNIVERSE = sorted(set(UNIVERSE))


# ─────────────────────────────────────────────────────────────────────────
# Bollinger math
# ─────────────────────────────────────────────────────────────────────────

def fetch_intraday_bars(ticker: str, timeframe: str, lookback_bars: int) -> list[dict]:
    """Fetch the most recent N intraday bars. Returns list ordered oldest→newest."""
    alpaca = tc.get_alpaca()
    # Pull a generous window — Alpaca returns whatever's available
    end = datetime.now(timezone.utc)
    # Buffer: at 5-min bars, 1 trading day ~= 78 bars, so ~2 days covers lookback=40
    start = end - timedelta(days=3)
    try:
        bars = alpaca.get_bars(ticker, timeframe, start=start.isoformat(),
                               end=end.isoformat(), limit=lookback_bars + 20)
    except Exception as e:
        logger.debug(f"{ticker}: bar fetch failed ({e})")
        return []
    out: list[dict] = []
    for b in bars:
        out.append({
            "t": b.t, "o": float(b.o), "h": float(b.h),
            "l": float(b.l), "c": float(b.c), "v": int(b.v),
        })
    return out[-lookback_bars:]


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def stddev(values: list[float], mu: float) -> float:
    if len(values) < 2:
        return 0.0
    return math.sqrt(sum((v - mu) ** 2 for v in values) / len(values))


def compute_bollinger(closes: list[float], period: int, k: float) -> Optional[dict]:
    """Last-bar Bollinger: SMA + ±k·σ. None if insufficient data."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    mu = mean(window)
    sigma = stddev(window, mu)
    if sigma == 0:
        return None
    upper = mu + k * sigma
    lower = mu - k * sigma
    z = (closes[-1] - mu) / sigma
    return {"mu": mu, "sigma": sigma, "upper": upper, "lower": lower, "z": z}


# ─────────────────────────────────────────────────────────────────────────
# Exit handling
# ─────────────────────────────────────────────────────────────────────────

def get_minutes_until_close() -> Optional[int]:
    """Use Alpaca clock to get minutes until session close. None if closed."""
    try:
        clock = tc.get_alpaca().get_clock()
        if not clock.is_open:
            return None
        close_dt = clock.next_close
        now = clock.timestamp
        if close_dt.tzinfo is None:
            close_dt = close_dt.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return int((close_dt - now).total_seconds() / 60)
    except Exception as e:
        logger.warning(f"Alpaca clock query failed: {e}")
        return None


def process_exits(rules: dict) -> None:
    open_trades = tc.get_open_trades(STRATEGY_ID)
    if not open_trades:
        return

    cfg = rules["position_limits"]
    bb_cfg = rules["bollinger"]
    stop_pct = Decimal(str(cfg["stop_loss_pct"]))
    max_hold = int(cfg["max_hold_minutes"])
    eod_window = int(cfg["eod_flatten_minutes_before_close"])

    minutes_to_close = get_minutes_until_close()
    eod_flush = minutes_to_close is not None and minutes_to_close <= eod_window
    if eod_flush:
        logger.info(f"EOD flatten: {minutes_to_close}min to close, "
                    f"flushing {len(open_trades)} positions")

    now = datetime.now(timezone.utc)

    for t in open_trades:
        ticker = t["ticker"]
        entry_time = t["entry_time"]
        if entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)
        entry_price = Decimal(str(t["entry_price"]))

        # EOD flatten short-circuits everything
        if eod_flush:
            _exit_at_market(t, ticker, tc.ExitReason.EOD_FLATTEN,
                            f"{minutes_to_close}min to close")
            continue

        # Max hold
        age_min = (now - entry_time).total_seconds() / 60
        if age_min >= max_hold:
            _exit_at_market(t, ticker, tc.ExitReason.TIME_EXIT,
                            f"{int(age_min)}min hold ≥ {max_hold}min")
            continue

        # Pull bars for current price + Bollinger middle-band check
        bars = fetch_intraday_bars(ticker, bb_cfg["timeframe"], bb_cfg["lookback_bars"])
        if not bars:
            continue
        last_close = Decimal(str(bars[-1]["c"]))

        # Stop loss
        sl_level = entry_price * (Decimal("1") - stop_pct)
        if last_close <= sl_level:
            _exit_at_market(t, ticker, tc.ExitReason.STOP_LOSS,
                            f"close ${last_close} ≤ stop ${sl_level:.2f}")
            continue

        # Target: middle Bollinger band
        bb = compute_bollinger([b["c"] for b in bars],
                                bb_cfg["period"], bb_cfg["stddev"])
        if bb is None:
            continue
        target = Decimal(str(bb["mu"]))
        if last_close >= target:
            _exit_at_market(t, ticker, tc.ExitReason.TARGET,
                            f"close ${last_close} ≥ SMA20 ${target:.2f}")


def _exit_at_market(trade: dict, ticker: str, reason: tc.ExitReason,
                    reason_str: str) -> None:
    qty = int(trade["qty"])
    try:
        alpaca = tc.get_alpaca()
        order = alpaca.submit_order(symbol=ticker, qty=qty, side="sell",
                                    type="market", time_in_force="day")
        fill = Decimal(str(order.filled_avg_price or
                           alpaca.get_latest_trade(ticker).price))
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


# ─────────────────────────────────────────────────────────────────────────
# Entry scanning
# ─────────────────────────────────────────────────────────────────────────

def scan_universe(rules: dict, exclude_held: set[str]) -> list[dict]:
    """Returns oversold candidates ordered by z-score magnitude (most oversold first)."""
    bb_cfg = rules["bollinger"]
    filt = rules["entry_filters"]

    min_price = float(filt["min_price"])
    min_avg_vol = float(filt["min_avg_volume"])
    vol_mult = float(filt["volume_confirm_mult"])
    min_z = float(filt["min_z_score"])

    candidates: list[dict] = []
    for ticker in UNIVERSE:
        if ticker in exclude_held:
            continue

        bars = fetch_intraday_bars(ticker, bb_cfg["timeframe"], bb_cfg["lookback_bars"])
        if len(bars) < bb_cfg["period"]:
            continue

        last = bars[-1]
        if last["c"] < min_price:
            continue

        # Volume confirm — last bar vs trailing N-1 average
        prior_vols = [b["v"] for b in bars[:-1]]
        avg_vol = mean([float(v) for v in prior_vols])
        if avg_vol <= 0 or float(last["v"]) < avg_vol * vol_mult:
            continue
        # Avg daily volume guard — extrapolate from intraday window
        # 5Min bars: ~78 per session. Window is ~3 days, expect ~234 bars max.
        bars_per_day = 78
        if len(prior_vols) >= bars_per_day:
            daily_window = prior_vols[-bars_per_day:]
            estimated_adv = sum(daily_window)
        else:
            estimated_adv = sum(prior_vols) * (bars_per_day / max(len(prior_vols), 1))
        if estimated_adv < min_avg_vol:
            continue

        bb = compute_bollinger([b["c"] for b in bars],
                                bb_cfg["period"], bb_cfg["stddev"])
        if bb is None:
            continue
        # Entry trigger: last close below lower band AND z ≤ -min_z
        if last["c"] >= bb["lower"]:
            continue
        if bb["z"] > -min_z:
            continue

        candidates.append({
            "ticker": ticker,
            "close": last["c"],
            "mu": bb["mu"],
            "sigma": bb["sigma"],
            "lower": bb["lower"],
            "upper": bb["upper"],
            "z": bb["z"],
            "volume": last["v"],
            "avg_volume": avg_vol,
            "vol_mult": last["v"] / avg_vol if avg_vol else 0,
        })

    # Most oversold first (most negative z)
    candidates.sort(key=lambda c: c["z"])
    return candidates


def process_entries(rules: dict) -> None:
    cfg = rules["position_limits"]
    eod_window = int(cfg["eod_flatten_minutes_before_close"])
    size = Decimal(str(cfg["size_per_signal"]))
    max_positions = int(cfg["max_positions"])

    minutes_to_close = get_minutes_until_close()
    if minutes_to_close is None:
        logger.info("Market closed — skipping entries")
        return
    # No new entries inside EOD flatten window — would just close them immediately
    if minutes_to_close <= eod_window + 30:
        logger.info(f"Within {eod_window+30}min of close — skipping new entries")
        return

    open_trades = tc.get_open_trades(STRATEGY_ID)
    held = {t["ticker"] for t in open_trades}
    open_count = len(open_trades)

    if open_count >= max_positions:
        logger.info(f"At position cap ({max_positions}); no entries")
        return

    candidates = scan_universe(rules, held)
    if not candidates:
        logger.info("No oversold candidates")
        return

    logger.info(f"Scan: {len(candidates)} oversold candidates "
                f"(most oversold: {candidates[0]['ticker']} z={candidates[0]['z']:.2f})")

    alpaca = tc.get_alpaca()
    for cand in candidates:
        if open_count >= max_positions:
            break
        ticker = cand["ticker"]
        try:
            trade = alpaca.get_latest_trade(ticker)
            price = Decimal(str(trade.price))
        except Exception as e:
            logger.warning(f"No fresh price for {ticker}: {e}")
            continue

        qty = int(size / price)
        if qty < 1:
            continue

        signal_id = tc.log_signal(
            STRATEGY_ID, ticker, tc.Action.BUY,
            reason=f"bollinger reversion: z={cand['z']:.2f}σ, "
                   f"close ${cand['close']:.2f} < lower ${cand['lower']:.2f}, "
                   f"vol {cand['vol_mult']:.1f}× avg",
            value=cand["z"],
            acted_on=True,
            raw_payload=cand,
        )

        sl_price = price * (Decimal("1") - Decimal(str(cfg["stop_loss_pct"])))
        target_price = Decimal(str(cand["mu"]))  # middle band as TP

        try:
            order = tc.place_order(
                strategy_id=STRATEGY_ID,
                ticker=ticker,
                side=tc.Action.BUY,
                qty=qty,
                order_type="market",
                signal_id=signal_id,
                stop_loss=sl_price,
                target=target_price,
                metadata={
                    "entry_z_score": cand["z"],
                    "bollinger_lower": cand["lower"],
                    "bollinger_mu": cand["mu"],
                    "bollinger_upper": cand["upper"],
                    "bollinger_sigma": cand["sigma"],
                    "entry_volume": cand["volume"],
                    "entry_vol_mult": cand["vol_mult"],
                    "max_hold_minutes": int(cfg["max_hold_minutes"]),
                },
            )
            logger.info(f"BUY {ticker} {qty}@${price} (z={cand['z']:.2f}σ, "
                        f"target SMA ${target_price:.2f}, order={order['alpaca_order_id']})")
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
    allowed, reason = tc.should_run(STRATEGY_ID, requires_market_open=True)
    if not allowed:
        logger.info(f"Skipping run: {reason}")
        return 0

    rules = tc.get_strategy_rules(STRATEGY_ID) or {}
    rules = {**DEFAULTS, **rules}
    for k in ("bollinger", "entry_filters", "position_limits"):
        rules[k] = {**DEFAULTS[k], **rules.get(k, {})}

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

            # Exits before entries — frees capacity + handles EOD flatten
            process_exits(rules)
            process_entries(rules)

        except Exception:
            logger.exception(f"{STRATEGY_ID} cycle failed")
            tc.update_strategy_status(STRATEGY_ID, "error",
                                      "exception in main loop (see log)")
            return 1

    logger.info(f"=== {STRATEGY_ID} cycle end ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
