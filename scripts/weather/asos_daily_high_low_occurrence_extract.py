#!/usr/bin/env python3
"""
Time-of-occurrence extraction for daily high/low (item 2 of
WEATHERBHN-ASOS-MULTIVARIATE-PROBABILITY-MODEL-SCOPING).

For every local calendar day, finds the timestamp and value of the daily
high and low of the true-NWS series -- a trailing 5-minute average of
air_temp_f, recomputed every minute (same reconstruction as item 1's
degree-crossing extraction). This has to scan the continuous averaged
series per day, not just crossing points: weather_silver_asos_degree_
crossings only marks degree BOUNDARIES, the actual peak/trough between
two crossings isn't captured there.

Chunked per station-year, same memory-safety rationale as item 1 (LA is
a shared production trading box with confirmed low headroom).

KNOWN EDGE EFFECT: chunking by UTC calendar year means the local calendar
day that straddles each year boundary (Dec 31 in fixed local-standard-time)
gets split across two query chunks. Whichever chunk runs first computes
that one day's high/low from partial data (roughly 16 or 8 hours instead
of 24); ON CONFLICT DO NOTHING means the second chunk's attempt at the
same day is silently skipped. This affects at most ~1 day/year/station
(~34 days total across the full 2011-2026 archive, both cities combined,
out of ~11,000 station-days) -- accepted as a negligible fraction for this
characterization pass, not re-engineered around.

Fixed local-standard-time (no DST), same as item 1:
    KLAX -> UTC-8 year round
    KMIA -> UTC-5 year round

Usage:
    python3 asos_daily_high_low_occurrence_extract.py --station KLAX --dry-run
    python3 asos_daily_high_low_occurrence_extract.py --station KLAX
    python3 asos_daily_high_low_occurrence_extract.py --station KMIA

Environment:
    DATABASE_URL  PostgreSQL connection string (from /etc/bhn-trading/env)
"""
import argparse
import os
import sys
import time

import psycopg2

STATION_CFG = {
    "KLAX": {
        "source_table": "weather_bronze_asos_historic_lax_1min",
        "utc_offset_hours": -8,
        "years": range(2010, 2027),
    },
    "KMIA": {
        "source_table": "weather_bronze_asos_historic_mia_1min",
        "utc_offset_hours": -5,
        "years": range(2011, 2027),
    },
}

TARGET_TABLE = "weather_silver_asos_daily_high_low_occurrence"

_INSERT_COLUMNS = ("station_code", "local_date", "season",
                   "high_temp_f", "high_occurred_at", "high_local_minute",
                   "low_temp_f", "low_occurred_at", "low_local_minute")


def _core_select_sql(source_table: str) -> str:
    """Shared WITH...SELECT body for both INSERT (live) and COUNT (dry-run)."""
    return f"""
        WITH avg5 AS (
            SELECT
                observed_at,
                AVG(air_temp_f) OVER (
                    ORDER BY observed_at
                    RANGE BETWEEN INTERVAL '4 minutes' PRECEDING AND CURRENT ROW
                ) AS rolling_avg
            FROM {source_table}
            WHERE station_code = %(station_code)s
              AND air_temp_f IS NOT NULL
              AND air_temp_f BETWEEN -20 AND 130
              AND observed_at >= %(start)s AND observed_at < %(end)s
        ),
        localized AS (
            SELECT
                observed_at,
                rolling_avg,
                (observed_at + (%(offset)s || ' hours')::interval) AS local_ts
            FROM avg5
        ),
        highs AS (
            SELECT DISTINCT ON (local_ts::date)
                local_ts::date AS local_date,
                observed_at AS high_occurred_at,
                rolling_avg AS high_temp_f,
                (EXTRACT(HOUR FROM local_ts) * 60 + EXTRACT(MINUTE FROM local_ts))::smallint AS high_local_minute
            FROM localized
            ORDER BY local_ts::date, rolling_avg DESC, observed_at ASC
        ),
        lows AS (
            SELECT DISTINCT ON (local_ts::date)
                local_ts::date AS local_date,
                observed_at AS low_occurred_at,
                rolling_avg AS low_temp_f,
                (EXTRACT(HOUR FROM local_ts) * 60 + EXTRACT(MINUTE FROM local_ts))::smallint AS low_local_minute
            FROM localized
            ORDER BY local_ts::date, rolling_avg ASC, observed_at ASC
        )
        SELECT
            %(station_code)s::text AS station_code,
            h.local_date,
            CASE
                WHEN EXTRACT(MONTH FROM h.local_date) IN (12,1,2) THEN 'winter'
                WHEN EXTRACT(MONTH FROM h.local_date) IN (3,4,5)  THEN 'spring'
                WHEN EXTRACT(MONTH FROM h.local_date) IN (6,7,8)  THEN 'summer'
                ELSE 'fall'
            END AS season,
            h.high_temp_f, h.high_occurred_at, h.high_local_minute,
            l.low_temp_f, l.low_occurred_at, l.low_local_minute
        FROM highs h
        JOIN lows l ON l.local_date = h.local_date
    """


def parse_args():
    p = argparse.ArgumentParser(description="ASOS daily high/low occurrence extraction")
    p.add_argument("--station", required=True, choices=list(STATION_CFG))
    p.add_argument("--dry-run", action="store_true",
                   help="Report per-year day counts only, no writes")
    return p.parse_args()


def main():
    args = parse_args()
    cfg = STATION_CFG[args.station]

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("ERROR: DATABASE_URL not set")

    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    t0 = time.time()
    total = 0

    try:
        if not args.dry_run:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {TARGET_TABLE} WHERE station_code = %s", (args.station,))
                existing = cur.fetchone()[0]
            if existing:
                sys.exit(f"{args.station}: {existing:,} rows already exist in {TARGET_TABLE} -- "
                          f"delete existing rows for this station first if re-running")

        core_sql = _core_select_sql(cfg["source_table"])

        for year in cfg["years"]:
            params = {
                "station_code": args.station,
                "start": f"{year}-01-01",
                "end": f"{year + 1}-01-01",
                "offset": str(cfg["utc_offset_hours"]),
            }
            with conn.cursor() as cur:
                if args.dry_run:
                    cur.execute(f"SELECT COUNT(*), MIN(local_date), MAX(local_date) FROM ({core_sql}) sub", params)
                    n, dmin, dmax = cur.fetchone()
                    print(f"{args.station} {year}: {n:,} days would be inserted "
                          f"({dmin} -> {dmax})" if n else f"{args.station} {year}: 0 days")
                else:
                    col_list = ", ".join(_INSERT_COLUMNS)
                    cur.execute(
                        f"INSERT INTO {TARGET_TABLE} ({col_list}) {core_sql} "
                        f"ON CONFLICT (station_code, local_date) DO NOTHING",
                        params,
                    )
                    n = cur.rowcount
                    conn.commit()
                    print(f"{args.station} {year}: {n:,} days inserted "
                          f"({time.time() - t0:.0f}s elapsed)")
            total += n

        if args.dry_run:
            print(f"\nDRY RUN complete -- {total:,} total days across {args.station}'s "
                  f"archive, no writes made.")
        else:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*), MIN(local_date), MAX(local_date) "
                            f"FROM {TARGET_TABLE} WHERE station_code = %s", (args.station,))
                count, dmin, dmax = cur.fetchone()
            print(f"\n=== {args.station} COMPLETE ===")
            print(f"Total days: {count:,}")
            print(f"Range: {dmin} -> {dmax}")
            print(f"Elapsed: {time.time() - t0:.0f}s")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
