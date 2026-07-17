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
from zoneinfo import ZoneInfo

from scipy.stats import norm, t as student_t

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


def calculate_bucket_probability(predicted_tmax_f: float, sigma: float,
                                  bucket_floor: Optional[float],
                                  bucket_cap: Optional[float]) -> tuple[float, str]:
    """Verbatim port of cp4_kelly_sizer.calculate_bucket_probability() --
    same scipy.stats calls (norm / student_t), same eff_floor/eff_cap
    sentinels, same 2-sigma threshold, same df=5 -- not an independent
    approximation. Operator requirement 2026-07-18: the dashboard's
    Model % must match CP4's actual math, not just disclose that it
    differs, since CP4 switches to Student-t (fatter tails) beyond 2sigma
    and a pure-Gaussian display number understated tail-bucket
    probability relative to what CP4 actually sizes trades against.

    Returns (prob, distribution_used) where distribution_used is
    'gaussian' or 'student_t' -- exposed so the dashboard can show which
    one applied, same as CP4's own return value.
    """
    eff_floor = bucket_floor if bucket_floor is not None else -9999.0
    eff_cap = bucket_cap if bucket_cap is not None else 9999.0
    sigma = max(sigma, 0.01)

    bucket_mid = (eff_floor + eff_cap) / 2.0
    sigma_dist = abs(bucket_mid - predicted_tmax_f) / sigma

    if sigma_dist > 2.0:
        prob = (student_t.cdf(eff_cap, df=5, loc=predicted_tmax_f, scale=sigma)
                - student_t.cdf(eff_floor, df=5, loc=predicted_tmax_f, scale=sigma))
        dist = 'student_t'
    else:
        prob = (norm.cdf(eff_cap, loc=predicted_tmax_f, scale=sigma)
                - norm.cdf(eff_floor, loc=predicted_tmax_f, scale=sigma))
        dist = 'gaussian'

    return max(0.0, min(1.0, float(prob))), dist


# ---------------------------------------------------------------------------
# Market close time (Kalshi's "Last Trading Time") -- from operator-
# confirmed Kalshi contract-rules data 2026-07-18, NOT derived from CP4's
# SETTLEMENT_UTC_HOUR above (that's CP4's own still-unfixed "4PM local"
# settlement-clock assumption, a different and currently-flagged-buggy
# concept -- do not conflate the two).
#
# Legacy-template cities: Last Trading Time = 11:59:00 PM LOCAL CIVIL time
# (DST-aware -- e.g. EDT in summer, EST in winter for KMIA).
# New-template cities: Last Trading Time = 11:58:59 PM LOCAL STANDARD time
# -- always the station's fixed non-DST offset, even during DST months.
# Same DST-vs-standard-time care as weather_station_climatology's LST
# conversion (build_station_climatology_2026_07_17.py) -- Phoenix-style
# "always standard time" stations are exactly what this distinction exists
# to get right.
LEGACY_TEMPLATE_CITIES = {'KDEN', 'KMIA', 'KNYC', 'KORD', 'KAUS'}
NEW_TEMPLATE_CITIES = {'KLAX', 'KPHX', 'KDFW'}

STATION_TZ = {
    'KMIA': 'America/New_York', 'KNYC': 'America/New_York',
    'KDEN': 'America/Denver',
    'KLAX': 'America/Los_Angeles',
    'KPHX': 'America/Phoenix',
    'KAUS': 'America/Chicago', 'KORD': 'America/Chicago', 'KDFW': 'America/Chicago',
}
# Fixed (never DST-adjusted) standard UTC offset, for the new-template rule.
STATION_STANDARD_OFFSET_HOURS = {
    'KMIA': -5, 'KNYC': -5,
    'KDEN': -7,
    'KLAX': -8,
    'KPHX': -7,
    'KAUS': -6, 'KORD': -6, 'KDFW': -6,
}


def market_close_time_utc(station_code: str, target_date: date) -> Optional[datetime]:
    """Kalshi's Last Trading Time for this contract, in UTC. Returns None
    for a station with no confirmed template classification -- never
    guessed at."""
    if station_code in LEGACY_TEMPLATE_CITIES:
        local_dt = datetime(target_date.year, target_date.month, target_date.day,
                             23, 59, 0, tzinfo=ZoneInfo(STATION_TZ[station_code]))
        return local_dt.astimezone(timezone.utc)
    if station_code in NEW_TEMPLATE_CITIES:
        offset_hours = STATION_STANDARD_OFFSET_HOURS[station_code]
        # Standard-time instant is a fixed offset from UTC by definition --
        # no zoneinfo/DST resolution needed or wanted here.
        naive_standard = datetime(target_date.year, target_date.month, target_date.day, 23, 58, 59)
        return (naive_standard - timedelta(hours=offset_hours)).replace(tzinfo=timezone.utc)
    return None
