"""BHN Strat 9 — Visual Crossing historical actuals backfill.

Fills weather_bronze_visual_crossing_actuals (bronze) and
weather_silver_actuals_conformed (silver, actual_source='visual_crossing')
with daily tmax/tmin/precip/humidity from Visual Crossing.

Bronze separation rationale: weather_bronze_nws_actuals has UNIQUE
(station_code, target_date). Writing VC data there would block NWS CLI
inserts for overlapping dates (or vice versa). The dedicated VC table lets
both sources coexist; silver ties them via the actual_source discriminator.

Modes
-----
Manual (explicit range):
    python3 weather_vc_backfill.py --start 2023-01-01 --end 2023-12-31
    python3 weather_vc_backfill.py --cities KDEN,KMIA --start 2025-01-01

Auto (cron-friendly, DB-aware windowing):
    python3 weather_vc_backfill.py --cities KDEN,KMIA --auto --days-per-run 500

    In --auto mode the script queries the DB to find the next unfilled chunk
    of up to --days-per-run days per city, working backwards from the 3-year
    target until all history is loaded. Once a city's backfill is complete the
    script fetches yesterday instead (keeps actuals current).

    Free-tier budget: 1,000 records/day.
    2 cities × 500 days = 1,000 records — exactly on budget.

VC plan requirements
--------------------
    Free tier (1,000 records/day): daily incremental only (~2 records/day for
        2 cities). Not enough for a multi-year backfill run.
    Basic plan ($35/mo, 10,000 records/day): required for --days-per-run 500
        with 2+ cities. 2 cities × 500 days = 1,000 records/run — fine.

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

# Full 8-city set — filtered at runtime via --cities
_ALL_CITIES = {
    "KMIA": ("Miami",              "KMIA"),
    "KDEN": ("Denver",             "KDEN"),
    "KPHX": ("Phoenix",            "KPHX"),
    "KLAX": ("Los Angeles",        "KLAX"),
    "KDFW": ("Dallas/Fort Worth",  "KDFW"),
    "KNYC": ("New York City",      "KNYC"),
    "KORD": ("Chicago O'Hare",     "KORD"),
    "KAUS": ("Austin",             "KAUS"),
}

_DEFAULT_CITIES = list(_ALL_CITIES.keys())


# ─────────────────────────────────────────────────────────────────────────────
# VC fetch
# ─────────────────────────────────────────────────────────────────────────────

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
        "unitGroup":   "us",
        "elements":    "datetime,tempmax,tempmin,precip,humidity",
        "include":     "days",
        "key":         api_key,
        "contentType": "json",
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


# ─────────────────────────────────────────────────────────────────────────────
# Auto-windowing: find the next unfilled chunk per city
# ─────────────────────────────────────────────────────────────────────────────

def _get_next_window(station_code: str, target_start: date, target_end: date,
                     max_days: int) -> Optional[tuple[date, date]]:
    """Query DB to find the next date window to fill for this station.

    Strategy:
      1. No data at all       → fetch target_start … target_start + max_days - 1
      2. Gap before earliest  → fill backwards (oldest missing data first)
      3. Gap after latest     → fill forward
      4. Backfill complete    → return None (caller switches to yesterday fetch)

    Returns (window_start, window_end) clamped to target_start/target_end,
    or None when the full range is covered.
    """
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT MIN(target_date), MAX(target_date), COUNT(*)
                FROM weather_bronze_visual_crossing_actuals
                WHERE station_code = %s
                  AND target_date BETWEEN %s AND %s
            """, (station_code, target_start, target_end))
            row = cur.fetchone()

    earliest, latest, count = row if row else (None, None, 0)
    count = count or 0

    if count == 0:
        # Nothing yet — start from the beginning of the target range
        ws = target_start
        we = min(ws + timedelta(days=max_days - 1), target_end)
        return ws, we

    if earliest > target_start:
        # Have data but there's a gap before it — fill backwards
        we = earliest - timedelta(days=1)
        ws = max(target_start, we - timedelta(days=max_days - 1))
        return ws, we

    if latest < target_end:
        # Fill forward from the last known date
        ws = latest + timedelta(days=1)
        we = min(ws + timedelta(days=max_days - 1), target_end)
        return ws, we

    # Full target range is covered
    return None


# ─────────────────────────────────────────────────────────────────────────────
# DB writes
# ─────────────────────────────────────────────────────────────────────────────

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
    actual_source='visual_crossing' coexists with 'nws_cli' on the same date.
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


# ─────────────────────────────────────────────────────────────────────────────
# Core fetch+write for one city over one window
# ─────────────────────────────────────────────────────────────────────────────

def _process_city(icao: str, city_name: str, vc_loc: str,
                  start: date, end: date, api_key: str,
                  dry_run: bool) -> tuple[int, int]:
    """Fetch VC data for one city over [start, end] and write to DB.
    Returns (bronze_inserted, silver_upserted).
    """
    logger.info(f"{icao}: fetching {start} → {end} ({(end - start).days + 1} days)")
    days = _fetch_vc_range(vc_loc, start, end, api_key)
    if not days:
        logger.warning(f"{icao}: no data returned — skipping")
        return 0, 0

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
                    city=city_name, station_code=icao, vc_querylocation=vc_loc,
                    target_date=target_date, tmax_f=tmax_f, tmin_f=tmin_f,
                    precip_in=precip_in, humidity_pct=humidity_pct, raw_day=day,
                )
                if inserted:
                    bronze_count += 1
                else:
                    bronze_skip += 1

                _insert_silver(
                    conn,
                    city=city_name, station_code=icao,
                    target_date=target_date, tmax_f=tmax_f, tmin_f=tmin_f,
                )
                silver_count += 1

            except Exception as e:
                logger.warning(f"{icao} {day.get('datetime', '?')}: write failed: {e}")
                conn.rollback()

    logger.info(
        f"{icao}: {bronze_count} new bronze rows "
        f"({bronze_skip} already existed), {silver_count} silver upserts"
    )
    return bronze_count, silver_count


