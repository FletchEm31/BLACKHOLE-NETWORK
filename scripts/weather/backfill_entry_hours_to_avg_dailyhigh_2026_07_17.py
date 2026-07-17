#!/usr/bin/env python3
"""
One-time backfill: entry_hours_to_avg_dailyhigh for existing
weather_position_exits rows that predate the 2026-07-17e schema change
(sql/migrations/2026-07-17e-create-station-climatology-and-entry-hours-to-avg-dailyhigh.sql).

Unlike backfill_entry_sigma_2026_07_17.py (which needed to reconstruct a
value from historical log lines because sigma decays and was never frozen
pre-migration), this backfill is exact: entry_captured_at is already a
frozen, trustworthy column on every existing row, and
weather_station_climatology is static reference data — so every row can be
computed directly with the same formula exit_audit_logger.py now uses going
forward, no approximation or log-matching needed.

Idempotent: only updates rows where entry_hours_to_avg_dailyhigh IS NULL,
so re-running is safe. Rows for a station/month with no
weather_station_climatology entry (e.g. a not-yet-populated station) are
left NULL, not guessed at, and reported separately.
"""
import os
import sys
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exit_audit_logger import _entry_hours_to_avg_dailyhigh  # noqa: E402


def _get_conn():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        host = os.environ.get('PG_HOST')
        port = os.environ.get('PG_PORT', '5432')
        db   = os.environ.get('PG_DB')
        user = os.environ.get('PG_USER')
        pwd  = os.environ.get('PG_PASSWORD', '')
        if host and db and user:
            import urllib.parse
            db_url = (f'postgresql://{urllib.parse.quote(user)}:'
                      f'{urllib.parse.quote(pwd)}@{host}:{port}/{db}')
        else:
            sys.exit('ERROR: Neither DATABASE_URL nor PG_HOST/PG_DB/PG_USER are set')
    return psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor)


def main():
    dry_run = '--dry-run' in sys.argv
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT contract_ticker, station_code, target_date, entry_captured_at
                FROM weather_position_exits
                WHERE entry_hours_to_avg_dailyhigh IS NULL
                  AND entry_captured_at IS NOT NULL
                ORDER BY contract_ticker
            """)
            rows = cur.fetchall()

        computed, no_climatology = 0, []
        updates = []
        for r in rows:
            value = _entry_hours_to_avg_dailyhigh(
                conn, r['station_code'], r['target_date'], r['entry_captured_at']
            )
            if value is None:
                no_climatology.append((r['contract_ticker'], r['station_code']))
                continue
            computed += 1
            updates.append((r['contract_ticker'], value))

        print(f'Rows needing backfill: {len(rows)}')
        print(f'Computed (climatology available): {computed}')
        print(f'No climatology row for station/month (left NULL): {len(no_climatology)}')
        for ticker, station in no_climatology:
            print(f'  NO CLIMATOLOGY: {ticker} ({station})')

        if dry_run:
            print('\n--dry-run: no UPDATEs executed.')
            return

        with conn.cursor() as cur:
            for ticker, value in updates:
                cur.execute("""
                    UPDATE weather_position_exits
                    SET entry_hours_to_avg_dailyhigh = %s
                    WHERE contract_ticker = %s
                      AND entry_hours_to_avg_dailyhigh IS NULL
                """, (value, ticker))
        conn.commit()
        print(f'\nBackfilled {len(updates)} rows.')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
