#!/usr/bin/env python3
"""
Model-vs-Market Kalshi pricing project, step 1 (cross-sectional divergence
study -- see WEATHERBHN-MODEL-VS-MARKET-KALSHI-PRICING-SCOPING-2026-07-20.md).

NO REALIZED-OUTCOME VALIDATION. This computes where the model's bucket
probability diverges from the market's yes_mid across every snapshot,
both HIGH and LOW sides, all three cities -- but only the 348 rows
covered by model_vs_market_backtest.py (step 2) have an actual settled
outcome to check against. Everything here is a pattern, not evidence of
edge, and must be reported as such.

IMPORTANT LIMITATION, stated plainly rather than implied away: there is
no continuously-updating historical model-probability time series stored
anywhere. "Model probability" here is reconstructed ONCE PER (station,
date, side) using that day's calibrated forecast
(weather_gold_city_day_features.nws_tmax_calibrated_f /
nws_tmin_calibrated_f) and that station/season/variable's RMSE
(model_calibration, source_model='nws', lead_time_hours=24) as sigma,
run through calculate_bucket_probability() -- the SAME function
cp4_kelly_sizer.py uses for the live ladder, imported directly, not
reimplemented. This value is held CONSTANT across every snapshot that
day, even though the market's price moves intraday. Divergence numbers
here reflect "model's day-level forecast vs. market's intraday price,"
not a true point-in-time comparison -- a real limitation of what's
available, not a design choice, and it means any intraday pattern found
(e.g. divergence widening near market close) is really just the market
moving while the model's single number stays fixed, not the model
updating.

Usage:
    python3 model_vs_market_divergence.py

Environment:
    DATABASE_URL  PostgreSQL connection string (from /etc/bhn-trading/env)
"""
import os
import sys

import pandas as pd
import psycopg2
import psycopg2.extras

sys.path.insert(0, "/opt/bhn/trading")
from cp4_kelly_sizer import calculate_bucket_probability  # reuse, don't re-derive

LEAD_TIME_HOURS = 24
SOURCE_MODEL = "nws"


