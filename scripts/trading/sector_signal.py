#!/usr/bin/env python3
"""
sector_signal.py — Indicators + scoring for Strat 8 (BHN-SECTOR-ROTATION).

PORTED FROM QC `TheOmniscientParadox` — indicator block + score formula
identical. QC's framework indicator objects (rocp/std/rsi/sma) are
replaced with pure-Python equivalents over Alpaca daily bars.

Universe (verbatim from QC):
  SOXL, TECL, TQQQ, FAS, ERX, UUP, TMF, BIL
  Last symbol (BIL) is the 'safe' / cash equivalent.

Per-symbol indicators (daily resolution, matching QC):
  roc_fast = ROCP(9)       — 9-day rate of change percent
  roc_med  = ROCP(21)      — 21-day rate of change percent
  roc_slow = ROCP(63)      — 63-day rate of change percent
  vol      = STD(21)       — 21-day standard deviation of CLOSES
                             (NOT returns — QC's std() applies to the
                             raw price series passed in; we mirror that)
  rsi      = RSI(14)       — 14-day Wilder's RSI
  sma      = SMA(50)       — 50-day simple moving average

SPY also tracked separately:
  spy_sma  = SMA(200)      — for the bear-market-defense overlay

Score formula (verbatim):
  weighted_mom = roc_fast*0.5 + roc_med*0.3 + roc_slow*0.2
  risk_adj_mom = weighted_mom / vol     (vol=1.0 if would-be-zero)
  trend_score  = 1.0 if price > sma else 0.5
  rsi_penalty  = 0.9 if rsi > 85 OR rsi < 30 else 1.0
  final_score  = risk_adj_mom × trend_score × rsi_penalty

The SAFE asset (BIL) is excluded from scoring (per QC: `if s == self.safe: continue`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import trading_core as tc


UNIVERSE_SECTOR = ("SOXL", "TECL", "TQQQ", "FAS", "ERX", "UUP", "TMF", "BIL")
SAFE_TICKER = "BIL"
BENCHMARK_TICKER = "SPY"

ROC_FAST_PERIOD = 9
ROC_MED_PERIOD = 21
ROC_SLOW_PERIOD = 63
VOL_PERIOD = 21
RSI_PERIOD = 14
SMA_PERIOD = 50
SPY_SMA_PERIOD = 200

# Score weights (operator-spec + QC: 0.5/0.3/0.2)
WEIGHT_FAST = 0.5
WEIGHT_MED = 0.3
WEIGHT_SLOW = 0.2

# RSI thresholds for penalty
RSI_OVERBOUGHT = 85
RSI_OVERSOLD = 30
RSI_PENALTY = 0.9

# Trend filter
TREND_BELOW_SMA_MULTIPLIER = 0.5
TREND_ABOVE_SMA_MULTIPLIER = 1.0

# Vol annualization (252 trading days/yr)
TRADING_DAYS_PER_YEAR = 252


@dataclass
class SectorScore:
    ticker: str
    price: float
    sma_50: float
    rsi_14: float
    vol_21: float
    vol_annualized: float
    roc_fast: float
    roc_med: float
    roc_slow: float
    weighted_mom: float
    risk_adj_mom: float
    trend_score: float
    rsi_penalty: float
    final_score: float
    n_bars: int


@dataclass
class SectorSignal:
    """Result of one rebalance evaluation."""
    scores: dict                          # {ticker: SectorScore}
    sorted_assets: list                   # [(ticker, final_score), ...] DESC
    best_asset: Optional[str]
    best_score: Optional[float]
    spy_close: Optional[float]
    spy_sma_200: Optional[float]
    spy_trend: Optional[bool]             # close > 200MA
    safe_ticker: str = SAFE_TICKER


# ─────────────────────────────────────────────────────────────────────────
# Bar fetching
# ─────────────────────────────────────────────────────────────────────────

def fetch_daily_closes(ticker: str, n_bars_needed: int) -> Optional[list[float]]:
    """Return last n_bars_needed daily CLOSES, oldest-first. None on failure."""
    alpaca = tc.get_alpaca()
    end = datetime.now(timezone.utc).date()
    # Generous calendar lookback for weekends + holidays
    start = end - timedelta(days=int(n_bars_needed * 1.6) + 10)
    try:
        from alpaca_trade_api import TimeFrame
        bars = list(alpaca.get_bars(
            ticker, TimeFrame.Day,
            start=start.isoformat(), end=end.isoformat(),
            adjustment="all",
        ))
        if not bars:
            return None
        return [float(b.c) for b in bars]
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────
# Indicator computations (pure Python; mirrors QC's daily-resolution behavior)
# ─────────────────────────────────────────────────────────────────────────

def rocp(closes: list[float], period: int) -> Optional[float]:
    """Rate of change percent = (current - N-ago) / N-ago. Matches QC's ROCP."""
    if len(closes) < period + 1:
        return None
    past = closes[-(period + 1)]
    now = closes[-1]
    if past <= 0:
        return None
    return (now - past) / past


