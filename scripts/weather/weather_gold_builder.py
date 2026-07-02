#!/usr/bin/env python3
"""
Populate weather_gold_city_day_features from bronze/silver/calibration tables.

Scope: KDEN, KLAX, KMIA only (the three Kalshi-tradeable cities).

Usage:
    python3 weather_gold_builder.py [--dry-run] [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD]

Environment:
    DATABASE_URL  PostgreSQL connection (peer auth: postgresql:///eventhorizon)
"""

import argparse
import os
import sys
from datetime import date, timedelta

import psycopg2
import psycopg2.extras

TRADEABLE_STATIONS = ('KDEN', 'KLAX', 'KMIA')

# Kalshi contracts settle at 4PM ET = 20:00 UTC.
# We take the latest snapshot at or before (target_date - 1 day 20:00 UTC),
# representing the market state available ~24h before settlement.
KALSHI_REF_HOUR_UTC = 20


def parse_args():
    p = argparse.ArgumentParser(description="Build weather_gold_city_day_features")
    p.add_argument("--dry-run", action="store_true",
                   help="Print rows that would be inserted, do not write")
    p.add_argument("--start-date", type=date.fromisoformat, default=None,
                   help="First target_date to process (default: 30 days ago)")
    p.add_argument("--end-date", type=date.fromisoformat, default=None,
                   help="Last target_date to process (default: yesterday)")
    return p.parse_args()


def get_conn():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("ERROR: DATABASE_URL not set")
    return psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor)


def season_for(d: date) -> str:
    m = d.month
    if m in (12, 1, 2):
        return "winter"
    if m in (3, 4, 5):
        return "spring"
    if m in (6, 7, 8):
        return "summer"
    return "fall"


def fetch_candidates(conn, start: date, end: date) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT station_code, target_date
            FROM weather_silver_forecast_error
            WHERE station_code = ANY(%s)
              AND source_name = 'nws'
              AND lead_hours = 24
              AND target_date BETWEEN %s AND %s
            ORDER BY station_code, target_date
        """, (list(TRADEABLE_STATIONS), start, end))
        return cur.fetchall()


def fetch_actuals(conn, start: date, end: date) -> dict:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT station_code, target_date,
                   final_tmax_f, final_tmin_f,
                   settlement_label_high, is_final
            FROM weather_silver_actuals_conformed
            WHERE station_code = ANY(%s)
              AND actual_source = 'nws_cli'
              AND target_date BETWEEN %s AND %s
        """, (list(TRADEABLE_STATIONS), start, end))
        return {(r["station_code"], r["target_date"]): r for r in cur.fetchall()}


def fetch_forecasts(conn, start: date, end: date) -> dict:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT station_code, target_date, source_name, feature_name,
                   AVG(forecast_value) AS forecast_value
            FROM weather_silver_forecast_error
            WHERE station_code = ANY(%s)
              AND lead_hours = 24
              AND target_date BETWEEN %s AND %s
            GROUP BY station_code, target_date, source_name, feature_name
        """, (list(TRADEABLE_STATIONS), start, end))
        rows = cur.fetchall()

    result = {}
    for r in rows:
        key = (r["station_code"], r["target_date"])
        if key not in result:
            result[key] = {}
        col = f"{r['source_name']}__{r['feature_name']}"
        result[key][col] = float(r["forecast_value"]) if r["forecast_value"] is not None else None
    return result


def fetch_calibration(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT station_code, variable, source_model, season, mean_bias, rmse
            FROM model_calibration
            WHERE lead_time_hours = 24
        """)
        return {
            (r["station_code"], r["variable"], r["source_model"], r["season"]): r
            for r in cur.fetchall()
        }


