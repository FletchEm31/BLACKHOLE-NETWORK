#!/usr/bin/env python3
"""
Model-vs-Market Kalshi pricing project, step 2 (the only genuine backtest
in this project -- see WEATHERBHN-MODEL-VS-MARKET-KALSHI-PRICING-SCOPING-
2026-07-20.md). Everything else in that project (LOW-side, unresolved
HIGH-side snapshots) is a cross-sectional divergence study with NO
realized-outcome validation and must not be blended with this.

Uses weather_model_accuracy's already-stored bhn_predicted_probability
and market_implied_probability directly -- these ARE the model's real
computed probability at bet time (calculate_bucket_probability() in
cp4_kelly_sizer.py already produced them), so this does not re-derive
anything.

divergence = bhn_predicted_probability - market_implied_probability
(YES-side, signed). Tests a simple rule: "take the model's side
(YES if divergence > 0, NO if divergence < 0) whenever
|divergence| exceeds a threshold" against the real recorded outcome,
for a sweep of thresholds. Split by market_liquidity (joined from
weather_gold_contract_ledger) -- liquid and illiquid are NEVER pooled
into the same summary number.

Scope: 348 rows total, HIGH-side only, KDEN (174) + KMIA (174),
2026-06-12 to 2026-07-16 -- a single summer window. Any pattern here is
preliminary and season-specific; watch for small-sample effects once
split by liquidity/city (see the scoping doc's own warning about this).

Usage:
    python3 model_vs_market_backtest.py

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
    return pd.read_sql("""
        SELECT ma.contract_id, ma.region, ma.bhn_predicted_probability,
               ma.market_implied_probability, ma.actual_outcome,
               l.market_liquidity, l.station_code
        FROM weather_model_accuracy ma
        LEFT JOIN weather_gold_contract_ledger l ON l.contract_ticker = ma.contract_id
        WHERE ma.resolved_at IS NOT NULL
    """, conn)


def evaluate(df: pd.DataFrame, liquidity_label: str):
    n = len(df)
    if n == 0:
        print(f"  {liquidity_label}: 0 rows -- skipped")
        return

    print(f"\n  --- {liquidity_label} (n={n}) ---")
    for thresh in THRESHOLDS:
        take = df[df["abs_divergence"] >= thresh]
        if len(take) == 0:
            print(f"    threshold={thresh:.2f}: 0 contracts qualify")
            continue
        # rule bet: YES if divergence > 0 (model thinks more likely than market), else NO
        bet_yes = take["divergence"] > 0
        rule_correct = (bet_yes & take["actual_outcome"]) | (~bet_yes & ~take["actual_outcome"])
        hit_rate = rule_correct.mean() * 100
        print(f"    threshold={thresh:.2f}: {len(take):>4} contracts qualify, "
              f"rule hit rate = {hit_rate:.1f}%")


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

    print(f"Total resolved rows: {len(df)}")
    print(f"By station: {df['station_code'].value_counts().to_dict()}")
    print(f"By liquidity: {df['market_liquidity'].value_counts(dropna=False).to_dict()}")

    print("\n=== ALL STATIONS COMBINED, split by liquidity (never pooled) ===")
    for liq in df["market_liquidity"].dropna().unique():
        evaluate(df[df["market_liquidity"] == liq], f"liquidity={liq}")
    if df["market_liquidity"].isna().any():
        evaluate(df[df["market_liquidity"].isna()], "liquidity=UNKNOWN (no ledger match)")

    print("\n=== PER STATION x LIQUIDITY (small-sample warning applies) ===")
    for station in df["station_code"].dropna().unique():
        sub = df[df["station_code"] == station]
        for liq in sub["market_liquidity"].dropna().unique():
            evaluate(sub[sub["market_liquidity"] == liq], f"{station} / liquidity={liq}")


if __name__ == "__main__":
    main()
