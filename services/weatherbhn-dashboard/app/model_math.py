"""Faithful port of cp4_kelly_sizer.py / cp3_inference.py's sigma math, for
display-only use when no weather_position_exits row exists yet for a given
station/date (i.e. no bucket has qualified as a trade this cycle -- CP4
still evaluates every bucket every cycle, per weather_gold_contract_ledger's
SKIP rows, but never persists sigma_used unless something qualifies).

Read-only: this module never writes to the DB and never feeds into any
trading decision -- it exists purely so the dashboard can show a sigma
value on days nothing has qualified, computed with the exact same formula
CP4 itself uses, per the operator's requirement that sigma markers be
"computed fresh from that day's actual mu/sigma, never fixed/hardcoded."

Kept as a duplicate of cp4_kelly_sizer.py's logic (not imported) since this
service deploys independently of scripts/weather/ -- verify these stay in
sync if the source formula ever changes.
"""
import math
from datetime import date, datetime, timedelta, timezone
from typing import Optional

# Mirrors cp4_kelly_sizer.py SETTLEMENT_UTC_HOUR exactly.
SETTLEMENT_UTC_HOUR = {
    'KLAX': 0, 'KDEN': 22, 'KMIA': 20, 'KNYC': 20, 'KAUS': 21, 'KORD': 21,
}
SIGMA_FLOOR_RATIO = 0.20


def settlement_dt(station_code: str, target_date: date) -> datetime:
    settle_hour = SETTLEMENT_UTC_HOUR.get(station_code, 20)
    if settle_hour == 0:
        return (datetime(target_date.year, target_date.month, target_date.day,
                          0, 0, 0, tzinfo=timezone.utc) + timedelta(days=1))
    return datetime(target_date.year, target_date.month, target_date.day,
                     settle_hour, 0, 0, tzinfo=timezone.utc)


def calculate_time_decayed_sigma(base_sigma: float, station_code: str,
                                  evaluation_time_utc: datetime,
                                  target_date: date) -> float:
    """Verbatim port of cp4_kelly_sizer.calculate_time_decayed_sigma()."""
    now = evaluation_time_utc if evaluation_time_utc.tzinfo else evaluation_time_utc.replace(tzinfo=timezone.utc)
    settle = settlement_dt(station_code, target_date)
    hours_remaining = max((settle - now).total_seconds() / 3600.0, 0.0)
    decay_factor = math.sqrt(min(hours_remaining, 24.0) / 24.0)
    return max(base_sigma * decay_factor, base_sigma * SIGMA_FLOOR_RATIO)


def season_for(d: date) -> str:
    """Verbatim port of cp3_inference._season_for()."""
    m = d.month
    if m in (12, 1, 2): return 'winter'
    if m in (3, 4, 5):  return 'spring'
    if m in (6, 7, 8):  return 'summer'
    return 'fall'
