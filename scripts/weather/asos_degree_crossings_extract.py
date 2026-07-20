#!/usr/bin/env python3
"""
Degree-crossing extraction for the LAX/MIA multivariate probability model
(item 1 of WEATHERBHN-ASOS-MULTIVARIATE-PROBABILITY-MODEL-SCOPING).

For every 1-minute row, reconstructs the true NWS methodology -- a
trailing 5-minute average recomputed every minute (confirmed against NWS's
own ASOS documentation, not the raw instantaneous reading and not the
5-min subsample table, which is a pure snapshot) -- and logs every
whole-degree transition of that averaged series into
weather_silver_asos_degree_crossings.

Runs one station-year at a time on purpose: LA is a shared production
trading box with limited headroom (confirmed low free memory before
building this), and a single window-function pass over the full ~15M-row
combined archive risks starving the live trading pipeline. Chunking keeps
each query's working set to roughly one year of 1-minute data
(~500K-1.5M rows), independent of total archive size.

The 5-minute rolling window uses a time-based RANGE frame (INTERVAL '4
minutes' PRECEDING), not a row-count frame, so occasional gaps in the
1-minute feed don't silently shift the window to include stale readings
from far outside the true 5-minute span.

Local-standard-time (fixed, no DST) hour/date bucketing per station:
    KLAX -> UTC-8 year round
    KMIA -> UTC-5 year round
This is a working assumption for this characterization pass, not yet
validated against genuine CLI occurrence times.

Usage:
    python3 asos_degree_crossings_extract.py --station KLAX --dry-run
    python3 asos_degree_crossings_extract.py --station KLAX
    python3 asos_degree_crossings_extract.py --station KMIA

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
        "utc_offset_hours": -8,   # PST, fixed year-round
        "years": range(2010, 2027),
    },
    "KMIA": {
        "source_table": "weather_bronze_asos_historic_mia_1min",
        "utc_offset_hours": -5,   # EST, fixed year-round
        "years": range(2011, 2027),
    },
}

TARGET_TABLE = "weather_silver_asos_degree_crossings"


def _core_select_sql(source_table: str) -> str:
    """The WITH...SELECT body shared by both the INSERT (live run) and the
    COUNT wrapper (dry run) -- kept as one string so the two modes can
    never drift apart in what they compute. Only {source_table} is a
    Python-side substitution; everything else (%(name)s) is a psycopg2
    query parameter, filled in per-year at execute() time."""
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
        prevd AS (
            SELECT
                observed_at, rolling_avg,
                LAG(rolling_avg) OVER (ORDER BY observed_at) AS prev_avg,
                LAG(observed_at) OVER (ORDER BY observed_at) AS prev_at
            FROM avg5
        ),
        crossings AS (
            SELECT
                observed_at, rolling_avg, prev_avg,
                CASE WHEN rolling_avg > prev_avg THEN 'rising' ELSE 'falling' END AS direction,
                CASE WHEN rolling_avg > prev_avg THEN FLOOR(rolling_avg) ELSE CEIL(rolling_avg) END AS crossed_degree,
                (observed_at + (%(offset)s || ' hours')::interval) AS local_ts
            FROM prevd
            WHERE prev_avg IS NOT NULL
              AND observed_at - prev_at <= INTERVAL '2 minutes'  -- skip crossings spanning a real data gap
              AND FLOOR(rolling_avg) IS DISTINCT FROM FLOOR(prev_avg)
        )
        SELECT
            %(station_code)s::text AS station_code,
            observed_at AS crossing_at,
            rolling_avg AS rolling_avg_temp_f,
            prev_avg AS prev_rolling_avg_temp_f,
            direction,
            crossed_degree AS crossed_degree_f,
            local_ts::date AS local_date,
            EXTRACT(HOUR FROM local_ts)::smallint AS local_hour,
            CASE
                WHEN EXTRACT(MONTH FROM local_ts::date) IN (12,1,2) THEN 'winter'
                WHEN EXTRACT(MONTH FROM local_ts::date) IN (3,4,5)  THEN 'spring'
                WHEN EXTRACT(MONTH FROM local_ts::date) IN (6,7,8)  THEN 'summer'
                ELSE 'fall'
            END AS season
        FROM crossings
    """


_INSERT_COLUMNS = ("station_code", "crossing_at", "rolling_avg_temp_f", "prev_rolling_avg_temp_f",
                    "direction", "crossed_degree_f", "local_date", "local_hour", "season")


def parse_args():
    p = argparse.ArgumentParser(description="ASOS degree-crossing extraction")
    p.add_argument("--station", required=True, choices=list(STATION_CFG))
    p.add_argument("--dry-run", action="store_true",
                   help="Report per-year crossing counts only, no writes")
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
                          f"this script does not dedupe, delete existing rows for this station first")

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
                    cur.execute(f"SELECT COUNT(*), MIN(crossing_at), MAX(crossing_at) FROM ({core_sql}) sub", params)
                    n, tmin, tmax = cur.fetchone()
                    print(f"{args.station} {year}: {n:,} crossings would be inserted "
                          f"({tmin} -> {tmax})" if n else f"{args.station} {year}: 0 crossings")
                else:
                    col_list = ", ".join(_INSERT_COLUMNS)
                    cur.execute(f"INSERT INTO {TARGET_TABLE} ({col_list}) {core_sql}", params)
                    n = cur.rowcount
                    conn.commit()
                    print(f"{args.station} {year}: {n:,} crossings inserted "
                          f"({time.time() - t0:.0f}s elapsed)")
            total += n

        if args.dry_run:
            print(f"\nDRY RUN complete -- {total:,} total crossings across {args.station}'s "
                  f"archive, no writes made.")
        else:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*), MIN(crossing_at), MAX(crossing_at) "
                            f"FROM {TARGET_TABLE} WHERE station_code = %s", (args.station,))
                count, tmin, tmax = cur.fetchone()
            print(f"\n=== {args.station} COMPLETE ===")
            print(f"Total crossings: {count:,}")
            print(f"Range: {tmin} -> {tmax}")
            print(f"Elapsed: {time.time() - t0:.0f}s")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
