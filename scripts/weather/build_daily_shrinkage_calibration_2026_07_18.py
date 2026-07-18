#!/usr/bin/env python3
"""
Build (or refresh) weather_model_calibration_daily -- a day-of-year sigma
estimate, shrinkage-blended between real (thin) NWS forecast-error RMSE and
a deep climatological stddev prior. See sql/migrations/
2026-07-18c-day-of-year-shrinkage-calibration.sql for the full rationale.

Formula (per station_code/variable/day_of_year, circular +-window_days):
    sample_rmse    = sqrt(mean(forecast_error_f^2))     -- weather_silver_forecast_error
    prior_stddev   = stddev(tmax_f)                      -- weather_bronze_noaa_daily_actuals
    blended_var    = (prior_pseudo_n * prior_stddev^2 + sample_size * sample_rmse^2)
                      / (prior_pseudo_n + sample_size)
    blended_sigma  = sqrt(blended_var)

NOT wired into cp4_kelly_sizer.py or any live trading logic -- this script
only populates weather_model_calibration_daily for review/backtest.

Usage:
    python3 build_daily_shrinkage_calibration_2026_07_18.py [--dry-run]
        [--window-days N] [--prior-pseudo-n N] [--lead-hours N]
        [--min-samples N] [--variable tmax_f]

Environment:
    DATABASE_URL  PostgreSQL connection string (peer auth: postgresql:///eventhorizon)
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

# CP4's real settlement stations. KNYC intentionally does NOT map to the
# much-longer KJFK series in weather_bronze_noaa_daily_actuals -- see
# migration file header for the settlement-station-identity caveat this
# avoids repeating.
STATION_ICAO = {
    'KDEN': 'KDEN', 'KLAX': 'KLAX', 'KMIA': 'KMIA',
    'KNYC': 'KNYC', 'KAUS': 'KAUS', 'KORD': 'KORD',
}

DEFAULT_WINDOW_DAYS   = 12
DEFAULT_PRIOR_PSEUDO_N = 30
DEFAULT_LEAD_HOURS     = 24
DEFAULT_MIN_SAMPLES    = 1   # a day-of-year window can legitimately have very few real samples right now
DEFAULT_VARIABLE       = 'tmax_f'


def parse_args():
    p = argparse.ArgumentParser(description="Build day-of-year shrinkage sigma calibration")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    p.add_argument("--prior-pseudo-n", type=int, default=DEFAULT_PRIOR_PSEUDO_N)
    p.add_argument("--lead-hours", type=int, default=DEFAULT_LEAD_HOURS)
    p.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES,
                   help="Minimum sample_size (real forecast-error count) to write a row at all")
    p.add_argument("--variable", type=str, default=DEFAULT_VARIABLE)
    return p.parse_args()


def get_conn():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("ERROR: DATABASE_URL not set")
    return psycopg2.connect(db_url)


def circular_doy_distance(doy_a: np.ndarray, doy_b: int, year_len: int = 366) -> np.ndarray:
    """Shortest distance between day-of-year values, wrapping across the
    Dec 31 -> Jan 1 boundary (e.g. day 365 and day 3 are 4 days apart, not 362)."""
    raw = np.abs(doy_a - doy_b)
    return np.minimum(raw, year_len - raw)


def load_forecast_errors(conn, station_code: str, variable: str, lead_hours: int) -> pd.DataFrame:
    df = pd.read_sql(
        """
        SELECT target_date, forecast_error_f
        FROM weather_silver_forecast_error
        WHERE station_code = %(station)s AND feature_name = %(variable)s
          AND source_name = 'nws' AND lead_hours = %(lead_hours)s
          AND forecast_error_f IS NOT NULL
        """,
        conn, params={"station": station_code, "variable": variable, "lead_hours": lead_hours},
    )
    if df.empty:
        return df
    df["doy"] = pd.to_datetime(df["target_date"]).dt.dayofyear
    return df


def load_noaa_actuals(conn, icao_code: str, variable: str) -> pd.DataFrame:
    col = {"tmax_f": "tmax_f", "tmin_f": "tmin_f"}.get(variable)
    if col is None:
        return pd.DataFrame()
    df = pd.read_sql(
        f"""
        SELECT date, {col} AS value
        FROM weather_bronze_noaa_daily_actuals
        WHERE icao_code = %(icao)s AND {col} IS NOT NULL
        """,
        conn, params={"icao": icao_code},
    )
    if df.empty:
        return df
    df["doy"] = pd.to_datetime(df["date"]).dt.dayofyear
    return df


def build_station_rows(station_code: str, args) -> list[dict]:
    conn = get_conn()
    try:
        fc = load_forecast_errors(conn, station_code, args.variable, args.lead_hours)
        icao = STATION_ICAO[station_code]
        clim = load_noaa_actuals(conn, icao, args.variable)
    finally:
        conn.close()

    if clim.empty:
        print(f"  {station_code}: no NOAA daily-actuals rows for icao_code={icao!r} -- skipping station")
        return []

    fc_doy = fc["doy"].to_numpy() if not fc.empty else np.array([])
    fc_err = fc["forecast_error_f"].to_numpy(dtype=float) if not fc.empty else np.array([])
    clim_doy = clim["doy"].to_numpy()
    clim_val = clim["value"].to_numpy(dtype=float)

    rows = []
    for doy in range(1, 367):
        sample_size = 0
        sample_mean_bias = None
        sample_rmse = None
        if fc_doy.size:
            mask = circular_doy_distance(fc_doy, doy) <= args.window_days
            window_err = fc_err[mask]
            sample_size = int(window_err.size)
            if sample_size > 0:
                sample_mean_bias = float(np.mean(window_err))
                sample_rmse = float(np.sqrt(np.mean(window_err ** 2)))

        clim_mask = circular_doy_distance(clim_doy, doy) <= args.window_days
        window_clim = clim_val[clim_mask]
        prior_n = int(window_clim.size)
        if prior_n < 2:
            continue  # can't compute a stddev from <2 points -- skip, never guess
        prior_mean = float(np.mean(window_clim))
        prior_stddev = float(np.std(window_clim, ddof=1))

        if sample_size < args.min_samples:
            # Not enough real forecast-error data yet for this day-of-year
            # window at all -- still worth recording the prior-only estimate
            # (blend collapses to the prior when sample_size=0), so CP4 (once
            # wired) always has *something* rather than a NULL gap.
            sample_rmse_for_blend = 0.0
            weight_n = 0
        else:
            sample_rmse_for_blend = sample_rmse
            weight_n = sample_size

        prior_var = prior_stddev ** 2
        sample_var = sample_rmse_for_blend ** 2
        blended_var = (args.prior_pseudo_n * prior_var + weight_n * sample_var) / (args.prior_pseudo_n + weight_n)
        blended_sigma = float(np.sqrt(blended_var))
        blend_weight_sample = weight_n / (args.prior_pseudo_n + weight_n)

        rows.append({
            "station_code": station_code, "variable": args.variable, "day_of_year": doy,
            "lead_time_hours": args.lead_hours, "source_model": "nws", "window_days": args.window_days,
            "sample_size": sample_size, "sample_mean_bias": sample_mean_bias, "sample_rmse": sample_rmse,
            "prior_n": prior_n, "prior_mean_tmax_f": prior_mean, "prior_stddev": prior_stddev,
            "prior_pseudo_n": args.prior_pseudo_n, "blend_weight_sample": round(blend_weight_sample, 4),
            "blended_sigma": round(blended_sigma, 4),
        })
    return rows


UPSERT_SQL = """
INSERT INTO weather_model_calibration_daily (
    station_code, variable, day_of_year, lead_time_hours, source_model, window_days,
    sample_size, sample_mean_bias, sample_rmse,
    prior_n, prior_mean_tmax_f, prior_stddev,
    prior_pseudo_n, blend_weight_sample, blended_sigma
) VALUES (
    %(station_code)s, %(variable)s, %(day_of_year)s, %(lead_time_hours)s, %(source_model)s, %(window_days)s,
    %(sample_size)s, %(sample_mean_bias)s, %(sample_rmse)s,
    %(prior_n)s, %(prior_mean_tmax_f)s, %(prior_stddev)s,
    %(prior_pseudo_n)s, %(blend_weight_sample)s, %(blended_sigma)s
)
ON CONFLICT (station_code, variable, day_of_year, lead_time_hours, source_model) DO UPDATE SET
    window_days          = EXCLUDED.window_days,
    sample_size           = EXCLUDED.sample_size,
    sample_mean_bias      = EXCLUDED.sample_mean_bias,
    sample_rmse           = EXCLUDED.sample_rmse,
    prior_n                = EXCLUDED.prior_n,
    prior_mean_tmax_f       = EXCLUDED.prior_mean_tmax_f,
    prior_stddev             = EXCLUDED.prior_stddev,
    prior_pseudo_n            = EXCLUDED.prior_pseudo_n,
    blend_weight_sample        = EXCLUDED.blend_weight_sample,
    blended_sigma                = EXCLUDED.blended_sigma,
    computed_at                    = NOW()
"""


def main():
    args = parse_args()
    print(f"Building day-of-year shrinkage calibration: variable={args.variable} "
          f"lead_hours={args.lead_hours} window_days={args.window_days} "
          f"prior_pseudo_n={args.prior_pseudo_n}" + (" [DRY RUN]" if args.dry_run else ""))

    all_rows = []
    for station_code in STATION_ICAO:
        print(f"\n{station_code}:")
        rows = build_station_rows(station_code, args)
        if rows:
            n_with_sample = sum(1 for r in rows if r["sample_size"] > 0)
            avg_sigma = np.mean([r["blended_sigma"] for r in rows])
            print(f"  {len(rows)} day-of-year rows, {n_with_sample} with real forecast-error samples, "
                  f"avg blended_sigma={avg_sigma:.3f}")
        all_rows.extend(rows)

    if args.dry_run:
        print(f"\n=== DRY RUN — {len(all_rows)} rows would be upserted ===")
        for r in all_rows[:5]:
            print(r)
        print("... (showing first 5) — no DB writes.")
        return 0

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, UPSERT_SQL, all_rows, page_size=200)
        conn.commit()
    finally:
        conn.close()

    print(f"\n=== LIVE RUN COMPLETE — {len(all_rows)} rows upserted ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
