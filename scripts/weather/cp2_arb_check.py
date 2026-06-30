#!/usr/bin/env python3
"""
CP2: Structural Parity / Arb Check — Checkpoint 2.
Scans all open Kalshi buckets for a station/date and checks if yes_bid + no_ask != 1.00.

CRITICAL: Always reads real no_ask from DB.
NEVER derives no_price = 1 - yes_price.

Usage (standalone):
    python3 cp2_arb_check.py --station KLAX --date 2026-06-29

Environment:
    DATABASE_URL  PostgreSQL connection (peer auth: postgresql:///eventhorizon)
"""

import argparse
import os
import sys
from datetime import date

import psycopg2
import psycopg2.extras

ARB_THRESHOLD = 0.01  # flag if yes_bid + no_ask deviates from 1.00 by more than 1 cent


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


def check_structural_arb(station_code: str, target_date: date,
                         conn=None) -> dict:
    """
    Returns:
        {
            'arb_found': bool,
            'opportunities': [
                {
                    'bucket_label': str,
                    'bucket_floor': float,
                    'bucket_cap': float,
                    'yes_bid': float,    # in decimal (0.0–1.0)
                    'no_ask': float,     # in decimal — ALWAYS from DB, never derived
                    'sum': float,        # yes_bid + no_ask
                    'arb_gap': float,    # sum - 1.0 (negative = buy-both arb)
                    'arb_type': str,     # 'buy_both' | 'overpriced' | 'normal'
                }
            ],
            'snapshot_retrieved_at': datetime,
            'total_buckets': int,
        }
    """
    close_conn = conn is None
    if conn is None:
        conn = get_conn()

    try:
        with conn.cursor() as cur:
            # Use the most recent snapshot for this station/date
            cur.execute("""
                SELECT bucket_label, bucket_type, bucket_floor, bucket_cap,
                       yes_bid, yes_ask, no_bid, no_ask,
                       (yes_bid + no_ask)                       AS sum_decimal,
                       ABS(1.0 - (yes_bid + no_ask))           AS arb_gap_decimal,
                       retrieved_at
                FROM weather_bronze_kalshi_market_snapshots
                WHERE station_code = %s
                  AND target_date = %s
                  AND retrieved_at = (
                      SELECT MAX(retrieved_at)
                      FROM weather_bronze_kalshi_market_snapshots
                      WHERE station_code = %s AND target_date = %s
                  )
                  AND yes_bid IS NOT NULL
                  AND no_ask IS NOT NULL
                ORDER BY bucket_floor NULLS LAST
            """, (station_code, target_date, station_code, target_date))
            rows = cur.fetchall()

    finally:
        if close_conn:
            conn.close()

    if not rows:
        return {
            "arb_found": False,
            "opportunities": [],
            "snapshot_retrieved_at": None,
            "total_buckets": 0,
        }

    snap_time = rows[0]["retrieved_at"]
    opportunities = []
    arb_found = False

    for r in rows:
        yes_bid = float(r["yes_bid"])
        no_ask = float(r["no_ask"])    # always from DB — never 1 - yes_price
        total = yes_bid + no_ask
        gap = total - 1.0

        if gap < -ARB_THRESHOLD:
            arb_type = "buy_both"   # buy yes + buy no for < $1, collect $1
            arb_found = True
        elif gap > ARB_THRESHOLD:
            arb_type = "overpriced"
        else:
            arb_type = "normal"

        opportunities.append({
            "bucket_label": r["bucket_label"],
            "bucket_floor": float(r["bucket_floor"]) if r["bucket_floor"] is not None else None,
            "bucket_cap": float(r["bucket_cap"]) if r["bucket_cap"] is not None else None,
            "yes_bid": yes_bid,
            "no_ask": no_ask,
            "sum": round(total, 4),
            "arb_gap": round(gap, 4),
            "arb_gap_cents": round(gap * 100, 2),
            "arb_type": arb_type,
        })

    return {
        "arb_found": arb_found,
        "opportunities": opportunities,
        "snapshot_retrieved_at": snap_time,
        "total_buckets": len(rows),
    }


def _parse_args():
    p = argparse.ArgumentParser(description="CP2 structural arb check")
    p.add_argument("--station", required=True)
    p.add_argument("--date", required=True, type=date.fromisoformat)
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    result = check_structural_arb(args.station, args.date)

    print(f"CP2 Arb Check: {args.station} {args.date}")
    print(f"Snapshot: {result['snapshot_retrieved_at']}")
    print(f"Total buckets: {result['total_buckets']}")
    print(f"Arb found: {result['arb_found']}")
    print()
    print(f"{'Bucket':10} {'Floor':>6} {'Cap':>6} {'yes_bid':>8} {'no_ask':>8} {'sum':>6} {'gap¢':>7} {'type'}")
    print("-" * 72)
    for o in result["opportunities"]:
        print(f"{o['bucket_label']:10} "
              f"{(o['bucket_floor'] or 0):>6.0f} {(o['bucket_cap'] or 0):>6.0f} "
              f"{o['yes_bid']:>8.3f} {o['no_ask']:>8.3f} "
              f"{o['sum']:>6.3f} {o['arb_gap_cents']:>7.2f}  {o['arb_type']}")
