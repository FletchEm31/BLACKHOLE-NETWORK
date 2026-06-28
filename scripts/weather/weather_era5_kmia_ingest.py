#!/usr/bin/env python3
"""
ERA5 KMIA ingestion script.
Reads a downloaded GRIB or ZIP file, parses with cfgrib/xarray,
and upserts into weather_bronze_era5_kmia.

Usage:
    python3 weather_era5_kmia_ingest.py --file <path> [--dry-run]

Environment:
    DATABASE_URL  PostgreSQL connection string (from /etc/bhn-trading/env)
"""

import argparse
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import cfgrib
import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
import xarray as xr

# ERA5 cfgrib short names that map directly to table column names
EXPECTED_VARS = ["u10", "v10", "d2m", "t2m", "msl", "sp", "tp", "tcc", "cbh",
                 "mwd", "mwp", "sst", "swh"]

# Bounding box (loose — sanity filter only)
LAT_MIN, LAT_MAX = 25.4, 26.1
LON_MIN, LON_MAX = -80.6, -79.9


def parse_args():
    p = argparse.ArgumentParser(description="ERA5 KMIA bronze ingest")
    p.add_argument("--file", required=True, help="Path to GRIB or ZIP file")
    p.add_argument("--dry-run", action="store_true",
                   help="Print stats only, do not write to DB")
    return p.parse_args()


def extract_grib(path: Path) -> Path:
    """If path is a ZIP, extract and return path to the GRIB file inside."""
    if path.suffix.lower() == ".zip":
        tmp = tempfile.mkdtemp(prefix="era5_kmia_")
        print(f"Extracting {path} -> {tmp}")
        with zipfile.ZipFile(path) as zf:
            zf.extractall(tmp)
        gribs = list(Path(tmp).rglob("*.grib")) + list(Path(tmp).rglob("*.grib2"))
        if not gribs:
            sys.exit("ERROR: no .grib/.grib2 file found inside ZIP")
        return gribs[0]
    return path


def load_grib(grib_path: Path) -> pd.DataFrame:
    """
    Open all datasets from GRIB, merge on (valid_time, latitude, longitude),
    return flat DataFrame.

    cfgrib returns one Dataset per typeOfLevel (e.g. heightAboveGround,
    meanSea, surface). We merge with join='outer' so ocean variables
    (mwd, mwp, sst, swh) appear as NaN over land cells.
    """
    print(f"Opening GRIB: {grib_path}")
    datasets = cfgrib.open_datasets(str(grib_path))
    print(f"  {len(datasets)} dataset(s) found in GRIB")

    if not datasets:
        sys.exit("ERROR: cfgrib returned no datasets")

    merged = xr.merge(datasets, join="outer", compat="override")
    df = merged.to_dataframe().reset_index()

    # Normalize column names — cfgrib may use 'time' or 'valid_time'
    if "time" in df.columns and "valid_time" not in df.columns:
        df = df.rename(columns={"time": "valid_time"})

    # Ensure valid_time is timezone-aware UTC
    if "valid_time" in df.columns:
        df["valid_time"] = pd.to_datetime(df["valid_time"], utc=True)

    # Spatial filter sanity check
    before = len(df)
    df = df[
        (df["latitude"] >= LAT_MIN) & (df["latitude"] <= LAT_MAX) &
        (df["longitude"] >= LON_MIN) & (df["longitude"] <= LON_MAX)
    ]
    if len(df) < before:
        print(f"  Spatial filter: {before} -> {len(df)} rows")

    # Round lat/lon to 4 dp to avoid float drift
    df["latitude"] = df["latitude"].round(4)
    df["longitude"] = df["longitude"].round(4)

    return df


def build_insert_rows(df: pd.DataFrame) -> list:
    """Convert DataFrame to list of dicts matching the table schema."""
    rows = []
    for _, r in df.iterrows():
        row = {
            "valid_time": r.get("valid_time"),
            "latitude":   r.get("latitude"),
            "longitude":  r.get("longitude"),
        }
        for col in EXPECTED_VARS:
            val = r.get(col, np.nan)
            row[col] = None if (val is None or (isinstance(val, float) and np.isnan(val))) else float(val)
        rows.append(row)
    return rows


UPSERT_SQL = """
INSERT INTO weather_bronze_era5_kmia
    (valid_time, latitude, longitude,
     u10, v10, d2m, t2m, msl, sp, tp, tcc, cbh,
     mwd, mwp, sst, swh)
VALUES
    (%(valid_time)s, %(latitude)s, %(longitude)s,
     %(u10)s, %(v10)s, %(d2m)s, %(t2m)s, %(msl)s, %(sp)s, %(tp)s, %(tcc)s, %(cbh)s,
     %(mwd)s, %(mwp)s, %(sst)s, %(swh)s)
ON CONFLICT (valid_time, latitude, longitude) DO UPDATE SET
    u10         = EXCLUDED.u10,
    v10         = EXCLUDED.v10,
    d2m         = EXCLUDED.d2m,
    t2m         = EXCLUDED.t2m,
    msl         = EXCLUDED.msl,
    sp          = EXCLUDED.sp,
    tp          = EXCLUDED.tp,
    tcc         = EXCLUDED.tcc,
    cbh         = EXCLUDED.cbh,
    mwd         = EXCLUDED.mwd,
    mwp         = EXCLUDED.mwp,
    sst         = EXCLUDED.sst,
    swh         = EXCLUDED.swh,
    ingested_at = NOW();
"""


def dry_run_report(df: pd.DataFrame, rows: list):
    print("\n=== DRY RUN ===")
    print(f"Total rows:     {len(rows):,}")
    print(f"Unique times:   {df['valid_time'].nunique()}")
    print(f"Lat range:      {df['latitude'].min()} - {df['latitude'].max()}")
    print(f"Lon range:      {df['longitude'].min()} - {df['longitude'].max()}")
    print(f"Time range:     {df['valid_time'].min()} - {df['valid_time'].max()}")
    print("\nVariable coverage (non-null %):")
    for col in EXPECTED_VARS:
        if col in df.columns:
            pct = df[col].notna().mean() * 100
            print(f"  {col:<6} {pct:5.1f}%")
        else:
            print(f"  {col:<6}  NOT FOUND in GRIB")
    print("\nSample rows:")
    sample_cols = ["valid_time", "latitude", "longitude"] + [c for c in EXPECTED_VARS if c in df.columns]
    print(df[sample_cols].head(3).to_string())
    print("\nDRY RUN complete -- no DB writes.")


def live_run(rows: list):
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("ERROR: DATABASE_URL not set -- source /etc/bhn-trading/env first")

    print(f"\nConnecting to DB...")
    conn = psycopg2.connect(db_url)
    conn.autocommit = False

    with conn.cursor() as cur:
        print(f"Upserting {len(rows):,} rows...")
        psycopg2.extras.execute_batch(cur, UPSERT_SQL, rows, page_size=500)
        conn.commit()

        cur.execute("SELECT COUNT(*), MIN(valid_time), MAX(valid_time) FROM weather_bronze_era5_kmia;")
        count, tmin, tmax = cur.fetchone()

    conn.close()
    print(f"\n=== LIVE RUN COMPLETE ===")
    print(f"Table rows:  {count:,}")
    print(f"Time range:  {tmin} - {tmax}")


def main():
    args = parse_args()
    grib_path = extract_grib(Path(args.file))
    df = load_grib(grib_path)
    rows = build_insert_rows(df)

    if args.dry_run:
        dry_run_report(df, rows)
    else:
        live_run(rows)


if __name__ == "__main__":
    main()
