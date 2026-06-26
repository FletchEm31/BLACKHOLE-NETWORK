#!/usr/bin/env python3
"""load-noaa-actuals.py — Bulk loader for NOAA GHCND daily summaries and
1981-2010 hourly normals into the eventhorizon database.

Handles both CDO export formats:
  - Old format (Miami, Denver): STATION, NAME, LATITUDE, LONGITUDE, ELEVATION,
    DATE, COL, COL_ATTRIBUTES, COL2, COL2_ATTRIBUTES, ...
  - New format (LA, JFK, Chicago): STATION, NAME, DATE, COL, COL2, ...

Run from LA as root after SCPing the CSV files:
    python3 scripts/load-noaa-actuals.py --dir /tmp/noaa-csvs

Or specify individual file sets:
    python3 scripts/load-noaa-actuals.py --dir /tmp/noaa-csvs --daily-only
    python3 scripts/load-noaa-actuals.py --dir /tmp/noaa-csvs --hourly-only

Prerequisites:
    sudo -u postgres pg_dump eventhorizon > /mnt/eh-nvme-hot/backups/pre-noaa-actuals-$(date +%Y%m%d-%H%M).sql
    sudo -u postgres psql -d eventhorizon -f sql/migrations/2026-06-25-noaa-actuals.sql
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("noaa_loader")


# ── Station ICAO mapping ────────────────────────────────────────────────────

DAILY_ICAO_MAP: dict[str, str] = {
    "USW00012839": "KMIA",   # Miami International Airport
    "USW00094846": "KORD",   # Chicago O'Hare International Airport
    "USW00003017": "KDEN",   # Denver International Airport
    "USW00023174": "KLAX",   # Los Angeles International Airport
    "USW00094789": "KJFK",   # JFK International Airport
}

HOURLY_ICAO_MAP: dict[str, str] = {
    "USW00012839": "KMIA",   # Miami International Airport
    "USW00023036": "KAFF",   # Aurora Buckley Field ANGB (≠ KDEN/Denver Intl)
}

# Expected CDO filenames (partial match — strip leading path)
DAILY_FILE_PATTERNS = [
    "NOAA Daily Summary - Miami",
    "NOAA Daily Summary - Chicago",
    "NOAA Daily Summary - Los Angeles",
    "NOAA Daily Summary - New York",
    "NOAA Daily Summary - Denver",
]

HOURLY_FILE_PATTERNS = [
    "NOAA Hourly Weather - Miami",
    "NOAA Hourly Weather - Denver",
]


# ── Helpers ─────────────────────────────────────────────────────────────────

def _pg_conn() -> psycopg2.extensions.connection:
    """Connect to eventhorizon. Tries peer auth (run as postgres) first,
    falls back to env-configured password if PGPASSWORD is set."""
    dsn_parts = ["dbname=eventhorizon"]
    pg_host = os.environ.get("PGHOST")
    pg_user = os.environ.get("PGUSER", "ehuser")
    pg_pass = os.environ.get("PGPASSWORD")
    if pg_host:
        dsn_parts += [f"host={pg_host}", f"user={pg_user}"]
    if pg_pass:
        dsn_parts.append(f"password={pg_pass}")
    dsn = " ".join(dsn_parts)
    return psycopg2.connect(dsn)


def _f(val: Optional[str]) -> Optional[float]:
    """Parse a CDO numeric field; empty/whitespace → None."""
    if val is None:
        return None
    v = val.strip()
    if v == "" or v.upper() in ("T", "M", "-9999", "-999.9"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _parse_daily_date(val: str) -> datetime.date:
    """Parse M/D/YYYY CDO date → Python date."""
    return datetime.strptime(val.strip(), "%m/%d/%Y").date()


def _find_files(directory: Path, patterns: list[str]) -> list[Path]:
    found: list[Path] = []
    for f in sorted(directory.iterdir()):
        if f.suffix.lower() == ".csv":
            for pat in patterns:
                if pat.lower() in f.name.lower():
                    found.append(f)
                    break
    return found


# ── Daily actuals loader ─────────────────────────────────────────────────────

def load_daily_file(conn: psycopg2.extensions.connection, path: Path) -> int:
    """Parse one CDO daily summary CSV and upsert into
    weather_bronze_noaa_daily_actuals. Returns number of rows upserted."""
    logger.info(f"Loading daily: {path.name}")
    rows: list[tuple] = []

    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            station_id = raw.get("STATION", "").strip()
            icao_code = DAILY_ICAO_MAP.get(station_id)
            if not icao_code:
                continue  # unknown station in this file — skip

            try:
                obs_date = _parse_daily_date(raw["DATE"])
            except (KeyError, ValueError) as e:
                logger.warning(f"Bad date in row {reader.line_num}: {e}")
                continue

            rows.append((
                station_id,
                icao_code,
                raw.get("NAME", "").strip().strip('"'),
                obs_date,
                _f(raw.get("TMAX")),
                _f(raw.get("TMIN")),
                _f(raw.get("TAVG")),
                _f(raw.get("PRCP")),
                _f(raw.get("SNOW")),
                _f(raw.get("SNWD")),
                _f(raw.get("AWND")),
                _f(raw.get("PSUN")),
                _f(raw.get("WSF2")),
                _f(raw.get("WSF5")),
                _f(raw.get("WSFG")),
            ))

    if not rows:
        logger.warning(f"No usable rows in {path.name}")
        return 0

    sql = """
        INSERT INTO weather_bronze_noaa_daily_actuals
            (station_id, icao_code, station_name, date,
             tmax_f, tmin_f, tavg_f,
             prcp_in, snow_in, snwd_in,
             awnd_mph, psun_pct,
             wsf2_mph, wsf5_mph, wsfg_mph)
        VALUES %s
        ON CONFLICT (station_id, date) DO UPDATE SET
            tmax_f       = COALESCE(EXCLUDED.tmax_f,    weather_bronze_noaa_daily_actuals.tmax_f),
            tmin_f       = COALESCE(EXCLUDED.tmin_f,    weather_bronze_noaa_daily_actuals.tmin_f),
            tavg_f       = COALESCE(EXCLUDED.tavg_f,    weather_bronze_noaa_daily_actuals.tavg_f),
            prcp_in      = COALESCE(EXCLUDED.prcp_in,   weather_bronze_noaa_daily_actuals.prcp_in),
            snow_in      = COALESCE(EXCLUDED.snow_in,   weather_bronze_noaa_daily_actuals.snow_in),
            snwd_in      = COALESCE(EXCLUDED.snwd_in,   weather_bronze_noaa_daily_actuals.snwd_in),
            awnd_mph     = COALESCE(EXCLUDED.awnd_mph,  weather_bronze_noaa_daily_actuals.awnd_mph),
            psun_pct     = COALESCE(EXCLUDED.psun_pct,  weather_bronze_noaa_daily_actuals.psun_pct),
            wsf2_mph     = COALESCE(EXCLUDED.wsf2_mph,  weather_bronze_noaa_daily_actuals.wsf2_mph),
            wsf5_mph     = COALESCE(EXCLUDED.wsf5_mph,  weather_bronze_noaa_daily_actuals.wsf5_mph),
            wsfg_mph     = COALESCE(EXCLUDED.wsfg_mph,  weather_bronze_noaa_daily_actuals.wsfg_mph),
            loaded_at    = NOW()
    """
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, page_size=2000)
    conn.commit()

    logger.info(f"  → {len(rows):,} rows upserted from {path.name}")
    return len(rows)


# ── Hourly normals loader ────────────────────────────────────────────────────

def load_hourly_file(conn: psycopg2.extensions.connection, path: Path) -> int:
    """Parse one CDO hourly normals CSV and upsert into
    weather_bronze_noaa_hourly_normals. Returns number of rows upserted."""
    logger.info(f"Loading hourly normals: {path.name}")
    rows: list[tuple] = []

    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            station_id = raw.get("STATION", "").strip()
            icao_code = HOURLY_ICAO_MAP.get(station_id)
            if not icao_code:
                logger.warning(f"Unknown hourly station: {station_id}")
                continue

            date_str = raw.get("DATE", "").strip()  # e.g. "01-01T01:00:00"
            if not date_str or "T" not in date_str:
                continue

            month_day, time_part = date_str.split("T", 1)
            try:
                hour = int(time_part.split(":")[0])
            except (ValueError, IndexError):
                continue

            rows.append((
                station_id,
                icao_code,
                raw.get("NAME", "").strip().strip('"'),
                month_day,
                hour,
                date_str,
                _f(raw.get("HLY-TEMP-NORMAL")),
                _f(raw.get("HLY-TEMP-10PCTL")),
                _f(raw.get("HLY-TEMP-90PCTL")),
                _f(raw.get("HLY-DEWP-NORMAL")),
                _f(raw.get("HLY-DEWP-10PCTL")),
                _f(raw.get("HLY-DEWP-90PCTL")),
                _f(raw.get("HLY-HIDX-NORMAL")),
                _f(raw.get("HLY-WCHL-NORMAL")),
                _f(raw.get("HLY-PRES-NORMAL")),
                _f(raw.get("HLY-PRES-10PCTL")),
                _f(raw.get("HLY-PRES-90PCTL")),
                _f(raw.get("HLY-WIND-AVGSPD")),
                _f(raw.get("HLY-WIND-VCTDIR")),
                _f(raw.get("HLY-WIND-VCTSPD")),
                _f(raw.get("HLY-WIND-1STDIR")),
                _f(raw.get("HLY-WIND-1STPCT")),
                _f(raw.get("HLY-WIND-2NDDIR")),
                _f(raw.get("HLY-WIND-2NDPCT")),
                _f(raw.get("HLY-WIND-PCTCLM")),
                _f(raw.get("HLY-CLOD-PCTCLR")),
                _f(raw.get("HLY-CLOD-PCTFEW")),
                _f(raw.get("HLY-CLOD-PCTSCT")),
                _f(raw.get("HLY-CLOD-PCTBKN")),
                _f(raw.get("HLY-CLOD-PCTOVC")),
                _f(raw.get("HLY-CLDH-NORMAL")),
                _f(raw.get("HLY-HTDH-NORMAL")),
            ))

    if not rows:
        logger.warning(f"No usable rows in {path.name}")
        return 0

    sql = """
        INSERT INTO weather_bronze_noaa_hourly_normals
            (station_id, icao_code, station_name,
             month_day, hour, normal_date_str,
             temp_normal_f, temp_10pct_f, temp_90pct_f,
             dewp_normal_f, dewp_10pct_f, dewp_90pct_f,
             hidx_normal_f, wchl_normal_f,
             pres_normal, pres_10pct, pres_90pct,
             wind_avgspd_mph, wind_vctdir_deg, wind_vctspd_mph,
             wind_1stdir, wind_1stpct, wind_2nddir, wind_2ndpct, wind_pctclm,
             clod_pct_clr, clod_pct_few, clod_pct_sct, clod_pct_bkn, clod_pct_ovc,
             cldh_normal, htdh_normal)
        VALUES %s
        ON CONFLICT (station_id, month_day, hour) DO UPDATE SET
            temp_normal_f   = EXCLUDED.temp_normal_f,
            temp_10pct_f    = EXCLUDED.temp_10pct_f,
            temp_90pct_f    = EXCLUDED.temp_90pct_f,
            dewp_normal_f   = EXCLUDED.dewp_normal_f,
            dewp_10pct_f    = EXCLUDED.dewp_10pct_f,
            dewp_90pct_f    = EXCLUDED.dewp_90pct_f,
            hidx_normal_f   = EXCLUDED.hidx_normal_f,
            wchl_normal_f   = EXCLUDED.wchl_normal_f,
            pres_normal     = EXCLUDED.pres_normal,
            pres_10pct      = EXCLUDED.pres_10pct,
            pres_90pct      = EXCLUDED.pres_90pct,
            wind_avgspd_mph = EXCLUDED.wind_avgspd_mph,
            wind_vctdir_deg = EXCLUDED.wind_vctdir_deg,
            wind_vctspd_mph = EXCLUDED.wind_vctspd_mph,
            wind_1stdir     = EXCLUDED.wind_1stdir,
            wind_1stpct     = EXCLUDED.wind_1stpct,
            wind_2nddir     = EXCLUDED.wind_2nddir,
            wind_2ndpct     = EXCLUDED.wind_2ndpct,
            wind_pctclm     = EXCLUDED.wind_pctclm,
            clod_pct_clr    = EXCLUDED.clod_pct_clr,
            clod_pct_few    = EXCLUDED.clod_pct_few,
            clod_pct_sct    = EXCLUDED.clod_pct_sct,
            clod_pct_bkn    = EXCLUDED.clod_pct_bkn,
            clod_pct_ovc    = EXCLUDED.clod_pct_ovc,
            cldh_normal     = EXCLUDED.cldh_normal,
            htdh_normal     = EXCLUDED.htdh_normal,
            loaded_at       = NOW()
    """
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, page_size=2000)
    conn.commit()

    logger.info(f"  → {len(rows):,} rows upserted from {path.name}")
    return len(rows)


# ── Row-count verification ───────────────────────────────────────────────────

def print_row_counts(conn: psycopg2.extensions.connection) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                icao_code,
                COUNT(*)                    AS total_rows,
                MIN(date)                   AS earliest_date,
                MAX(date)                   AS latest_date,
                COUNT(*) FILTER (WHERE tmax_f IS NOT NULL) AS has_tmax,
                COUNT(*) FILTER (WHERE prcp_in IS NOT NULL) AS has_prcp
            FROM weather_bronze_noaa_daily_actuals
            GROUP BY icao_code
            ORDER BY icao_code
        """)
        rows = cur.fetchall()

    print("\n── weather_bronze_noaa_daily_actuals ──────────────────────────────")
    print(f"{'ICAO':<8} {'rows':>8} {'earliest':>12} {'latest':>12} {'has_tmax':>10} {'has_prcp':>10}")
    print("-" * 65)
    for r in rows:
        print(f"{r[0]:<8} {r[1]:>8,} {str(r[2]):>12} {str(r[3]):>12} {r[4]:>10,} {r[5]:>10,}")
    print(f"{'TOTAL':<8} {sum(r[1] for r in rows):>8,}")

    cur2 = conn.cursor()
    cur2.execute("""
        SELECT icao_code, COUNT(*) AS rows
        FROM weather_bronze_noaa_hourly_normals
        GROUP BY icao_code
        ORDER BY icao_code
    """)
    hourly_rows = cur2.fetchall()

    print("\n── weather_bronze_noaa_hourly_normals ─────────────────────────────")
    print(f"{'ICAO':<8} {'rows':>8} {'expected':>10} {'note'}")
    print("-" * 55)
    for r in hourly_rows:
        # 365 days × 24 hours = 8,760 per station (CDO uses 1-24 so may be 8,784 for leap offsets)
        expected = 8760
        note = "(1981-2010 normals)"
        print(f"{r[0]:<8} {r[1]:>8,} {expected:>10,} {note}")
    cur2.close()


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load NOAA GHCND daily + hourly normals into eventhorizon"
    )
    parser.add_argument(
        "--dir", required=True,
        help="Directory containing the NOAA CSV files"
    )
    parser.add_argument(
        "--daily-only", action="store_true",
        help="Load only the 5 daily summary files"
    )
    parser.add_argument(
        "--hourly-only", action="store_true",
        help="Load only the 2 hourly normals files"
    )
    parser.add_argument(
        "--counts-only", action="store_true",
        help="Print row counts only (no loading)"
    )
    args = parser.parse_args()

    csv_dir = Path(args.dir)
    if not csv_dir.is_dir():
        logger.error(f"Directory not found: {csv_dir}")
        return 1

    try:
        conn = _pg_conn()
    except Exception as e:
        logger.error(f"DB connection failed: {e}")
        logger.error("Try: PGHOST=localhost PGUSER=ehuser PGPASSWORD=<pw> python3 load-noaa-actuals.py ...")
        logger.error("Or run as postgres: sudo -u postgres python3 load-noaa-actuals.py ...")
        return 1

    if args.counts_only:
        print_row_counts(conn)
        return 0

    total_daily = 0
    total_hourly = 0

    if not args.hourly_only:
        daily_files = _find_files(csv_dir, ["NOAA Daily Summary"])
        if not daily_files:
            logger.warning(f"No 'NOAA Daily Summary' CSVs found in {csv_dir}")
        for f in daily_files:
            total_daily += load_daily_file(conn, f)

    if not args.daily_only:
        hourly_files = _find_files(csv_dir, ["NOAA Hourly Weather"])
        if not hourly_files:
            logger.warning(f"No 'NOAA Hourly Weather' CSVs found in {csv_dir}")
        for f in hourly_files:
            total_hourly += load_hourly_file(conn, f)

    logger.info(f"Load complete — {total_daily:,} daily rows, {total_hourly:,} hourly rows")

    print_row_counts(conn)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