def build_bucket_lookup(conn) -> pd.DataFrame:
    """One row per (station_code, target_date, contract_side, bucket_floor,
    bucket_cap) that actually has market snapshots -- small (hundreds to
    low thousands of rows), not the full ~26.7M snapshot table."""
    buckets = pd.read_sql("""
        SELECT DISTINCT station_code, target_date, contract_side, bucket_floor, bucket_cap
        FROM weather_silver_market_conformed
        WHERE bucket_floor IS NOT NULL OR bucket_cap IS NOT NULL
    """, conn)

    forecasts = pd.read_sql("""
        SELECT station_code, target_date, nws_tmax_calibrated_f, nws_tmin_calibrated_f
        FROM weather_gold_city_day_features
    """, conn)

    calib = pd.read_sql("""
        SELECT station_code, variable, season, rmse
        FROM model_calibration
        WHERE source_model = %(source_model)s AND lead_time_hours = %(lead)s
    """, conn, params={"source_model": SOURCE_MODEL, "lead": LEAD_TIME_HOURS})

    def season_for(d) -> str:
        m = d.month
        if m in (12, 1, 2):
            return "winter"
        if m in (3, 4, 5):
            return "spring"
        if m in (6, 7, 8):
            return "summer"
        return "fall"

    buckets = buckets.merge(forecasts, on=["station_code", "target_date"], how="left")
    buckets["season"] = pd.to_datetime(buckets["target_date"]).apply(season_for)

    calib_tmax = calib[calib["variable"] == "tmax_f"].rename(columns={"rmse": "rmse_tmax"})
    calib_tmin = calib[calib["variable"] == "tmin_f"].rename(columns={"rmse": "rmse_tmin"})
    buckets = buckets.merge(calib_tmax[["station_code", "season", "rmse_tmax"]],
                             on=["station_code", "season"], how="left")
    buckets = buckets.merge(calib_tmin[["station_code", "season", "rmse_tmin"]],
                             on=["station_code", "season"], how="left")

    rows = []
    skipped_no_forecast = 0
    for _, r in buckets.iterrows():
        if r["contract_side"] == "high":
            blended_mean, sigma = r["nws_tmax_calibrated_f"], r["rmse_tmax"]
        else:
            blended_mean, sigma = r["nws_tmin_calibrated_f"], r["rmse_tmin"]
        if pd.isna(blended_mean) or pd.isna(sigma):
            skipped_no_forecast += 1
            continue
        prob, dist_used, sigma_dist = calculate_bucket_probability(
            predicted_tmax_f=float(blended_mean), sigma=float(sigma),
            bucket_floor=float(r["bucket_floor"]) if pd.notna(r["bucket_floor"]) else None,
            bucket_cap=float(r["bucket_cap"]) if pd.notna(r["bucket_cap"]) else None,
            blended_mean=float(blended_mean),
        )
        rows.append({
            "station_code": r["station_code"], "target_date": r["target_date"],
            "contract_side": r["contract_side"], "bucket_floor": r["bucket_floor"],
            "bucket_cap": r["bucket_cap"], "model_probability": prob,
            "distribution_used": dist_used,
        })

    print(f"Bucket-days with a forecast+sigma available: {len(rows):,} "
          f"(skipped {skipped_no_forecast:,} for missing forecast/calibration)")
    return pd.DataFrame(rows)


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("ERROR: DATABASE_URL not set")

    conn = psycopg2.connect(db_url)
    try:
        lookup = build_bucket_lookup(conn)

        with conn.cursor() as cur:
            cur.execute("""
                CREATE TEMP TABLE model_bucket_lookup (
                    station_code TEXT, target_date DATE, contract_side TEXT,
                    bucket_floor NUMERIC, bucket_cap NUMERIC,
                    model_probability NUMERIC, distribution_used TEXT
                )
            """)
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO model_bucket_lookup VALUES %s",
                [(r.station_code, r.target_date, r.contract_side,
                  r.bucket_floor if pd.notna(r.bucket_floor) else None,
                  r.bucket_cap if pd.notna(r.bucket_cap) else None,
                  r.model_probability, r.distribution_used)
                 for r in lookup.itertuples()],
            )
            cur.execute("CREATE INDEX ON model_bucket_lookup (station_code, target_date, contract_side)")

            cur.execute("""
                SELECT m.station_code, m.contract_side, m.market_liquidity_flag,
                       COUNT(*) AS n_snapshots,
                       COUNT(DISTINCT m.market_ticker) AS n_contracts,
                       ROUND(AVG(l.model_probability - m.yes_mid)::numeric, 4) AS mean_divergence,
                       ROUND(STDDEV(l.model_probability - m.yes_mid)::numeric, 4) AS stddev_divergence,
                       ROUND(AVG(ABS(l.model_probability - m.yes_mid))::numeric, 4) AS mean_abs_divergence
                FROM weather_silver_market_conformed m
                JOIN model_bucket_lookup l
                  ON l.station_code = m.station_code AND l.target_date = m.target_date
                 AND l.contract_side = m.contract_side
                 AND l.bucket_floor IS NOT DISTINCT FROM m.bucket_floor
                 AND l.bucket_cap IS NOT DISTINCT FROM m.bucket_cap
                GROUP BY m.station_code, m.contract_side, m.market_liquidity_flag
                ORDER BY m.station_code, m.contract_side, m.market_liquidity_flag
            """)
            print(f"\n{'station':<8} {'side':<6} {'liquidity':<10} {'n_snap':>9} {'n_ctr':>6} "
                  f"{'mean_div':>9} {'stddev':>8} {'mean_abs':>9}")
            for row in cur.fetchall():
                station, side, liq, n_snap, n_ctr, mean_div, stddev_div, mean_abs = row
                print(f"{station:<8} {side:<6} {str(liq):<10} {n_snap:>9,} {n_ctr:>6} "
                      f"{mean_div:>9} {stddev_div:>8} {mean_abs:>9}")
    finally:
        conn.close()

    print("\nNO REALIZED-OUTCOME VALIDATION for any row above -- these are divergence patterns")
    print("only. See model_vs_market_backtest.py for the sole outcome-validated piece (348")
    print("resolved HIGH-side rows, KDEN+KMIA).")


if __name__ == "__main__":
    main()
