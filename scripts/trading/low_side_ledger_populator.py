#!/usr/bin/env python3
"""BHN — Low-side ledger data populator (STORAGE ONLY, NOT A TRADING SCRIPT).

Writes real Kalshi Low-temperature contract data (ticker, market prices,
NWS/GFS tmin_f forecasts) into weather_gold_contract_ledger with
contract_side='low', so Low-side data has the same storage rigor as
High-side while Fletch designs the actual Low-side strategy later.

Explicitly does NOT compute:
  - calibrated_prob / raw_model_prob (no Low-side inference model exists yet)
  - edge / edge_pct / edge_rank
  - recommended_action / signal_strength / stake_fraction / stake_usd
All of the above are left NULL. skip_reason is set to a fixed marker
explaining why. This script must never be wired into cp4_kelly_sizer.py's
write_to_ledger()/run_cp4_kelly() — those remain High-only and untouched.

Usage:
    python3 low_side_ledger_populator.py [--dry-run]

Run manually or via a separate systemd timer — NOT part of
core_trading_orchestrator.py's CP1-CP4 cycle.
"""
import argparse
import os
import sys
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

SKIP_REASON_DATA_ONLY = (
    "DATA_ONLY — Low-side storage pass (low_side_ledger_populator.py). "
    "No calibration model or sizing logic exists for Low yet; "
    "calibrated_prob/edge/recommended_action intentionally left NULL."
)


def _pg_connect():
    return psycopg2.connect(
        host=os.environ["PG_HOST"],
        port=os.environ.get("PG_PORT", "5432"),
        dbname=os.environ["PG_DB"],
        user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"],
    )


_LATEST_LOW_MARKETS = """
    SELECT DISTINCT ON (market_ticker)
        market_ticker, city, station_code, bucket_floor, bucket_cap,
        bucket_label, target_date, yes_bid, yes_ask, no_bid, no_ask,
        yes_mid, market_status
    FROM weather_bronze_kalshi_market_snapshots
    WHERE contract_side = 'low'
      AND target_date >= CURRENT_DATE
    ORDER BY market_ticker, retrieved_at DESC
"""

_LATEST_FORECAST = """
    SELECT tmin_f
    FROM weather_silver_forecast_conformed
    WHERE station_code = %(station_code)s
      AND target_date = %(target_date)s
      AND source_name = %(source_name)s
      AND is_latest_run = TRUE
    ORDER BY forecast_run_time DESC
    LIMIT 1
"""

_UPSERT_LOW_LEDGER_ROW = """
    INSERT INTO weather_gold_contract_ledger (
        city, station_code, target_date, contract_side, contract_ticker,
        bucket_floor, bucket_cap, bucket_label,
        nws_forecast_f, gfs_forecast_f,
        market_implied_prob, market_yes_mid,
        skip_reason, is_active, signal_generated_at,
        yes_bid, yes_ask, no_bid, no_ask,
        market_liquidity
    ) VALUES (
        %(city)s, %(station_code)s, %(target_date)s, 'low', %(contract_ticker)s,
        %(bucket_floor)s, %(bucket_cap)s, %(bucket_label)s,
        %(nws_forecast_f)s, %(gfs_forecast_f)s,
        %(market_implied_prob)s, %(market_yes_mid)s,
        %(skip_reason)s, %(is_active)s, %(signal_generated_at)s,
        %(yes_bid)s, %(yes_ask)s, %(no_bid)s, %(no_ask)s,
        %(market_liquidity)s
    )
    ON CONFLICT (contract_ticker) DO UPDATE SET
        nws_forecast_f       = EXCLUDED.nws_forecast_f,
        gfs_forecast_f       = EXCLUDED.gfs_forecast_f,
        market_implied_prob  = EXCLUDED.market_implied_prob,
        market_yes_mid       = EXCLUDED.market_yes_mid,
        skip_reason          = EXCLUDED.skip_reason,
        is_active            = EXCLUDED.is_active,
        signal_generated_at  = EXCLUDED.signal_generated_at,
        yes_bid              = EXCLUDED.yes_bid,
        yes_ask              = EXCLUDED.yes_ask,
        no_bid               = EXCLUDED.no_bid,
        no_ask               = EXCLUDED.no_ask,
        market_liquidity     = EXCLUDED.market_liquidity,
        ledger_updated_at    = NOW()
    -- Only touches columns this script owns. calibrated_prob, edge,
    -- recommended_action, stake_usd, etc. are never written or overwritten
    -- here, so a future Low strategy pass can populate them independently.
"""


def populate(dry_run: bool = False) -> int:
    conn = _pg_connect()
    conn.autocommit = False
    rows_written = 0
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_LATEST_LOW_MARKETS)
            markets = cur.fetchall()

            for m in markets:
                params = dict(m)

                cur.execute(_LATEST_FORECAST, {
                    "station_code": m["station_code"],
                    "target_date": m["target_date"],
                    "source_name": "nws",
                })
                nws_row = cur.fetchone()
                params["nws_forecast_f"] = nws_row["tmin_f"] if nws_row else None

                cur.execute(_LATEST_FORECAST, {
                    "station_code": m["station_code"],
                    "target_date": m["target_date"],
                    "source_name": "open_meteo_gfs_seamless",
                })
                gfs_row = cur.fetchone()
                params["gfs_forecast_f"] = gfs_row["tmin_f"] if gfs_row else None

                params["contract_ticker"] = m["market_ticker"]
                params["market_implied_prob"] = m["yes_mid"]
                params["market_yes_mid"] = m["yes_mid"]
                params["market_liquidity"] = None
                params["skip_reason"] = SKIP_REASON_DATA_ONLY
                params["is_active"] = True
                params["signal_generated_at"] = datetime.now(timezone.utc)

                if dry_run:
                    print(f"[DRY RUN] would upsert {params['contract_ticker']} "
                          f"({params['station_code']}, {params['target_date']}, "
                          f"nws_tmin_f={params['nws_forecast_f']})")
                else:
                    cur.execute(_UPSERT_LOW_LEDGER_ROW, params)
                rows_written += 1

        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return rows_written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                         help="Print what would be written without touching the DB")
    args = parser.parse_args()

    count = populate(dry_run=args.dry_run)
    print(f"low_side_ledger_populator: {count} Low-side ledger rows "
          f"{'would be ' if args.dry_run else ''}written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
