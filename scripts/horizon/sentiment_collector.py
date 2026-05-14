#!/usr/bin/env python3
"""
sentiment_collector.py — HORIZON market sentiment collector.

Pulls daily sentiment indicators from 4 free sources, upserts into
market_sentiment. Best-effort per source — one bad scrape does NOT block
the others. Partial rows (some sources NULL) are acceptable.

Cadence: systemd timer at 17:30 ET daily.

Sources:
  1. alternative.me F&G index    — daily 0-100 + label, free JSON API
  2. CBOE put/call ratio          — daily, scraped from cboe.com CSV
  3. OpenInsider                  — Form 4 filings, scraped HTML, S&P 500 only
  4. AAII Sentiment Survey        — weekly (Thursdays), forward-filled into daily

Egress note: LA → external via Hillsboro. The CBOE / OpenInsider / AAII
sources are HTML scrapes that can silently break when the upstream site
changes layout; each is wrapped in try/except so the row still writes.

Insider buy/sell ratio definition: dollar-weighted, trailing 5 trading days,
S&P 500 universe only. Ratio = sum(buy_$) / sum(sell_$).

S&P 500 list: hardcoded constant below; regenerate quarterly from
datahub.io/core/s-and-p-500-companies or any current source.

Env (/etc/bhn-trading/env):
  PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PASSWORD

CLI:
  python3 sentiment_collector.py                  # all sources
  python3 sentiment_collector.py --source fng     # one source
  python3 sentiment_collector.py --dry-run
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "trading"))
import trading_core as tc


logger = tc.get_logger("horizon_sentiment_collector")


HTTP_HEADERS = {
    "User-Agent": "BHN-Sentiment-Collector/1.0 (operator@eventhorizonvpn.com)",
    "Accept":     "text/html,application/json,text/csv,*/*",
}


# ─────────────────────────────────────────────────────────────────────────
# S&P 500 universe — hardcoded constant
# Regenerate quarterly from datahub.io/core/s-and-p-500-companies or
# Wikipedia. This list is the filter applied to OpenInsider Form 4 filings.
# Length here is a representative subset of the top names; expand as needed.
# ─────────────────────────────────────────────────────────────────────────
SP500_TICKERS: frozenset[str] = frozenset({
    # Top 50 by market cap (illustrative; regenerate from authoritative source)
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "BRK.B", "LLY", "AVGO",
    "TSLA", "JPM", "WMT", "V", "XOM", "UNH", "MA", "PG", "JNJ", "HD",
    "COST", "ORCL", "ABBV", "BAC", "MRK", "CVX", "KO", "ADBE", "PEP", "CRM",
    "TMO", "ACN", "MCD", "CSCO", "LIN", "ABT", "WFC", "AMD", "DHR", "DIS",
    "VZ", "INTC", "IBM", "PM", "NOW", "GE", "QCOM", "TXN", "AMGN", "CAT",
    # Tier 2 — sector representatives
    "PFE", "NEE", "BMY", "RTX", "GS", "BLK", "SCHW", "AMAT", "PYPL", "T",
    "INTU", "BKNG", "MO", "C", "ETN", "AXP", "DE", "LOW", "BA", "SPGI",
    "TJX", "PLD", "CB", "GILD", "MDT", "MMC", "ADP", "VRTX", "MDLZ", "SYK",
    "ADI", "REGN", "ZTS", "SO", "ISRG", "DUK", "CI", "LRCX", "FI", "ELV",
    "MU", "BSX", "CME", "PGR", "EQIX", "BDX", "ITW", "WM", "AON", "EOG",
    # Tier 3 — high-insider-activity names
    "F", "GM", "DAL", "AAL", "UAL", "LUV", "CCL", "NCLH", "RCL", "MAR",
    "HLT", "EXPE", "ABNB", "UBER", "LYFT", "DASH", "SHOP", "SNAP", "PINS", "ROKU",
    "SQ", "PYPL", "COIN", "HOOD", "SOFI", "AFRM", "PLTR", "SNOW", "DDOG", "NET",
    "OKTA", "CRWD", "ZS", "PANW", "FTNT", "MDB", "DBX", "BOX", "TWLO", "ZM",
})


# ─────────────────────────────────────────────────────────────────────────
# Source 1: alternative.me Fear & Greed Index
# ─────────────────────────────────────────────────────────────────────────

FNG_URL = "https://api.alternative.me/fng/"


def fetch_fng(limit: int = 30) -> list[dict]:
    """Returns list of {date, value, value_classification} entries (newest first)."""
    try:
        resp = requests.get(FNG_URL, params={"limit": limit, "format": "json"},
                             headers=HTTP_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data") or []
        out = []
        for r in data:
            try:
                ts = int(r.get("timestamp", "0"))
                d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
                out.append({
                    "date":  d,
                    "value": int(r.get("value", 0)),
                    "label": r.get("value_classification") or "",
                })
            except (TypeError, ValueError):
                continue
        return out
    except Exception as e:
        logger.warning(f"fng: fetch failed — {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────
# Source 2: CBOE put/call ratio
# CBOE publishes a daily CSV at:
#   https://cdn.cboe.com/api/global/us_indices/daily_prices/equity_pc_archive.csv
# (Equity put/call ratio; one of the more useful sentiment views.)
# ─────────────────────────────────────────────────────────────────────────

CBOE_PC_URL = ("https://cdn.cboe.com/api/global/us_indices/daily_prices/"
               "equity_pc_archive.csv")


def fetch_cboe_putcall(lookback_days: int = 30) -> dict[date, float]:
    """Returns {date: put_call_ratio} for the last `lookback_days`. Empty on failure."""
    try:
        resp = requests.get(CBOE_PC_URL, headers=HTTP_HEADERS, timeout=15)
        resp.raise_for_status()
        df = pd.read_csv(StringIO(resp.text))
    except Exception as e:
        logger.warning(f"cboe: fetch failed — {e}")
        return {}

    # CSV column names vary; common forms: "Date", "Put/Call Ratio"
    # Match case-insensitively
    cols = {c.lower(): c for c in df.columns}
    date_col = cols.get("date") or cols.get("trade date") or list(df.columns)[0]
    pcr_col = None
    for c in df.columns:
        if "ratio" in c.lower() or "p/c" in c.lower() or "putcall" in c.lower().replace(" ", "").replace("/", ""):
            pcr_col = c
            break
    if pcr_col is None:
        logger.warning(f"cboe: could not identify put/call column in {list(df.columns)}")
        return {}

    try:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=[date_col, pcr_col])
        cutoff = pd.Timestamp(date.today() - timedelta(days=lookback_days))
        df = df[df[date_col] >= cutoff]
        out: dict[date, float] = {}
        for _, r in df.iterrows():
            d = r[date_col].date()
            try:
                out[d] = float(r[pcr_col])
            except (TypeError, ValueError):
                continue
        return out
    except Exception as e:
        logger.warning(f"cboe: parse failed — {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────
# Source 3: OpenInsider — Form 4 dollar-weighted insider buy/sell ratio
# ─────────────────────────────────────────────────────────────────────────

OPENINSIDER_URL = ("http://openinsider.com/screener?s=&o=&pl=&ph=&ll=&lh="
                   "&fd=7&fdr=&td=0&tdr=&fdlyl=&fdlyh=&daysago=&xp=1&xs=1&vl="
                   "&vh=&ocl=&och=&sic1=-1&sicl=100&sich=9999&grp=0&nfl=&nfh="
                   "&nil=&nih=&nol=&noh=&v2l=&v2h=&oc2l=&oc2h=&sortcol=0&"
                   "cnt=500&page=1")


def fetch_openinsider(lookback_days: int = 5) -> Optional[float]:
    """Compute dollar-weighted insider buy/sell ratio over trailing
    `lookback_days` trading days, filtered to S&P 500 tickers.
    Returns None on fetch/parse failure."""
    try:
        resp = requests.get(OPENINSIDER_URL, headers=HTTP_HEADERS, timeout=20)
        resp.raise_for_status()
        # OpenInsider returns HTML with a single results table. Parse with pandas.read_html.
        tables = pd.read_html(StringIO(resp.text))
    except Exception as e:
        logger.warning(f"openinsider: fetch/parse failed — {e}")
        return None

    if not tables:
        return None
    df = tables[0]
    # Expected columns include: 'Ticker', 'Filing Date', 'Trans Type', 'Value' or '$Value'
    # Normalize column names
    df.columns = [str(c).strip() for c in df.columns]
    ticker_col = next((c for c in df.columns if c.lower() == "ticker"), None)
    type_col   = next((c for c in df.columns if "trans" in c.lower() and "type" in c.lower()), None)
    date_col   = next((c for c in df.columns if "filing" in c.lower() and "date" in c.lower()), None)
    value_col  = next((c for c in df.columns if c.strip().lower() in ("value", "$value", "$ value")), None)
    if not all((ticker_col, type_col, date_col, value_col)):
        logger.warning(f"openinsider: column detection failed — found {list(df.columns)}")
        return None

    try:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        # Filter to S&P 500 + last N days
        cutoff = pd.Timestamp(date.today() - timedelta(days=lookback_days * 2))  # calendar-days buffer
        df = df.dropna(subset=[date_col, ticker_col, type_col, value_col])
        df = df[df[date_col] >= cutoff]
        df = df[df[ticker_col].str.upper().isin(SP500_TICKERS)]
        if df.empty:
            return None

        # Parse $ value — values like "$1,234,567" or "1234567"
        def _to_dollars(v) -> float:
            s = str(v).replace("$", "").replace(",", "").strip()
            try:
                return float(s)
            except ValueError:
                return 0.0

        df["_dollars"] = df[value_col].apply(_to_dollars).abs()
        # OpenInsider trans types: "P - Purchase" (buy), "S - Sale" (sell)
        df["_is_buy"]  = df[type_col].astype(str).str.contains("P -", case=False, na=False)
        df["_is_sell"] = df[type_col].astype(str).str.contains("S -", case=False, na=False)

        buy_total  = df.loc[df["_is_buy"],  "_dollars"].sum()
        sell_total = df.loc[df["_is_sell"], "_dollars"].sum()
        if sell_total <= 0:
            return float("inf") if buy_total > 0 else None
        return float(buy_total / sell_total)
    except Exception as e:
        logger.warning(f"openinsider: ratio compute failed — {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────
# Source 4: AAII Sentiment Survey
# AAII publishes weekly Thursday. The historical-data XLS is at:
#   https://www.aaii.com/files/surveys/sentiment.xls
# This sheet contains decades of weekly Bull/Neutral/Bear pct readings.
# ─────────────────────────────────────────────────────────────────────────

AAII_URL = "https://www.aaii.com/files/surveys/sentiment.xls"


def fetch_aaii() -> Optional[tuple[date, float, float, float]]:
    """Returns (week_ending, bull_pct, bear_pct, neutral_pct) for the most
    recent AAII survey. None on failure.

    bull/bear/neutral_pct are returned as PERCENTAGES (e.g. 38.5 for 38.5%)
    to match the existing market_sentiment column expectations."""
    try:
        resp = requests.get(AAII_URL, headers=HTTP_HEADERS, timeout=20)
        resp.raise_for_status()
        df = pd.read_excel(StringIO(resp.content.decode("latin-1")) if False else resp.content,
                            sheet_name=0, header=None, engine="xlrd")
    except Exception as e:
        logger.warning(f"aaii: fetch/parse failed — {e}")
        return None

    # AAII's spreadsheet has a header somewhere in the first 5 rows. Locate
    # the row that has "Date" / "Bullish" / "Bearish" headers, then read below it.
    header_row = None
    for i in range(min(10, len(df))):
        row_vals = [str(v).strip().lower() for v in df.iloc[i].tolist()]
        if "bullish" in row_vals and "bearish" in row_vals:
            header_row = i
            break
    if header_row is None:
        logger.warning("aaii: could not locate Bullish/Bearish header in spreadsheet")
        return None

    headers = [str(v).strip().lower() for v in df.iloc[header_row].tolist()]
    data = df.iloc[header_row + 1:].copy()
    data.columns = headers
    data = data.dropna(how="all")

    try:
        date_col    = next(c for c in headers if c in ("date", "reported date", "week ending"))
        bull_col    = next(c for c in headers if c == "bullish")
        neutral_col = next(c for c in headers if c == "neutral")
        bear_col    = next(c for c in headers if c == "bearish")
    except StopIteration:
        logger.warning(f"aaii: header columns not found — {headers}")
        return None

    data[date_col] = pd.to_datetime(data[date_col], errors="coerce")
    data = data.dropna(subset=[date_col, bull_col, bear_col])
    data = data.sort_values(date_col)
    last = data.iloc[-1]

    def _to_pct(v) -> float:
        # AAII stores fractions (0.385) historically — multiply by 100 if so.
        try:
            f = float(v)
        except (TypeError, ValueError):
            return float("nan")
        return f * 100.0 if 0.0 <= f <= 1.0 else f

    return (
        last[date_col].date(),
        _to_pct(last[bull_col]),
        _to_pct(last[bear_col]),
        _to_pct(last[neutral_col]),
    )


# ─────────────────────────────────────────────────────────────────────────
# Upsert
# ─────────────────────────────────────────────────────────────────────────

UPSERT_SQL = """
    INSERT INTO market_sentiment (
        date,
        fear_greed_index, fear_greed_label,
        put_call_ratio, insider_buy_sell_ratio,
        aaii_bull_pct, aaii_bear_pct, aaii_neutral_pct,
        aaii_week_ending
    )
    VALUES (
        %(date)s,
        %(fng_value)s, %(fng_label)s,
        %(pcr)s, %(insider)s,
        %(bull)s, %(bear)s, %(neutral)s,
        %(aaii_week)s
    )
    ON CONFLICT (date) DO UPDATE SET
        fear_greed_index        = COALESCE(EXCLUDED.fear_greed_index, market_sentiment.fear_greed_index),
        fear_greed_label        = COALESCE(EXCLUDED.fear_greed_label, market_sentiment.fear_greed_label),
        put_call_ratio          = COALESCE(EXCLUDED.put_call_ratio,   market_sentiment.put_call_ratio),
        insider_buy_sell_ratio  = COALESCE(EXCLUDED.insider_buy_sell_ratio,
                                            market_sentiment.insider_buy_sell_ratio),
        aaii_bull_pct           = COALESCE(EXCLUDED.aaii_bull_pct,    market_sentiment.aaii_bull_pct),
        aaii_bear_pct           = COALESCE(EXCLUDED.aaii_bear_pct,    market_sentiment.aaii_bear_pct),
        aaii_neutral_pct        = COALESCE(EXCLUDED.aaii_neutral_pct, market_sentiment.aaii_neutral_pct),
        aaii_week_ending        = COALESCE(EXCLUDED.aaii_week_ending, market_sentiment.aaii_week_ending),
        fetched_at              = NOW()
