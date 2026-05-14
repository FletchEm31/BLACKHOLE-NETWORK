#!/usr/bin/env python3
"""
macro_collector.py — HORIZON macro indicators collector (FRED API).

Pulls 10 FRED series, dense forward-fills into one row per business day,
upserts into macro_daily.

Cadence: systemd timer at 17:00 ET daily. Idempotent — ON CONFLICT (date)
DO UPDATE.

  First run / --backfill : 5 years of daily rows.
  Subsequent runs        : last 10 business days (catches any backdated
                            FRED revisions and the new trading day).

Series mapping (FRED ID → column):
  VIXCLS         → vix                       (daily)
  T10Y2Y         → yield_curve_10y2y         (daily)
  T10Y3M         → yield_curve_10y3m         (daily)
  DFF            → fed_funds_rate            (daily)
  CPIAUCSL       → cpi                       (monthly, forward-filled)
  UNRATE         → unemployment              (monthly, forward-filled)
  GDP            → gdp                       (quarterly, forward-filled)
  UMCSENT        → consumer_sentiment        (monthly, forward-filled)
  BAMLH0A0HYM2   → high_yield_spread         (daily)
  DTWEXBGS       → dollar_index              (daily)

Forward-fill semantics: every business day from start through today gets
one row. Slow-moving series carry the last published value until FRED
publishes a new release. This is the join-friendly layout for backtests +
HORIZON's analyze_ticker view.

Egress note: this runs on LA. FRED API is at api.stlouisfed.org; egress
through Hillsboro per LA isolation policy. FRED rate limit is 120 req/min
— 10 series × 1 req each is well under.

Env (/etc/bhn-trading/env):
  FRED_API_KEY                 (free at fred.stlouisfed.org)
  PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PASSWORD

CLI:
  python3 macro_collector.py                  # 10-day rolling update
  python3 macro_collector.py --backfill       # 5 years
  python3 macro_collector.py --series VIXCLS  # single series
  python3 macro_collector.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "trading"))
import trading_core as tc


logger = tc.get_logger("horizon_macro_collector")


FRED_API_BASE = "https://api.stlouisfed.org/fred/series/observations"

# FRED series ID → macro_daily column name
SERIES_MAP: dict[str, str] = {
    "VIXCLS":       "vix",
    "T10Y2Y":       "yield_curve_10y2y",
    "T10Y3M":       "yield_curve_10y3m",
    "DFF":          "fed_funds_rate",
    "CPIAUCSL":     "cpi",
    "UNRATE":       "unemployment",
    "GDP":          "gdp",
    "UMCSENT":      "consumer_sentiment",
    "BAMLH0A0HYM2": "high_yield_spread",
    "DTWEXBGS":     "dollar_index",
}

BACKFILL_YEARS = 5
ROLLING_BUSINESS_DAYS = 10


# ─────────────────────────────────────────────────────────────────────────
# FRED fetch
# ─────────────────────────────────────────────────────────────────────────

def _fred_get(series_id: str, observation_start: date, observation_end: date,
                api_key: str, attempts: int = 3) -> Optional[list[dict]]:
    """GET /fred/series/observations with retry + 429 backoff."""
    params = {
        "series_id":           series_id,
        "api_key":             api_key,
        "file_type":           "json",
        "observation_start":   observation_start.isoformat(),
        "observation_end":     observation_end.isoformat(),
    }
    for attempt in range(attempts):
        try:
            resp = requests.get(FRED_API_BASE, params=params, timeout=30)
            if resp.status_code == 429:
                wait = 2 ** attempt + 5
                logger.warning(f"FRED 429 on {series_id}; sleeping {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            obs = data.get("observations", [])
            return obs
        except requests.RequestException as e:
            logger.warning(f"FRED fetch attempt {attempt+1}/{attempts} for {series_id} "
                           f"failed: {e}")
            time.sleep(2 ** attempt)
    logger.error(f"FRED fetch failed after {attempts} attempts: {series_id}")
    return None


def fetch_series(series_id: str, start: date, end: date,
                  api_key: str) -> Optional[pd.Series]:
    """Returns a pd.Series indexed by date (datetime) with the FRED values
    (floats; FRED's '.' sentinel for missing → NaN). None on fetch failure."""
    obs = _fred_get(series_id, start, end, api_key)
    if obs is None:
        return None
    if not obs:
        logger.warning(f"{series_id}: FRED returned 0 observations for "
                       f"{start}..{end}")
        return pd.Series(dtype="float64")

    dates, values = [], []
    for o in obs:
        try:
            d = date.fromisoformat(o["date"])
        except (KeyError, ValueError):
            continue
        v_raw = o.get("value", ".")
        if v_raw in (".", "", None):
            v = float("nan")
        else:
            try:
                v = float(v_raw)
            except ValueError:
                v = float("nan")
        dates.append(d)
        values.append(v)
    return pd.Series(values, index=pd.to_datetime(dates), name=series_id)


# ─────────────────────────────────────────────────────────────────────────
# Forward-fill into business-day grid
# ─────────────────────────────────────────────────────────────────────────

def build_daily_grid(start: date, end: date,
                      series_data: dict[str, pd.Series]) -> pd.DataFrame:
    """Construct a DataFrame indexed by business-day date with one column per
    FRED series. Slow-moving series are forward-filled across days where
    FRED has no new observation."""
    idx = pd.date_range(start=start, end=end, freq="B")
    df = pd.DataFrame(index=idx)

    for series_id, col_name in SERIES_MAP.items():
        s = series_data.get(series_id)
        if s is None or s.empty:
            df[col_name] = float("nan")
            continue
        # Reindex onto our business-day grid + forward-fill
        s_aligned = s.reindex(idx, method="ffill")
        df[col_name] = s_aligned

    df.index.name = "date"
    df = df.reset_index()
    df["date"] = df["date"].dt.date
    return df


# ─────────────────────────────────────────────────────────────────────────
# Upsert
# ─────────────────────────────────────────────────────────────────────────

UPSERT_SQL = """
    INSERT INTO macro_daily (
        date,
        vix, yield_curve_10y2y, yield_curve_10y3m, fed_funds_rate,
        cpi, unemployment, gdp, consumer_sentiment,
        high_yield_spread, dollar_index
    )
    VALUES (
        %(date)s,
        %(vix)s, %(yield_curve_10y2y)s, %(yield_curve_10y3m)s, %(fed_funds_rate)s,
        %(cpi)s, %(unemployment)s, %(gdp)s, %(consumer_sentiment)s,
        %(high_yield_spread)s, %(dollar_index)s
    )
    ON CONFLICT (date) DO UPDATE SET
        vix                 = EXCLUDED.vix,
        yield_curve_10y2y   = EXCLUDED.yield_curve_10y2y,
        yield_curve_10y3m   = EXCLUDED.yield_curve_10y3m,
        fed_funds_rate      = EXCLUDED.fed_funds_rate,
        cpi                 = EXCLUDED.cpi,
        unemployment        = EXCLUDED.unemployment,
        gdp                 = EXCLUDED.gdp,
        consumer_sentiment  = EXCLUDED.consumer_sentiment,
        high_yield_spread   = EXCLUDED.high_yield_spread,
        dollar_index        = EXCLUDED.dollar_index,
        fetched_at          = NOW()
"""


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN check
            return None
        return f
    except (TypeError, ValueError):
        return None


def upsert_rows(df: pd.DataFrame, dry_run: bool = False) -> int:
    if df.empty:
        return 0
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "date":                r["date"],
            "vix":                 _safe_float(r.get("vix")),
            "yield_curve_10y2y":   _safe_float(r.get("yield_curve_10y2y")),
            "yield_curve_10y3m":   _safe_float(r.get("yield_curve_10y3m")),
            "fed_funds_rate":      _safe_float(r.get("fed_funds_rate")),
            "cpi":                 _safe_float(r.get("cpi")),
            "unemployment":        _safe_float(r.get("unemployment")),
            "gdp":                 _safe_float(r.get("gdp")),
            "consumer_sentiment":  _safe_float(r.get("consumer_sentiment")),
            "high_yield_spread":   _safe_float(r.get("high_yield_spread")),
            "dollar_index":        _safe_float(r.get("dollar_index")),
        })

    if dry_run:
        logger.info(f"dry-run: would upsert {len(rows)} macro_daily rows")
        return len(rows)

    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(UPSERT_SQL, row)
    return len(rows)


