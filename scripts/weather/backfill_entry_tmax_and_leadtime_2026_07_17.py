#!/usr/bin/env python3
"""
One-time backfill: entry_predicted_tmax_f / entry_hours_to_settle for the
87 settled weather_position_exits rows that predate the 2026-07-17b schema
change (sql/migrations/2026-07-17b-add-entry-tmax-and-lead-time-columns.sql).

Going forward, exit_audit_logger.py's record_paper_trade() populates both
columns directly at first qualification.

entry_predicted_tmax_f: reconstructed from the orchestrator log's per-cycle
CP3 summary line ("STATION target_date: CP1=PASS CP2=... CP3=92.4F(...)"),
logged every cycle regardless of qualification. Matched to entry_captured_at
by NEAREST timestamp (not exact-second) within a 120s tolerance -- the CP3
line is logged by the caller AFTER record_paper_trade()'s DB writes
complete, so it lags entry_captured_at by a small, variable gap. Cycles run
5 minutes apart, so 120s cannot cross into an adjacent cycle. Approximate;
~84% match rate on the existing 87 rows in testing. Left NULL when
unmatched, not guessed at.

entry_hours_to_settle: NOT reconstructed from the log -- computed exactly
as _settlement_dt(station_code, target_date) - entry_captured_at, using the
same settlement-time function cp4_kelly_sizer.py itself uses. Zero
approximation error, 100% coverage.

Idempotent: only updates rows where the target column IS NULL.
"""
import os
import re
import sys
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cp4_kelly_sizer import _settlement_dt  # noqa: E402

LOG_PATH = '/var/log/bhn-trading/weather-orchestrator.log'
TOLERANCE_SEC = 120

CP3_RE = re.compile(
    r'^(?P<ts>\S+)Z INFO (?P<station>[A-Z]{4}) (?P<target_date>\d{4}-\d{2}-\d{2}): '
    r'CP1=\S+ CP2=\S+ CP3=(?P<tmax>-?[\d.]+)F'
)


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


def load_cp3_lines():
    by_key = {}
    with open(LOG_PATH, 'r', errors='replace') as f:
        for line in f:
            m = CP3_RE.match(line)
            if not m:
                continue
            key = (m.group('station'), m.group('target_date'))
            dt = datetime.strptime(m.group('ts'), '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
            by_key.setdefault(key, []).append((dt.timestamp(), float(m.group('tmax'))))
    for k in by_key:
        by_key[k].sort()
    return by_key


def nearest(cp3_list, target_epoch, tolerance):
    best, best_gap = None, tolerance + 1
    for epoch, tmax in cp3_list:
        gap = abs(epoch - target_epoch)
        if gap < best_gap:
            best_gap, best = gap, tmax
    return best


def main():
    dry_run = '--dry-run' in sys.argv
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT contract_ticker, station_code, target_date, entry_captured_at
                FROM weather_position_exits
                WHERE scored_at IS NOT NULL
                  AND (entry_predicted_tmax_f IS NULL OR entry_hours_to_settle IS NULL)
                ORDER BY contract_ticker
            """)
            rows = cur.fetchall()

        cp3_by_key = load_cp3_lines()

        tmax_matched = tmax_unmatched = 0
        updates = []
        for r in rows:
            entry_dt = r['entry_captured_at']
            key = (r['station_code'], r['target_date'].isoformat())
            entry_tmax = nearest(cp3_by_key.get(key, []), entry_dt.timestamp(), TOLERANCE_SEC)
            if entry_tmax is not None:
                tmax_matched += 1
            else:
                tmax_unmatched += 1

            settle_dt = _settlement_dt(r['station_code'], r['target_date'])
            entry_hours = round(max((settle_dt - entry_dt).total_seconds() / 3600.0, 0.0), 2)

            updates.append((r['contract_ticker'], entry_tmax, entry_hours))

        print(f'Rows needing backfill: {len(rows)}')
        print(f'entry_predicted_tmax_f matched: {tmax_matched}  unmatched (left NULL): {tmax_unmatched}')
        print(f'entry_hours_to_settle: {len(updates)} (exact, 100% coverage)')

        if dry_run:
            print('\n--dry-run: no UPDATEs executed.')
            return

        with conn.cursor() as cur:
            for ticker, entry_tmax, entry_hours in updates:
                cur.execute("""
                    UPDATE weather_position_exits
                    SET entry_predicted_tmax_f = COALESCE(entry_predicted_tmax_f, %s),
                        entry_hours_to_settle   = COALESCE(entry_hours_to_settle, %s)
                    WHERE contract_ticker = %s
                """, (entry_tmax, entry_hours, ticker))
        conn.commit()
        print(f'\nBackfilled {len(updates)} rows.')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
