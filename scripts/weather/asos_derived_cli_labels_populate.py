#!/usr/bin/env python3
"""
Populate weather_silver_actuals_conformed with CLI-equivalent historical
labels derived from the reconstructed true-NWS series (trailing 5-min
average, fixed-LST calendar day -- weather_silver_asos_daily_high_low_
occurrence), for the full 15-year LAX/MIA archive.

Tagged actual_source = 'asos_derived_cli_algorithm' -- explicitly NOT
'nws_cli' -- so this cannot become a second version of the mislabeling
bug already fixed once this session (Visual Crossing data mislabeled as
certified NWS CLI). This is a different, disclosed, validated derivation,
not a claim to be the official NWS report.

Validation summary before populating (both cities, both reference
sources -- see WEATHERBHN-ASOS-MULTIVARIATE-... and the ASOS-to-CLI
session notes for detail):
    KLAX vs genuine CLI (13 clean/non-gap days): TMAX 13/13 within 0.5F,
        TMIN 13/13 within 1.0F
    KLAX vs NOAA daily actuals (5,773 days): TMAX 93.6% within 1F,
        TMIN 95.6% within 1F
    KMIA vs genuine CLI (29 clean/non-gap days): TMAX 28/29 within 1F,
        TMIN 27/29 within 1F
    KMIA vs NOAA daily actuals (5,452 days): TMAX 95.3% within 1F,
        TMIN 96.1% within 1F

Day-level data-completeness has NOT been filtered here -- known gaps
exist (most recent ~2 weeks near the live feed edge, and scattered
historical gaps), same as the underlying 1-min archive's own "M" missing
markers. Rows here are only as good as the underlying day's data
completeness; no per-day quality flag is set.

Usage:
    python3 asos_derived_cli_labels_populate.py --station KLAX --dry-run
    python3 asos_derived_cli_labels_populate.py --station KLAX
    python3 asos_derived_cli_labels_populate.py --station KMIA

Environment:
    DATABASE_URL  PostgreSQL connection string (from /etc/bhn-trading/env)
"""
import argparse
import os
import sys

import psycopg2

STATION_CITY = {"KLAX": "Los Angeles", "KMIA": "Miami"}
ACTUAL_SOURCE = "asos_derived_cli_algorithm"


def parse_args():
    p = argparse.ArgumentParser(description="Populate ASOS-derived CLI-equivalent labels")
    p.add_argument("--station", required=True, choices=list(STATION_CITY))
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("ERROR: DATABASE_URL not set")

    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM weather_silver_actuals_conformed "
                "WHERE station_code = %s AND actual_source = %s",
                (args.station, ACTUAL_SOURCE),
            )
            existing = cur.fetchone()[0]
        if existing and not args.dry_run:
            sys.exit(f"{args.station}: {existing:,} rows already exist under actual_source="
                      f"'{ACTUAL_SOURCE}' -- delete existing rows for this station first if re-running")

        select_sql = """
            SELECT %(city)s::text AS city, %(station)s::text AS station_code,
                   local_date AS target_date, high_temp_f, low_temp_f
            FROM weather_silver_asos_daily_high_low_occurrence
            WHERE station_code = %(station)s
        """
        params = {"city": STATION_CITY[args.station], "station": args.station}

        if args.dry_run:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*), MIN(target_date), MAX(target_date) FROM ({select_sql}) sub", params)
                n, dmin, dmax = cur.fetchone()
            print(f"{args.station}: {n:,} rows would be inserted under actual_source='{ACTUAL_SOURCE}' "
                  f"({dmin} -> {dmax})")
            return

        with conn.cursor() as cur:
            cur.execute(f"""
                INSERT INTO weather_silver_actuals_conformed
                    (city, station_code, target_date, final_tmax_f, final_tmin_f,
                     actual_source, report_issued_at, is_final)
                SELECT city, station_code, target_date, high_temp_f, low_temp_f,
                       '{ACTUAL_SOURCE}', NULL, TRUE
                FROM ({select_sql}) sub
                ON CONFLICT (station_code, target_date, actual_source) DO NOTHING
            """, params)
            n = cur.rowcount
        conn.commit()

        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*), MIN(target_date), MAX(target_date) "
                "FROM weather_silver_actuals_conformed "
                "WHERE station_code = %s AND actual_source = %s",
                (args.station, ACTUAL_SOURCE),
            )
            count, dmin, dmax = cur.fetchone()
        print(f"{args.station}: {n:,} rows inserted under actual_source='{ACTUAL_SOURCE}'")
        print(f"Table now has {count:,} rows for this station/source, {dmin} -> {dmax}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