# ─────────────────────────────────────────────────────────────────────────
# Top-level pipeline
# ─────────────────────────────────────────────────────────────────────────

def collect(backfill: bool, single_series: Optional[str] = None,
             dry_run: bool = False) -> int:
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        raise RuntimeError("FRED_API_KEY not set in env. Add to /etc/bhn-trading/env "
                           "(free key at fred.stlouisfed.org/docs/api/api_key.html).")

    end = date.today()
    if backfill:
        start = date(end.year - BACKFILL_YEARS, end.month, end.day)
    else:
        # Rolling 10 business days. For forward-fill correctness on slow-moving
        # series, we need a longer fetch window — go back far enough that
        # CPI/GDP/UMCSENT have at least one observation. CPI is monthly → 35
        # calendar days back. GDP is quarterly → 100 days. Use 120 for safety.
        start = end - timedelta(days=120)

    series_list = [single_series] if single_series else list(SERIES_MAP.keys())
    if single_series and single_series not in SERIES_MAP:
        raise RuntimeError(f"Unknown FRED series id: {single_series!r}. "
                           f"Valid: {list(SERIES_MAP.keys())}")

    # Fetch each series. Light pacing for politeness.
    series_data: dict[str, pd.Series] = {}
    for sid in series_list:
        logger.info(f"fetching {sid} ({start}..{end})")
        s = fetch_series(sid, start, end, api_key)
        if s is None:
            logger.warning(f"{sid}: fetch failed — column will be NaN")
            series_data[sid] = pd.Series(dtype="float64")
        else:
            series_data[sid] = s
        time.sleep(0.2)

    # If --series specified, only that column gets values; others come from
    # existing macro_daily on conflict (NOT overwritten because EXCLUDED for
    # those columns is NULL — but our ON CONFLICT writes EXCLUDED unconditionally).
    # To avoid wiping existing data, fill missing series from PG.
    if single_series:
        series_data = _fill_other_series_from_pg(series_data, start, end)

    df = build_daily_grid(start, end, series_data)

    # Trim to the actually-targeted window. Backfill: full range. Rolling:
    # last N business days only.
    if not backfill:
        df = df.tail(ROLLING_BUSINESS_DAYS)

    n = upsert_rows(df, dry_run=dry_run)
    logger.info(f"macro: {n} rows {'(dry-run)' if dry_run else 'upserted'} "
                f"({'backfill' if backfill else 'rolling'} mode)")
    return n


