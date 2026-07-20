#!/usr/bin/env python3
"""
Redo of model_vs_market_backtest.py using VALIDATED ground truth instead of
the raw stored weather_model_accuracy.actual_outcome (which is based on
whatever weather_silver_actuals_conformed 'nws_cli' value existed at
settlement time -- known to be wrong for at least 2 of the 348 rows, see
the KDEN bronze/silver divergence and CLI preliminary-vs-finalized scoping
docs from this session).

Ground truth preference order per row:
    1. weather_silver_actuals_conformed 'asos_derived_cli_algorithm'
       (validated this session against genuine CLI + NOAA; KLAX/KMIA only,
       no historical 1-min/5-min ASOS archive exists for KDEN)
    2. weather_bronze_noaa_daily_actuals (all stations, validated against
       genuine CLI earlier this session)
    3. fall back to the original stored actual_outcome if neither source
       has data for that station/date (flagged explicitly, not silently
       trusted)

Reports: how many of the 348 rows have a validated source at all, how many
flip vs. the original stored outcome, and the full liquidity x
hours-to-settlement hit-rate table recomputed on validated outcomes.

Usage:
    python3 model_vs_market_backtest_validated.py

Environment:
    DATABASE_URL  PostgreSQL connection string (from /etc/bhn-trading/env)
"""
import os
import sys

import numpy as np
import pandas as pd
import psycopg2

THRESHOLDS = (0.05, 0.10, 0.15, 0.20, 0.30)


def load_data(conn) -> pd.DataFrame:
    df = pd.read_sql("""
        SELECT ma.contract_id, ma.region, ma.bhn_predicted_probability,
               ma.market_implied_probability, ma.actual_outcome AS stored_outcome,
               l.market_liquidity, l.station_code, l.target_date,
               l.bucket_floor, l.bucket_cap, l.signal_generated_at, ma.resolved_at
        FROM weather_model_accuracy ma
        LEFT JOIN weather_gold_contract_ledger l ON l.contract_ticker = ma.contract_id
        WHERE ma.resolved_at IS NOT NULL
    """, conn)

    asos = pd.read_sql("""
        SELECT station_code, target_date, final_tmax_f AS asos_tmax
        FROM weather_silver_actuals_conformed
        WHERE actual_source = 'asos_derived_cli_algorithm'
    """, conn)
    noaa = pd.read_sql("""
        SELECT icao_code AS station_code, date AS target_date, tmax_f AS noaa_tmax
        FROM weather_bronze_noaa_daily_actuals
    """, conn)

    df["target_date"] = pd.to_datetime(df["target_date"])
    asos["target_date"] = pd.to_datetime(asos["target_date"])
    noaa["target_date"] = pd.to_datetime(noaa["target_date"])

    df = df.merge(asos, on=["station_code", "target_date"], how="left")
    df = df.merge(noaa, on=["station_code", "target_date"], how="left")
    return df


def yes_settled(value, floor, cap) -> bool | None:
    if pd.isna(value):
        return None
    ok = True
    if pd.notna(floor):
        ok = ok and (value >= floor)
    if pd.notna(cap):
        ok = ok and (value < cap)
    return bool(ok)


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("ERROR: DATABASE_URL not set")

    conn = psycopg2.connect(db_url)
    try:
        df = load_data(conn)
    finally:
        conn.close()

    df["divergence"] = df["bhn_predicted_probability"].astype(float) - df["market_implied_probability"].astype(float)
    df["abs_divergence"] = df["divergence"].abs()
    df["hours_to_resolve"] = (
        pd.to_datetime(df["resolved_at"], utc=True) - pd.to_datetime(df["signal_generated_at"], utc=True)
    ).dt.total_seconds() / 3600.0

    # validated value: prefer ASOS-derived (KMIA only), else NOAA, else None
    df["validated_value"] = df["asos_tmax"].where(df["asos_tmax"].notna(), df["noaa_tmax"])
    df["validated_source"] = np.where(df["asos_tmax"].notna(), "asos_derived",
                              np.where(df["noaa_tmax"].notna(), "noaa", "none"))

    df["validated_outcome"] = df.apply(
        lambda r: yes_settled(r["validated_value"], r["bucket_floor"], r["bucket_cap"]), axis=1)

    print(f"Total resolved rows: {len(df)}")
    print(f"Validated-source coverage: {df['validated_source'].value_counts().to_dict()}")

    have_both = df[df["validated_outcome"].notna()].copy()
    n_flip = (have_both["validated_outcome"] != have_both["stored_outcome"]).sum()
    print(f"\nRows with a validated outcome to compare: {len(have_both)}")
    print(f"Rows where validated outcome DIFFERS from originally stored outcome: {n_flip}")
    if n_flip:
        flips = have_both[have_both["validated_outcome"] != have_both["stored_outcome"]]
        print(flips[["contract_id", "station_code", "target_date", "validated_source",
                      "validated_value", "stored_outcome", "validated_outcome",
                      "bucket_floor", "bucket_cap"]].to_string(index=False))

    # For rows with no validated source at all, fall back to stored outcome
    # (flagged, not silently blended) so the backtest can still run on the full 348.
    df["final_outcome"] = df["validated_outcome"].where(df["validated_outcome"].notna(), df["stored_outcome"])
    n_fallback = (df["validated_source"] == "none").sum()
    print(f"\nRows with NO validated source (fell back to originally stored outcome): {n_fallback}")

    print("\n=== Hit-rate table on VALIDATED outcomes, liquidity x hours-to-settlement ===")
    df["time_bucket"] = pd.cut(df["hours_to_resolve"], bins=[-np.inf, 24, 72, np.inf],
                                labels=["lt_24h", "24_72h", "gt_72h"])

    for time_bucket in ["lt_24h", "24_72h", "gt_72h"]:
        sub_t = df[df["time_bucket"] == time_bucket]
        for liq in ("illiquid", "liquid", "thin"):
            sub = sub_t[sub_t["market_liquidity"] == liq]
            if len(sub) == 0:
                continue
            for thresh in (0.15,):
                take = sub[sub["abs_divergence"] >= thresh]
                if len(take) == 0:
                    print(f"  {time_bucket:<8} {liq:<10} n={len(sub):>4}  threshold={thresh}: 0 qualify")
                    continue
                bet_yes = take["divergence"] > 0
                correct = (bet_yes & take["final_outcome"]) | (~bet_yes & ~take["final_outcome"])
                print(f"  {time_bucket:<8} {liq:<10} n={len(sub):>4}  threshold={thresh}: "
                      f"{len(take):>3} qualify, hit rate = {correct.mean()*100:.1f}%")


if __name__ == "__main__":
    main()
