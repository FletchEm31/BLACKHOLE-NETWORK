#!/usr/bin/env python3
"""
events_calendar.py — HORIZON forward-looking market events collector.

Maintains a 12-month forward calendar of market-moving events for HORIZON's
context-injection layer.

NO systemd timer — invoked from morning_brief_generator pre-fetch or manually.
Idempotent: UNIQUE (event_date, event_type, COALESCE(ticker,'')).

Event sources:
  fomc                — hardcoded constant (regenerate ANNUALLY from
                          federalreserve.gov/monetarypolicy/fomccalendars.htm)
  cpi_release         — hardcoded (BLS calendar; regen annually)
  nfp_release         — hardcoded (BLS calendar; regen annually)
  pce_release         — hardcoded (BEA calendar; regen annually)
  gdp_release         — hardcoded (BEA calendar; regen annually)
  unemployment_release— same as nfp_release (BLS combines into Employment Situation)
  options_expiry      — computed: 3rd Friday of every month
  earnings            — FMP API earnings calendar for the strategy universe

Expected-impact mapping (hardcoded):
  fomc, cpi_release, nfp_release, pce_release          → high
  gdp_release, unemployment_release                     → high
  earnings, options_expiry                              → medium

Env (/etc/bhn-trading/env):
  FMP_API_KEY                  (already required by strat_2_value)
  PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PASSWORD

CLI:
  python3 events_calendar.py                  # all sources, next 12 months
  python3 events_calendar.py --source fomc    # one source
  python3 events_calendar.py --dry-run

Library use:
  from events_calendar import refresh_events
  refresh_events()  # called from morning_brief_generator.py pre-fetch
"""
from __future__ import annotations

import argparse
import calendar
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "trading"))
import trading_core as tc


logger = tc.get_logger("horizon_events_calendar")


HTTP_HEADERS = {
    "User-Agent": "BHN-Events-Calendar/1.0 (operator@eventhorizonvpn.com)",
}


# ─────────────────────────────────────────────────────────────────────────
# Strategy universe — earnings calendar filter
# ─────────────────────────────────────────────────────────────────────────

UNIVERSE: tuple[str, ...] = (
    "QLD", "PSQ", "QID", "SQQQ", "QQQ", "JPST", "SPY",
    "SOXL", "TECL", "TQQQ", "FAS", "ERX", "UUP", "TMF", "BIL",
    "UNG", "USO", "CORN", "WEAT", "SOYB", "GLD", "SLV",
    "IWM", "DIA", "XLF", "XLK", "XLE", "XLV", "XLU",
    "VXX", "UVIX", "SVXY",
)


EXPECTED_IMPACT_BY_TYPE: dict[str, str] = {
    "fomc":                 "high",
    "cpi_release":          "high",
    "nfp_release":          "high",
    "pce_release":          "high",
    "gdp_release":          "high",
    "unemployment_release": "high",
    "earnings":             "medium",
    "options_expiry":       "medium",
}


# ─────────────────────────────────────────────────────────────────────────
# FOMC meeting dates — hardcoded, REGENERATE ANNUALLY from
# https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
# Format: list of date strings. Convention: the SECOND day of each 2-day
# meeting (the day of the statement + press conference).
# ─────────────────────────────────────────────────────────────────────────
FOMC_MEETING_DATES_2026: tuple[str, ...] = (
    "2026-01-28",   # Jan 27-28
    "2026-03-18",   # Mar 17-18
    "2026-04-29",   # Apr 28-29
    "2026-06-17",   # Jun 16-17
    "2026-07-29",   # Jul 28-29
    "2026-09-16",   # Sep 15-16
    "2026-11-04",   # Nov 3-4
    "2026-12-16",   # Dec 15-16
)
FOMC_MEETING_DATES_2027: tuple[str, ...] = (
    # Placeholder — replace with actual 2027 calendar when Fed publishes
    # mid-2026. Until then, the constant is empty for the out year.
)


