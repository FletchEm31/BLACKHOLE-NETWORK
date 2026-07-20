#!/usr/bin/env python3
"""
One-off loader: KDFW / KPHX NOAA daily summaries pulled from NCEI's public
Access Data Service (no auth needed, unlike the CDO web-export CSVs the
other 6 stations were manually staged from):

    https://www.ncei.noaa.gov/access/services/data/v1
        ?dataset=daily-summaries&stations={GHCND_ID}
        &dataTypes=TMAX,TMIN,PRCP,SNOW,SNWD,AWND,WSF2,WSF5,WSFG,PSUN
        &startDate=1970-01-01&endDate=2026-07-19
        &format=csv&units=standard

units=standard returns °F/inches/mph directly -- matches
weather_bronze_noaa_daily_actuals' column units exactly, no conversion.

Station IDs confirmed via web search against NOAA's own station detail
pages (not guessed): USW00003927 = Dallas-Fort Worth Intl (coords match
this project's KDFW lat/lon almost exactly); USW00023183 = Phoenix Sky
Harbor Intl ("PHOENIX AIRPORT, AZ US" in GHCND).

Usage:
    python3 load-noaa-access-api-kdfw-kphx.py --file /tmp/noaa_kdfw.csv --icao KDFW --station-id USW00003927
    python3 load-noaa-access-api-kdfw-kphx.py --file /tmp/noaa_kphx.csv --icao KPHX --station-id USW00023183
"""
import argparse
import csv
import os
import sys

import psycopg2


def _f(v):
    v = (v or "").strip()
    return None if v == "" else float(v)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--file", required=True)
    p.add_argument("--icao", required=True)
    p.add_argument("--station-id", required=True)
    p.add_argument("--station-name", default="")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    rows = []
    with open(args.file, newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append((
                args.station_id, args.icao, args.station_name, r["DATE"],
                _f(r.get("TMAX")), _f(r.get("TMIN")), None,
                _f(r.get("PRCP")), _f(r.get("SNOW")), _f(r.get("SNWD")),
                _f(r.get("AWND")), _f(r.get("WSF2")), _f(r.get("WSF5")),
                _f(r.get("WSFG")), _f(r.get("PSUN")),
            ))

    print(f"{args.icao}: {len(rows)} rows parsed from {args.file}")
    if args.dry_run:
        print("sample:", rows[0])
        print("sample:", rows[-1])
        return

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("ERROR: DATABASE_URL not set")

    conn = psycopg2.connect(db_url)
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, """
            INSERT INTO weather_bronze_noaa_daily_actuals
                (station_id, icao_code, station_name, date, tmax_f, tmin_f, tavg_f,
                 prcp_in, snow_in, snwd_in, awnd_mph, wsf2_mph, wsf5_mph, wsfg_mph, psun_pct)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (station_id, date) DO NOTHING
        """, rows, page_size=1000)
    conn.commit()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*), MIN(date), MAX(date) FROM weather_bronze_noaa_daily_actuals WHERE icao_code = %s",
            (args.icao,),
        )
        count, dmin, dmax = cur.fetchone()
    conn.close()
    print(f"{args.icao}: table now has {count} rows, {dmin} -> {dmax}")


if __name__ == "__main__":
    import psycopg2.extras
    main()
