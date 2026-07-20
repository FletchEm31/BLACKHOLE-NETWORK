#!/usr/bin/env python3
"""
One-off tuning sweep to check whether the item-4 corrected conclusion
(narrow early-morning HIGH-side edge, no LOW-side edge) is a structural
finding or an artifact of k=30 / season-only z-score normalization.
Not meant to become a permanent script -- results get folded into
WEATHERBHN-ANALOG-DAY-MATCHING-CORRECTED-CONCLUSION-2026-07-20.md.
"""
import os
import sys

import numpy as np
import pandas as pd
import psycopg2

sys.path.insert(0, "/opt/bhn/trading")
import asos_analog_day_matching as m

TEST_HOURS = (0, 6, 12, 18)
EXCLUDE_WINDOW_DAYS = m.EXCLUDE_WINDOW_DAYS


def zscore_by_season_hour(df, feature_cols):
    """Alternative to m._zscore_by_season: normalize per (season) only is
    what's already tested; this variant groups by season AND treats each
    checkpoint-hour's column separately (which it already does, since
    hour is baked into the column name) -- the real alternative worth
    testing is normalizing by (season, MONTH) for finer seasonal
    granularity than the 4-way season bucket."""
    z = df.copy()
    month = df["local_date"].dt.month
    for col in feature_cols:
        grp_mean = df.groupby(month)[col].transform("mean")
        grp_std = df.groupby(month)[col].transform("std").replace(0, np.nan)
        z[col] = (df[col] - grp_mean) / grp_std
    return z


def sweep_station(df, station, year):
    print(f"\n########## {station} ##########")
    for as_of_hour in TEST_HOURS:
        feature_cols = m._feature_cols(as_of_hour)
        year_mask = df["local_date"].dt.year == year
        query_indices = df[year_mask].index

        for k in (10, 30, 100):
            zdf = m._zscore_by_season(df, feature_cols)
            highs, lows, actual_h, actual_l = [], [], [], []
            for idx in query_indices:
                dist = m.analog_distribution(df, zdf, idx, feature_cols, k=k)
                if dist is None:
                    continue
                highs.append(dist["high_median"])
                lows.append(dist["low_median"])
                actual_h.append(df.loc[idx, "high_temp_f"])
                actual_l.append(df.loc[idx, "low_temp_f"])
            if not highs:
                continue
            rmse_h = np.sqrt(np.mean((np.array(highs) - np.array(actual_h)) ** 2))
            rmse_l = np.sqrt(np.mean((np.array(lows) - np.array(actual_l)) ** 2))
            print(f"  hour={as_of_hour:>2}  k={k:>3}  season-norm   "
                  f"analog_hi={rmse_h:.2f}  analog_lo={rmse_l:.2f}  n={len(highs)}")

        # month-level normalization, k=30 only (the variant most likely to matter)
        zdf_month = zscore_by_season_hour(df, feature_cols)
        highs, lows, actual_h, actual_l = [], [], [], []
        for idx in query_indices:
            dist = m.analog_distribution(df, zdf_month, idx, feature_cols, k=30)
            if dist is None:
                continue
            highs.append(dist["high_median"])
            lows.append(dist["low_median"])
            actual_h.append(df.loc[idx, "high_temp_f"])
            actual_l.append(df.loc[idx, "low_temp_f"])
        if highs:
            rmse_h = np.sqrt(np.mean((np.array(highs) - np.array(actual_h)) ** 2))
            rmse_l = np.sqrt(np.mean((np.array(lows) - np.array(actual_l)) ** 2))
            print(f"  hour={as_of_hour:>2}  k=30  month-norm    "
                  f"analog_hi={rmse_h:.2f}  analog_lo={rmse_l:.2f}  n={len(highs)}")

        # persistence baseline for reference at this hour
        ph, pl, pn = m._persistence_offset_baseline(df, query_indices, as_of_hour)
        if ph is not None:
            print(f"  hour={as_of_hour:>2}  (persist baseline)         "
                  f"persist_hi={ph:.2f}  persist_lo={pl:.2f}  n={pn}")


def main():
    db_url = os.environ.get("DATABASE_URL")
    conn = psycopg2.connect(db_url)
    try:
        for station in ("KLAX", "KMIA"):
            df = m._load_data(conn, station)
            sweep_station(df, station, 2024)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