def _fill_other_series_from_pg(series_data: dict[str, pd.Series],
                                start: date, end: date) -> dict[str, pd.Series]:
    """When --series is used, fill the other 9 columns from existing macro_daily
    so the upsert doesn't blank them. Best-effort: missing columns stay NaN."""
    try:
        with tc.get_pg_conn() as conn:
            with conn.cursor() as cur:
                cols = ", ".join(SERIES_MAP.values())
                cur.execute(f"SELECT date, {cols} FROM macro_daily "
                            f"WHERE date >= %s AND date <= %s ORDER BY date",
                            (start, end))
                rows = cur.fetchall()
        if not rows:
            return series_data
        df_existing = pd.DataFrame(rows, columns=["date"] + list(SERIES_MAP.values()))
        df_existing["date"] = pd.to_datetime(df_existing["date"])
        for sid, col in SERIES_MAP.items():
            if sid in series_data and not series_data[sid].empty:
                continue
            series_data[sid] = df_existing.set_index("date")[col].dropna()
    except Exception as e:
        logger.warning(f"single-series mode: PG fallback for other columns failed: {e}")
    return series_data


# ─────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="HORIZON FRED macro collector")
    parser.add_argument("--backfill", action="store_true",
                        help=f"First-run mode: {BACKFILL_YEARS}-year history. Default rolling.")
    parser.add_argument("--series", default=None,
                        help="Single FRED series id (e.g. VIXCLS). Skips others.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log only, no PG writes.")
    args = parser.parse_args()

    logger.info(f"=== macro-collector start (backfill={args.backfill}, "
                f"series={args.series or 'all'}, dry_run={args.dry_run}) ===")
    try:
        collect(backfill=args.backfill, single_series=args.series, dry_run=args.dry_run)
    except Exception:
        logger.exception("macro-collector failed")
        return 1
    logger.info("=== macro-collector end ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
