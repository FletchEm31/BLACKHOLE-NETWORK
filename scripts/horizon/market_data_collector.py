#!/usr/bin/env python3
"""
market_data_collector.py — HORIZON daily OHLCV + indicators collector.

Pulls daily bars from Alpaca for the 29-ticker strategy universe, computes
14 indicators per (ticker, date), upserts into market_daily.

Cadence: systemd timer at 16:30 ET (30min after close). Idempotent —
re-runs same day overwrite via ON CONFLICT (ticker, date) DO UPDATE.

  First run / --backfill : pulls 400 trading days per ticker.
  Subsequent runs        : pulls 5-day rolling window per ticker.

Indicators (pandas-ta):
  sma_20, sma_50, sma_100, sma_200
  rsi_14 (Wilder smoothing)
  atr_14
  bb_upper, bb_lower, bb_width (= (upper-lower) / SMA20)
  roc_9, roc_21, roc_63
  volume_ratio (= today_volume / 20d_avg_volume)
  high_52w, low_52w, pct_from_52w_high (= (close - high_52w) / high_52w, negative)

Reuses scripts/trading/trading_core for PG conn + logger + Alpaca client.
trading_core enforces paper-only Alpaca, which is fine here — market data
endpoints are identical on paper and live URLs.

Egress note: this runs on LA. External API calls (Alpaca data API at
data.alpaca.markets) egress through Hillsboro per the LA egress isolation
policy. If Hillsboro WG handshake is broken, this script will fail with
connection timeouts — diagnose WG before retrying.

Env (/etc/bhn-trading/env):
  ALPACA_API_KEY, ALPACA_API_SECRET, ALPACA_BASE_URL  (paper URL OK)
  PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PASSWORD

CLI:
  python3 market_data_collector.py                  # 5-day rolling update
  python3 market_data_collector.py --backfill       # 400 trading days
  python3 market_data_collector.py --ticker QQQ     # single ticker
  python3 market_data_collector.py --dry-run        # log only, no PG writes
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
# pandas-ta proper requires Python >=3.12 as of 0.4.x (PyPI yanked older
# Py3.10-compatible versions). pandas-ta-classic is the maintained fork
# that still ships wheels for 3.10. Try both; the API surface is identical.
try:
    import pandas_ta as ta
except ImportError:
    import pandas_ta_classic as ta

# scripts/horizon/ imports scripts/trading/trading_core via path insert
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "trading"))
import trading_core as tc


logger = tc.get_logger("horizon_market_data_collector")


# ─────────────────────────────────────────────────────────────────────────
# Universe (29 tickers across 5 strategy-universe groupings)
# ─────────────────────────────────────────────────────────────────────────

UNIVERSE: tuple[str, ...] = (
    # BHN-NASDAQ-LONG / Strat 6
    "QLD", "PSQ", "QID", "SQQQ", "QQQ", "JPST", "SPY",
    # BHN-SECTOR-ROTATION / Strat 8
    "SOXL", "TECL", "TQQQ", "FAS", "ERX", "UUP", "TMF", "BIL",
    # Commodity / weather
    "UNG", "USO", "CORN", "WEAT", "SOYB", "GLD", "SLV",
    # Broad market
    "IWM", "DIA", "XLF", "XLK", "XLE", "XLV", "XLU",
    # Volatility
    "VXX", "UVIX", "SVXY",
)

BACKFILL_TRADING_DAYS = 400          # ≥ SMA200 + 52w + buffer
ROLLING_TRADING_DAYS = 5             # daily incremental update window
INDICATOR_MIN_BARS = 252             # rows needed before all indicators valid


# ─────────────────────────────────────────────────────────────────────────
# Alpaca bar fetch
# ─────────────────────────────────────────────────────────────────────────

def fetch_bars(ticker: str, trading_days: int) -> Optional[pd.DataFrame]:
    """Fetch the last `trading_days` daily bars from Alpaca. Returns a
    DataFrame indexed by date with columns: open, high, low, close, volume.
    None on failure / no data."""
    alpaca = tc.get_alpaca()
    end = datetime.now(timezone.utc).date()
    # 1.6× calendar buffer for weekends + holidays
    calendar_lookback = int(trading_days * 1.6) + 14
    start = end - timedelta(days=calendar_lookback)

    try:
        from alpaca_trade_api import TimeFrame
        # feed="iex" — required for the basic / free Alpaca plan; SIP feed
        # needs a paid subscription. IEX-only historical bars are good enough
        # for daily OHLCV indicators (rebalance signals, not microstructure).
        # Older Alpaca SDKs default to "iex"; SDK 3.x defaults to SIP-then-fallback,
        # which fails hard on this account class — so we pin it explicitly.
        feed_pref = os.environ.get("ALPACA_DATA_FEED", "iex")
        bars_iter = alpaca.get_bars(
            ticker, TimeFrame.Day,
            start=start.isoformat(), end=end.isoformat(),
            adjustment="all",      # split + dividend adjusted
            feed=feed_pref,
        )
        rows = []
        for b in bars_iter:
            # b.t is a pd.Timestamp (UTC); we key by the local trading date
            d = b.t.date() if hasattr(b.t, "date") else date.fromisoformat(str(b.t)[:10])
            rows.append((d, float(b.o), float(b.h), float(b.l), float(b.c), int(b.v)))
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
        df = df.sort_values("date").reset_index(drop=True)
        return df
    except Exception as e:
        logger.warning(f"{ticker}: Alpaca fetch failed — {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────
# Indicator computation
# ─────────────────────────────────────────────────────────────────────────

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add indicator columns to `df` in-place-style. Returns the augmented
    DataFrame. NaN rows are preserved — upsert handles partial data by
    storing NULL for whichever indicators aren't yet computable."""
    if df.empty:
        return df

    # Simple moving averages
    df["sma_20"]  = ta.sma(df["close"], length=20)
    df["sma_50"]  = ta.sma(df["close"], length=50)
    df["sma_100"] = ta.sma(df["close"], length=100)
    df["sma_200"] = ta.sma(df["close"], length=200)

    # Wilder's RSI (pandas-ta default = Wilder smoothing)
    df["rsi_14"] = ta.rsi(df["close"], length=14)

    # ATR — Wilder method
    df["atr_14"] = ta.atr(df["high"], df["low"], df["close"], length=14, mamode="rma")

    # Bollinger bands 20, 2σ. Normalize bb_width = (upper - lower) / sma_20.
    bb = ta.bbands(df["close"], length=20, std=2)
    if bb is not None and not bb.empty:
        # pandas-ta column naming: BBL_20_2.0, BBU_20_2.0, BBM_20_2.0
        df["bb_lower"] = bb.iloc[:, 0]      # BBL
        df["bb_upper"] = bb.iloc[:, 2]      # BBU
        bb_mid = bb.iloc[:, 1]              # BBM (= SMA20)
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / bb_mid

    # Rate of change. pandas-ta returns absolute pct (e.g. 5.2 for 5.2%).
    # We store as decimal (0.052) for downstream math — divide by 100.
    df["roc_9"]  = ta.roc(df["close"], length=9)  / 100.0
    df["roc_21"] = ta.roc(df["close"], length=21) / 100.0
    df["roc_63"] = ta.roc(df["close"], length=63) / 100.0

    # Volume ratio: today / 20-day average. Guard against zero-volume days.
    vol_avg_20 = df["volume"].rolling(window=20, min_periods=20).mean()
    df["volume_ratio"] = df["volume"] / vol_avg_20.replace(0, pd.NA)

    # 52-week high/low. 252 trading days ≈ 1 year.
    df["high_52w"] = df["high"].rolling(window=252, min_periods=20).max()
    df["low_52w"]  = df["low"].rolling(window=252, min_periods=20).min()
    # Negative pct distance from high. 0 when at high, -0.07 for 7% below.
    df["pct_from_52w_high"] = (df["close"] - df["high_52w"]) / df["high_52w"]

    return df