# ─────────────────────────────────────────────────────────────────────────
# Major macro releases — hardcoded, REGENERATE ANNUALLY from BLS / BEA
# https://www.bls.gov/schedule/news_release/empsit.htm        (NFP / UE)
# https://www.bls.gov/schedule/news_release/cpi.htm           (CPI)
# https://www.bea.gov/news/schedule                            (GDP, PCE)
# ─────────────────────────────────────────────────────────────────────────
CPI_RELEASE_DATES_2026: tuple[str, ...] = (
    "2026-01-14", "2026-02-12", "2026-03-11", "2026-04-10",
    "2026-05-12", "2026-06-11", "2026-07-15", "2026-08-12",
    "2026-09-10", "2026-10-15", "2026-11-13", "2026-12-10",
)
NFP_RELEASE_DATES_2026: tuple[str, ...] = (
    # NFP = Employment Situation = first Friday of each month (typical)
    "2026-01-09", "2026-02-06", "2026-03-06", "2026-04-03",
    "2026-05-01", "2026-06-05", "2026-07-02", "2026-08-07",
    "2026-09-04", "2026-10-02", "2026-11-06", "2026-12-04",
)
PCE_RELEASE_DATES_2026: tuple[str, ...] = (
    "2026-01-30", "2026-02-27", "2026-03-27", "2026-04-30",
    "2026-05-29", "2026-06-26", "2026-07-31", "2026-08-28",
    "2026-09-25", "2026-10-30", "2026-11-25", "2026-12-22",
)
GDP_RELEASE_DATES_2026: tuple[str, ...] = (
    # Advance Q4 (Jan), Q1 (Apr), Q2 (Jul), Q3 (Oct)
    "2026-01-29", "2026-04-29", "2026-07-30", "2026-10-29",
)


# ─────────────────────────────────────────────────────────────────────────
# Options expiry — monthly 3rd Friday (no weeklies/quarterlies/VIX-Wed)
# ─────────────────────────────────────────────────────────────────────────

def third_friday(year: int, month: int) -> date:
    """Return the 3rd Friday of the given month."""
    first = date(year, month, 1)
    # weekday(): Mon=0..Sun=6. Friday=4.
    first_friday_offset = (4 - first.weekday()) % 7
    return first + timedelta(days=first_friday_offset + 14)


def opex_dates(start: date, end: date) -> list[date]:
    """All 3rd-Friday opex dates in [start, end]."""
    out: list[date] = []
    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        d = third_friday(y, m)
        if start <= d <= end:
            out.append(d)
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


# ─────────────────────────────────────────────────────────────────────────
# FMP earnings calendar
# ─────────────────────────────────────────────────────────────────────────

# FMP deprecated v3/v4 earnings endpoints 2025-08-31 ("Legacy Endpoint" 403).
# /stable/earnings-calendar is the current replacement. Response shape:
#   [{"symbol":"AAPL","date":"2026-07-30","epsActual":null,"epsEstimated":1.86,
#     "revenueActual":null,"revenueEstimated":107946900000,"lastUpdated":"2026-05-14"}, ...]
FMP_EARNINGS_URL = "https://financialmodelingprep.com/stable/earnings-calendar"


def fetch_earnings(start: date, end: date, api_key: str) -> list[dict]:
    """Returns list of {date, ticker, description} for the universe over [start, end]."""
    params = {
        "from":    start.isoformat(),
        "to":      end.isoformat(),
        "apikey":  api_key,
    }
    try:
        resp = requests.get(FMP_EARNINGS_URL, params=params, headers=HTTP_HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"fmp earnings: fetch failed — {e}")
        return []
    if not isinstance(data, list):
        return []

    universe_set = set(UNIVERSE)
    out: list[dict] = []
    for r in data:
        sym = (r.get("symbol") or "").upper()
        if sym not in universe_set:
            continue
        d_raw = r.get("date")
        try:
            d = date.fromisoformat(d_raw)
        except (TypeError, ValueError):
            continue
        eps_est = r.get("epsEstimated")
        rev_est = r.get("revenueEstimated")
        desc = f"{sym} earnings"
        if eps_est is not None:
            desc += f" (eps est {eps_est})"
        if rev_est is not None:
            desc += f", revenue est {rev_est}"
        out.append({"date": d, "ticker": sym, "description": desc})
    return out


# ─────────────────────────────────────────────────────────────────────────
# Upsert
# ─────────────────────────────────────────────────────────────────────────

UPSERT_SQL = """
    INSERT INTO market_events
        (event_date, event_type, ticker, description, expected_impact)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (event_date, event_type, COALESCE(ticker, '')) DO UPDATE SET
        description       = EXCLUDED.description,
        expected_impact   = EXCLUDED.expected_impact
"""


def upsert_events(events: Iterable[dict], dry_run: bool = False) -> int:
    n = 0
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            for e in events:
                event_type = e["event_type"]
                impact = EXPECTED_IMPACT_BY_TYPE.get(event_type, "low")
                row = (
                    e["event_date"],
                    event_type,
                    e.get("ticker"),
                    e.get("description") or event_type,
                    impact,
                )
                if dry_run:
                    logger.info(f"dry-run upsert: {row}")
                else:
                    cur.execute(UPSERT_SQL, row)
                n += 1
    return n


