#!/usr/bin/env python3
"""
Loader for month-chunked ASOS historical files fetched via
asos_monthly_fetch.sh (per "ASOS 1M-5M Automation Curl Data
Reqauests.txt" -- one station, one month, one resolution at a time).

Unlike the LAX/MIA loaders (operator-staged single/few large files, no
lat/lon columns), this source format includes GIS lat/lon per row --
confirmed from actual file content, not assumed from the doc:
    station,station_name,lat,lon,valid(UTC),tmpf,dwpf,sknt,drct,
    gust_drct,gust_sknt,ptype,precip,pres1,pres2,pres3

Loads every ``{station}_{resolution}_*.csv`` file in a directory,
chronologically, via a TEMP staging table + COPY, then
INSERT ... ON CONFLICT DO NOTHING into the real bronze table -- safe to
re-run after a partial/interrupted load.

Usage:
    python3 asos_historic_monthly_loader.py --station KDEN --resolution 1min \
        --iem-code DEN --data-dir /opt/bhn/trading/asos_raw/DEN_1min --dry-run
    python3 asos_historic_monthly_loader.py --station KDEN --resolution 1min \
        --iem-code DEN --data-dir /opt/bhn/trading/asos_raw/DEN_1min

Environment:
    DATABASE_URL  PostgreSQL connection string (from /etc/bhn-trading/env)
"""
from __future__ import annotations

import argparse
import csv
import glob
import io
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import psycopg2

_EXPECTED_HEADER = ["station", "station_name", "lat", "lon", "valid(UTC)",
                    "tmpf", "dwpf", "sknt", "drct", "gust_drct", "gust_sknt",
                    "ptype", "precip", "pres1", "pres2", "pres3"]

_COLUMNS = ["station_code", "observed_at", "latitude", "longitude",
            "air_temp_f", "dew_point_temp_f", "wind_speed_kt", "wind_direction_deg",
            "gust_direction_deg", "gust_speed_kt", "precip_type_code", "precip_in",
            "pressure_1_inhg", "pressure_2_inhg", "pressure_3_inhg", "source_file"]

BATCH_SIZE = 200_000


def _m(v: str) -> Optional[str]:
    v = v.strip()
    return None if (v == "" or v == "M") else v


def _parse_observed_at(valid_str: str) -> Optional[str]:
    valid_str = valid_str.strip()
    try:
        datetime.strptime(valid_str, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return valid_str.replace(" ", "T") + ":00+00"


def _iter_rows(path: Path, station_code: str, iem_code: str):
    warned = False
    with path.open("r", encoding="ascii", errors="replace", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != _EXPECTED_HEADER:
            sys.exit(f"ERROR: {path.name} header mismatch.\n"
                      f"  expected: {_EXPECTED_HEADER}\n  got:      {header}")
        for row in reader:
            if not row or len(row) < len(_EXPECTED_HEADER):
                continue  # malformed/truncated row (e.g. file still being written)
            observed_at = _parse_observed_at(row[4])
            if observed_at is None:
                continue
            if row[0].strip() != iem_code and not warned:
                print(f"WARNING: {path.name} has non-{iem_code} station rows "
                      f"(saw {row[0]!r}) -- loading anyway", file=sys.stderr)
                warned = True
            yield (
                station_code, observed_at, _m(row[2]), _m(row[3]),
                _m(row[5]), _m(row[6]), _m(row[7]), _m(row[8]),
                _m(row[9]), _m(row[10]),
                (row[11].strip() or None) if row[11].strip() != "M" else None,
                _m(row[12]), _m(row[13]), _m(row[14]), _m(row[15]),
                path.name,
            )


def _flush_batch(cur, table: str, rows: list[tuple]) -> int:
    if not rows:
        return 0
    stage = f"stage_{table}"
    cur.execute(f"CREATE TEMP TABLE IF NOT EXISTS {stage} (LIKE {table} INCLUDING DEFAULTS)")
    cur.execute(f"TRUNCATE {stage}")
    buf = io.StringIO()
    writer = csv.writer(buf)
    for r in rows:
        writer.writerow(["" if v is None else v for v in r])
    buf.seek(0)
    col_list = ", ".join(_COLUMNS)
    cur.copy_expert(f"COPY {stage} ({col_list}) FROM STDIN WITH (FORMAT csv, NULL '')", buf)
    cur.execute(f"""
        INSERT INTO {table} ({col_list})
        SELECT {col_list} FROM {stage}
        ON CONFLICT (station_code, observed_at) DO NOTHING
    """)
    return cur.rowcount


def parse_args():
    p = argparse.ArgumentParser(description="Monthly-chunked ASOS historical loader")
    p.add_argument("--station", required=True, help="ICAO station code, e.g. KDEN")
    p.add_argument("--iem-code", required=True, help="IEM 3-letter code, e.g. DEN")
    p.add_argument("--resolution", required=True, choices=["1min", "5min"])
    p.add_argument("--data-dir", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    return p.parse_args()


def main():
    args = parse_args()
    table = f"weather_bronze_asos_historic_{args.station[1:].lower()}_{args.resolution}"

    files = sorted(glob.glob(os.path.join(args.data_dir, f"{args.iem_code}_{args.resolution}_*.csv")))
    if not files:
        sys.exit(f"ERROR: no files matching {args.iem_code}_{args.resolution}_*.csv in {args.data_dir}")
    print(f"Found {len(files)} monthly files for {args.station} {args.resolution}")

    if args.dry_run:
        total = 0
        for fp in files:
            n = sum(1 for _ in _iter_rows(Path(fp), args.station, args.iem_code))
            print(f"  {os.path.basename(fp)}: {n:,} rows")
            total += n
        print(f"\nDRY RUN complete -- {total:,} total rows across {len(files)} files, no writes made.")
        return

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("ERROR: DATABASE_URL not set")

    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    t0 = time.time()
    total_read = total_inserted = 0

    try:
        with conn.cursor() as cur:
            for fp in files:
                path = Path(fp)
                batch = []
                file_read = file_inserted = 0
                for row in _iter_rows(path, args.station, args.iem_code):
                    batch.append(row)
                    if len(batch) >= args.batch_size:
                        file_inserted += _flush_batch(cur, table, batch)
                        file_read += len(batch)
                        conn.commit()
                        batch = []
                if batch:
                    file_inserted += _flush_batch(cur, table, batch)
                    file_read += len(batch)
                    conn.commit()
                total_read += file_read
                total_inserted += file_inserted
                print(f"  {path.name}: {file_read:,} read, {file_inserted:,} new "
                      f"({time.time() - t0:.0f}s elapsed)")

            cur.execute(f"SELECT COUNT(*), MIN(observed_at), MAX(observed_at) FROM {table}")
            count, tmin, tmax = cur.fetchone()
    finally:
        conn.close()

    print(f"\n=== {args.station} {args.resolution} COMPLETE ===")
    print(f"Rows read: {total_read:,}  Rows inserted: {total_inserted:,}")
    print(f"Table total: {count:,}  Range: {tmin} -> {tmax}")
    print(f"Elapsed: {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
