#!/usr/bin/env python3
"""
strategy_value.py — BHN Strategy 2: Buffett Value Screening.

Iterates a fixed universe (rules.json strat_2_value.universe) every cycle and
applies the deep-value filter. ALL criteria must be true:
  - P/E ratio < pe_max (default 15)
  - P/B ratio < pb_max (default 1.5)
  - Debt/Equity < de_max (default 0.5)
  - ROE > roe_min (default 15%)
  - 52-week decline >= decline_52w_min_pct (default 10%)
  - No earnings in next earnings_blackout_days (default 7)

Equal-weight position sizing. Capital allocation: $25,000.
Exit: P/E exceeds pe_target_max (default 25) OR stop_loss_pct hit OR
max_hold_days elapsed.

Cadence: daily post-market (17:00 ET via cron).

FMP free-tier endpoints (250 calls/day budget):
  - GET /profile/{symbol}                  ← company name, sector, price
  - GET /ratios-ttm/{symbol}               ← P/E, P/B, ROE, dividend yield
  - GET /balance-sheet-statement/{symbol}  ← totalDebt + totalEquity → D/E
  - GET /earning_calendar                  ← 1 bulk call covers entire window
At 20-symbol default universe: 20×3 + 1 = 61 calls per cycle.

NB: the previous /stock-screener-based implementation required a paid FMP
plan. This file replaces that with the fixed-universe-from-rules approach.
52-week decline still comes from Alpaca daily bars (no FMP quota cost).
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

# Hardcoded defaults — overridden by rules.json strat_2_value block.
# Key names align with rules_schema.STRAT_2_VALUE_SCHEMA so the merge is
# a plain dict update, not a translation layer.
DEFAULTS = {
    "screener_filters": {
        "pe_max":              15.0,
        "pb_max":              1.5,
        "de_max":              0.5,
        "roe_min":             15.0,    # ROE threshold IS percentage points (15 = 15%)
        "decline_52w_min_pct": 0.10,    # fractional (0-1)
    },
    "exit": {
        "hold_days":     90,
        "stop_loss_pct": 0.20,
        "pe_target_max": 25.0,
    },
    "position": {
        "max_positions": 6,
    },
    "earnings_blackout_days": 7,
    "universe": [],   # operator-provided in rules.json; empty = nothing to evaluate
}


# ─────────────────────────────────────────────────────────────────────────
# FMP free-tier endpoints
# ─────────────────────────────────────────────────────────────────────────

def fmp_get(endpoint: str, params: Optional[dict] = None) -> Optional[Any]:
    """GET against FMP with retries + rate-limit handling."""
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


def get_profile(ticker: str) -> Optional[dict]:
    """GET /profile/{symbol} — companyName, sector, price."""
    data = fmp_get(f"/profile/{ticker}")
    return data[0] if isinstance(data, list) and data else None


def get_ratios_ttm(ticker: str) -> Optional[dict]:
    """GET /ratios-ttm/{symbol} — P/E, P/B, ROE, dividend yield (all TTM)."""
    data = fmp_get(f"/ratios-ttm/{ticker}")
    return data[0] if isinstance(data, list) and data else None


def get_balance_sheet(ticker: str) -> Optional[dict]:
    """GET /balance-sheet-statement/{symbol} — most-recent annual report.
    We compute D/E from totalDebt + totalStockholdersEquity ourselves."""
    data = fmp_get(f"/balance-sheet-statement/{ticker}", {"limit": 1})
    return data[0] if isinstance(data, list) and data else None


def get_upcoming_earnings(within_days: int) -> set[str]:
    """One bulk /earning_calendar call covers the entire window for the whole
    market. Returns the set of tickers reporting earnings within the window."""
    today = date.today()
    end = today + timedelta(days=within_days)
    data = fmp_get("/earning_calendar", {
        "from": today.isoformat(),
        "to":   end.isoformat(),
    })
    if not isinstance(data, list):
        return set()
    return {row.get("symbol", "").upper() for row in data if row.get("symbol")}


# ─────────────────────────────────────────────────────────────────────────
# 52-week decline (Alpaca daily bars — no FMP quota cost)
# ─────────────────────────────────────────────────────────────────────────

def get_52w_decline_pct(ticker: str) -> Optional[Decimal]:
    """Returns positive fractional decline from 52-week high to current close.
    e.g. peak $100, current $80 → Decimal("0.20")."""
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
        bars = list(bars_iter)
        if not bars or len(bars) < 20:
            return None
        closes = [Decimal(str(b.c)) for b in bars]
        peak = max(closes)
        current = closes[-1]
        if peak == 0:
            return None
        return (peak - current) / peak  # fractional 0-1
    except Exception as e:
        logger.debug(f"{ticker}: 52w-decline fetch failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────
# Per-symbol Buffett filter
# ─────────────────────────────────────────────────────────────────────────

def _compute_de_ratio(bs: dict) -> Optional[float]:
    """totalDebt / totalStockholdersEquity. FMP's field names vary slightly
    by tier and statement vintage; fall back across known variants."""
    debt = (bs.get("totalDebt")
            or bs.get("totalLongTermDebt")
            or bs.get("longTermDebt"))
    equity = (bs.get("totalStockholdersEquity")
              or bs.get("totalEquity"))
    if debt is None or equity is None:
        return None
    try:
        debt = float(debt); equity = float(equity)
    except (TypeError, ValueError):
        return None
    if equity <= 0:
        return None  # negative-equity firms get filtered out as undefined
    return debt / equity


def passes_buffett_filter(
    ticker: str,
    upcoming_earnings: set[str],
    filters: dict,
) -> Optional[dict]:
    """Returns enriched candidate dict if every Buffett criterion passes,
    None otherwise. Fetches 3 FMP endpoints + 1 Alpaca daily-bars call."""
    if ticker in upcoming_earnings:
        logger.debug(f"{ticker}: skip (earnings within blackout window)")
        return None

    profile = get_profile(ticker)
    if not profile:
        logger.debug(f"{ticker}: skip (no profile)")
        return None

    ratios = get_ratios_ttm(ticker)
    if not ratios:
        logger.debug(f"{ticker}: skip (no ratios-ttm)")
        return None

    pe  = ratios.get("peRatioTTM") or ratios.get("peRatio")
    pb  = (ratios.get("priceToBookRatioTTM")
           or ratios.get("priceBookValueRatioTTM")
           or ratios.get("pbRatio"))
    roe = ratios.get("returnOnEquityTTM") or ratios.get("returnOnEquity")
    div_yield = (ratios.get("dividendYielTTM")        # FMP's actual misspelling
                 or ratios.get("dividendYieldTTM"))

    if pe is None or pb is None or roe is None:
        logger.debug(f"{ticker}: skip (ratios incomplete: pe={pe}, pb={pb}, roe={roe})")
        return None
    try:
        pe = float(pe); pb = float(pb); roe = float(roe)
    except (TypeError, ValueError):
        return None

    # FMP returns ROE as a fraction (0.18 = 18%); the schema expects the
    # filter threshold in percentage points (15 = 15%). Coerce ROE to %.
    roe_pct = roe * 100.0 if abs(roe) < 1.5 else roe

    bs = get_balance_sheet(ticker)
    if not bs:
        logger.debug(f"{ticker}: skip (no balance-sheet-statement)")
        return None
    de = _compute_de_ratio(bs)
    if de is None:
        logger.debug(f"{ticker}: skip (D/E unavailable from balance sheet)")
        return None

    if pe >= filters["pe_max"]:
        logger.debug(f"{ticker}: skip (P/E {pe:.2f} ≥ {filters['pe_max']})")
        return None
    if pb >= filters["pb_max"]:
        logger.debug(f"{ticker}: skip (P/B {pb:.2f} ≥ {filters['pb_max']})")
        return None
    if de >= filters["de_max"]:
        logger.debug(f"{ticker}: skip (D/E {de:.2f} ≥ {filters['de_max']})")
        return None
    if roe_pct <= filters["roe_min"]:
        logger.debug(f"{ticker}: skip (ROE {roe_pct:.1f}% ≤ {filters['roe_min']}%)")
        return None

    decline = get_52w_decline_pct(ticker)
    if decline is None or decline < Decimal(str(filters["decline_52w_min_pct"])):
        logger.debug(
            f"{ticker}: skip (52w decline {decline} < {filters['decline_52w_min_pct']})"
        )
        return None

    return {
        "ticker":              ticker,
        "pe":                  pe,
        "pb":                  pb,
        "debt_equity":         de,
        "roe":                 roe_pct / 100.0,   # store as fraction for consistency
        "decline_52w_pct":     float(decline),
        "price":               float(profile.get("price", 0) or 0),
        "company":             profile.get("companyName", ""),
        "sector":              profile.get("sector", ""),
        "dividend_yield_ttm":  float(div_yield) if div_yield is not None else None,
    }


# ─────────────────────────────────────────────────────────────────────────
# Exit logic — P/E target OR stop loss OR hold_days timeout
# ─────────────────────────────────────────────────────────────────────────

def process_exits(rules: dict) -> None:
    open_trades = tc.get_open_trades(STRATEGY_ID)
    if not open_trades:
        return

    exit_cfg = rules["exit"]
    stop_pct = Decimal(str(exit_cfg["stop_loss_pct"]))
    hold_days = int(exit_cfg["hold_days"])
    pe_target_max = float(exit_cfg["pe_target_max"])
    now = datetime.now(timezone.utc)

    alpaca = tc.get_alpaca()

    for t in open_trades:
        ticker = t["ticker"]
        entry_time = t["entry_time"]
        if entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)
        entry_price = Decimal(str(t["entry_price"]))

        # 1. hold_days timeout
        age = now - entry_time
        if age >= timedelta(days=hold_days):
            _exit_at_market(t, ticker, tc.ExitReason.TIME_EXIT,
                            f"hold_days={hold_days} expired (age={age.days}d)")
            continue

        # 2. stop loss
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

        # 3. P/E target reached — mean reversion done
        ratios = get_ratios_ttm(ticker)
        pe_val = (ratios or {}).get("peRatioTTM") or (ratios or {}).get("peRatio")
        if pe_val is None:
            continue
        try:
            pe_val = float(pe_val)
        except (TypeError, ValueError):
            continue
        if pe_val > pe_target_max:
            _exit_at_market(t, ticker, tc.ExitReason.TARGET,
                            f"P/E {pe_val:.2f} > {pe_target_max} — mean reversion complete")


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
# Entry logic — iterate the fixed universe
# ─────────────────────────────────────────────────────────────────────────

def process_entries(rules: dict, allocation: Decimal) -> None:
    universe = rules.get("universe") or []
    if not universe:
        logger.info("Universe empty — nothing to evaluate this cycle")
        return

    filters = rules["screener_filters"]
    pos_cfg = rules["position"]
    max_positions = int(pos_cfg["max_positions"])
    blackout_days = int(rules.get("earnings_blackout_days", 7))

    open_trades = tc.get_open_trades(STRATEGY_ID)
    open_tickers = {t["ticker"] for t in open_trades}
    open_count = len(open_trades)
    if open_count >= max_positions:
        logger.info(f"At position limit ({open_count}/{max_positions}); no new entries")
        return

    # One bulk call covers earnings for the whole market.
    upcoming = get_upcoming_earnings(blackout_days)
    logger.info(
        f"Upcoming earnings in next {blackout_days}d: {len(upcoming)} tickers market-wide"
    )

    qualifying: list[dict] = []
    for ticker in universe:
        ticker = ticker.upper()
        if ticker in open_tickers:
            continue
        candidate = passes_buffett_filter(ticker, upcoming, filters)
        if candidate:
            qualifying.append(candidate)
            logger.info(
                f"PASS {ticker}: P/E={candidate['pe']:.2f} P/B={candidate['pb']:.2f} "
                f"D/E={candidate['debt_equity']:.2f} ROE={candidate['roe']:.2%} "
                f"decline={candidate['decline_52w_pct']:.2%}"
            )

    if not qualifying:
        logger.info("No universe candidates passed the Buffett filter this cycle")
        return

    # Rank deepest-value first by P/E.
    qualifying.sort(key=lambda c: c["pe"])

    slots = max_positions - open_count
    selected = qualifying[:slots]
    logger.info(f"{len(qualifying)} qualifying / taking top {len(selected)} by P/E")

    alpaca = tc.get_alpaca()
    stop_pct = Decimal(str(rules["exit"]["stop_loss_pct"]))

    for c in selected:
        ticker = c["ticker"]

        # Live price (profile.price can be stale)
        try:
            bar = alpaca.get_latest_trade(ticker)
            price = Decimal(str(bar.price))
        except Exception as e:
            logger.warning(f"Skip {ticker}: no live price ({e})")
            continue

        per_position = allocation / Decimal(max_positions)
        qty = int(per_position / price)
        if qty < 1:
            logger.info(f"Skip {ticker}: position size <1 share at ${price}")
            continue

        stop_loss = price * (Decimal("1") - stop_pct)

        signal_id = tc.log_signal(
            STRATEGY_ID, ticker, tc.Action.BUY,
            reason=(f"value-screen pass — P/E {c['pe']:.2f} P/B {c['pb']:.2f} "
                    f"D/E {c['debt_equity']:.2f} ROE {c['roe']:.2%} "
                    f"-{c['decline_52w_pct']:.2%} from 52wH"),
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
                    "entry_pe":              c["pe"],
                    "entry_pb":              c["pb"],
                    "entry_debt_equity":     c["debt_equity"],
                    "entry_roe":             c["roe"],
                    "entry_52w_decline_pct": c["decline_52w_pct"],
                    "company":               c.get("company", ""),
                    "sector":                c.get("sector", ""),
                    "dividend_yield_ttm":    c.get("dividend_yield_ttm"),
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

def _merge_rules(defaults: dict, overrides: dict) -> dict:
    """Shallow merge defaults + overrides, with the three known nested
    subblocks (screener_filters, exit, position) merged at the field level
    so a partial override doesn't blow away unrelated defaults."""
    merged: dict = {**defaults, **overrides}
    for k in ("screener_filters", "exit", "position"):
        if k in defaults or k in overrides:
            merged[k] = {**defaults.get(k, {}), **overrides.get(k, {})}
    return merged


