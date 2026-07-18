#!/usr/bin/env python3
"""
Proves the exact failure mode found auditing the 2026-07-18 side column:
a NO bet and a YES bet on the SAME contract_ticker must persist as two
distinct rows, not collide/overwrite via ON CONFLICT (contract_ticker, side).

Uses an obviously-fake station_code/contract_ticker/target_date so it can
never collide with real data, and runs the whole thing inside one
transaction that is always ROLLED BACK -- nothing is ever committed, so
this is safe to run against the live production database.

Usage:
    python3 test_side_collision_2026_07_18.py
Exits 0 and prints PASS on success, exits 1 and prints the failure on any
assertion violation.
"""
import sys
from datetime import date, timedelta

from exit_audit_logger import record_paper_trade, _get_conn

FAKE_STATION = 'KTEST'
FAKE_TICKER  = 'TEST-COLLISION-DO-NOT-USE-2026-07-18'
# A few days out, not decades: entry_hours_to_settle is NUMERIC(6,2), max
# ~9999 hours (~416 days) -- a distant fake date like 2099 overflows it.
# FAKE_STATION/FAKE_TICKER are what actually guarantee no collision with
# real data, not the date.
FAKE_DATE    = date.today() + timedelta(days=5)


def _bucket(no_ask_cents: float) -> dict:
    return {
        'qualifies':        True,
        'market_ticker':    FAKE_TICKER,
        'bucket_label':     '90-91',
        'bucket_floor':     90.0,
        'bucket_cap':       91.0,
        'model_prob_cents': 60.0,
        'no_ask_cents':     no_ask_cents,
        'edge_cents':       12.0,
        'contracts':        10,
        'stake_usd':        5.0,
        'hours_to_settle':  48.0,
        'sigma_used':       3.5,
    }


def main():
    conn = _get_conn()
    conn.autocommit = False
    try:
        n_no  = record_paper_trade(conn, FAKE_STATION, FAKE_DATE, 90.5,
                                    [_bucket(80.0)], side='NO')
        n_yes = record_paper_trade(conn, FAKE_STATION, FAKE_DATE, 90.5,
                                    [_bucket(20.0)], side='YES')

        assert n_no == 1, f'expected 1 row inserted for NO, got {n_no}'
        assert n_yes == 1, f'expected 1 row inserted for YES, got {n_yes}'

        with conn.cursor() as cur:
            cur.execute("""
                SELECT side, no_ask_cents
                FROM weather_position_exits
                WHERE contract_ticker = %s
                ORDER BY side
            """, (FAKE_TICKER,))
            rows = cur.fetchall()

        assert len(rows) == 2, (
            f'expected 2 distinct rows (one NO, one YES) for the same '
            f'contract_ticker, got {len(rows)} -- the exact collision bug '
            f'this test exists to catch'
        )
        by_side = {r['side']: float(r['no_ask_cents']) for r in rows}
        assert by_side == {'NO': 80.0, 'YES': 20.0}, (
            f'row data does not match what was inserted per side: {by_side}'
        )

        print('PASS: NO and YES rows on the same contract_ticker coexist '
              'as two distinct rows, correctly attributed by side.')
        print(f'      rows found: {rows}')
        return 0
    except AssertionError as e:
        print(f'FAIL: {e}')
        return 1
    finally:
        conn.rollback()  # ALWAYS rollback -- nothing from this test is ever committed
        conn.close()


if __name__ == '__main__':
    sys.exit(main())
