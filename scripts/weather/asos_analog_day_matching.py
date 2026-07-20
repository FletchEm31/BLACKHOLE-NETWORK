#!/usr/bin/env python3
"""
Analog-day matching (item 4 of WEATHERBHN-ASOS-MULTIVARIATE-PROBABILITY-
MODEL-SCOPING) -- the piece that turns the historical characterization
work (items 1-3) into an actual live number: given a day's trajectory so
far (temp, dewpoint, wind, pressure at the fixed checkpoints observed up
to some hour), find the N most similar historical same-season day-so-far
trajectories and build an empirical probability distribution for where
today's final high/low lands.

Data: weather_silver_asos_daily_trajectory_snapshots (8 fixed local-
standard-time checkpoints/day: 0,3,6,9,12,15,18,21) joined to
weather_silver_asos_daily_high_low_occurrence (the actual outcome).
Both are small enough (~5,800 days x ~40 features/station) to pull
entirely into pandas and do the nearest-neighbor search in-memory --
no new DB table needed for this piece, it's query-time computation.

Wind direction is circular (0/360 wrap) -- handled by converting to
sin/cos components before any distance calculation, not naive degree
differences.

Distance: Euclidean over z-scored features, where each feature is
normalized against its own (season, checkpoint-hour) population mean/std
-- "72F at 9am" means something different in January vs July, so
normalization has to be season-aware, not global.

Backtest mode excludes the query day itself and a +/-7-day window in the
same calendar year from the candidate pool (avoids trivial
autocorrelation inflating the apparent skill).

Usage:
    # Backtest: how well would analog matching have predicted actual
    # highs/lows for every day in a given year, at a given as-of hour?
    python3 asos_analog_day_matching.py --station KLAX --backtest-year 2024 --as-of-hour 12

    # Live query: analog distribution for a specific date using whatever
    # checkpoints already exist for it (e.g. an in-progress day)
    python3 asos_analog_day_matching.py --station KLAX --query-date 2026-07-15 --as-of-hour 12

Environment:
    DATABASE_URL  PostgreSQL connection string (from /etc/bhn-trading/env)
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
import psycopg2

CHECKPOINT_HOURS = (0, 3, 6, 9, 12, 15, 18, 21)
K_NEIGHBORS = 30
EXCLUDE_WINDOW_DAYS = 7


def _load_data(conn, station: str) -> pd.DataFrame:
    snaps = pd.read_sql(
        """SELECT local_date, local_hour, air_temp_f, dew_point_temp_f,
                  wind_speed_kt, wind_direction_deg, pressure_1_inhg
           FROM weather_silver_asos_daily_trajectory_snapshots
           WHERE station_code = %(station)s""",
        conn, params={"station": station},
    )
    hilo = pd.read_sql(
        """SELECT local_date, season, high_temp_f, low_temp_f,
                  high_local_minute, low_local_minute
           FROM weather_silver_asos_daily_high_low_occurrence
           WHERE station_code = %(station)s""",
        conn, params={"station": station},
    )
    running = pd.read_sql(
        """SELECT local_date, local_hour, running_high_so_far_f, running_low_so_far_f
           FROM weather_silver_asos_running_extremes
           WHERE station_code = %(station)s""",
        conn, params={"station": station},
    )

    snaps["wdir_sin"] = np.sin(np.radians(snaps["wind_direction_deg"]))
    snaps["wdir_cos"] = np.cos(np.radians(snaps["wind_direction_deg"]))

    pivot = snaps.pivot(index="local_date", columns="local_hour",
                         values=["air_temp_f", "dew_point_temp_f", "wind_speed_kt",
                                 "wdir_sin", "wdir_cos", "pressure_1_inhg"])
    pivot.columns = [f"{feat}_{hr}" for feat, hr in pivot.columns]
    pivot = pivot.reset_index()

    run_pivot = running.pivot(index="local_date", columns="local_hour",
                               values=["running_high_so_far_f", "running_low_so_far_f"])
    run_pivot.columns = [f"{feat}_{hr}" for feat, hr in run_pivot.columns]
    run_pivot = run_pivot.reset_index()

    df = pivot.merge(hilo, on="local_date", how="inner").merge(run_pivot, on="local_date", how="inner")
    df["local_date"] = pd.to_datetime(df["local_date"])
    return df


def _feature_cols(as_of_hour: int) -> list[str]:
    hours = [h for h in CHECKPOINT_HOURS if h <= as_of_hour]
    feats = []
    for base in ("air_temp_f", "dew_point_temp_f", "wind_speed_kt", "wdir_sin", "wdir_cos", "pressure_1_inhg"):
        feats += [f"{base}_{h}" for h in hours]
    return feats


def _zscore_by_season(df: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    """Normalize each feature against its own (season) population mean/std.
    (Checkpoint-hour is already baked into the column name, so grouping by
    season alone gives each hour x season combination its own scale.)"""
    z = df.copy()
    for col in feature_cols:
        grp_mean = df.groupby("season")[col].transform("mean")
        grp_std = df.groupby("season")[col].transform("std").replace(0, np.nan)
        z[col] = (df[col] - grp_mean) / grp_std
    return z


def analog_distribution(df: pd.DataFrame, zdf: pd.DataFrame, query_idx: int,
                         feature_cols: list[str], k: int = K_NEIGHBORS) -> dict | None:
    """Returns the empirical high/low distribution from the k nearest
    analog days to df.loc[query_idx], or None if the query row has any
    missing feature (can't compute a distance)."""
    query_vec = zdf.loc[query_idx, feature_cols].values.astype(float)
    if np.isnan(query_vec).any():
        return None

    query_date = df.loc[query_idx, "local_date"]
    query_season = df.loc[query_idx, "season"]

    pool = zdf[
        (df["season"] == query_season) &
        ((df["local_date"] - query_date).abs() > pd.Timedelta(days=EXCLUDE_WINDOW_DAYS))
    ]
    pool_feats = pool[feature_cols].values.astype(float)
    valid = ~np.isnan(pool_feats).any(axis=1)
    pool = pool[valid]
    pool_feats = pool_feats[valid]
    if len(pool) < k:
        return None

    dists = np.linalg.norm(pool_feats - query_vec, axis=1)
    nearest_idx = pool.index[np.argsort(dists)[:k]]

    neighbor_highs = df.loc[nearest_idx, "high_temp_f"].values
    neighbor_lows = df.loc[nearest_idx, "low_temp_f"].values

    return {
        "high_mean": neighbor_highs.mean(), "high_median": np.median(neighbor_highs),
        "high_p10": np.percentile(neighbor_highs, 10), "high_p90": np.percentile(neighbor_highs, 90),
        "low_mean": neighbor_lows.mean(), "low_median": np.median(neighbor_lows),
        "low_p10": np.percentile(neighbor_lows, 10), "low_p90": np.percentile(neighbor_lows, 90),
    }


def _persistence_offset_baseline(df: pd.DataFrame, query_indices, as_of_hour: int):
    """Stronger baseline than unconditional climatology: running high/low
    ALREADY OBSERVED as of as_of_hour, plus a season/hour-specific mean
    offset (actual_final - running_so_far) computed leave-out style
    (excludes the query day +/- EXCLUDE_WINDOW_DAYS, same as the analog
    pool). This baseline DOES use today's information -- if analog
    matching can't beat this, the RMSE gain over pure climatology was
    just 'using today's data' rather than a real analog-matching edge."""
    high_col = f"running_high_so_far_f_{as_of_hour}"
    low_col = f"running_low_so_far_f_{as_of_hour}"
    preds_high, preds_low, actual_h, actual_l = [], [], [], []

    for idx in query_indices:
        if pd.isna(df.loc[idx, high_col]) or pd.isna(df.loc[idx, low_col]):
            continue
        row_season = df.loc[idx, "season"]
        query_date = df.loc[idx, "local_date"]
        pool = df[(df["season"] == row_season) &
                  ((df["local_date"] - query_date).abs() > pd.Timedelta(days=EXCLUDE_WINDOW_DAYS))]
        pool = pool.dropna(subset=[high_col, low_col])
        if len(pool) < 10:
            continue
        offset_high = (pool["high_temp_f"] - pool[high_col]).mean()
        offset_low = (pool["low_temp_f"] - pool[low_col]).mean()
        preds_high.append(df.loc[idx, high_col] + offset_high)
        preds_low.append(df.loc[idx, low_col] + offset_low)
        actual_h.append(df.loc[idx, "high_temp_f"])
        actual_l.append(df.loc[idx, "low_temp_f"])

    if not preds_high:
        return None, None, 0
    rmse_high = float(np.sqrt(np.mean((np.array(preds_high) - np.array(actual_h)) ** 2)))
    rmse_low = float(np.sqrt(np.mean((np.array(preds_low) - np.array(actual_l)) ** 2)))
    return rmse_high, rmse_low, len(preds_high)


def run_backtest(df: pd.DataFrame, year: int, as_of_hours=None):
    """Sweeps all (or the given) checkpoint hours in one run and reports,
    per hour: analog-matching RMSE/coverage, the persistence+offset
    baseline (uses today's data, the fair comparison), and the
    unconditional climatology baseline (uses none -- kept only for
    context, not as the headline comparison)."""
    hours = as_of_hours or list(CHECKPOINT_HOURS)
    year_mask = df["local_date"].dt.year == year
    query_indices_all = df[year_mask].index

    rows = []
    for as_of_hour in hours:
        feature_cols = _feature_cols(as_of_hour)
        zdf = _zscore_by_season(df, feature_cols)

        results = []
        for idx in query_indices_all:
            dist = analog_distribution(df, zdf, idx, feature_cols)
            if dist is None:
                continue
            actual_high = df.loc[idx, "high_temp_f"]
            actual_low = df.loc[idx, "low_temp_f"]
            results.append({
                "actual_high": actual_high, "actual_low": actual_low,
                **dist,
                "high_in_p10_p90": dist["high_p10"] <= actual_high <= dist["high_p90"],
                "low_in_p10_p90": dist["low_p10"] <= actual_low <= dist["low_p90"],
            })

        if not results:
            rows.append({"hour": as_of_hour, "n": 0})
            continue

        res = pd.DataFrame(results)
        analog_rmse_high = float(np.sqrt(np.mean((res["high_median"] - res["actual_high"]) ** 2)))
        analog_rmse_low = float(np.sqrt(np.mean((res["low_median"] - res["actual_low"]) ** 2)))
        high_cov = float(res["high_in_p10_p90"].mean() * 100)
        low_cov = float(res["low_in_p10_p90"].mean() * 100)

        # unconditional climatology (context only, not the headline comparison)
        clim_preds_high, clim_preds_low, actual_h, actual_l = [], [], [], []
        for idx in query_indices_all:
            row_season = df.loc[idx, "season"]
            query_date = df.loc[idx, "local_date"]
            pool = df[(df["season"] == row_season) &
                      ((df["local_date"] - query_date).abs() > pd.Timedelta(days=EXCLUDE_WINDOW_DAYS))]
            if len(pool) < 10:
                continue
            clim_preds_high.append(pool["high_temp_f"].mean())
            clim_preds_low.append(pool["low_temp_f"].mean())
            actual_h.append(df.loc[idx, "high_temp_f"])
            actual_l.append(df.loc[idx, "low_temp_f"])
        clim_rmse_high = float(np.sqrt(np.mean((np.array(clim_preds_high) - np.array(actual_h)) ** 2)))
        clim_rmse_low = float(np.sqrt(np.mean((np.array(clim_preds_low) - np.array(actual_l)) ** 2)))

        persist_rmse_high, persist_rmse_low, persist_n = _persistence_offset_baseline(
            df, query_indices_all, as_of_hour)

        rows.append({
            "hour": as_of_hour, "n": len(res),
            "analog_high_rmse": analog_rmse_high, "analog_low_rmse": analog_rmse_low,
            "persist_high_rmse": persist_rmse_high, "persist_low_rmse": persist_rmse_low,
            "clim_high_rmse": clim_rmse_high, "clim_low_rmse": clim_rmse_low,
            "high_cov": high_cov, "low_cov": low_cov,
        })

    print(f"\n=== Backtest: {year}, k={K_NEIGHBORS}, all checkpoint hours ===")
    print(f"{'hour':>5} {'n':>5}  {'analog_hi':>10} {'persist_hi':>11} {'clim_hi':>8}  "
          f"{'analog_lo':>10} {'persist_lo':>11} {'clim_lo':>8}  {'cov_hi':>7} {'cov_lo':>7}")
    for r in rows:
        if r["n"] == 0:
            print(f"{r['hour']:>5} {0:>5}  (no valid days)")
            continue
        ph = f"{r['persist_high_rmse']:.2f}" if r['persist_high_rmse'] is not None else "n/a"
        pl = f"{r['persist_low_rmse']:.2f}" if r['persist_low_rmse'] is not None else "n/a"
        print(f"{r['hour']:>5} {r['n']:>5}  {r['analog_high_rmse']:>10.2f} {ph:>11} {r['clim_high_rmse']:>8.2f}  "
              f"{r['analog_low_rmse']:>10.2f} {pl:>11} {r['clim_low_rmse']:>8.2f}  "
              f"{r['high_cov']:>6.1f}% {r['low_cov']:>6.1f}%")
    print("\nRMSE in F. 'persist_*' = running-so-far + season/hour offset (uses today's data --")
    print("the fair comparison). 'clim_*' = unconditional same-season mean (uses none -- context only).")


def run_query(df: pd.DataFrame, as_of_hour: int, query_date: str):
    feature_cols = _feature_cols(as_of_hour)
    zdf = _zscore_by_season(df, feature_cols)
    match = df[df["local_date"] == pd.Timestamp(query_date)]
    if match.empty:
        sys.exit(f"ERROR: {query_date} not found in trajectory data for this station")
    idx = match.index[0]
    dist = analog_distribution(df, zdf, idx, feature_cols)
    if dist is None:
        sys.exit(f"ERROR: {query_date} has missing checkpoint data at/before hour {as_of_hour}, "
                  f"or insufficient same-season pool -- can't compute analog distribution")
    print(f"\n=== Analog distribution for {query_date}, as-of {as_of_hour}:00 local, k={K_NEIGHBORS} ===")
    print(f"HIGH: median={dist['high_median']:.1f}F  mean={dist['high_mean']:.1f}F  "
          f"[P10={dist['high_p10']:.1f}, P90={dist['high_p90']:.1f}]")
    print(f"LOW:  median={dist['low_median']:.1f}F  mean={dist['low_mean']:.1f}F  "
          f"[P10={dist['low_p10']:.1f}, P90={dist['low_p90']:.1f}]")
    if not match.empty and pd.notna(match.iloc[0]["high_temp_f"]):
        print(f"\n(actual high/low for this date, if already resolved: "
              f"{match.iloc[0]['high_temp_f']}F / {match.iloc[0]['low_temp_f']}F)")


def parse_args():
    p = argparse.ArgumentParser(description="ASOS analog-day matching")
    p.add_argument("--station", required=True, choices=["KLAX", "KMIA"])
    p.add_argument("--as-of-hour", type=int, choices=CHECKPOINT_HOURS,
                   help="Required for --query-date. For --backtest-year, omit to sweep all 8 checkpoint hours.")
    p.add_argument("--backtest-year", type=int)
    p.add_argument("--query-date")
    return p.parse_args()


def main():
    args = parse_args()
    if not args.backtest_year and not args.query_date:
        sys.exit("ERROR: specify --backtest-year or --query-date")
    if args.query_date and args.as_of_hour is None:
        sys.exit("ERROR: --query-date requires --as-of-hour")

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("ERROR: DATABASE_URL not set")

    conn = psycopg2.connect(db_url)
    try:
        df = _load_data(conn, args.station)
    finally:
        conn.close()

    if args.backtest_year:
        hours = [args.as_of_hour] if args.as_of_hour is not None else None
        run_backtest(df, args.backtest_year, as_of_hours=hours)
    else:
        run_query(df, args.as_of_hour, args.query_date)


if __name__ == "__main__":
    main()
