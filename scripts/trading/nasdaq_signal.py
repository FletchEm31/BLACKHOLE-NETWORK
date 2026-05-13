#!/usr/bin/env python3
"""
nasdaq_signal.py — Shared signal computation for Strat 6 (NASDAQ-LONG) + Strat 7 (NASDAQ-SHORT).

PORTED FROM QC `Two_2Algorithm` — signal logic identical. Only plumbing
changed: QC's History()/AddEquity()/Linear-algebra calls are replaced with
Alpaca bar fetches + pure-Python OLS. The QC source lives in operator
records; this is the line-by-line port.

Mechanic (verbatim from QC):
  * Universe ranked: QLD, PSQ, QID (three ETFs, that's it — NOT QQQ/SPY/JPST)
  * Reference series: SQQQ (the regression target — independent variable)
  * Lookback: 18 daily bars of OPEN prices (QC uses 'open', not 'close')
  * Returns: percent returns, np.diff(a) / a[:-1]
  * Regression: solve b = slope·a + intercept where
        a = ETF percent returns
        b = SQQQ percent returns
    via np.linalg.lstsq([a, ones], b) → coef[0]=slope, coef[1]=intercept
  * Score per ETF = the INTERCEPT (coef[1]), not the slope
  * Rank scores DESCENDING; leader = highest intercept
  * If leader == QLD → LONG signal fires (go 99% QLD)
  * If leader != QLD → PARK signal fires (go 99% JPST)
  * Rebalance Monday, 10 minutes after market open

What the intercept means here: when ETF returns are zero on average,
what's SQQQ's expected return? A high positive intercept means SQQQ
returned positive even when this ETF was flat — i.e. SQQQ is detached
from this ETF. For QLD (long QQQ) → high intercept = bear regime (SQQQ
rallying while QLD flat). For PSQ/QID (short QQQ) → high intercept = also
bear regime. The QC strategy ranks intercepts and takes long QLD only
when QLD's intercept exceeds PSQ's and QID's — interpreted as "the bear
narrative is no longer pricing in pure short ETFs first."

Departures from the summary spec (intentional — QC source is truth per
operator's 'Keep strategy logic identical' directive):
  - NO SPY 200-day MA filter (summary said required; not in QC)
  - NO 20% gap rule between leader and runner-up (summary said required; not in QC)
  - NO 50/50 QQQ/JPST split (QC parks 99% in single name)
  - Universe ranked is exactly 3 names (summary listed 7)

Strat 7 (NASDAQ-SHORT) has no QC source — its short-side logic is
operator-spec only. The short signal builder in this module mirrors the
QC ranking pattern but applies the operator's separate entry condition.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import trading_core as tc


# QC source: self.tickers = ["QLD", "PSQ", "QID"]
UNIVERSE_RANKED = ("QLD", "PSQ", "QID")
REGRESSION_TARGET = "SQQQ"
LONG_LEADER = "QLD"
PARK_TICKER = "JPST"
SHORT_LEADERS = ("PSQ", "QID")            # Strat 7 only (no QC source)
BENCHMARK_TICKER = "SPY"                  # Strat 7 200MA overlay (no QC source)

REGRESSION_LOOKBACK_DAYS = 18             # QC: self.lookback = 18
TARGET_HOLDING_FRACTION = 0.99            # QC: SetHoldings(symbol, 0.99)

# Strat 7 short-side parameters (operator summary only — no QC source)
SPY_MA_PERIOD = 200
SPY_BELOW_MA_DAYS_REQUIRED_FOR_SHORT = 5
# "No 3 consecutive closes above 200MA recently" — operator-specified third entry
# filter. "Recently" interpreted as the last 20 trading days (~1 calendar month).
# Adjustable via rules.json if the operator wants a different window.
SPY_NO_BREAK_LOOKBACK_DAYS = 20
SPY_NO_BREAK_STREAK_MAX = 3


@dataclass
class RegressionScore:
    ticker: str
    intercept: float
    slope: float
    n_observations: int


@dataclass
class NasdaqSignal:
    """Result of one rank+decide cycle. Always returned even when no signal fires
    so the caller can log the reasoning + audit metadata."""
    fires: bool
    direction: str                        # "long" | "park" | "short" | "none"
    target_ticker: Optional[str]          # what to hold if fires
    leader: Optional[str]
    leader_score: Optional[float]
    runner_up: Optional[str]
    runner_up_score: Optional[float]
    rankings: list[RegressionScore]
    spy_close: Optional[float] = None     # captured for metadata, NOT a filter (QC has no filter)
    spy_ma_200: Optional[float] = None
    spy_above_ma: Optional[bool] = None
    spy_days_below_ma_streak: Optional[int] = None
    reason: str = ""


# ─────────────────────────────────────────────────────────────────────────
# Bar fetching — QC uses .History(symbols, lookback, Resolution.Daily)["open"]
# ─────────────────────────────────────────────────────────────────────────

def fetch_daily_opens(ticker: str, lookback_days: int) -> Optional[list[tuple]]:
    """Returns [(date, open_float), ...] oldest-first, or None on failure.
    QC pulls the OPEN price column from its History DataFrame; we mirror that
    so the regression input is identical."""
    alpaca = tc.get_alpaca()
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=int(lookback_days * 1.5) + 5)
    try:
        from alpaca_trade_api import TimeFrame
        bars = list(alpaca.get_bars(
            ticker, TimeFrame.Day,
            start=start.isoformat(), end=end.isoformat(),
            adjustment="all",
        ))
        if not bars:
            return None
        return [(
            b.t.date() if hasattr(b.t, "date") else None,
            float(b.o),
        ) for b in bars]
    except Exception:
        return None


def pct_returns(opens: list[float]) -> list[float]:
    """np.diff(a) / a[:-1] — verbatim from QC RegressionScore()."""
    out = []
    for i in range(1, len(opens)):
        prev = opens[i-1]
        if prev <= 0:
            return []
        out.append((opens[i] - prev) / prev)
    return out


def align_series(a: list[tuple], b: list[tuple]) -> tuple[list[float], list[float]]:
    """Inner-join two [(date, value)] series on date. Preserves chronological order."""
    map_a = dict(a)
    map_b = dict(b)
    common = sorted(set(map_a.keys()) & set(map_b.keys()))
    return [map_a[d] for d in common], [map_b[d] for d in common]


# ─────────────────────────────────────────────────────────────────────────
# OLS via lstsq-equivalent — QC: np.linalg.lstsq([[a, 1]], b)
# We solve the same system in pure Python (no numpy dependency in trading dir):
#     b = slope · a + intercept
# Closed form:
#     slope     = cov(a, b) / var(a)
#     intercept = mean(b) - slope · mean(a)
# Numerically equivalent to lstsq for this 2-parameter system.
# ─────────────────────────────────────────────────────────────────────────

def regression_score(a: list[float], b: list[float]) -> Optional[tuple[float, float]]:
    """Returns (slope, intercept) for b = slope·a + intercept, or None on
    degenerate input. Mirrors QC's RegressionScore() which returns the
    intercept (coef[1] from lstsq) — we return both so the caller can log."""
    n = len(a)
    if n < 2 or len(b) != n:
        return None
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    var_a = sum((ai - mean_a) ** 2 for ai in a)
    if var_a == 0:
        return None
    cov_ab = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
    slope = cov_ab / var_a
    intercept = mean_b - slope * mean_a
    return slope, intercept


# ─────────────────────────────────────────────────────────────────────────
# Per-ETF score against SQQQ
# ─────────────────────────────────────────────────────────────────────────

def compute_scores(universe: tuple[str, ...] = UNIVERSE_RANKED,
                    lookback_days: int = REGRESSION_LOOKBACK_DAYS) -> list[RegressionScore]:
    """For each ticker in universe: regress SQQQ pct-returns onto ETF pct-returns
    over the lookback window. Return list sorted by intercept DESCENDING
    (leader first). Mirrors QC's `scores.sort(key=lambda x: x[1], reverse=True)`
    where x[1] is the intercept."""
    sqqq_series = fetch_daily_opens(REGRESSION_TARGET, lookback_days + 5)
    if not sqqq_series:
        return []
    sqqq_dated = [(d, p) for d, p in sqqq_series if d is not None][-(lookback_days + 1):]
    if len(sqqq_dated) < 3:
        return []
    sqqq_returns_dated = [
        (sqqq_dated[i][0], (sqqq_dated[i][1] - sqqq_dated[i-1][1]) / sqqq_dated[i-1][1])
        for i in range(1, len(sqqq_dated))
        if sqqq_dated[i-1][1] > 0
    ]

    scores: list[RegressionScore] = []
    for ticker in universe:
        series = fetch_daily_opens(ticker, lookback_days + 5)
        if not series:
            continue
        dated = [(d, p) for d, p in series if d is not None][-(lookback_days + 1):]
        if len(dated) < 3:
            continue
        rets_dated = [
            (dated[i][0], (dated[i][1] - dated[i-1][1]) / dated[i-1][1])
            for i in range(1, len(dated))
            if dated[i-1][1] > 0
        ]
        # QC's regression: x = [etf_returns, 1], b = sqqq_returns
        # → b = slope·etf_returns + intercept
        a, b = align_series(rets_dated, sqqq_returns_dated)
        if len(a) < 3:
            continue
        result = regression_score(a, b)
        if result is None:
            continue
        slope, intercept = result
        scores.append(RegressionScore(
            ticker=ticker, intercept=intercept, slope=slope, n_observations=len(a),
        ))

    # QC: scores.sort(key=lambda x: x[1], reverse=True) — descending by intercept
    scores.sort(key=lambda s: s.intercept, reverse=True)
    return scores


# ─────────────────────────────────────────────────────────────────────────
# SPY 200MA overlay (Strat 7 only — operator spec, not in QC)
# ─────────────────────────────────────────────────────────────────────────

def spy_200ma_state() -> Optional[dict]:
    """Return SPY's 200MA state: today's close + MA, above/below flag,
    consecutive-days-below streak ending today, and max-consecutive-days-above
    streak observed within the last SPY_NO_BREAK_LOOKBACK_DAYS trading days.

    The last field implements the operator's third short-side entry filter:
    "SPY has NOT had 3 consecutive closes above 200MA recently." We interpret
    'recently' as the last 20 trading days (configurable via the module
    constant SPY_NO_BREAK_LOOKBACK_DAYS).
    """
    series = fetch_daily_opens(BENCHMARK_TICKER, SPY_MA_PERIOD + SPY_NO_BREAK_LOOKBACK_DAYS + 10)
    if not series or len(series) < SPY_MA_PERIOD + 1:
        return None
    closes = [p for _, p in series]
    ma_today = sum(closes[-SPY_MA_PERIOD:]) / SPY_MA_PERIOD
    close_today = closes[-1]

    # Consecutive days below 200MA, ending today
    below_streak = 0
    for i in range(len(closes) - 1, SPY_MA_PERIOD - 1, -1):
        window = closes[i - SPY_MA_PERIOD + 1:i + 1]
        ma_i = sum(window) / SPY_MA_PERIOD
        if closes[i] < ma_i:
            below_streak += 1
        else:
            break

    # Max consecutive ABOVE-MA streak within the last N trading days
    max_above_streak_recent = 0
    current_above_run = 0
    lookback_start = max(SPY_MA_PERIOD, len(closes) - SPY_NO_BREAK_LOOKBACK_DAYS)
    for i in range(lookback_start, len(closes)):
        window = closes[i - SPY_MA_PERIOD + 1:i + 1]
        ma_i = sum(window) / SPY_MA_PERIOD
        if closes[i] > ma_i:
            current_above_run += 1
            if current_above_run > max_above_streak_recent:
                max_above_streak_recent = current_above_run
        else:
            current_above_run = 0

    return {
        "close": close_today,
        "ma_200": ma_today,
        "above_ma": close_today > ma_today,
        "days_below_streak": below_streak,
        "max_above_streak_recent": max_above_streak_recent,
    }


# ─────────────────────────────────────────────────────────────────────────
# Composed signals
# ─────────────────────────────────────────────────────────────────────────

def evaluate_long_signal(scores: Optional[list[RegressionScore]] = None,
                          spy: Optional[dict] = None) -> NasdaqSignal:
    """QC behavior: if QLD is the highest-intercept ticker, fire LONG (99% QLD).
    Otherwise, fire PARK (99% JPST). There is no 'no signal' state in the QC
    code — it's always invested in something. We expose the SPY 200MA for
    audit metadata only (not used as a filter — QC doesn't have one)."""
    if scores is None:
        scores = compute_scores()
    if spy is None:
        spy = spy_200ma_state()

    spy_payload = {
        "spy_close": spy["close"] if spy else None,
        "spy_ma_200": spy["ma_200"] if spy else None,
        "spy_above_ma": spy["above_ma"] if spy else None,
        "spy_days_below_ma_streak": spy["days_below_streak"] if spy else None,
    }

    if not scores:
        return NasdaqSignal(
            fires=False, direction="none", target_ticker=None,
            leader=None, leader_score=None, runner_up=None, runner_up_score=None,
            rankings=[], **spy_payload,
            reason="no regression data available",
        )

    leader = scores[0]
    runner_up = scores[1] if len(scores) > 1 else None
    if leader.ticker == LONG_LEADER:
        return NasdaqSignal(
            fires=True, direction="long", target_ticker=LONG_LEADER,
            leader=leader.ticker, leader_score=leader.intercept,
            runner_up=runner_up.ticker if runner_up else None,
            runner_up_score=runner_up.intercept if runner_up else None,
            rankings=scores, **spy_payload,
            reason=(f"QLD intercept {leader.intercept:+.6f} ranks #1 of "
                    f"{[s.ticker for s in scores]}"),
        )
    return NasdaqSignal(
        fires=True, direction="park", target_ticker=PARK_TICKER,
        leader=leader.ticker, leader_score=leader.intercept,
        runner_up=runner_up.ticker if runner_up else None,
        runner_up_score=runner_up.intercept if runner_up else None,
        rankings=scores, **spy_payload,
        reason=(f"{leader.ticker} ranks #1 (intercept {leader.intercept:+.6f}), "
                f"not QLD — parking in {PARK_TICKER}"),
    )


def evaluate_short_signal(scores: Optional[list[RegressionScore]] = None,
                           spy: Optional[dict] = None) -> NasdaqSignal:
    """Strat 7 — operator spec only, no QC source.
    Fires SHORT when:
      - PSQ or QID has the highest intercept (bear ETFs winning the ranking)
      - SPY is below its 200-day MA for ≥5 consecutive trading days
    Otherwise, no signal (Strat 7 has no park behavior — Strat 6 handles cash).
    """
    if scores is None:
        scores = compute_scores()
    if spy is None:
        spy = spy_200ma_state()

    spy_payload = {
        "spy_close": spy["close"] if spy else None,
        "spy_ma_200": spy["ma_200"] if spy else None,
        "spy_above_ma": spy["above_ma"] if spy else None,
        "spy_days_below_ma_streak": spy["days_below_streak"] if spy else None,
    }

    if not scores:
        return NasdaqSignal(
            fires=False, direction="none", target_ticker=None,
            leader=None, leader_score=None, runner_up=None, runner_up_score=None,
            rankings=[], **spy_payload, reason="no regression data available",
        )
    if spy is None:
        return NasdaqSignal(
            fires=False, direction="none", target_ticker=None,
            leader=scores[0].ticker, leader_score=scores[0].intercept,
            runner_up=scores[1].ticker if len(scores) > 1 else None,
            runner_up_score=scores[1].intercept if len(scores) > 1 else None,
            rankings=scores, **spy_payload, reason="SPY 200MA data unavailable",
        )

    leader = scores[0]
    runner_up = scores[1] if len(scores) > 1 else None
    streak = spy["days_below_streak"]
    max_above_recent = spy.get("max_above_streak_recent", 0)
    no_recent_break = max_above_recent < SPY_NO_BREAK_STREAK_MAX
    fires = (
        leader.ticker in SHORT_LEADERS
        and not spy["above_ma"]
        and streak >= SPY_BELOW_MA_DAYS_REQUIRED_FOR_SHORT
        and no_recent_break
    )
    if fires:
        reason = (f"{leader.ticker} #1 (intercept {leader.intercept:+.6f}); "
                  f"SPY below 200MA {streak}d ≥ "
                  f"{SPY_BELOW_MA_DAYS_REQUIRED_FOR_SHORT}d; max above-streak "
                  f"in last {SPY_NO_BREAK_LOOKBACK_DAYS}d = {max_above_recent} "
                  f"< {SPY_NO_BREAK_STREAK_MAX} required")
    elif leader.ticker not in SHORT_LEADERS:
        reason = f"leader is {leader.ticker} (intercept {leader.intercept:+.6f}), not PSQ/QID"
    elif spy["above_ma"]:
        reason = f"SPY {spy['close']:.2f} > 200MA {spy['ma_200']:.2f} — bear filter blocks"
    elif streak < SPY_BELOW_MA_DAYS_REQUIRED_FOR_SHORT:
        reason = (f"SPY below 200MA only {streak}d < "
                  f"{SPY_BELOW_MA_DAYS_REQUIRED_FOR_SHORT}d required")
    else:
        reason = (f"SPY had {max_above_recent} consecutive closes above 200MA in "
                  f"last {SPY_NO_BREAK_LOOKBACK_DAYS}d ≥ {SPY_NO_BREAK_STREAK_MAX} "
                  f"forbidden — bear regime not yet committed")

    return NasdaqSignal(
        fires=fires,
        direction="short" if fires else "none",
        target_ticker=leader.ticker if fires else None,
        leader=leader.ticker, leader_score=leader.intercept,
        runner_up=runner_up.ticker if runner_up else None,
        runner_up_score=runner_up.intercept if runner_up else None,
        rankings=scores, **spy_payload, reason=reason,
    )