# ─────────────────────────────────────────────────────────────────────────
# Upsert
# ─────────────────────────────────────────────────────────────────────────

UPSERT_SQL = """
    INSERT INTO market_daily (
        ticker, date,
        open, high, low, close, volume,
        sma_20, sma_50, sma_100, sma_200,
        rsi_14, atr_14,
        bb_upper, bb_lower, bb_width,
        roc_9, roc_21, roc_63,
        volume_ratio,
        high_52w, low_52w, pct_from_52w_high
    )
    VALUES (
        %(ticker)s, %(date)s,
        %(open)s, %(high)s, %(low)s, %(close)s, %(volume)s,
        %(sma_20)s, %(sma_50)s, %(sma_100)s, %(sma_200)s,
        %(rsi_14)s, %(atr_14)s,
        %(bb_upper)s, %(bb_lower)s, %(bb_width)s,
        %(roc_9)s, %(roc_21)s, %(roc_63)s,
        %(volume_ratio)s,
        %(high_52w)s, %(low_52w)s, %(pct_from_52w_high)s
    )
    ON CONFLICT (ticker, date) DO UPDATE SET
        open               = EXCLUDED.open,
        high               = EXCLUDED.high,
        low                = EXCLUDED.low,
        close              = EXCLUDED.close,
        volume             = EXCLUDED.volume,
        sma_20             = EXCLUDED.sma_20,
        sma_50             = EXCLUDED.sma_50,
        sma_100            = EXCLUDED.sma_100,
        sma_200            = EXCLUDED.sma_200,
        rsi_14             = EXCLUDED.rsi_14,
        atr_14             = EXCLUDED.atr_14,
        bb_upper           = EXCLUDED.bb_upper,
        bb_lower           = EXCLUDED.bb_lower,
        bb_width           = EXCLUDED.bb_width,
        roc_9              = EXCLUDED.roc_9,
        roc_21             = EXCLUDED.roc_21,
        roc_63             = EXCLUDED.roc_63,
        volume_ratio       = EXCLUDED.volume_ratio,
        high_52w           = EXCLUDED.high_52w,
        low_52w            = EXCLUDED.low_52w,
        pct_from_52w_high  = EXCLUDED.pct_from_52w_high,
        fetched_at         = NOW()
"""


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def upsert_ticker_rows(ticker: str, df: pd.DataFrame, rolling_days: Optional[int] = None,
                       dry_run: bool = False) -> int:
    """Upsert (ticker, date) rows from `df`. If rolling_days is set, only
    the last N rows are written — used for daily incremental runs to avoid
    re-touching 400 rows when only 1 day is new. Returns rows upserted."""
    if df.empty:
        return 0
    if rolling_days is not None:
        df = df.tail(rolling_days)

    rows = []
    for _, r in df.iterrows():
        rows.append({
            "ticker": ticker,
            "date": r["date"],
            "open":  _safe_float(r["open"]),
            "high":  _safe_float(r["high"]),
            "low":   _safe_float(r["low"]),
            "close": _safe_float(r["close"]),
            "volume": int(r["volume"]) if not pd.isna(r["volume"]) else None,
            "sma_20":  _safe_float(r.get("sma_20")),
            "sma_50":  _safe_float(r.get("sma_50")),
            "sma_100": _safe_float(r.get("sma_100")),
            "sma_200": _safe_float(r.get("sma_200")),
            "rsi_14":  _safe_float(r.get("rsi_14")),
            "atr_14":  _safe_float(r.get("atr_14")),
            "bb_upper": _safe_float(r.get("bb_upper")),
            "bb_lower": _safe_float(r.get("bb_lower")),
            "bb_width": _safe_float(r.get("bb_width")),
            "roc_9":   _safe_float(r.get("roc_9")),
            "roc_21":  _safe_float(r.get("roc_21")),
            "roc_63":  _safe_float(r.get("roc_63")),
            "volume_ratio":      _safe_float(r.get("volume_ratio")),
            "high_52w":          _safe_float(r.get("high_52w")),
            "low_52w":           _safe_float(r.get("low_52w")),
            "pct_from_52w_high": _safe_float(r.get("pct_from_52w_high")),
        })

    if dry_run:
        logger.info(f"{ticker}: dry-run, would upsert {len(rows)} rows")
        return len(rows)

    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(UPSERT_SQL, row)
    return len(rows)


