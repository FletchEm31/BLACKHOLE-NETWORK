#!/usr/bin/env python3
"""
build_station_climatology_2026_07_17.py — populate weather_station_climatology.

One-time (idempotent, safe to re-run) build of the climatological average
daily-high time-of-day, per (station_code, month), from
weather_bronze_noaa_hourly_normals. See
sql/migrations/2026-07-17e-create-station-climatology-and-entry-hours-to-avg-dailyhigh.sql
for the full rationale and the hour-convention verification.

Method, per (station_code, month):
  1. Pull all weather_bronze_noaa_hourly_normals rows for the station's
     source ICAO where month_day falls in that month.
  2. Average temp_normal_f per hour across all days in the month, take the
     argmax hour. This hour is LST (Local Standard Time — the station's
     fixed, non-DST-observing UTC offset, confirmed empirically, NOT civil
     local time and NOT UTC — see migration file header).
  3. average_dailyhigh_time_utc: convert the LST hour to UTC using the
     station's STANDARD (never DST-adjusted) UTC offset. This is a fixed,
     year-round-constant conversion by definition of LST.
  4. average_dailyhigh_time_local: convert that same UTC instant to civil
     local clock time using the station's real IANA timezone, on a
     mid-month reference date (day=15) — this resolves DST correctly per
     actual historical rule for that specific month, so March/November
     (the two DST-transition months in the US) land on whichever side of
     the transition the 15th actually falls on. This is a deliberate,
     documented approximation for a display/reference value — not used for
     the entry_hours_to_avg_dailyhigh calculation itself, which is always
     computed from average_dailyhigh_time_utc (a real DST-independent
     instant) combined with the trade's own target_date.

KPHX (Phoenix) never observes DST (IANA zone America/Phoenix) — using real
IANA zones here means no special-casing is needed; zoneinfo applies
Arizona's permanent-standard-time rule automatically.

KDEN has no exact hourly-normals station — KAFF (Buckley AFB, ~10mi away)
is used as an explicit, labeled proxy per operator decision 2026-07-17
(source_station_note is set so this is never silently mistaken for exact
Denver Intl data).
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras


def _get_conn():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        host = os.environ.get('PG_HOST')
        port = os.environ.get('PG_PORT', '5432')
        db   = os.environ.get('PG_DB')
        user = os.environ.get('PG_USER')
        pwd  = os.environ.get('PG_PASSWORD', '')
        if host and db and user:
            import urllib.parse
            db_url = (f'postgresql://{urllib.parse.quote(user)}:'
                      f'{urllib.parse.quote(pwd)}@{host}:{port}/{db}')
        else:
            sys.exit('ERROR: Neither DATABASE_URL nor PG_HOST/PG_DB/PG_USER are set')
    return psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor)


# station_code -> (source_station_icao, note or None)
# note is set whenever the source station is not an exact match for the
# trading station -- surfaced in the DB row so it's never silently confused
# with exact data downstream.
STATION_SOURCE: dict[str, tuple[str, str | None]] = {
    'KMIA': ('KMIA', None),
    'KLAX': ('KLAX', None),
    'KDEN': ('KAFF', 'proxy, not exact — KAFF is Buckley AFB, ~10mi from Denver Intl; '
                      'no exact KDEN station in weather_bronze_noaa_hourly_normals'),
    # Future 5-city scope (not wired into the trading pipeline yet, but
    # populated here so the table doesn't need a second pass when they go
    # live — see operator scope note on the dashboard task).
    'KPHX': ('KPHX', None),
    'KAUS': ('KAUS', None),
    'KORD': ('KORD', None),
    # KDFW: no exact station currently loaded in weather_bronze_noaa_hourly_normals
    # (only KDAL, a different specific field, ~13mi away) -- left unpopulated
    # rather than silently proxied. Revisit if/when true KDFW hourly-normals
    # data is sourced.
    # KNYC: no Central Park hourly-normals row -- only KJFK. Matches the
    # already-established KNYC->KJFK precedent used for daily actuals
    # elsewhere in this codebase (load-noaa-actuals.py), but left out here
    # until an operator explicitly confirms the same proxy treatment should
    # apply to the hourly-normals climatology use case.
}

# station_code -> (IANA timezone, standard (non-DST) UTC offset hours)
# Standard offset is used for the LST->UTC step (LST is fixed year-round by
# definition). The IANA zone is used only for the local-civil-time display
# conversion, where DST must be resolved per actual historical date.
STATION_TZ: dict[str, tuple[str, int]] = {
    'KMIA': ('America/New_York', -5),
    'KLAX': ('America/Los_Angeles', -8),
    'KDEN': ('America/Denver', -7),
    'KPHX': ('America/Phoenix', -7),   # no DST -- standard offset == year-round offset
    'KAUS': ('America/Chicago', -6),
    'KORD': ('America/Chicago', -6),
}

REFERENCE_YEAR = 2024  # arbitrary, non-leap-sensitive; only month/day-15/hour matter


def compute_argmax_hour(cur, source_icao: str, month: int) -> int:
    cur.execute("""
        SELECT hour, AVG(temp_normal_f) AS avg_temp
        FROM weather_bronze_noaa_hourly_normals
        WHERE icao_code = %s
          AND split_part(month_day, '-', 1)::int = %s
        GROUP BY hour
        ORDER BY avg_temp DESC
        LIMIT 1
    """, (source_icao, month))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f'no hourly-normals rows for {source_icao} month={month}')
    return int(row['hour'])


def lst_hour_to_utc_time(lst_hour: int, standard_offset_hours: int):
    """LST is defined as a fixed year-round offset from UTC (never DST-adjusted).
    utc_hour = lst_hour - standard_offset_hours (mod 24), since
    LST = UTC + standard_offset_hours  =>  UTC = LST - standard_offset_hours."""
    utc_hour = (lst_hour - standard_offset_hours) % 24
    return utc_hour  # integer hour, 0-23; normals data has no finer resolution


def utc_hour_to_local_civil_time(utc_hour: int, month: int, iana_tz: str):
    """Resolve DST-aware civil local clock time for a representative
    mid-month (day=15) date in REFERENCE_YEAR. Uses real zoneinfo so the
    correct historical DST rule applies automatically (including Arizona's
    permanent standard time)."""
    utc_dt = datetime(REFERENCE_YEAR, month, 15, utc_hour, 0, tzinfo=timezone.utc)
    local_dt = utc_dt.astimezone(ZoneInfo(iana_tz))
    return local_dt.time()


_UPSERT_SQL = """
    INSERT INTO weather_station_climatology (
        station_code, month, average_dailyhigh_hour_lst,
        average_dailyhigh_time_utc, average_dailyhigh_time_local,
        local_timezone, source_station_icao, source_station_note,
        source_normal_period, computed_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
    ON CONFLICT (station_code, month) DO UPDATE SET
        average_dailyhigh_hour_lst   = EXCLUDED.average_dailyhigh_hour_lst,
        average_dailyhigh_time_utc   = EXCLUDED.average_dailyhigh_time_utc,
        average_dailyhigh_time_local = EXCLUDED.average_dailyhigh_time_local,
        local_timezone                = EXCLUDED.local_timezone,
        source_station_icao           = EXCLUDED.source_station_icao,
        source_station_note           = EXCLUDED.source_station_note,
        source_normal_period          = EXCLUDED.source_normal_period,
        computed_at                    = NOW()
"""


def main():
    dry_run = '--dry-run' in sys.argv
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT normal_period FROM weather_bronze_noaa_hourly_normals LIMIT 1")
            row = cur.fetchone()
            normal_period = row['normal_period'] if row else None

            rows_written = 0
            for station_code, (source_icao, note) in STATION_SOURCE.items():
                iana_tz, std_offset = STATION_TZ[station_code]
                for month in range(1, 13):
                    lst_hour = compute_argmax_hour(cur, source_icao, month)
                    utc_hour = lst_hour_to_utc_time(lst_hour, std_offset)
                    local_time = utc_hour_to_local_civil_time(utc_hour, month, iana_tz)

                    print(f'{station_code} month={month:2d}  source={source_icao:5s}  '
                          f'lst_hour={lst_hour:2d}  utc={utc_hour:02d}:00  '
                          f'local={local_time.strftime("%H:%M")} {iana_tz}'
                          + (f'  [{note}]' if note else ''))

                    if not dry_run:
                        cur.execute(_UPSERT_SQL, (
                            station_code, month, lst_hour,
                            f'{utc_hour:02d}:00:00', local_time.strftime('%H:%M:%S'),
                            iana_tz, source_icao, note, normal_period,
                        ))
                        rows_written += 1

        if dry_run:
            print('\n--dry-run: no rows written.')
        else:
            conn.commit()
            print(f'\nWrote {rows_written} rows to weather_station_climatology.')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
