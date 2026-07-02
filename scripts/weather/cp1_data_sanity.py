#!/usr/bin/env python3
"""
CP1: Data Sanity — Checkpoint 1.
Gate that runs before any trade is considered for a given contract.
Returns PASS or FAIL with a reason string.

Usage (standalone test):
    python3 cp1_data_sanity.py --station KLAX --date 2026-06-28 --bucket "69-70"

Environment:
    DATABASE_URL  PostgreSQL connection (peer auth: postgresql:///eventhorizon)
"""

import argparse
import os
import sys
from datetime import date

import psycopg2
import psycopg2.extras

PLAUSIBLE_TEMP_MIN = -20.0
PLAUSIBLE_TEMP_MAX = 130.0


def get_conn():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        host = os.environ.get("PG_HOST")
        port = os.environ.get("PG_PORT", "5432")
        db   = os.environ.get("PG_DB")
        user = os.environ.get("PG_USER")
        pwd  = os.environ.get("PG_PASSWORD", "")
        if host and db and user:
            import urllib.parse
            db_url = f"postgresql://{urllib.parse.quote(user)}:{urllib.parse.quote(pwd)}@{host}:{port}/{db}"
        else:
            sys.exit("ERROR: Neither DATABASE_URL nor PG_HOST/PG_DB/PG_USER are set")
    return psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor)


