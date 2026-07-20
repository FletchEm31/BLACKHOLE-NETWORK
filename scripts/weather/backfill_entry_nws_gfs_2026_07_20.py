#!/usr/bin/env python3
"""
One-time backfill: entry_nws_maxt_f / entry_gfs_maxt_f (/ entry_model_maxt_f
where applicable) for weather_position_exits rows where these came back
NULL at first capture -- confirmed transient (2026-07-20 dashboard-table
audit): target_date 2026-07-18 (8/8 rows) and 2026-07-19 (2/3 rows) are
NULL, but 2026-07-17 (0/3) and 2026-07-20 (0/1) are fully populated, so
this is not an ongoing regression -- just a data-availability gap at the
exact moment those specific contracts' first-ever qualifying cycle ran
(forecast data for a target_date 1-2 days out not yet collected).

These entry_* columns are frozen at first INSERT by design (exit_audit_
logger.py's ON CONFLICT DO UPDATE deliberately excludes them) -- they will
NEVER auto-populate no matter how much time passes, hence this backfill.

Re-runs cp3_inference.run_cp3_inference() fresh for each affected
(station_code, target_date) -- now that forecast data has long since
landed for these near-term dates, this returns the same nws_forecast_f/
om_tmax_f the live system would use today. model_maxt_f only backfilled
when cp3 mode == 'xgboost' (mirrors core_trading_orchestrator.py's own
"only a real model prediction when mode=='xgboost'" rule, 2026-07-19).

Idempotent: only updates rows where the target column IS NULL.
"""
import os
import sys

import psycopg2
import psycopg2.extras

sys.path.insert(0, '/opt/bhn/trading')
from cp3_inference import run_cp3_inference  # noqa: E402


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


def main(dry_run: bool = True):
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, station_code, target_date, entry_nws_maxt_f, entry_gfs_maxt_f, entry_model_maxt_f
            FROM weather_position_exits
            WHERE entry_nws_maxt_f IS NULL OR entry_gfs_maxt_f IS NULL
            ORDER BY target_date, station_code
        """)
        rows = cur.fetchall()

    print(f'{len(rows)} rows with at least one NULL entry_nws/gfs_maxt_f')

    cp3_cache = {}
    updated = 0
    for r in rows:
        key = (r['station_code'], r['target_date'])
        if key not in cp3_cache:
            cp3_cache[key] = run_cp3_inference(r['station_code'], r['target_date'], conn)
        cp3 = cp3_cache[key]

        nws_v = cp3.get('nws_forecast_f')
        gfs_v = cp3.get('om_tmax_f')
        model_v = cp3['predicted_tmax_f'] if cp3.get('mode') == 'xgboost' else None

        set_nws = r['entry_nws_maxt_f'] is None and nws_v is not None
        set_gfs = r['entry_gfs_maxt_f'] is None and gfs_v is not None
        set_model = r['entry_model_maxt_f'] is None and model_v is not None

        if not (set_nws or set_gfs or set_model):
            print(f"  id={r['id']} {key} -- no new data available, skipped")
            continue

        print(f"  id={r['id']} {key} -- nws={nws_v if set_nws else '(unchanged)'} "
              f"gfs={gfs_v if set_gfs else '(unchanged)'} "
              f"model={model_v if set_model else '(unchanged)'}")
        updated += 1

        if not dry_run:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE weather_position_exits
                    SET entry_nws_maxt_f = COALESCE(entry_nws_maxt_f, %s),
                        entry_gfs_maxt_f = COALESCE(entry_gfs_maxt_f, %s),
                        entry_model_maxt_f = COALESCE(entry_model_maxt_f, %s)
                    WHERE id = %s
                """, (nws_v, gfs_v, model_v, r['id']))

    if not dry_run:
        conn.commit()
    print(f'{updated} rows {"would be " if dry_run else ""}updated'
          f'{" (dry run -- pass --apply to write)" if dry_run else ""}')
    conn.close()


if __name__ == '__main__':
    main(dry_run='--apply' not in sys.argv)