def fetch_kalshi_snapshots(conn, start: date, end: date) -> dict:
    # HIGH-only: find_closest_bucket() below matches against ref_tmax (a
    # HIGH-side value). Must filter contract_side explicitly now that
    # LOW-series tickers (KXLOWT*) are also being collected for these
    # stations — without this, a LOW snapshot could be picked as "latest"
    # and its buckets (overnight-low ranges) matched against a daytime-high
    # calibrated value.
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (station_code, target_date)
                station_code, target_date, retrieved_at
            FROM weather_bronze_kalshi_market_snapshots
            WHERE station_code = ANY(%s)
              AND target_date BETWEEN %s AND %s
              AND contract_side = 'high'
              AND retrieved_at <= (
                  (target_date - 1)::timestamp + make_interval(hours => %s)
              ) AT TIME ZONE 'UTC'
            ORDER BY station_code, target_date, retrieved_at DESC
        """, (list(TRADEABLE_STATIONS), start, end, KALSHI_REF_HOUR_UTC))
        return {(r["station_code"], r["target_date"]): r["retrieved_at"] for r in cur.fetchall()}


def fetch_kalshi_buckets(conn, station: str, target_date: date, snap_time) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT bucket_label, bucket_type, bucket_floor, bucket_cap,
                   yes_bid, yes_ask, no_bid, no_ask, yes_mid
            FROM weather_bronze_kalshi_market_snapshots
            WHERE station_code = %s
              AND target_date = %s
              AND retrieved_at = %s
              AND contract_side = 'high'
            ORDER BY bucket_floor NULLS LAST
        """, (station, target_date, snap_time))
        return cur.fetchall()


def find_closest_bucket(buckets: list[dict], calibrated_tmax: float) -> dict | None:
    if not buckets or calibrated_tmax is None:
        return None

    between = [b for b in buckets if b["bucket_type"] == "between"
               and b["bucket_floor"] is not None and b["bucket_cap"] is not None]

    # Try exact bracket first
    for b in between:
        if float(b["bucket_floor"]) <= calibrated_tmax <= float(b["bucket_cap"]):
            return b

    # Fall back to closest midpoint among all buckets
    def midpoint(b):
        if b["bucket_floor"] is not None and b["bucket_cap"] is not None:
            return (float(b["bucket_floor"]) + float(b["bucket_cap"])) / 2
        if b["bucket_floor"] is not None:
            return float(b["bucket_floor"])
        return float("inf")

    all_buckets = buckets
    return min(all_buckets, key=lambda b: abs(midpoint(b) - calibrated_tmax))