def main() -> int:
    allowed, reason = tc.should_run(STRATEGY_ID)
    if not allowed:
        logger.info(f"Skipping run: {reason}")
        return 0

    overrides = tc.get_strategy_rules(STRATEGY_ID) or {}
    rules = _merge_rules(DEFAULTS, overrides)

    allocation = tc.get_rls_capital_allocation(STRATEGY_ID)
    if allocation <= 0:
        logger.warning(f"Allocation is ${allocation} — refusing to run")
        return 1

    env = tc._load_env()
    if not env.get("fmp_key"):
        logger.error("FMP_API_KEY not in env — strategy_value cannot run")
        tc.update_strategy_status(STRATEGY_ID, "error", "FMP_API_KEY not configured")
        return 1

    universe_size = len(rules.get("universe") or [])
    logger.info(
        f"=== {STRATEGY_ID} cycle start (allocation=${allocation}, "
        f"universe={universe_size}, expected FMP calls ≤ {universe_size * 3 + 1}) ==="
    )

    with tc.pg_advisory_lock(abs(hash(STRATEGY_ID)) % (2**31)):
        try:
            with tc.get_pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE trading_strategies SET last_run_at = NOW() WHERE id = %s",
                        (STRATEGY_ID,),
                    )

            process_exits(rules)
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
