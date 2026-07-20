#!/usr/bin/env python3
"""
Daily trajectory snapshot extraction -- foundational table for items 3
(multivariate conditioning) and 4 (analog-day matching) of
WEATHERBHN-ASOS-MULTIVARIATE-PROBABILITY-MODEL-SCOPING.

For each local calendar day, pulls the exact 1-minute row (temp, dewpoint,
wind speed/direction, station pressure, precip flag) at 8 fixed
local-standard-time checkpoints: 00, 03, 06, 09, 12, 15, 18, 21. Exact-
minute join (not nearest-neighbor) -- a checkpoint simply comes back NULL
if that exact minute is missing from the source ("M" gap), which is
expected and fine for a first-pass characterization.

Same fixed local-standard-time (no DST) convention as items 1/2:
    KLAX -> UTC-8 year round
    KMIA -> UTC-5 year round

Chunked per station-year, same memory-safety rationale as items 1/2.

Usage:
    python3 asos_daily_trajectory_snapshots_extract.py --station KLAX --dry-run
    python3 asos_daily_trajectory_snapshots_extract.py --station KLAX
    python3 asos_daily_trajectory_snapshots_extract.py --station KMIA

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

CHECKPOINT_HOURS = (0, 3, 6, 9, 12, 15, 18, 21)
TARGET_TABLE = "weather_silver_asos_daily_trajectory_snapshots"

_INSERT_COLUMNS = ("station_code", "local_date", "local_hour", "observed_at",
                   "air_temp_f", "dew_point_temp_f", "wind_speed_kt",
                   "wind_direction_deg", "pressure_1_inhg", "has_precip")


def _core_select_sql(source_table: str) -> str:
    hours_list = ", ".join(str(h) for h in CHECKPOINT_HOURS)
    return f"""
        WITH days AS (
            SELECT DISTINCT (observed_at + (%(offset)s || ' hours')::interval)::date AS local_date
            FROM {source_table}
            WHERE station_code = %(station_code)s
              AND observed_at >= %(start)s AND observed_at < %(end)s
        ),
        checkpoints AS (
            SELECT
                d.local_date, h.local_hour,
                (d.local_date + (h.local_hour || ' hours')::interval
                 - (%(offset)s || ' hours')::interval) AS target_utc
            FROM days d
            CROSS JOIN (VALUES {", ".join(f"({h})" for h in CHECKPOINT_HOURS)}) AS h(local_hour)
        )
        SELECT
            %(station_code)s::text AS station_code,
            c.local_date, c.local_hour, s.observed_at,
            s.air_temp_f, s.dew_point_temp_f, s.wind_speed_kt,
            s.wind_direction_deg, s.pressure_1_inhg,
            (s.precip_type_code IS NOT NULL AND s.precip_type_code != 'NP') AS has_precip
        FROM checkpoints c
        JOIN {source_table} s
          ON s.station_code = %(station_code)s AND s.observed_at = c.target_utc
    """


def parse_args():
    p = argparse.ArgumentParser(description="ASOS daily trajectory snapshot extraction")
    p.add_argument("--station", required=True, choices=list(STATION_CFG))
    p.add_argument("--dry-run", action="store_true",
                   help="Report per-year snapshot counts only, no writes")
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
                    cur.execute(f"SELECT COUNT(*) FROM ({core_sql}) sub", params)
                    n = cur.fetchone()[0]
                    print(f"{args.station} {year}: {n:,} snapshots would be inserted")
                else:
                    col_list = ", ".join(_INSERT_COLUMNS)
                    cur.execute(
                        f"INSERT INTO {TARGET_TABLE} ({col_list}) {core_sql} "
                        f"ON CONFLICT (station_code, local_date, local_hour) DO NOTHING",
                        params,
                    )
                    n = cur.rowcount
                    conn.commit()
                    print(f"{args.station} {year}: {n:,} snapshots inserted "
                          f"({time.time() - t0:.0f}s elapsed)")
            total += n

        if args.dry_run:
            print(f"\nDRY RUN complete -- {total:,} total snapshots across {args.station}'s "
                  f"archive, no writes made.")
        else:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*), COUNT(DISTINCT local_date) "
                            f"FROM {TARGET_TABLE} WHERE station_code = %s", (args.station,))
                count, ndays = cur.fetchone()
            print(f"\n=== {args.station} COMPLETE ===")
            print(f"Total snapshots: {count:,} across {ndays:,} distinct days")
            print(f"Elapsed: {time.time() - t0:.0f}s")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