# ─────────────────────────────────────────────────────────────────────────
# Top-level
# ─────────────────────────────────────────────────────────────────────────

def _parse_dates(strs: Iterable[str]) -> list[date]:
    out = []
    for s in strs:
        try:
            out.append(date.fromisoformat(s))
        except ValueError:
            pass
    return out


def gather_events(today: Optional[date] = None,
                   horizon_days: int = 365,
                   sources: tuple[str, ...] = ("fomc", "macro", "opex", "earnings")
                   ) -> list[dict]:
    """Gather all events into a single list. `sources` filter:
       fomc      → FOMC meetings
       macro     → CPI/NFP/PCE/GDP releases
       opex      → monthly options expiry
       earnings  → FMP earnings for universe
    """
    today = today or date.today()
    end = today + timedelta(days=horizon_days)
    events: list[dict] = []

    if "fomc" in sources:
        fomc_dates = _parse_dates(FOMC_MEETING_DATES_2026 + FOMC_MEETING_DATES_2027)
        for d in fomc_dates:
            if today <= d <= end:
                events.append({"event_date": d, "event_type": "fomc",
                                "ticker": None, "description": "FOMC statement + press conference"})

    if "macro" in sources:
        for d in _parse_dates(CPI_RELEASE_DATES_2026):
            if today <= d <= end:
                events.append({"event_date": d, "event_type": "cpi_release",
                                "ticker": None, "description": "BLS CPI release"})
        for d in _parse_dates(NFP_RELEASE_DATES_2026):
            if today <= d <= end:
                events.append({"event_date": d, "event_type": "nfp_release",
                                "ticker": None, "description": "BLS Employment Situation (NFP)"})
                # Same release publishes unemployment rate — log separately for HORIZON queryability
                events.append({"event_date": d, "event_type": "unemployment_release",
                                "ticker": None, "description": "BLS unemployment rate (same release as NFP)"})
        for d in _parse_dates(PCE_RELEASE_DATES_2026):
            if today <= d <= end:
                events.append({"event_date": d, "event_type": "pce_release",
                                "ticker": None, "description": "BEA PCE release"})
        for d in _parse_dates(GDP_RELEASE_DATES_2026):
            if today <= d <= end:
                events.append({"event_date": d, "event_type": "gdp_release",
                                "ticker": None, "description": "BEA GDP advance estimate"})

    if "opex" in sources:
        for d in opex_dates(today, end):
            events.append({"event_date": d, "event_type": "options_expiry",
                            "ticker": None, "description": "Monthly options expiry (3rd Friday)"})

    if "earnings" in sources:
        api_key = os.environ.get("FMP_API_KEY")
        if not api_key:
            logger.info("earnings: FMP_API_KEY not set — skipping earnings sub-source")
        else:
            earnings = fetch_earnings(today, end, api_key)
            for e in earnings:
                events.append({"event_date": e["date"], "event_type": "earnings",
                                "ticker": e["ticker"], "description": e["description"]})

    return events


def refresh_events(today: Optional[date] = None, horizon_days: int = 365,
                    sources: tuple[str, ...] = ("fomc", "macro", "opex", "earnings"),
                    dry_run: bool = False) -> int:
    events = gather_events(today=today, horizon_days=horizon_days, sources=sources)
    if not events:
        logger.info("no events gathered")
        return 0
    n = upsert_events(events, dry_run=dry_run)
    logger.info(f"events: {n} rows {'(dry-run)' if dry_run else 'upserted'} "
                f"across sources={sources}")
    return n


# ─────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="HORIZON events calendar")
    parser.add_argument("--source", choices=["all", "fomc", "macro", "opex", "earnings"],
                        default="all")
    parser.add_argument("--horizon-days", type=int, default=365,
                        help="How far forward to populate (default 365).")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sources: tuple[str, ...]
    if args.source == "all":
        sources = ("fomc", "macro", "opex", "earnings")
    else:
        sources = (args.source,)

    logger.info(f"=== events-calendar start (sources={sources}, "
                f"horizon_days={args.horizon_days}, dry_run={args.dry_run}) ===")
    try:
        refresh_events(horizon_days=args.horizon_days, sources=sources, dry_run=args.dry_run)
    except Exception:
        logger.exception("events-calendar failed")
        return 1
    logger.info("=== events-calendar end ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
