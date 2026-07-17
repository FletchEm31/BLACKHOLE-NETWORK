#!/usr/bin/env python3
"""
One-time backfill: entry_edge_cents / entry_model_prob_no_cents for the 87
settled weather_position_exits rows that predate the 2026-07-17 schema
change (sql/migrations/2026-07-17-add-entry-time-edge-columns.sql).

Going forward, exit_audit_logger.py's record_paper_trade() populates these
columns directly at first qualification. This script only backfills history
that already existed before that code change landed.

Method: the orchestrator log (unrotated, covers the full settled-trade
window) logs "BET_NO <ticker> edge=X.X¢ stake=$Y.YY" only when a bucket
first qualifies -- the same cycle as entry_captured_at. The first such log
line per ticker is therefore the edge at entry. entry_model_prob_no_cents
is derived from it: edge_cents = model_prob_no_cents - no_ask_cents, so
model_prob_no_cents_at_entry = entry_edge (from log) + entry_no_ask_cents
(already frozen in the DB).

Known caveat, not fixed here: 3 of 85 matched trades reconstruct to an
entry_model_prob_no_cents above 100 (physically impossible for a
probability*100). Root cause suspected: entry_no_ask_cents on those rows
was backfilled by migration 003 (2026-07-03) using a method that doesn't
exactly line up with the literal first-qualifying-cycle price for a few
pre-migration rows. Stored as computed for transparency, not silently
corrected -- flagged in this script's summary output every run.

Idempotent: only updates rows where entry_edge_cents IS NULL, so re-running
is safe and won't clobber correctly-populated rows.
"""
import os
import re
import sys

import psycopg2
import psycopg2.extras

LOG_PATH = '/var/log/bhn-trading/weather-orchestrator.log'

LOG_RE = re.compile(
    r'^(?P<ts>\S+)Z INFO\s+BET_NO (?P<ticker>\S+)\s+edge=(?P<edge>-?[\d.]+)\S\s+stake=\$(?P<stake>[\d.]+)'
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


def first_bet_no_edge_by_ticker():
    first = {}
    with open(LOG_PATH, 'r', errors='replace') as f:
        for line in f:
            m = LOG_RE.match(line)
            if not m:
                continue
            ticker = m.group('ticker')
            if ticker not in first:
                first[ticker] = (m.group('ts'), float(m.group('edge')))
    return first


def main():
    dry_run = '--dry-run' in sys.argv

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT contract_ticker, entry_captured_at, entry_no_ask_cents
                FROM weather_position_exits
                WHERE scored_at IS NOT NULL
                  AND entry_edge_cents IS NULL
                ORDER BY contract_ticker
            """)
            rows = cur.fetchall()

        first_edges = first_bet_no_edge_by_ticker()

        matched, unmatched, anomalous = [], [], []
        for r in rows:
            ticker = r['contract_ticker']
            if ticker not in first_edges:
                unmatched.append(ticker)
                continue
            log_ts, entry_edge = first_edges[ticker]
            entry_no_ask = float(r['entry_no_ask_cents'])
            entry_model_prob = round(entry_edge + entry_no_ask, 2)
            if not (0 <= entry_model_prob <= 100):
                anomalous.append((ticker, entry_model_prob))
            matched.append((ticker, entry_edge, entry_model_prob))

        print(f'Settled rows needing backfill: {len(rows)}')
        print(f'Matched to a first-qualifying log line: {len(matched)}')
        print(f'Unmatched (left NULL): {len(unmatched)}')
        for t in unmatched:
            print(f'  UNMATCHED: {t}')
        print(f'Anomalous (entry_model_prob outside 0-100, stored as-is): {len(anomalous)}')
        for t, p in anomalous:
            print(f'  ANOMALOUS: {t}  entry_model_prob_no_cents={p}')

        if dry_run:
            print('\n--dry-run: no UPDATEs executed.')
            return

        with conn.cursor() as cur:
            for ticker, entry_edge, entry_model_prob in matched:
                cur.execute("""
                    UPDATE weather_position_exits
                    SET entry_edge_cents = %s,
                        entry_model_prob_no_cents = %s
                    WHERE contract_ticker = %s
                      AND entry_edge_cents IS NULL
                """, (entry_edge, entry_model_prob, ticker))
        conn.commit()
        print(f'\nBackfilled {len(matched)} rows.')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