def build_row(station: str, target_date: date, actuals: dict,
              forecasts: dict, calibration: dict,
              kalshi_snaps: dict, conn) -> dict | None:
    season = season_for(target_date)
    key = (station, target_date)

    fc = forecasts.get(key, {})
    nws_tmax = fc.get("nws__tmax_f")
    nws_tmin = fc.get("nws__tmin_f")
    om_tmax = fc.get("open_meteo_gfs_seamless__tmax_f")
    om_tmin = fc.get("open_meteo_gfs_seamless__tmin_f")

    if nws_tmax is None and om_tmax is None:
        return None

    def cal(var, model):
        r = calibration.get((station, var, model, season))
        return (float(r["mean_bias"]) if r and r["mean_bias"] else None,
                float(r["rmse"]) if r and r["rmse"] else None)

    nws_tmax_bias, nws_tmax_rmse = cal("tmax_f", "nws")
    nws_tmin_bias, nws_tmin_rmse = cal("tmin_f", "nws")
    om_tmax_bias, om_tmax_rmse = cal("tmax_f", "open_meteo_gfs_seamless")
    om_tmin_bias, om_tmin_rmse = cal("tmin_f", "open_meteo_gfs_seamless")

    def calibrate(raw, bias):
        if raw is None or bias is None:
            return None
        return round(raw - bias, 4)

    nws_tmax_cal = calibrate(nws_tmax, nws_tmax_bias)
    nws_tmin_cal = calibrate(nws_tmin, nws_tmin_bias)
    om_tmax_cal = calibrate(om_tmax, om_tmax_bias)
    om_tmin_cal = calibrate(om_tmin, om_tmin_bias)

    act = actuals.get(key, {})

    snap_time = kalshi_snaps.get(key)
    bucket = None
    if snap_time:
        buckets = fetch_kalshi_buckets(conn, station, target_date, snap_time)
        ref_tmax = nws_tmax_cal or nws_tmax or om_tmax_cal or om_tmax
        bucket = find_closest_bucket(buckets, ref_tmax) if ref_tmax else None

    implied_prob = float(bucket["yes_bid"]) if bucket and bucket["yes_bid"] is not None else None
    spread = (float(bucket["yes_ask"]) - float(bucket["yes_bid"])
              if bucket and bucket["yes_ask"] is not None and bucket["yes_bid"] is not None
              else None)

    return {
        "station_code": station,
        "target_date": target_date,
        "season": season,
        "actual_tmax_f": act.get("final_tmax_f"),
        "actual_tmin_f": act.get("final_tmin_f"),
        "settlement_label_high": act.get("settlement_label_high"),
        "actuals_is_final": act.get("is_final"),
        "nws_tmax_f": nws_tmax,
        "nws_tmin_f": nws_tmin,
        "om_tmax_f": om_tmax,
        "om_tmin_f": om_tmin,
        "nws_tmax_mean_bias": nws_tmax_bias,
        "nws_tmax_rmse": nws_tmax_rmse,
        "nws_tmin_mean_bias": nws_tmin_bias,
        "nws_tmin_rmse": nws_tmin_rmse,
        "om_tmax_mean_bias": om_tmax_bias,
        "om_tmax_rmse": om_tmax_rmse,
        "om_tmin_mean_bias": om_tmin_bias,
        "om_tmin_rmse": om_tmin_rmse,
        "nws_tmax_calibrated_f": nws_tmax_cal,
        "nws_tmin_calibrated_f": nws_tmin_cal,
        "om_tmax_calibrated_f": om_tmax_cal,
        "om_tmin_calibrated_f": om_tmin_cal,
        "kalshi_snapshot_retrieved_at": snap_time,
        "kalshi_closest_bucket_label": bucket["bucket_label"] if bucket else None,
        "kalshi_closest_bucket_floor": float(bucket["bucket_floor"]) if bucket and bucket["bucket_floor"] else None,
        "kalshi_closest_bucket_cap": float(bucket["bucket_cap"]) if bucket and bucket["bucket_cap"] else None,
        "kalshi_closest_yes_bid": float(bucket["yes_bid"]) if bucket and bucket["yes_bid"] is not None else None,
        "kalshi_closest_yes_ask": float(bucket["yes_ask"]) if bucket and bucket["yes_ask"] is not None else None,
        "kalshi_closest_no_bid": float(bucket["no_bid"]) if bucket and bucket["no_bid"] is not None else None,
        "kalshi_closest_no_ask": float(bucket["no_ask"]) if bucket and bucket["no_ask"] is not None else None,
        "kalshi_closest_yes_mid": float(bucket["yes_mid"]) if bucket and bucket["yes_mid"] is not None else None,
        "kalshi_implied_prob_yes": implied_prob,
        "kalshi_bid_ask_spread": spread,
    }