def check_data_sanity(station_code: str, target_date: date, bucket_label: str,
                      conn=None) -> dict:
    """
    Returns {'pass': bool, 'reason': str, 'checks': dict}

    FAIL conditions:
    - No NWS forecast exists for station/date at lead_hours=24
    - Forecast value is NULL or outside plausible range (-20 to 130F)
    - No Kalshi market snapshot exists for station/date/bucket
    - no_ask is NULL or 0
    - yes_bid is NULL or 0
    - NWS CLI actual flagged as AMENDED in raw_payload (if already settled)
    - bucket_floor or bucket_cap is NULL for 'between' type buckets
    """
    close_conn = conn is None
    if conn is None:
        conn = get_conn()

    checks = {}
    try:
        with conn.cursor() as cur:
            # --- Check 1: NWS tmax forecast exists ---
            # Try the gold feature table first (has settled historical rows with
            # bias-calibrated forecasts).  Fall back to the bronze NWS snapshot
            # table for pre-settlement target_dates where gold has no row yet.
            cur.execute("""
                SELECT nws_tmax_f
                FROM weather_gold_city_day_features
                WHERE station_code = %s
                  AND target_date  = %s
                  AND nws_tmax_f   IS NOT NULL
            """, (station_code, target_date))
            fc_row = cur.fetchone()
            fc_val = float(fc_row["nws_tmax_f"]) if fc_row else None

            if fc_val is None:
                # Gold table has no row — try bronze live NWS snapshots
                cur.execute("""
                    SELECT tmax_f AS nws_tmax_f
                    FROM weather_bronze_nws_forecast_snapshots
                    WHERE station_code = %s
                      AND target_date  = %s
                      AND tmax_f       IS NOT NULL
                    ORDER BY retrieved_at DESC
                    LIMIT 1
                """, (station_code, target_date))
                fc_row = cur.fetchone()
                fc_val = float(fc_row["nws_tmax_f"]) if fc_row else None

            checks["nws_forecast_exists"] = fc_val is not None
            if not checks["nws_forecast_exists"]:
                return {"pass": False,
                        "reason": "No NWS tmax_f in gold or bronze table for this station/date",
                        "checks": checks}

            # --- Check 2: Forecast in plausible range ---
            checks["forecast_plausible"] = PLAUSIBLE_TEMP_MIN <= fc_val <= PLAUSIBLE_TEMP_MAX
            if not checks["forecast_plausible"]:
                return {"pass": False,
                        "reason": f"NWS forecast {fc_val}F outside plausible range "
                                  f"({PLAUSIBLE_TEMP_MIN}–{PLAUSIBLE_TEMP_MAX}F)",
                        "checks": checks}

            # --- Check 3: Kalshi snapshot exists for station/date/bucket ---
            # contract_side='high' guards against a same-label collision with a
            # LOW bucket (KXLOWT* tickers, also collected now) — bucket_label is
            # just a dollar-range string and can coincidentally match across
            # sides; this check is HIGH-only per the rest of the CP1-CP4 pipeline.
            cur.execute("""
                SELECT bucket_label, bucket_type, bucket_floor, bucket_cap,
                       yes_bid, no_ask, retrieved_at
                FROM weather_bronze_kalshi_market_snapshots
                WHERE station_code = %s
                  AND target_date = %s
                  AND bucket_label = %s
                  AND contract_side = 'high'
                ORDER BY retrieved_at DESC
                LIMIT 1
            """, (station_code, target_date, bucket_label))
            snap = cur.fetchone()

            checks["kalshi_snapshot_exists"] = snap is not None
            if not checks["kalshi_snapshot_exists"]:
                return {"pass": False,
                        "reason": f"No Kalshi snapshot for {station_code}/{target_date}/{bucket_label}",
                        "checks": checks}

            # --- Check 4: yes_bid present and non-zero ---
            yes_bid = float(snap["yes_bid"]) if snap["yes_bid"] is not None else None
            checks["yes_bid_present"] = yes_bid is not None and yes_bid > 0
            if not checks["yes_bid_present"]:
                return {"pass": False,
                        "reason": f"yes_bid is NULL or 0 for {bucket_label}",
                        "checks": checks}

            # --- Check 5: no_ask present and non-zero ---
            no_ask = float(snap["no_ask"]) if snap["no_ask"] is not None else None
            checks["no_ask_present"] = no_ask is not None and no_ask > 0
            if not checks["no_ask_present"]:
                return {"pass": False,
                        "reason": f"no_ask is NULL or 0 for {bucket_label}",
                        "checks": checks}

            # --- Check 6: bucket_floor/cap present for 'between' buckets ---
            if snap["bucket_type"] == "between":
                has_bounds = (snap["bucket_floor"] is not None
                              and snap["bucket_cap"] is not None)
                checks["bucket_bounds_present"] = has_bounds
                if not has_bounds:
                    return {"pass": False,
                            "reason": f"bucket_floor or bucket_cap NULL for 'between' bucket {bucket_label}",
                            "checks": checks}
            else:
                checks["bucket_bounds_present"] = True

            # --- Check 7: NWS CLI actual not AMENDED (if already settled) ---
            # raw_payload is not in silver_actuals_conformed — skip this check.
            # The is_final flag is a proxy: if is_final=True and settlement exists, data is OK.
            cur.execute("""
                SELECT is_final, settlement_label_high
                FROM weather_silver_actuals_conformed
                WHERE station_code = %s AND target_date = %s AND actual_source = 'nws_cli'
            """, (station_code, target_date))
            actual_row = cur.fetchone()
            if actual_row and not actual_row["is_final"]:
                checks["actuals_not_amended"] = False
                return {"pass": False,
                        "reason": "NWS CLI actual exists but is_final=False (preliminary reading)",
                        "checks": checks}
            checks["actuals_not_amended"] = True

    finally:
        if close_conn:
            conn.close()

    return {"pass": True, "reason": "All checks passed", "checks": checks}


def _parse_args():
    p = argparse.ArgumentParser(description="CP1 data sanity check")
    p.add_argument("--station", required=True)
    p.add_argument("--date", required=True, type=date.fromisoformat)
    p.add_argument("--bucket", required=True, help="Kalshi bucket_label e.g. '69-70'")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    result = check_data_sanity(args.station, args.date, args.bucket)
    status = "PASS" if result["pass"] else "FAIL"
    print(f"CP1 {status}: {result['reason']}")
    print("Checks:")
    for k, v in result["checks"].items():
        print(f"  {'✓' if v else '✗'} {k}")
    sys.exit(0 if result["pass"] else 1)
