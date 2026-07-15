#!/usr/bin/env python3
"""
Historical gold feature backfill for XGBoost bootstrap training.

Queries Open-Meteo Historical Forecast API to reconstruct GFS predictions
for past dates, pairs with NOAA daily actuals, inserts into
weather_gold_city_day_features with data_source='historical_backfill'.

ON CONFLICT DO NOTHING — live rows are NEVER overwritten by historical.

Usage:
    DATABASE_URL=postgresql:///eventhorizon python3 weather_historical_backfill.py \\
        --station KLAX --start-date 2020-01-01 --end-date 2026-06-09 [--dry-run]

Environment: DATABASE_URL (peer auth: postgresql:///eventhorizon)
"""
import argparse
import os
import sys
import time
import urllib.request
import json
from datetime import date, timedelta

import psycopg2
import psycopg2.extras

# Station metadata
# KORD/KAUS/KNYC coordinates sourced from NOAA's ghcnd-stations.txt
# (USW00094846, USW00013904, USW00094728) 2026-07-15 — not estimated, to
# avoid silently pulling the wrong city's weather. KNYC = Central Park
# (USW00094728), NOT JFK (USW00094789) — Kalshi settles NYC weather
# against Central Park.
STATION_COORDS = {
    'KLAX': (33.9425, -118.4081),
    'KDEN': (39.8617, -104.6731),
    'KMIA': (25.7959, -80.2870),
    'KORD': (41.9603, -87.9317),
    'KAUS': (30.1831, -97.6800),
    'KNYC': (40.7789, -73.9692),
}
STATION_TIMEZONE = {
    'KLAX': 'America/Los_Angeles',
    'KDEN': 'America/Denver',
    'KMIA': 'America/New_York',
    'KORD': 'America/Chicago',
    'KAUS': 'America/Chicago',
    'KNYC': 'America/New_York',
}
TRADEABLE_STATIONS = list(STATION_COORDS.keys())

# Open-Meteo Historical Forecast API
# Returns archived GFS NWP output for past dates (~day-of forecast approximation)
OM_HIST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

# Rate limiting — Open-Meteo free tier
REQUEST_DELAY_S = 0.25

INSERT_SQL = """
INSERT INTO weather_gold_city_day_features (
    station_code, target_date, season,
    actual_tmax_f, actual_tmin_f,
    om_tmax_f, om_tmin_f,
    data_source
) VALUES (
    %(station_code)s, %(target_date)s, %(season)s,
    %(actual_tmax_f)s, %(actual_tmin_f)s,
    %(om_tmax_f)s, %(om_tmin_f)s,
    %(data_source)s
)
ON CONFLICT (station_code, target_date) DO NOTHING
"""


def _get_conn():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("ERROR: DATABASE_URL not set")
    return psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor)


def _season_for(d: date) -> str:
    m = d.month
    if m in (12, 1, 2): return 'winter'
    if m in (3, 4, 5):  return 'spring'
    if m in (6, 7, 8):  return 'summer'
    return 'fall'


def fetch_om_forecast(station_code: str, start: date, end: date) -> dict:
    """
    Fetch GFS historical forecast from Open-Meteo for a date range.
    Returns {date: {'tmax_f': float|None, 'tmin_f': float|None}}.
    """
    lat, lon = STATION_COORDS[station_code]
    tz = STATION_TIMEZONE[station_code]

    params = (
        f"latitude={lat}&longitude={lon}"
        f"&start_date={start.isoformat()}&end_date={end.isoformat()}"
        f"&daily=temperature_2m_max,temperature_2m_min"
        f"&temperature_unit=fahrenheit"
        f"&timezone={tz.replace('/', '%2F')}"
    )
    url = f"{OM_HIST_URL}?{params}"

    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        print(f"  ERROR fetching Open-Meteo {station_code} {start}→{end}: {exc}")
        return {}

    result = {}
    try:
        dates  = data['daily']['time']
        tmaxes = data['daily']['temperature_2m_max']
        tmins  = data['daily']['temperature_2m_min']
        for d_str, tmax, tmin in zip(dates, tmaxes, tmins):
            result[date.fromisoformat(d_str)] = {
                'tmax_f': float(tmax) if tmax is not None else None,
                'tmin_f': float(tmin) if tmin is not None else None,
            }
    except KeyError as exc:
        print(f"  ERROR parsing Open-Meteo response: {exc}")

    return result


def fetch_noaa_actuals(conn, station_code: str, start: date, end: date) -> dict:
    """
    Returns {date: {'tmax_f': float, 'tmin_f': float}} for rows where tmax_f IS NOT NULL.
    NOAA table uses icao_code (not station_code) and date (not target_date).
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT date, tmax_f, tmin_f
            FROM weather_bronze_noaa_daily_actuals
            WHERE icao_code = %s
              AND date BETWEEN %s AND %s
              AND tmax_f IS NOT NULL
        """, (station_code, start, end))
        return {r['date']: {'tmax_f': float(r['tmax_f']),
                            'tmin_f': float(r['tmin_f']) if r['tmin_f'] is not None else None}
                for r in cur.fetchall()}