INSERT_SQL = """
INSERT INTO weather_gold_city_day_features (
    station_code, target_date, season,
    actual_tmax_f, actual_tmin_f, settlement_label_high, actuals_is_final,
    nws_tmax_f, nws_tmin_f, om_tmax_f, om_tmin_f,
    nws_tmax_mean_bias, nws_tmax_rmse, nws_tmin_mean_bias, nws_tmin_rmse,
    om_tmax_mean_bias, om_tmax_rmse, om_tmin_mean_bias, om_tmin_rmse,
    nws_tmax_calibrated_f, nws_tmin_calibrated_f,
    om_tmax_calibrated_f, om_tmin_calibrated_f,
    kalshi_snapshot_retrieved_at,
    kalshi_closest_bucket_label, kalshi_closest_bucket_floor, kalshi_closest_bucket_cap,
    kalshi_closest_yes_bid, kalshi_closest_yes_ask,
    kalshi_closest_no_bid, kalshi_closest_no_ask,
    kalshi_closest_yes_mid,
    kalshi_implied_prob_yes, kalshi_bid_ask_spread
) VALUES (
    %(station_code)s, %(target_date)s, %(season)s,
    %(actual_tmax_f)s, %(actual_tmin_f)s, %(settlement_label_high)s, %(actuals_is_final)s,
    %(nws_tmax_f)s, %(nws_tmin_f)s, %(om_tmax_f)s, %(om_tmin_f)s,
    %(nws_tmax_mean_bias)s, %(nws_tmax_rmse)s, %(nws_tmin_mean_bias)s, %(nws_tmin_rmse)s,
    %(om_tmax_mean_bias)s, %(om_tmax_rmse)s, %(om_tmin_mean_bias)s, %(om_tmin_rmse)s,
    %(nws_tmax_calibrated_f)s, %(nws_tmin_calibrated_f)s,
    %(om_tmax_calibrated_f)s, %(om_tmin_calibrated_f)s,
    %(kalshi_snapshot_retrieved_at)s,
    %(kalshi_closest_bucket_label)s, %(kalshi_closest_bucket_floor)s, %(kalshi_closest_bucket_cap)s,
    %(kalshi_closest_yes_bid)s, %(kalshi_closest_yes_ask)s,
    %(kalshi_closest_no_bid)s, %(kalshi_closest_no_ask)s,
    %(kalshi_closest_yes_mid)s,
    %(kalshi_implied_prob_yes)s, %(kalshi_bid_ask_spread)s
)
ON CONFLICT (station_code, target_date) DO NOTHING
"""


def main():
    args = parse_args()
    end = args.end_date or date.today()
    start = args.start_date or (end - timedelta(days=30))

    print(f"Gold builder: {start} → {end}"
          + (" [DRY RUN]" if args.dry_run else ""))

    conn = get_conn()
    try:
        candidates = fetch_candidates(conn, start, end)
        print(f"  {len(candidates)} (station, date) pairs with NWS 24h forecast")

        actuals = fetch_actuals(conn, start, end)
        forecasts = fetch_forecasts(conn, start, end)
        calibration = fetch_calibration(conn)
        kalshi_snaps = fetch_kalshi_snapshots(conn, start, end)

        rows = []
        for c in candidates:
            row = build_row(c["station_code"], c["target_date"],
                            actuals, forecasts, calibration, kalshi_snaps, conn)
            if row:
                rows.append(row)

        if args.dry_run:
            print(f"\n=== DRY RUN — {len(rows)} rows would be inserted ===")
            hdr = f"{'station':8} {'date':12} {'season':7} {'nws_tmax':>9} {'nws_cal':>8} {'kalshi_bucket':>15} {'yes_bid':>8}"
            print(hdr)
            print("-" * len(hdr))
            for r in rows:
                print(f"{r['station_code']:8} {str(r['target_date']):12} {r['season']:7}"
                      f" {(r['nws_tmax_f'] or 0):>9.1f} {(r['nws_tmax_calibrated_f'] or 0):>8.1f}"
                      f" {(r['kalshi_closest_bucket_label'] or 'n/a'):>15}"
                      f" {(r['kalshi_implied_prob_yes'] or 0):>8.3f}")
            print("\nDRY RUN complete — no DB writes.")
        else:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, INSERT_SQL, rows, page_size=100)
            conn.commit()
            print(f"\n=== LIVE RUN COMPLETE — {len(rows)} rows submitted ===")
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT station_code, count(*), min(target_date), max(target_date)
                    FROM weather_gold_city_day_features
                    GROUP BY station_code ORDER BY station_code
                """)
                for r in cur.fetchall():
                    print(f"  {r['station_code']:8} {r['count']:>4} rows  "
                          f"{r['min']} → {r['max']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
