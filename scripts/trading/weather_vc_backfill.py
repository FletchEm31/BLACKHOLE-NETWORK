"""BHN Strat 9 — Visual Crossing historical actuals backfill.

Fills weather_bronze_visual_crossing_actuals (bronze) and
weather_silver_actuals_conformed (silver, actual_source='visual_crossing')
with daily tmax/tmin/precip/humidity from Visual Crossing for all 8 cities.

Bronze separation rationale: weather_bronze_nws_actuals has UNIQUE
(station_code, target_date). Writing VC data there would block NWS CLI
inserts for overlapping dates (or vice versa). The dedicated VC table lets
both sources coexist; silver ties them via the actual_source discriminator.

One API call per city (date-range endpoint) = 8 requests total per run.
For 3-year backfill: 8 cities × ~1095 days ≈ 8,760 VC records. Requires
VC Basic plan (10,000 records/day). Free tier (1,000 records/day) is not
sufficient for a full 3-year run but works for incremental daily updates.

After the backfill, silver_forecast_error will auto-populate via the
_insert_silver JOIN against weather_silver_forecast_conformed.
Run weather_edge_calculator.py afterwards to refresh the gold edge sheet.

Usage:
    python3 weather_vc_backfill.py                              # 3-year backfill
    python3 weather_vc_backfill.py --start 2023-01-01 --end 2026-06-24
    python3 weather_vc_backfill.py --start 2026-06-01           # incremental fill
    python3 weather_vc_backfill.py --dry-run                    # fetch only, no DB writes

Requires:
    VISUAL_CROSSING_API_KEY in /etc/bhn-trading/strat9.env (or env)
    weather_bronze_visual_crossing_actuals table (migration 2026-06-25)
"""

import argparse
import json
import logging
import os
import sys
import time
import urllib.request
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


def _prime_env() -> None:
    for path in ("/etc/bhn-trading/env", "/etc/bhn-trading/strat9.env"):
        p = Path(path)
        if not p.is_file():
            continue
        for ln in p.read_text().splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            k = k.strip()
            if k not in os.environ:
                os.environ[k] = v.strip().strip('"').strip("'")


_prime_env()

sys.path.insert(0, os.path.dirname(__file__))
import trading_core as tc  # noqa: E402

logger = tc.get_logger("strat_9_vc_backfill")

VC_BASE_URL = "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"

# 8 Kalshi-aligned cities: (icao, city_name, vc_location)
# VC resolves ICAO codes directly for US airports.
CITIES = [
    ("KMIA", "Miami",              "KMIA"),
    ("KDEN", "Denver",             "KDEN"),
    ("KPHX", "Phoenix",            "KPHX"),
    ("KLAX", "Los Angeles",        "KLAX"),
    ("KDFW", "Dallas/Fort Worth",  "KDFW"),
    ("KNYC", "New York City",      "KNYC"),
    ("KORD", "Chicago O'Hare",     "KORD"),
    ("KAUS", "Austin",             "KAUS"),
]


