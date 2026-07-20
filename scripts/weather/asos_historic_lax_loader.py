#!/usr/bin/env python3
"""
Historical IEM ASOS loader for KLAX -- 1-minute and 5-minute resolutions.

Source files (operator-staged, flat CSV-in-.txt) live in
infrastructure/docs/WeatherBHN/. Real per-file date ranges were confirmed by
reading file content directly -- do NOT trust the embedded year ranges in
the filenames, they don't line up with what's actually inside (e.g.
ASOS1M-LAX14-11.txt is actually 2010-2014, not what the "14-11" suffix
would suggest, and the directory index doc's own filenames don't even
match what's on disk).

1-minute header: station,station_name,valid(UTC),tmpf,dwpf,sknt,drct,
                 gust_drct,gust_sknt,ptype,precip,pres1,pres2,pres3
5-minute header: station,station_name,valid(UTC),tmpf,dwpf,sknt,drct
The 5-minute IEM product is a genuinely reduced field set -- no
gust/precip/pressure columns exist in that feed at all. The two target
tables are shaped to match, not padded for symmetry.

Missing values in the source are the literal string "M", converted to
SQL NULL at load time.

Loads via a TEMP staging table + COPY (fast enough for the ~25M rows in
the 1-minute set) followed by INSERT ... ON CONFLICT DO NOTHING into the
real bronze table, so re-running the loader after a partial/interrupted
run is safe and idempotent.

Usage:
    python3 asos_historic_lax_loader.py --resolution 1min --dry-run
    python3 asos_historic_lax_loader.py --resolution 1min
    python3 asos_historic_lax_loader.py --resolution 5min

Environment:
    DATABASE_URL  PostgreSQL connection string (from /etc/bhn-trading/env)
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psycopg2

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "infrastructure" / "docs" / "WeatherBHN"

STATION_CODE = "KLAX"
SOURCE_STATION_ID = "LAX"  # what the IEM files call it -- sanity-checked, not trusted blindly

# Chronological order matters only for progress reporting, not correctness
# (natural key + ON CONFLICT DO NOTHING makes load order irrelevant to the
# final result). Real ranges confirmed by reading file content 2026-07-20:
#   ASOS1M-LAX14-11.txt  2010-01-01 08:28 -> 2014-12-31 00:58
#   ASOS1M-LAX20-15.txt  2015-01-01 00:00 -> 2020-12-31 00:58
#   ASOS1M-LAX26-21.txt  2021-01-01 08:00 -> 2026-07-18 05:34
#   ASOS5M-LAX14-11.txt  2011-01-01 00:00 -> 2014-12-31 22:55
#   ASOS5M-LAX17-15.txt  2015-01-01 00:00 -> 2017-12-31 22:55
#   ASOS5M-LAX21-18.txt  2018-01-01 00:00 -> 2021-12-31 12:55
RESOLUTIONS = {
    "1min": {
        "table": "weather_bronze_asos_historic_lax_1min",
        "files": ["ASOS1M-LAX14-11.txt", "ASOS1M-LAX20-15.txt", "ASOS1M-LAX26-21.txt"],
        "expected_header": ["station", "station_name", "valid(UTC)", "tmpf", "dwpf",
                            "sknt", "drct", "gust_drct", "gust_sknt", "ptype",
                            "precip", "pres1", "pres2", "pres3"],
        "columns": ["station_code", "observed_at", "air_temp_f", "dew_point_temp_f",
                    "wind_speed_kt", "wind_direction_deg", "gust_direction_deg",
                    "gust_speed_kt", "precip_type_code", "precip_in",
                    "pressure_1_inhg", "pressure_2_inhg", "pressure_3_inhg", "source_file"],
    },
    "5min": {
        "table": "weather_bronze_asos_historic_lax_5min",
        "files": ["ASOS5M-LAX14-11.txt", "ASOS5M-LAX17-15.txt", "ASOS5M-LAX21-18.txt"],
        "expected_header": ["station", "station_name", "valid(UTC)", "tmpf", "dwpf",
                             "sknt", "drct"],
        "columns": ["station_code", "observed_at", "air_temp_f", "dew_point_temp_f",
                    "wind_speed_kt", "wind_direction_deg", "source_file"],
    },
}

BATCH_SIZE = 200_000


def parse_args():
    p = argparse.ArgumentParser(description="Historical IEM ASOS KLAX bronze loader")
    p.add_argument("--resolution", required=True, choices=list(RESOLUTIONS),
                   help="Which source set to load")
    p.add_argument("--dry-run", action="store_true",
                   help="Validate format + report stats only, no DB writes")
    p.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    p.add_argument("--data-dir", default=None,
                   help="Override source file directory (default: repo-relative "
                        "infrastructure/docs/WeatherBHN)")
    return p.parse_args()


def _m(v: str) -> Optional[str]:
    """'M' or empty -> None (SQL NULL); otherwise pass the raw string through
    for Postgres to cast (skips a redundant float() round-trip)."""
    v = v.strip()
    return None if (v == "" or v == "M") else v


def _parse_observed_at(valid_str: str) -> Optional[str]:
    """'2010-01-01 08:28' -> ISO string with explicit UTC offset for COPY."""
    valid_str = valid_str.strip()
    try:
        datetime.strptime(valid_str, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return valid_str.replace(" ", "T") + ":00+00"


def _iter_rows(path: Path, resolution: str):
    """Yields cleaned row tuples (in RESOLUTIONS[resolution]['columns'] order)."""
    cfg = RESOLUTIONS[resolution]
    warned_station = False
    with path.open("r", encoding="ascii", errors="replace", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != cfg["expected_header"]:
            sys.exit(f"ERROR: {path.name} header mismatch.\n"
                      f"  expected: {cfg['expected_header']}\n"
                      f"  got:      {header}")

        for row in reader:
            if not row:
                continue
            observed_at = _parse_observed_at(row[2])
            if observed_at is None:
                continue  # unparseable timestamp -- skip, don't crash the whole load

            if row[0].strip() != SOURCE_STATION_ID and not warned_station:
                print(f"WARNING: {path.name} has non-{SOURCE_STATION_ID} station "
                      f"rows (saw {row[0]!r}) -- loading anyway, verify this is expected",
                      file=sys.stderr)
                warned_station = True

            if resolution == "1min":
                yield (
                    STATION_CODE, observed_at,
                    _m(row[3]), _m(row[4]), _m(row[5]), _m(row[6]),
                    _m(row[7]), _m(row[8]),
                    (row[9].strip() or None) if row[9].strip() != "M" else None,
                    _m(row[10]), _m(row[11]), _m(row[12]), _m(row[13]),
                    path.name,
                )
            else:
                yield (
                    STATION_CODE, observed_at,
                    _m(row[3]), _m(row[4]), _m(row[5]), _m(row[6]),
                    path.name,
                )


def _flush_batch(cur, table: str, columns: list[str], rows: list[tuple]) -> int:
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

    col_list = ", ".join(columns)
    cur.copy_expert(f"COPY {stage} ({col_list}) FROM STDIN WITH (FORMAT csv, NULL '')", buf)

    cur.execute(f"""
        INSERT INTO {table} ({col_list})
        SELECT {col_list} FROM {stage}
        ON CONFLICT (station_code, observed_at) DO NOTHING
    """)
    return cur.rowcount


def dry_run(resolution: str):
    cfg = RESOLUTIONS[resolution]
    for fname in cfg["files"]:
        path = DATA_DIR / fname
        if not path.is_file():
            print(f"{fname}: NOT FOUND at {path}")
            continue
        n = 0
        n_missing_temp = 0
        first_ts = last_ts = None
        sample = []
        for row in _iter_rows(path, resolution):
            n += 1
            if row[2] is None:
                n_missing_temp += 1
            if first_ts is None:
                first_ts = row[1]
            last_ts = row[1]
            if len(sample) < 3:
                sample.append(row)
        print(f"\n=== {fname} ({resolution}) ===")
        print(f"  rows parsed:      {n:,}")
        print(f"  first observed_at: {first_ts}")
        print(f"  last observed_at:  {last_ts}")
        print(f"  air_temp_f NULL:   {n_missing_temp:,} ({(n_missing_temp/n*100 if n else 0):.1f}%)")
        print(f"  sample rows:")
        for s in sample:
            print(f"    {s}")
    print("\nDRY RUN complete -- no DB writes.")


def live_run(resolution: str, batch_size: int):
    cfg = RESOLUTIONS[resolution]
    table = cfg["table"]
    columns = cfg["columns"]

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("ERROR: DATABASE_URL not set -- source /etc/bhn-trading/env first")

    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    total_read = 0
    total_inserted = 0
    t0 = time.time()

    try:
        with conn.cursor() as cur:
            for fname in cfg["files"]:
                path = DATA_DIR / fname
                if not path.is_file():
                    sys.exit(f"ERROR: {fname} not found at {path}")

                print(f"Loading {fname} ...")
                batch = []
                file_read = 0
                file_inserted = 0
                for row in _iter_rows(path, resolution):
                    batch.append(row)
                    if len(batch) >= batch_size:
                        file_inserted += _flush_batch(cur, table, columns, batch)
                        file_read += len(batch)
                        conn.commit()
                        elapsed = time.time() - t0
                        print(f"  ... {file_read:,} rows read, {file_inserted:,} new "
                              f"so far ({elapsed:.0f}s elapsed)")
                        batch = []
                if batch:
                    file_inserted += _flush_batch(cur, table, columns, batch)
                    file_read += len(batch)
                    conn.commit()

                total_read += file_read
                total_inserted += file_inserted
                print(f"  {fname}: {file_read:,} rows read, {file_inserted:,} new rows inserted")

            cur.execute(f"SELECT COUNT(*), MIN(observed_at), MAX(observed_at) FROM {table}")
            count, tmin, tmax = cur.fetchone()
    finally:
        conn.close()

    elapsed = time.time() - t0
    print(f"\n=== LIVE RUN COMPLETE ({resolution}) ===")
    print(f"Rows read:      {total_read:,}")
    print(f"Rows inserted:  {total_inserted:,} (rest were already present -- idempotent re-run)")
    print(f"Table rows:     {count:,}")
    print(f"Time range:     {tmin} - {tmax}")
    print(f"Elapsed:        {elapsed:.0f}s")


def main():
    global DATA_DIR
    args = parse_args()
    if args.data_dir:
        DATA_DIR = Path(args.data_dir)
    if args.dry_run:
        dry_run(args.resolution)
    else:
        live_run(args.resolution, args.batch_size)


if __name__ == "__main__":
    main()
