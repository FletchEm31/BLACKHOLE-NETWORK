#!/usr/bin/env python3
"""
One-time backfill: entry_sigma_used for the 87 settled weather_position_exits
rows that predate the 2026-07-17d schema change
(sql/migrations/2026-07-17d-add-entry-sigma-column.sql).

Going forward, exit_audit_logger.py's record_paper_trade() populates this
column directly at first qualification (reusing the same sigma_used value
already captured in the params dict -- see _RECORD_SQL).

Method: same as backfill_entry_tmax_and_leadtime_2026_07_17.py's
entry_predicted_tmax_f reconstruction, and the entry-time sigma
reconstruction already done ad-hoc tonight for
WEATHERBHN-SIGMA-ZONE-ANALYSIS-2026-07-17.md (73/87 coverage there).
write_to_ledger()'s dry_run branch (cp4_kelly_sizer.py) prints one line per
(station, target_date) per cycle:

    [DRY RUN] KDEN 2026-07-01: 23.82h to settle  sigma=3.098°F  6 buckets — ...

This line has NO timestamp of its own -- it inherits the timestamp of the
most recent preceding "=== [DRY RUN] orchestrator cycle start ===" line
(which IS logged via logger.info with a timestamp prefix). Matched to each
row's frozen entry_captured_at by nearest cycle-start timestamp, tolerance
120s (cycles run 5 minutes apart, so 120s cannot cross into an adjacent
cycle). Approximate; ~84% match rate expected based on tonight's ad-hoc
run and the identical-shaped entry_predicted_tmax_f backfill. Left NULL
when unmatched, not guessed at.

Idempotent: only updates rows where entry_sigma_used IS NULL, so
re-running is safe and won't clobber correctly-populated rows.
"""
import os
import re
import sys
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

LOG_PATH = '/var/log/bhn-trading/weather-orchestrator.log'
TOLERANCE_SEC = 120

CYCLE_START_RE = re.compile(
    r'^(?P<ts>\S+)Z INFO === \[DRY RUN\] orchestrator cycle start'
)
SIGMA_RE = re.compile(
    r'^\[DRY RUN\] (?P<station>[A-Z]{4}) (?P<target_date>\d{4}-\d{2}-\d{2}): '
    r'[\d.]+h to settle\s+sigma=(?P<sigma>[\d.]+)\xb0F'
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


def load_sigma_lines():
    """
    Two-pass-in-one: walk the log once, tracking the most recent cycle-start
    timestamp, and stamp each sigma line with it (the sigma line has no
    timestamp of its own).
    """
    by_key = {}
    current_ts = None
    with open(LOG_PATH, 'r', errors='replace') as f:
        for line in f:
            m_cycle = CYCLE_START_RE.match(line)
            if m_cycle:
                current_ts = datetime.strptime(
                    m_cycle.group('ts'), '%Y-%m-%dT%H:%M:%S'
                ).replace(tzinfo=timezone.utc)
                continue
            m_sigma = SIGMA_RE.match(line)
            if m_sigma and current_ts is not None:
                key = (m_sigma.group('station'), m_sigma.group('target_date'))
                by_key.setdefault(key, []).append(
                    (current_ts.timestamp(), float(m_sigma.group('sigma')))
                )
    for k in by_key:
        by_key[k].sort()
    return by_key


def nearest(sigma_list, target_epoch, tolerance):
    best, best_gap = None, tolerance + 1
    for epoch, sigma in sigma_list:
        gap = abs(epoch - target_epoch)
        if gap < best_gap:
            best_gap, best = gap, sigma
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
                  AND entry_sigma_used IS NULL
                ORDER BY contract_ticker
            """)
            rows = cur.fetchall()

        sigma_by_key = load_sigma_lines()

        matched, unmatched = 0, []
        updates = []
        for r in rows:
            entry_dt = r['entry_captured_at']
            key = (r['station_code'], r['target_date'].isoformat())
            entry_sigma = nearest(sigma_by_key.get(key, []), entry_dt.timestamp(), TOLERANCE_SEC)
            if entry_sigma is not None:
                matched += 1
                updates.append((r['contract_ticker'], entry_sigma))
            else:
                unmatched.append(r['contract_ticker'])

        print(f'Settled rows needing backfill: {len(rows)}')
        print(f'Matched to a nearest cycle-start sigma line: {matched}')
        print(f'Unmatched (left NULL): {len(unmatched)}')
        for t in unmatched:
            print(f'  UNMATCHED: {t}')

        if dry_run:
            print('\n--dry-run: no UPDATEs executed.')
            return

        with conn.cursor() as cur:
            for ticker, entry_sigma in updates:
                cur.execute("""
                    UPDATE weather_position_exits
                    SET entry_sigma_used = %s
                    WHERE contract_ticker = %s
                      AND entry_sigma_used IS NULL
                """, (entry_sigma, ticker))
        conn.commit()
        print(f'\nBackfilled {len(updates)} rows.')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