"""


def upsert(target_date: date, payload: dict, dry_run: bool = False) -> None:
    row = {
        "date":         target_date,
        "fng_value":    payload.get("fng_value"),
        "fng_label":    payload.get("fng_label"),
        "pcr":          payload.get("pcr"),
        "insider":      payload.get("insider"),
        "bull":         payload.get("bull"),
        "bear":         payload.get("bear"),
        "neutral":      payload.get("neutral"),
        "aaii_week":    payload.get("aaii_week"),
    }
    if dry_run:
        logger.info(f"dry-run upsert: {row}")
        return
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(UPSERT_SQL, row)


# ─────────────────────────────────────────────────────────────────────────
# Top-level driver
# ─────────────────────────────────────────────────────────────────────────

def collect(source: str = "all", dry_run: bool = False) -> int:
    today = date.today()
    payload: dict = {}

    if source in ("all", "fng"):
        entries = fetch_fng(limit=30)
        if entries:
            latest = entries[0]
            payload["fng_value"] = latest["value"]
            payload["fng_label"] = latest["label"]
            logger.info(f"fng: {latest['date']} value={latest['value']} ({latest['label']})")
            # Backfill older F&G entries into their own dates too
            for e in entries[1:]:
                old_payload = {"fng_value": e["value"], "fng_label": e["label"]}
                upsert(e["date"], old_payload, dry_run=dry_run)
        else:
            logger.info("fng: no data")

    if source in ("all", "cboe"):
        pcr_by_date = fetch_cboe_putcall(lookback_days=30)
        if pcr_by_date:
            today_pcr = pcr_by_date.get(today)
            if today_pcr is not None:
                payload["pcr"] = today_pcr
            # Backfill historical pcr into prior dates
            for d, ratio in pcr_by_date.items():
                if d == today:
                    continue
                upsert(d, {"pcr": ratio}, dry_run=dry_run)
            logger.info(f"cboe: {len(pcr_by_date)} put/call rows processed "
                        f"(today={payload.get('pcr')})")
        else:
            logger.info("cboe: no data")

    if source in ("all", "insider"):
        ratio = fetch_openinsider(lookback_days=5)
        if ratio is not None:
            payload["insider"] = ratio
            logger.info(f"openinsider: ratio={ratio}")
        else:
            logger.info("openinsider: no data")

    if source in ("all", "aaii"):
        aaii = fetch_aaii()
        if aaii is not None:
            week_end, bull, bear, neutral = aaii
            payload["bull"]      = bull
            payload["bear"]      = bear
            payload["neutral"]   = neutral
            payload["aaii_week"] = week_end
            logger.info(f"aaii: week_ending={week_end} bull={bull} bear={bear} "
                        f"neutral={neutral}")
        else:
            logger.info("aaii: no data")

    if not payload:
        logger.warning("no sources returned data — skipping today's upsert")
        return 0

    upsert(today, payload, dry_run=dry_run)
    return 1


# ─────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="HORIZON sentiment collector")
    parser.add_argument("--source", choices=["all", "fng", "cboe", "insider", "aaii"],
                        default="all")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logger.info(f"=== sentiment-collector start (source={args.source}, "
                f"dry_run={args.dry_run}) ===")
    try:
        collect(source=args.source, dry_run=args.dry_run)
    except Exception:
        logger.exception("sentiment-collector failed")
        return 1
    logger.info("=== sentiment-collector end ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
