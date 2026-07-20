#!/usr/bin/env python3
"""
Running high/low-so-far extraction -- built to give the item-4 analog-day
matching backtest a fair, informed baseline: running high/low already
observed as of each checkpoint hour, from the continuous true-NWS series
(trailing 5-min average, recomputed every minute -- same reconstruction
as items 1/2), not from the 3-hour trajectory snapshots (which would
understate what's actually known by that hour).

Same fixed local-standard-time (no DST) convention, same 8 checkpoint
hours (0,3,6,9,12,15,18,21), same per-station-year chunking as the other
items (LA is a shared production trading box with confirmed low
headroom).

Usage:
    python3 asos_running_extremes_extract.py --station KLAX --dry-run
    python3 asos_running_extremes_extract.py --station KLAX
    python3 asos_running_extremes_extract.py --station KMIA

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
TARGET_TABLE = "weather_silver_asos_running_extremes"

_INSERT_COLUMNS = ("station_code", "local_date", "local_hour",
                   "running_high_so_far_f", "running_low_so_far_f")


def _core_select_sql(source_table: str) -> str:
    hours_values = ", ".join(f"({h})" for h in CHECKPOINT_HOURS)
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
                observed_at, rolling_avg,
                (observed_at + (%(offset)s || ' hours')::interval) AS local_ts
            FROM avg5
        ),
        running AS (
            SELECT
                local_ts::date AS local_date,
                EXTRACT(HOUR FROM local_ts)::int AS local_hour_of_row,
                MAX(rolling_avg) OVER (
                    PARTITION BY local_ts::date ORDER BY local_ts
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS running_high,
                MIN(rolling_avg) OVER (
                    PARTITION BY local_ts::date ORDER BY local_ts
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS running_low,
                local_ts
            FROM localized
        ),
        checkpoints AS (
            SELECT local_date, h.local_hour
            FROM (SELECT DISTINCT local_date FROM running) d
            CROSS JOIN (VALUES {hours_values}) AS h(local_hour)
        )
        -- For each (date, checkpoint hour), take the running max/min from the
        -- LAST row at-or-before that checkpoint hour (DISTINCT ON picks the
        -- latest local_ts within the hour bucket, giving the true
        -- running-so-far value as of that hour boundary).
        SELECT DISTINCT ON (c.local_date, c.local_hour)
            %(station_code)s::text AS station_code,
            c.local_date, c.local_hour,
            r.running_high AS running_high_so_far_f,
            r.running_low AS running_low_so_far_f
        FROM checkpoints c
        JOIN running r
          ON r.local_date = c.local_date AND r.local_hour_of_row <= c.local_hour
        ORDER BY c.local_date, c.local_hour, r.local_ts DESC
    """


def parse_args():
    p = argparse.ArgumentParser(description="ASOS running high/low-so-far extraction")
    p.add_argument("--station", required=True, choices=list(STATION_CFG))
    p.add_argument("--dry-run", action="store_true")
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
                    print(f"{args.station} {year}: {n:,} rows would be inserted")
                else:
                    col_list = ", ".join(_INSERT_COLUMNS)
                    cur.execute(
                        f"INSERT INTO {TARGET_TABLE} ({col_list}) {core_sql} "
                        f"ON CONFLICT (station_code, local_date, local_hour) DO NOTHING",
                        params,
                    )
                    n = cur.rowcount
                    conn.commit()
                    print(f"{args.station} {year}: {n:,} rows inserted "
                          f"({time.time() - t0:.0f}s elapsed)")
            total += n

        if args.dry_run:
            print(f"\nDRY RUN complete -- {total:,} total rows, no writes made.")
        else:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*), COUNT(DISTINCT local_date) "
                            f"FROM {TARGET_TABLE} WHERE station_code = %s", (args.station,))
                count, ndays = cur.fetchone()
            print(f"\n=== {args.station} COMPLETE ===")
            print(f"Total rows: {count:,} across {ndays:,} distinct days")
            print(f"Elapsed: {time.time() - t0:.0f}s")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
