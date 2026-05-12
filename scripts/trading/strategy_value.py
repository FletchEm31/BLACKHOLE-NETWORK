#!/usr/bin/env python3
"""
strategy_value.py — BHN Strategy 2: Buffett Value Screening.

Classic Buffett/Graham deep-value screen via FMP API. ALL criteria must be true:
  - P/E ratio < 15
  - P/B ratio < 1.5
  - Debt/Equity < 0.5
  - ROE > 15%
  - 52-week decline > 10% (beaten down from peak)
  - No earnings in next 7 days (avoid earnings-event risk)

Equal-weight position sizing. Max 10 positions. Capital allocation: $25,000.
Exit: P/E exceeds 25 OR 20% stop loss OR 90-day hold.

Cadence: daily post-market (17:00 ET via cron). Cron-triggered, not interval.

FMP quota strategy: Free tier = 250 calls/day. Use the bulk /stock-screener
endpoint to narrow ~500 S&P names down to ~50-150 P/E<15 candidates in ONE call.
Then key-metrics-ttm per candidate (~100 calls). Earnings calendar = 1 bulk call.
Alpaca daily bars for 52-week decline (free, no quota). Total: ~120 calls/day,
well under budget.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal
from typing import Any, Optional

import requests

import trading_core as tc


STRATEGY_ID = tc.StrategyId.VALUE.value
logger = tc.get_logger(STRATEGY_ID)

FMP_BASE = "https://financialmodelingprep.com/api/v3"

# ─── Hardcoded defaults (overridden by rules.json strat_2_value block) ────
DEFAULTS = {
    "filters": {
        "pe_max":             15.0,
        "pb_max":             1.5,
        "debt_equity_max":    0.5,
        "roe_min":            0.15,
        "decline_52w_min_pct": 10.0,
        "no_earnings_within_days": 7,
        # screener pre-filter (used to keep call volume tractable)
        "market_cap_min":     2_000_000_000,   # $2B — focuses on real businesses
        "volume_min":         500_000,         # daily liquidity floor
    },
    "position_limits": {
        "max_positions":      10,
        "stop_loss_pct":      0.20,            # 20%
        "exit_pe_max":        25.0,            # P/E exceeds 25 → exit (mean reversion done)
        "max_hold_days":      90,
    },
}


# ─────────────────────────────────────────────────────────────────────────
# FMP API wrapper
# ─────────────────────────────────────────────────────────────────────────

def fmp_get(endpoint: str, params: Optional[dict] = None) -> Optional[Any]:
    """
    GET against FMP with retries + rate-limit handling. Returns parsed JSON
    or None on failure. The API key is appended as a query param.
    """
    env = tc._load_env()
    key = env.get("fmp_key")
    if not key:
        logger.error("FMP_API_KEY not in env — strategy_value cannot run")
        return None
    url = f"{FMP_BASE}{endpoint}"
    p = dict(params or {})
    p["apikey"] = key

    for attempt in range(3):
        try:
            resp = requests.get(url, params=p, timeout=15)
            if resp.status_code == 429:
                logger.warning(f"FMP rate-limit hit on {endpoint}; backing off")
                time.sleep(2 ** attempt + 5)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.warning(f"FMP fetch attempt {attempt+1}/3 failed on {endpoint}: {e}")
            time.sleep(2 ** attempt)
    logger.error(f"FMP fetch failed after 3 attempts on {endpoint}")
    return None


def get_screener_candidates(filters: dict) -> list[dict]:
    """
    Use FMP /stock-screener with parameters it natively supports
    (marketCap, beta, P/E, volume, exchange). Returns candidates that
    pass the pre-filter; we'll narrow further via key-metrics-ttm.
    Bulk call — one API hit returns up to ~thousands of stocks.
    """
    params = {
        "marketCapMoreThan": int(filters["market_cap_min"]),
        "volumeMoreThan":    int(filters["volume_min"]),
        "peLessThan":        float(filters["pe_max"]),
        "exchange":          "NYSE,NASDAQ",
        "isEtf":             "false",
        "isFund":            "false",
        "isActivelyTrading": "true",
        "limit":             500,
    }
    data = fmp_get("/stock-screener", params)
    if not isinstance(data, list):
        logger.warning(f"Screener returned non-list: {type(data)}")
        return []
    logger.info(f"FMP screener returned {len(data)} P/E<{filters['pe_max']} candidates")
    return data


def get_key_metrics_ttm(ticker: str) -> Optional[dict]:
    """
    Fetch trailing-twelve-month key metrics for one ticker. Returns dict
    with: peRatio, pbRatio, debtToEquity, roe (or returnOnEquity), etc.
    """
    data = fmp_get(f"/key-metrics-ttm/{ticker}")
    if not data or not isinstance(data, list) or len(data) == 0:
        return None
    return data[0]


def get_upcoming_earnings(within_days: int) -> set[str]:
    """
    Tickers with earnings reports scheduled in the next `within_days` days.
    Bulk call — one API hit covers the whole window.
    """
    today = date.today()
    end = today + timedelta(days=within_days)
    data = fmp_get("/earning_calendar", {
        "from": today.isoformat(),
        "to": end.isoformat(),
    })
    if not isinstance(data, list):
        return set()
    return {row.get("symbol", "").upper() for row in data if row.get("symbol")}


def get_current_pe(ticker: str) -> Optional[float]:
    """For exit checks — fetch live P/E ratio."""
    metrics = get_key_metrics_ttm(ticker)
    if not metrics:
        return None
    pe = metrics.get("peRatio") or metrics.get("peRatioTTM")
    return float(pe) if pe is not None else None


# ─────────────────────────────────────────────────────────────────────────
# 52-week decline (via Alpaca daily bars — no FMP quota cost)
# ─────────────────────────────────────────────────────────────────────────

def get_52w_decline_pct(ticker: str) -> Optional[Decimal]:
    """
    Returns positive percent decline from 52-week high to current close.
    e.g., stock at $80 with $100 52w-high → returns Decimal("20.0").
    """
    alpaca = tc.get_alpaca()
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=400)  # 52w of trading days + holiday margin

    try:
        from alpaca_trade_api import TimeFrame
        bars_iter = alpaca.get_bars(
            ticker, TimeFrame.Day,
            start=start.isoformat(), end=end.isoformat(),
            adjustment="all",
        )
        bars = [b for b in bars_iter]
        if not bars or len(bars) < 20:
            return None
        # Use close prices for both peak and current
        closes = [Decimal(str(b.c)) for b in bars]
        peak = max(closes)
        current = closes[-1]
        if peak == 0:
            return None
        decline = (peak - current) / peak * Decimal("100")
        return decline
    except Exception as e:
        logger.debug(f"{ticker}: 52w-decline fetch failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────
# Per-candidate filter chain
# ─────────────────────────────────────────────────────────────────────────

def passes_full_buffett_filter(
    ticker: str, screener_row: dict, filters: dict,
    upcoming_earnings: set[str]
) -> Optional[dict]:
    """
    Returns enriched candidate dict if passes ALL Buffett criteria, None otherwise.
    """
    # Earnings risk — fast reject
    if ticker in upcoming_earnings:
        return None

    # Pull TTM metrics for P/B, D/E, ROE (P/E we already have from screener,
    # but re-confirm for freshness)
    metrics = get_key_metrics_ttm(ticker)
    if not metrics:
        return None

    pe = metrics.get("peRatio") or metrics.get("peRatioTTM") or screener_row.get("pe")
    pb = metrics.get("pbRatio") or metrics.get("pbRatioTTM")
    de = metrics.get("debtToEquity") or metrics.get("debtToEquityTTM")
    roe = metrics.get("roe") or metrics.get("returnOnEquity") or metrics.get("roeTTM")

    if any(v is None for v in (pe, pb, de, roe)):
        return None
    try:
        pe = float(pe); pb = float(pb); de = float(de); roe = float(roe)
    except (TypeError, ValueError):
        return None

    if pe >= filters["pe_max"]:        return None
    if pb >= filters["pb_max"]:        return None
    if de >= filters["debt_equity_max"]: return None
    if roe <= filters["roe_min"]:      return None

    # 52w decline — last check (most expensive — Alpaca bar fetch)
    decline = get_52w_decline_pct(ticker)
    if decline is None or decline < Decimal(str(filters["decline_52w_min_pct"])):
        return None

    return {
        "ticker": ticker,
        "pe": pe,
        "pb": pb,
        "debt_equity": de,
        "roe": roe,
        "decline_52w_pct": float(decline),
        "price": float(screener_row.get("price", 0) or 0),
        "company": screener_row.get("companyName", ""),
        "sector": screener_row.get("sector", ""),
    }


# ─────────────────────────────────────────────────────────────────────────
# Exit logic — P/E > 25 OR 20% stop OR 90-day hold
# ─────────────────────────────────────────────────────────────────────────

def process_exits(rules: dict) -> None:
    open_trades = tc.get_open_trades(STRATEGY_ID)
    if not open_trades:
        return

    limits = rules["position_limits"]
    stop_pct = Decimal(str(limits["stop_loss_pct"]))
    hold_days = int(limits["max_hold_days"])
    exit_pe_max = float(limits["exit_pe_max"])
    now = datetime.now(timezone.utc)

    alpaca = tc.get_alpaca()

    for t in open_trades:
        ticker = t["ticker"]
        entry_time = t["entry_time"]
        if entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)
        entry_price = Decimal(str(t["entry_price"]))

        # 1. 90-day hold
        age = now - entry_time
        if age >= timedelta(days=hold_days):
            _exit_at_market(t, ticker, tc.ExitReason.TIME_EXIT,
                            f"hold_days={hold_days} expired (age={age.days}d)")
            continue

        # 2. Stop loss
        try:
            bar = alpaca.get_latest_trade(ticker)
            current = Decimal(str(bar.price))
        except Exception as e:
            logger.warning(f"Exit check {ticker}: price unavailable ({e})")
            continue
        stop_level = entry_price * (Decimal("1") - stop_pct)
        if current <= stop_level:
            _exit_at_market(t, ticker, tc.ExitReason.STOP_LOSS,
                            f"price ${current} ≤ stop ${stop_level:.2f} "
                            f"({stop_pct*100:.0f}% stop)")
            continue

        # 3. P/E exceeded — mean reversion done
        pe = get_current_pe(ticker)
        if pe is not None and pe > exit_pe_max:
            _exit_at_market(t, ticker, tc.ExitReason.TARGET,
                            f"P/E reached {pe:.2f} > {exit_pe_max} — mean-reversion complete")
            continue


def _exit_at_market(trade: dict, ticker: str, reason: tc.ExitReason, reason_str: str) -> None:
    qty = int(trade["qty"])
    try:
        alpaca = tc.get_alpaca()
        order = alpaca.submit_order(symbol=ticker, qty=qty, side="sell",
                                    type="market", time_in_force="day")
        fill = Decimal(str(order.filled_avg_price or alpaca.get_latest_trade(ticker).price))
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
# Entry logic
# ─────────────────────────────────────────────────────────────────────────

def process_entries(rules: dict, allocation: Decimal) -> None:
    filters = rules["filters"]
    limits = rules["position_limits"]
    max_positions = int(limits["max_positions"])

    open_trades = tc.get_open_trades(STRATEGY_ID)
    open_tickers = {t["ticker"] for t in open_trades}
    open_count = len(open_trades)
    if open_count >= max_positions:
        logger.info(f"At position limit ({open_count}/{max_positions}); no new entries")
        return

    # 1. Bulk pre-filter via screener
    screener_results = get_screener_candidates(filters)
    if not screener_results:
        logger.info("Screener returned no candidates; no entries this cycle")
        return

    # 2. Earnings calendar (one bulk call)
    upcoming = get_upcoming_earnings(int(filters["no_earnings_within_days"]))
    logger.info(f"Upcoming earnings in next {filters['no_earnings_within_days']}d: {len(upcoming)} tickers")

    # 3. Per-candidate full Buffett filter
    qualifying: list[dict] = []
    for row in screener_results:
        ticker = (row.get("symbol") or "").upper()
        if not ticker or ticker in open_tickers:
            continue
        if not ticker.isalpha():  # skip BRK.B-style multi-class tickers (Alpaca may not trade them)
            continue
        candidate = passes_full_buffett_filter(ticker, row, filters, upcoming)
        if candidate:
            qualifying.append(candidate)
            logger.info(
                f"PASS {ticker}: P/E={candidate['pe']:.2f} P/B={candidate['pb']:.2f} "
                f"D/E={candidate['debt_equity']:.2f} ROE={candidate['roe']:.2%} "
                f"decline={candidate['decline_52w_pct']:.1f}%"
            )

    if not qualifying:
        logger.info("No candidates passed full Buffett filter this cycle")
        return

    # 4. Rank — operator's spec doesn't specify ordering, use lowest P/E (deepest value)
    qualifying.sort(key=lambda c: c["pe"])

    slots = max_positions - open_count
    selected = qualifying[:slots]
    logger.info(f"Found {len(qualifying)} qualifying candidates; taking top {len(selected)} by P/E")

    # 5. Place orders
    alpaca = tc.get_alpaca()
    for c in selected:
        ticker = c["ticker"]

        # Re-fetch live price (screener prices can be stale)
        try:
            bar = alpaca.get_latest_trade(ticker)
            price = Decimal(str(bar.price))
        except Exception as e:
            logger.warning(f"Skip {ticker}: no live price ({e})")
            continue

        # Equal weight
        per_position = allocation / Decimal(max_positions)
        qty = int(per_position / price)
        if qty < 1:
            logger.info(f"Skip {ticker}: position size <1 share at ${price}")
            continue

        stop_loss = price * (Decimal("1") - Decimal(str(limits["stop_loss_pct"])))

        signal_id = tc.log_signal(
            STRATEGY_ID, ticker, tc.Action.BUY,
            reason=f"value screen pass — P/E {c['pe']:.2f} P/B {c['pb']:.2f} "
                   f"D/E {c['debt_equity']:.2f} ROE {c['roe']:.2%} -{c['decline_52w_pct']:.1f}% from 52wH",
            value=c["pe"],
            acted_on=True,
            raw_payload=c,
        )

        try:
            order = tc.place_order(
                strategy_id=STRATEGY_ID,
                ticker=ticker,
                side=tc.Action.BUY,
                qty=qty,
                order_type="market",
                signal_id=signal_id,
                stop_loss=stop_loss,
                metadata={
                    "entry_pe": c["pe"], "entry_pb": c["pb"],
                    "entry_debt_equity": c["debt_equity"],
                    "entry_roe": c["roe"],
                    "entry_52w_decline_pct": c["decline_52w_pct"],
                    "company": c.get("company", ""),
                    "sector": c.get("sector", ""),
                },
            )
            logger.info(f"BUY {ticker} {qty}@${price} P/E={c['pe']:.2f} "
                        f"(order={order['alpaca_order_id']})")
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
    for k in ("filters", "position_limits"):
        rules[k] = {**DEFAULTS[k], **rules.get(k, {})}

    allocation = tc.get_rls_capital_allocation(STRATEGY_ID)
    if allocation <= 0:
        logger.warning(f"Allocation is ${allocation} — refusing to run")
        return 1

    env = tc._load_env()
    if not env.get("fmp_key"):
        logger.error("FMP_API_KEY not in env — strategy_value cannot run")
        tc.update_strategy_status(STRATEGY_ID, "error",
                                  "FMP_API_KEY not configured")
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

            # Exits first to free capacity
            process_exits(rules)

            # Then entries — screener + key-metrics + 52w decline + earnings filter
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