# ─────────────────────────────────────────────────────────────────────────────
# Run modes
# ─────────────────────────────────────────────────────────────────────────────

def run_manual(cities: list[tuple], start: date, end: date,
               dry_run: bool = False) -> None:
    """Explicit date range across all target cities."""
    api_key = os.environ.get("VISUAL_CROSSING_API_KEY", "")
    if not api_key:
        logger.error("VISUAL_CROSSING_API_KEY not set")
        sys.exit(1)

    days_in_range = (end - start).days + 1
    total_records = days_in_range * len(cities)
    logger.info(
        f"Manual backfill: {start} → {end} ({days_in_range} days "
        f"× {len(cities)} cities = ~{total_records} VC records)"
    )
    if total_records > 1000 and not dry_run:
        logger.warning(
            f"Range exceeds VC free-tier limit (1,000 records/day). "
            f"Requires VC Basic plan (10,000/day)."
        )

    total_bronze = total_silver = 0
    for icao, city_name, vc_loc in cities:
        b, s = _process_city(icao, city_name, vc_loc, start, end, api_key, dry_run)
        total_bronze += b
        total_silver += s
        time.sleep(3)

    if not dry_run:
        logger.info(
            f"Manual run complete: {total_bronze} new bronze, "
            f"{total_silver} silver upserts"
        )
    else:
        logger.info("Dry-run complete — no rows written")


def run_auto(cities: list[tuple], target_start: date, target_end: date,
             days_per_run: int, dry_run: bool = False) -> None:
    """DB-aware windowed backfill — designed for daily cron use.

    For each city:
      - Queries DB to find the next unfilled chunk (up to days_per_run days)
      - Fills backwards from oldest missing date, then forward
      - Once the full target range is covered, fetches yesterday instead
        (keeps actuals current after backfill is complete)

    Free-tier budget example: 2 cities × 500 days = 1,000 records/run.
    """
    api_key = os.environ.get("VISUAL_CROSSING_API_KEY", "")
    if not api_key:
        logger.error("VISUAL_CROSSING_API_KEY not set")
        sys.exit(1)

    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    total_bronze = total_silver = 0

    for icao, city_name, vc_loc in cities:
        window = _get_next_window(icao, target_start, target_end, days_per_run)

        if window is None:
            logger.info(f"{icao}: backfill complete — fetching yesterday ({yesterday}) only")
            ws, we = yesterday, yesterday
        else:
            ws, we = window
            days_remaining = (target_end - ws).days if ws > target_start else (ws - target_start).days
            logger.info(
                f"{icao}: backfill window {ws} → {we} "
                f"({(we - ws).days + 1} days)"
            )

        b, s = _process_city(icao, city_name, vc_loc, ws, we, api_key, dry_run)
        total_bronze += b
        total_silver += s
        time.sleep(3)

    if not dry_run:
        logger.info(
            f"Auto run complete: {total_bronze} new bronze, "
            f"{total_silver} silver upserts"
        )
    else:
        logger.info("Dry-run complete — no rows written")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    three_years_ago = datetime.now(timezone.utc).date() - timedelta(days=3 * 365)
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)

    parser = argparse.ArgumentParser(
        description="BHN Strat 9 — Visual Crossing historical actuals backfill"
    )
    parser.add_argument(
        "--cities",
        default=",".join(_DEFAULT_CITIES),
        help=(
            "Comma-separated ICAO codes to process. "
            f"Default: all 8 ({','.join(_DEFAULT_CITIES)})"
        ),
    )
    parser.add_argument(
        "--start",
        default=three_years_ago.isoformat(),
        help=(
            f"Start date (YYYY-MM-DD). "
            f"Manual mode: fetch start. Auto mode: backfill target boundary. "
            f"Default: {three_years_ago}"
        ),
    )
    parser.add_argument(
        "--end",
        default=yesterday.isoformat(),
        help=(
            f"End date (YYYY-MM-DD). "
            f"Manual mode: fetch end. Auto mode: backfill target boundary. "
            f"Default: {yesterday}"
        ),
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help=(
            "Auto mode: query DB to find next unfilled window per city. "
            "Designed for daily cron use."
        ),
    )
    parser.add_argument(
        "--days-per-run",
        type=int,
        default=500,
        help="Max days to fetch per city per run in --auto mode. Default: 500",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch from VC and log but do not write to DB",
    )
    args = parser.parse_args()

    # Resolve cities
    requested = [c.strip().upper() for c in args.cities.split(",") if c.strip()]
    unknown = [c for c in requested if c not in _ALL_CITIES]
    if unknown:
        logger.error(f"Unknown city codes: {unknown}. Valid: {list(_ALL_CITIES.keys())}")
        return 1
    cities = [(icao, *_ALL_CITIES[icao]) for icao in requested]

    # Parse dates
    try:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
    except ValueError as e:
        logger.error(f"Invalid date: {e}")
        return 1
    if start > end:
        logger.error(f"--start {start} is after --end {end}")
        return 1

    if args.auto:
        run_auto(
            cities=cities,
            target_start=start,
            target_end=end,
            days_per_run=args.days_per_run,
            dry_run=args.dry_run,
        )
    else:
        run_manual(cities=cities, start=start, end=end, dry_run=args.dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main())