def backfill_historical_gold(station_code: str, start_date: date, end_date: date,
                             dry_run: bool = True, conn=None) -> dict:
    """
    Backfills weather_gold_city_day_features for station_code between
    start_date and end_date using Open-Meteo GFS historical forecasts
    paired with NOAA daily actuals.

    Returns {'inserted': int, 'skipped_no_actual': int, 'skipped_no_forecast': int,
             'skipped_conflict': int (estimated from ON CONFLICT DO NOTHING)}
    """
    close_conn = conn is None
    if conn is None:
        conn = _get_conn()

    counters = {'inserted': 0, 'skipped_no_actual': 0, 'skipped_no_forecast': 0}

    # Batch by year to keep API requests manageable
    years = range(start_date.year, end_date.year + 1)

    try:
        for year in years:
            yr_start = max(start_date, date(year, 1, 1))
            yr_end   = min(end_date,   date(year, 12, 31))

            print(f"  [{station_code}] {year}: fetching Open-Meteo {yr_start}→{yr_end} ...",
                  end='', flush=True)

            forecasts = fetch_om_forecast(station_code, yr_start, yr_end)
            time.sleep(REQUEST_DELAY_S)

            actuals = fetch_noaa_actuals(conn, station_code, yr_start, yr_end)

            rows = []
            d = yr_start
            while d <= yr_end:
                fc  = forecasts.get(d)
                act = actuals.get(d)

                if act is None:
                    counters['skipped_no_actual'] += 1
                    d += timedelta(days=1)
                    continue

                if fc is None or fc['tmax_f'] is None:
                    counters['skipped_no_forecast'] += 1
                    d += timedelta(days=1)
                    continue

                rows.append({
                    'station_code':  station_code,
                    'target_date':   d,
                    'season':        _season_for(d),
                    'actual_tmax_f': act['tmax_f'],
                    'actual_tmin_f': act['tmin_f'],
                    'om_tmax_f':     fc['tmax_f'],
                    'om_tmin_f':     fc['tmin_f'],
                    'data_source':   'historical_backfill',
                })
                d += timedelta(days=1)

            print(f" {len(rows)} rows to insert")

            if dry_run:
                # Print sample rows for sanity check
                for r in rows[:3]:
                    print(f"    DRY RUN | {r['target_date']} {r['season']:6} "
                          f"om_tmax={r['om_tmax_f']:.1f}F actual={r['actual_tmax_f']:.1f}F")
                if len(rows) > 3:
                    last = rows[-1]
                    print(f"    ...     | {last['target_date']} {last['season']:6} "
                          f"om_tmax={last['om_tmax_f']:.1f}F actual={last['actual_tmax_f']:.1f}F")
            else:
                if rows:
                    with conn.cursor() as cur:
                        psycopg2.extras.execute_batch(cur, INSERT_SQL, rows, page_size=500)
                    conn.commit()

            counters['inserted'] += len(rows)

    finally:
        if close_conn:
            conn.close()

    return counters


def parse_args():
    p = argparse.ArgumentParser(description="Backfill gold table with historical GFS forecasts")
    p.add_argument('--dry-run', action='store_true',
                   help='Print what would be inserted; no DB writes')
    p.add_argument('--station', choices=TRADEABLE_STATIONS,
                   help='Single station (default: all three)')
    p.add_argument('--start-date', type=date.fromisoformat, default=date(2020, 1, 1))
    p.add_argument('--end-date',   type=date.fromisoformat, default=date(2026, 6, 9))
    p.add_argument('--years', type=int, default=None,
                   help='Last N years (overrides --start-date)')
    return p.parse_args()


def main():
    args = parse_args()

    end_date   = args.end_date
    start_date = args.start_date
    if args.years:
        start_date = date(end_date.year - args.years, end_date.month, end_date.day)

    stations = [args.station] if args.station else TRADEABLE_STATIONS

    mode = '[DRY RUN]' if args.dry_run else '[LIVE]'
    print(f"Historical backfill {mode}: {start_date} → {end_date}")
    print(f"Stations: {stations}")
    print()

    total = {'inserted': 0, 'skipped_no_actual': 0, 'skipped_no_forecast': 0}
    for station in stations:
        print(f"=== {station} ===")
        counts = backfill_historical_gold(station, start_date, end_date,
                                          dry_run=args.dry_run)
        for k, v in counts.items():
            total[k] += v
        print(f"  {station} total: {counts}")
        print()

    print(f"=== SUMMARY {'(DRY RUN — no writes)' if args.dry_run else '(LIVE)'} ===")
    for k, v in total.items():
        print(f"  {k}: {v}")

    if not args.dry_run:
        # Final verification
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT station_code, data_source, count(*),
                           min(target_date), max(target_date)
                    FROM weather_gold_city_day_features
                    GROUP BY station_code, data_source
                    ORDER BY station_code, data_source
                """)
                print()
                print("DB state after backfill:")
                for r in cur.fetchall():
                    print(f"  {r['station_code']:6} {r['data_source']:22} "
                          f"{r['count']:>5} rows  {r['min']} → {r['max']}")
        finally:
            conn.close()


if __name__ == "__main__":
    main()