# ─────────────────────────────────────────────────────────────────────────
# Per-ticker pipeline + universe driver
# ─────────────────────────────────────────────────────────────────────────

def collect_ticker(ticker: str, trading_days: int,
                    rolling_days: Optional[int] = None,
                    dry_run: bool = False) -> int:
    """Fetch → compute indicators → upsert for one ticker. Returns row count."""
    df = fetch_bars(ticker, trading_days)
    if df is None or df.empty:
        logger.warning(f"{ticker}: no bars returned, skipping")
        return 0
    df = compute_indicators(df)
    n = upsert_ticker_rows(ticker, df, rolling_days=rolling_days, dry_run=dry_run)
    if df is not None and len(df) < INDICATOR_MIN_BARS:
        logger.info(f"{ticker}: {len(df)} bars (< {INDICATOR_MIN_BARS} required for full "
                    f"indicator coverage — some columns will be NULL)")
    return n


def collect_universe(tickers: tuple[str, ...], backfill: bool, dry_run: bool = False) -> int:
    fetch_days   = BACKFILL_TRADING_DAYS if backfill else BACKFILL_TRADING_DAYS  # always fetch enough history for indicators
    rolling_days = None if backfill else ROLLING_TRADING_DAYS                  # but only write last N on incremental
    total = 0
    t_start = time.monotonic()
    for ticker in tickers:
        try:
            n = collect_ticker(ticker, trading_days=fetch_days,
                                rolling_days=rolling_days, dry_run=dry_run)
            total += n
            logger.info(f"{ticker}: {n} rows {'(dry-run)' if dry_run else 'upserted'}")
        except Exception:
            logger.exception(f"{ticker}: pipeline failed")
        # Alpaca rate-limit-friendly pacing. 200 req/min limit; 0.3s/ticker is safe.
        time.sleep(0.3)
    logger.info(f"universe done: total={total} rows across {len(tickers)} tickers "
                f"in {time.monotonic() - t_start:.1f}s "
                f"({'backfill' if backfill else 'rolling'} mode)")
    return total


# ─────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="HORIZON market data collector")
    parser.add_argument("--backfill", action="store_true",
                        help=f"First-run mode: write all {BACKFILL_TRADING_DAYS} trading days. "
                             f"Default is {ROLLING_TRADING_DAYS}-day rolling.")
    parser.add_argument("--ticker", default=None,
                        help="Single ticker (skips universe loop).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log only, no PG writes.")
    args = parser.parse_args()

    logger.info(f"=== market-data-collector start "
                f"(backfill={args.backfill}, ticker={args.ticker or 'universe'}, "
                f"dry_run={args.dry_run}) ===")
    try:
        if args.ticker:
            n = collect_ticker(
                args.ticker.upper(),
                trading_days=BACKFILL_TRADING_DAYS,
                rolling_days=None if args.backfill else ROLLING_TRADING_DAYS,
                dry_run=args.dry_run,
            )
            logger.info(f"{args.ticker.upper()}: {n} rows {'(dry-run)' if args.dry_run else 'upserted'}")
        else:
            collect_universe(UNIVERSE, backfill=args.backfill, dry_run=args.dry_run)
    except Exception:
        logger.exception("market-data-collector failed")
        return 1
    logger.info("=== market-data-collector end ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