def std_dev(closes: list[float], period: int) -> Optional[float]:
    """Sample stdev of the trailing `period` closes. QC's std() over the close
    series; mirrors np.std default (population std). We use n-1 sample for
    statistical correctness; the choice doesn't affect ranking."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    mean = sum(window) / period
    variance = sum((c - mean) ** 2 for c in window) / period   # population stdev
    return math.sqrt(variance)


def std_dev_returns(closes: list[float], period: int) -> Optional[float]:
    """Standard deviation of percent-change RETURNS over the trailing period.
    Used for vol targeting (rebalance step) — operator's vol-target formula
    is `target_vol / curr_vol` where curr_vol = std(returns) × sqrt(252)."""
    if len(closes) < period + 1:
        return None
    window = closes[-(period + 1):]
    rets = [(window[i] - window[i-1]) / window[i-1] for i in range(1, len(window))
            if window[i-1] > 0]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    variance = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(variance)


def sma(closes: list[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def rsi_wilders(closes: list[float], period: int = 14) -> Optional[float]:
    """Wilder's RSI — same as QC's MovingAverageType.WILDERS. Uses exponentially-
    smoothed gains/losses with alpha = 1/period."""
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i-1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    # First average = simple average of first `period` values
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Wilder smoothing for remaining values
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


# ─────────────────────────────────────────────────────────────────────────
# Per-ticker scoring
# ─────────────────────────────────────────────────────────────────────────

def score_ticker(ticker: str, closes: list[float]) -> Optional[SectorScore]:
    """Compute the full QC score for one ticker. Returns None if any required
    indicator is unavailable (insufficient history)."""
    roc_fast = rocp(closes, ROC_FAST_PERIOD)
    roc_med = rocp(closes, ROC_MED_PERIOD)
    roc_slow = rocp(closes, ROC_SLOW_PERIOD)
    vol = std_dev(closes, VOL_PERIOD)
    rsi = rsi_wilders(closes, RSI_PERIOD)
    sma_50 = sma(closes, SMA_PERIOD)
    price = closes[-1]

    if any(x is None for x in (roc_fast, roc_med, roc_slow, vol, rsi, sma_50)):
        return None

    # QC: `if vol == 0: vol = 1.0`
    if vol == 0:
        vol = 1.0

    weighted_mom = (roc_fast * WEIGHT_FAST + roc_med * WEIGHT_MED + roc_slow * WEIGHT_SLOW)
    risk_adj_mom = weighted_mom / vol
    trend_score = TREND_ABOVE_SMA_MULTIPLIER if price > sma_50 else TREND_BELOW_SMA_MULTIPLIER

    if rsi > RSI_OVERBOUGHT or rsi < RSI_OVERSOLD:
        rsi_pen = RSI_PENALTY
    else:
        rsi_pen = 1.0

    final = risk_adj_mom * trend_score * rsi_pen

    # Annualized vol for the position-sizing step (separate from the score-vol)
    vol_returns = std_dev_returns(closes, VOL_PERIOD)
    vol_annualized = (vol_returns or 0.0) * math.sqrt(TRADING_DAYS_PER_YEAR)

    return SectorScore(
        ticker=ticker, price=price, sma_50=sma_50, rsi_14=rsi,
        vol_21=vol, vol_annualized=vol_annualized,
        roc_fast=roc_fast, roc_med=roc_med, roc_slow=roc_slow,
        weighted_mom=weighted_mom, risk_adj_mom=risk_adj_mom,
        trend_score=trend_score, rsi_penalty=rsi_pen, final_score=final,
        n_bars=len(closes),
    )


# ─────────────────────────────────────────────────────────────────────────
# Top-level evaluation
# ─────────────────────────────────────────────────────────────────────────

def evaluate_sector_signal() -> SectorSignal:
    """Fetch closes for the full universe, score each non-safe ticker, rank
    DESC by final_score, and also fetch SPY for the 200MA defensive overlay."""
    n_needed = ROC_SLOW_PERIOD + 5
    scores: dict = {}
    sorted_assets: list = []

    for ticker in UNIVERSE_SECTOR:
        if ticker == SAFE_TICKER:
            continue
        closes = fetch_daily_closes(ticker, n_needed)
        if not closes:
            continue
        sc = score_ticker(ticker, closes)
        if sc is not None:
            scores[ticker] = sc

    if scores:
        sorted_assets = sorted(scores.items(), key=lambda kv: kv[1].final_score, reverse=True)

    # SPY 200MA overlay (always fetched — used by rebalance, not scoring)
    spy_closes = fetch_daily_closes(BENCHMARK_TICKER, SPY_SMA_PERIOD + 10)
    spy_close = spy_sma_200 = None
    spy_trend = None
    if spy_closes:
        spy_close = spy_closes[-1]
        spy_sma_200 = sma(spy_closes, SPY_SMA_PERIOD)
        if spy_sma_200 is not None:
            spy_trend = spy_close > spy_sma_200

    best_asset = sorted_assets[0][0] if sorted_assets else None
    best_score = sorted_assets[0][1].final_score if sorted_assets else None

    return SectorSignal(
        scores=scores,
        sorted_assets=[(t, s.final_score) for t, s in sorted_assets],
        best_asset=best_asset, best_score=best_score,
        spy_close=spy_close, spy_sma_200=spy_sma_200, spy_trend=spy_trend,
    )