def _fetch_vc_range(location: str, start: date, end: date, api_key: str,
                    retries: int = 3) -> list[dict]:
    """Fetch daily tmax/tmin/precip/humidity from Visual Crossing for a date range.
    Returns list of day dicts. Retries up to `retries` times on 429 with
    exponential backoff (60s, 120s, 180s).
    """
    url = (
        f"{VC_BASE_URL}/{urllib.parse.quote(location)}"
        f"/{start.isoformat()}/{end.isoformat()}"
    )
    params = urllib.parse.urlencode({
        "unitGroup":    "us",
        "elements":     "datetime,tempmax,tempmin,precip,humidity",
        "include":      "days",
        "key":          api_key,
        "contentType":  "json",
    })
    full_url = f"{url}?{params}"
    req = urllib.request.Request(full_url, headers={"User-Agent": "BHN-Strat9-Backfill/2.0"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())
                return data.get("days", [])
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                wait = 60 * (attempt + 1)
                logger.warning(f"{location}: 429 rate limit — sleeping {wait}s then retrying")
                time.sleep(wait)
            else:
                logger.error(f"{location}: VC fetch failed (HTTP {e.code}): {e}")
                return []
        except Exception as e:
            logger.error(f"{location}: VC fetch failed: {e}")
            return []
    return []


def _insert_bronze(conn, *, city: str, station_code: str, vc_querylocation: str,
                   target_date: date, tmax_f: float, tmin_f: float,
                   precip_in: Optional[float], humidity_pct: Optional[float],
                   raw_day: dict) -> bool:
    """Insert into weather_bronze_visual_crossing_actuals.
    Returns True if a new row was inserted (False = already exists).
    """
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO weather_bronze_visual_crossing_actuals
                (city, station_code, vc_querylocation, target_date,
                 final_tmax_f, final_tmin_f, precip_in, humidity_pct,
                 source_payload_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (station_code, target_date) DO NOTHING
        """, (city, station_code, vc_querylocation, target_date,
              tmax_f, tmin_f, precip_in, humidity_pct,
              json.dumps(raw_day)))
        return cur.rowcount == 1


def _insert_silver(conn, *, city: str, station_code: str, target_date: date,
                   tmax_f: float, tmin_f: float) -> None:
    """Upsert into silver actuals and populate forecast_error pairs.
    actual_source='visual_crossing' coexists with 'nws_cli' on the same date
    via the UNIQUE (station_code, target_date, actual_source) constraint.
    """
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO weather_silver_actuals_conformed
                (city, station_code, target_date, final_tmax_f, final_tmin_f,
                 actual_source, report_issued_at, is_final)
            VALUES (%s, %s, %s, %s, %s, 'visual_crossing', NULL, TRUE)
            ON CONFLICT (station_code, target_date, actual_source) DO UPDATE SET
                final_tmax_f = EXCLUDED.final_tmax_f,
                final_tmin_f = EXCLUDED.final_tmin_f,
                is_final     = TRUE
        """, (city, station_code, target_date, tmax_f, tmin_f))

        # tmax forecast error pairs
        cur.execute("""
            INSERT INTO weather_silver_forecast_error
                (city, station_code, target_date, feature_name, source_name,
                 forecast_run_time, lead_hours, forecast_value, actual_value,
                 forecast_error_f, error_sign)
            SELECT
                city, station_code, target_date, 'tmax_f', source_name,
                forecast_run_time, lead_hours, tmax_f, %s,
                %s - tmax_f,
                CASE WHEN %s - tmax_f > 0.1 THEN 'cold'
                     WHEN %s - tmax_f < -0.1 THEN 'hot'
                     ELSE 'exact' END
            FROM weather_silver_forecast_conformed
            WHERE station_code = %s AND target_date = %s AND tmax_f IS NOT NULL
            ON CONFLICT (station_code, target_date, feature_name, source_name, forecast_run_time)
            DO NOTHING
        """, (tmax_f, tmax_f, tmax_f, tmax_f, station_code, target_date))

        # tmin forecast error pairs
        cur.execute("""
            INSERT INTO weather_silver_forecast_error
                (city, station_code, target_date, feature_name, source_name,
                 forecast_run_time, lead_hours, forecast_value, actual_value,
                 forecast_error_f, error_sign)
            SELECT
                city, station_code, target_date, 'tmin_f', source_name,
                forecast_run_time, lead_hours, tmin_f, %s,
                %s - tmin_f,
                CASE WHEN %s - tmin_f > 0.1 THEN 'cold'
                     WHEN %s - tmin_f < -0.1 THEN 'hot'
                     ELSE 'exact' END
            FROM weather_silver_forecast_conformed
            WHERE station_code = %s AND target_date = %s AND tmin_f IS NOT NULL
            ON CONFLICT (station_code, target_date, feature_name, source_name, forecast_run_time)
            DO NOTHING
        """, (tmin_f, tmin_f, tmin_f, tmin_f, station_code, target_date))


def run_backfill(start: date, end: date, dry_run: bool = False) -> None:
    api_key = os.environ.get("VISUAL_CROSSING_API_KEY", "")
    if not api_key:
        logger.error("VISUAL_CROSSING_API_KEY not set — add to /etc/bhn-trading/strat9.env")
        sys.exit(1)

    days_in_range = (end - start).days + 1
    total_records = days_in_range * len(CITIES)
    logger.info(
        f"Backfill: {start} → {end} ({days_in_range} days × {len(CITIES)} cities "
        f"= ~{total_records} VC records)"
    )
    if total_records > 1000 and not dry_run:
        logger.warning(
            "Range exceeds VC free-tier limit (1,000 records/day). "
            "Requires VC Basic plan (10,000 records/day)."
        )

    total_bronze = 0
    total_silver = 0

    for icao, city_name, vc_loc in CITIES:
        logger.info(f"{icao}: fetching {start} → {end} from Visual Crossing")
        days = _fetch_vc_range(vc_loc, start, end, api_key)
        if not days:
            logger.warning(f"{icao}: no data returned — skipping")
            continue

        bronze_count = 0
        bronze_skip = 0
        silver_count = 0

        with tc.get_pg_conn() as conn:
            for day in days:
                try:
                    target_date = date.fromisoformat(day["datetime"])
                    tmax_f = day.get("tempmax")
                    tmin_f = day.get("tempmin")
                    if tmax_f is None or tmin_f is None:
                        continue

                    precip_in: Optional[float] = day.get("precip")
                    humidity_pct: Optional[float] = day.get("humidity")

                    if dry_run:
                        logger.info(
                            f"  [dry-run] {icao} {target_date}: "
                            f"tmax={tmax_f} tmin={tmin_f} "
                            f"precip={precip_in} humidity={humidity_pct}"
                        )
                        continue

                    inserted = _insert_bronze(
                        conn,
                        city=city_name,
                        station_code=icao,
                        vc_querylocation=vc_loc,
                        target_date=target_date,
                        tmax_f=tmax_f,
                        tmin_f=tmin_f,
                        precip_in=precip_in,
                        humidity_pct=humidity_pct,
                        raw_day=day,
                    )
                    if inserted:
                        bronze_count += 1
                    else:
                        bronze_skip += 1

                    _insert_silver(
                        conn,
                        city=city_name,
                        station_code=icao,
                        target_date=target_date,
                        tmax_f=tmax_f,
                        tmin_f=tmin_f,
                    )
                    silver_count += 1

                except Exception as e:
                    logger.warning(f"{icao} {day.get('datetime', '?')}: write failed: {e}")
                    conn.rollback()

        logger.info(
            f"{icao}: {bronze_count} new bronze rows "
            f"({bronze_skip} already existed), {silver_count} silver upserts"
        )
        total_bronze += bronze_count
        total_silver += silver_count
        time.sleep(3)  # stay within VC rate limits between cities

    if not dry_run:
        logger.info(
            f"Backfill complete: {total_bronze} new bronze rows, "
            f"{total_silver} silver upserts across {len(CITIES)} cities"
        )
        logger.info("Next: run weather_edge_calculator.py to refresh gold edge sheet")
    else:
        logger.info("Dry-run complete — no rows written")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="BHN Strat 9 — Visual Crossing historical actuals backfill"
    )
    three_years_ago = (datetime.now(timezone.utc).date() - timedelta(days=3 * 365))
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1))

    parser.add_argument(
        "--start",
        default=three_years_ago.isoformat(),
        help=f"Start date (YYYY-MM-DD). Default: 3 years ago ({three_years_ago})",
    )
    parser.add_argument(
        "--end",
        default=yesterday.isoformat(),
        help=f"End date (YYYY-MM-DD). Default: yesterday ({yesterday})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch from VC and log but do not write to DB",
    )
    args = parser.parse_args()

    try:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
    except ValueError as e:
        logger.error(f"Invalid date: {e}")
        return 1

    if start > end:
        logger.error(f"--start {start} is after --end {end}")
        return 1

    run_backfill(start, end, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
