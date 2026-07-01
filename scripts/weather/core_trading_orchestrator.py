#!/usr/bin/env python3
"""
core_trading_orchestrator.py — BHN WeatherBHN signal generator.

Runs CP1 → CP2 → CP3 → CP4 → ledger for each active (station, target_date).
Designed to run every 5 minutes via bhn-weather-orchestrator.timer.

Replaces weather_edge_calculator.py (v0_passthrough, retired 2026-06-30).

Active stations : KDEN, KLAX, KMIA (daily HIGH tmax only)
Active hours    : 06:00–23:59 local per station (no-ops overnight)
DRY_RUN env     : writes to ledger, never places real Kalshi orders
enabled env     : must be 'true' to write ledger (DRY_RUN takes precedence)

Environment (from /etc/bhn-trading/env + strat9.env):
    DATABASE_URL     postgresql connection string
    DRY_RUN          'true' = write ledger only (default); 'false' = live
    KALSHI_BANKROLL  USD bankroll for Kelly sizing (default 1000)
"""

import logging
import os
import sys
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras

sys.path.insert(0, '/opt/bhn/trading')
from cp1_data_sanity import check_data_sanity
from cp2_arb_check import check_structural_arb
from cp3_inference import run_cp3_inference
from cp4_kelly_sizer import write_to_ledger, _is_settled, run_cp4_kelly
from exit_audit_logger import record_paper_trade

logger = logging.getLogger('bhn.trading.weather_orchestrator')

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ACTIVE_STATIONS = ['KDEN', 'KLAX', 'KMIA']

STATION_TZ = {
    'KDEN': ZoneInfo('America/Denver'),
    'KLAX': ZoneInfo('America/Los_Angeles'),
    'KMIA': ZoneInfo('America/New_York'),
}

# Active market hours: 06:00 (open) to 00:00 (midnight, exclusive) local
ACTIVE_HOUR_START = 6
ACTIVE_HOUR_END   = 24

BANKROLL_USD = float(os.environ.get('KALSHI_BANKROLL', '1000'))

# DRY_RUN: writes to ledger but never calls Kalshi order API.
# Controlled by env — strat9.env sets DRY_RUN=true globally.
DRY_RUN = os.environ.get('DRY_RUN', 'true').lower() not in ('false', '0', 'no')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_conn():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        # Fall back to PG_* vars (BHN standard env format in /etc/bhn-trading/env)
        host = os.environ.get('PG_HOST')
        port = os.environ.get('PG_PORT', '5432')
        db   = os.environ.get('PG_DB')
        user = os.environ.get('PG_USER')
        pwd  = os.environ.get('PG_PASSWORD', '')
        if host and db and user:
            import urllib.parse
            db_url = f'postgresql://{urllib.parse.quote(user)}:{urllib.parse.quote(pwd)}@{host}:{port}/{db}'
        else:
            sys.exit('ERROR: Neither DATABASE_URL nor PG_HOST/PG_DB/PG_USER are set')
    return psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor)


def _is_market_hours(station_code: str, now_utc: datetime) -> bool:
    tz = STATION_TZ.get(station_code)
    if tz is None:
        return True
    local_now = now_utc.astimezone(tz)
    return ACTIVE_HOUR_START <= local_now.hour < ACTIVE_HOUR_END


def _get_active_targets(conn) -> list[tuple[str, date]]:
    """
    Return (station_code, target_date) pairs with live Kalshi snapshots
    in the last 2 hours, restricted to ACTIVE_STATIONS.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT station_code, target_date
            FROM weather_bronze_kalshi_market_snapshots
            WHERE retrieved_at >= NOW() - INTERVAL '2 hours'
              AND station_code = ANY(%s)
            ORDER BY station_code, target_date
        """, (ACTIVE_STATIONS,))
        return [(r['station_code'], r['target_date']) for r in cur.fetchall()]


def _first_bucket(conn, station_code: str, target_date: date) -> str | None:
    """First available bucket_label for the most recent snapshot — used as CP1 gate."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT bucket_label
            FROM weather_bronze_kalshi_market_snapshots
            WHERE station_code = %s
              AND target_date   = %s
              AND retrieved_at  = (
                  SELECT MAX(retrieved_at)
                  FROM weather_bronze_kalshi_market_snapshots
                  WHERE station_code = %s AND target_date = %s
              )
              AND yes_bid > 0
              AND no_ask  > 0
              AND no_ask  < 1
            ORDER BY bucket_floor NULLS LAST
            LIMIT 1
        """, (station_code, target_date, station_code, target_date))
        row = cur.fetchone()
    return row['bucket_label'] if row else None


# ---------------------------------------------------------------------------
# Per-station processing
# ---------------------------------------------------------------------------

def _process_station_date(conn, station_code: str, target_date: date,
                           now_utc: datetime) -> str:
    """
    Run CP1→CP2→CP3→CP4 for one (station, target_date).
    Returns a one-line summary for the cycle log.
    """
    # Settled contracts: no signal generated, no ledger write
    if _is_settled(station_code, target_date):
        return f'{station_code} {target_date}: SETTLED — skip'

    # Market hours gate: outside 06:00–midnight local = no-op cycle
    if not _is_market_hours(station_code, now_utc):
        return f'{station_code} {target_date}: outside market hours — skip'

    # --- CP1: data sanity ---
    first_bkt = _first_bucket(conn, station_code, target_date)
    if first_bkt is None:
        return f'{station_code} {target_date}: CP1=FAIL no active Kalshi snapshots'

    cp1 = check_data_sanity(station_code, target_date, first_bkt, conn)
    if not cp1['pass']:
        return f'{station_code} {target_date}: CP1=FAIL {cp1["reason"]}'

    # --- CP2: structural arb check (log only, never blocks) ---
    cp2 = check_structural_arb(station_code, target_date, conn)
    cp2_label = 'ARB_FOUND' if cp2['arb_found'] else 'no_arb'
    if cp2['arb_found']:
        arb_opps = [
            f"{o['bucket_label']}({o['arb_gap_cents']:+.1f}¢)"
            for o in cp2['opportunities']
            if o['arb_type'] == 'buy_both'
        ]
        logger.warning('CP2 ARB detected — %s %s: buy_both %s',
                       station_code, target_date, ', '.join(arb_opps))

    # --- CP3: XGBoost inference ---
    cp3 = run_cp3_inference(station_code, target_date, conn)
    if not cp3['pass'] or cp3['predicted_tmax_f'] is None:
        return (f'{station_code} {target_date}: '
                f'CP1=PASS CP2={cp2_label} CP3=FAIL({cp3["reason"]}) — skip')

    predicted_f = cp3['predicted_tmax_f']
    mode_tag    = 'xgb' if cp3['mode'] == 'xgboost' else 'fallback'
    cp3_label   = f'{predicted_f:.1f}F({mode_tag})'

    # --- CP4: Kelly sizing + paper trade capture + ledger write ---
    # Run Kelly once; pass pre-computed buckets to both functions to avoid
    # a second DB round-trip inside write_to_ledger.
    buckets = run_cp4_kelly(station_code, target_date, predicted_f,
                            cp3['model_rmse'], BANKROLL_USD)

    # Record qualifying signals as paper trades (always, even in DRY_RUN).
    # is_paper_trade=True when DRY_RUN=True so paper vs live stays distinct.
    paper_n = record_paper_trade(conn, station_code, target_date, predicted_f,
                                 buckets, is_paper_trade=DRY_RUN)
    if paper_n:
        conn.commit()

    result = write_to_ledger(
        conn=conn,
        station_code=station_code,
        target_date=target_date,
        predicted_tmax_f=predicted_f,
        nws_tmax_f=cp3.get('nws_forecast_f'),
        om_tmax_f=cp3.get('om_tmax_f'),
        model_rmse=cp3['model_rmse'],
        bankroll_usd=BANKROLL_USD,
        dry_run=DRY_RUN,
        _precomputed_buckets=buckets,
    )

    dry_tag   = '(dry)' if DRY_RUN else ''
    paper_tag = f' paper={paper_n}' if paper_n else ''

    pre_open_n = sum(1 for b in buckets if b.get('pre_open'))
    cp4_label  = (f"{result['bet_no']}_BET_NO/"
                  f"{result.get('skipped', 0)}_SKIP{paper_tag}{dry_tag}")
    if pre_open_n:
        cp4_label += f' [{pre_open_n}_PRE_OPEN]'

    # Log real Kalshi tickers for each qualifying signal
    for b in buckets:
        if b.get('qualifies'):
            logger.info('  BET_NO %s  edge=%.1f¢  stake=$%.2f',
                        b['market_ticker'], b['edge_cents'], b['stake_usd'])

    return (f'{station_code} {target_date}: '
            f'CP1=PASS CP2={cp2_label} CP3={cp3_label} CP4={cp4_label}')


# ---------------------------------------------------------------------------
# Cycle entry point
# ---------------------------------------------------------------------------

def run_cycle():
    conn     = _get_conn()
    now_utc  = datetime.now(timezone.utc)
    dry_pfx  = '[DRY RUN] ' if DRY_RUN else ''
    ts       = now_utc.strftime('%Y-%m-%d %H:%M UTC')

    logger.info('=== %sorchestrator cycle start %s ===', dry_pfx, ts)

    try:
        targets = _get_active_targets(conn)

        if not targets:
            logger.info('No active Kalshi targets in last 2h — cycle complete')
            return

        for station_code, target_date in targets:
            summary = _process_station_date(conn, station_code, target_date, now_utc)
            logger.info(summary)

    except Exception:
        logger.exception('Orchestrator cycle failed')
        raise
    finally:
        conn.close()

    logger.info('=== %sorchestrator cycle complete ===', dry_pfx)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import argparse

    p = argparse.ArgumentParser(description='BHN WeatherBHN trading orchestrator')
    p.add_argument('--dry-run', action='store_true',
                   help='Skip ledger writes (overrides DRY_RUN env)')
    p.add_argument('--station', choices=ACTIVE_STATIONS,
                   help='Process a single station (default: all active)')
    p.add_argument('--date', type=date.fromisoformat,
                   help='Target date override (skips active-snapshot query)')
    args = p.parse_args()

    if args.dry_run:
        os.environ['DRY_RUN'] = 'true'
        DRY_RUN = True

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)sZ %(levelname)s %(message)s',
        datefmt='%Y-%m-%dT%H:%M:%S',
    )

    # Single station/date override (for manual testing)
    if args.station and args.date:
        conn    = _get_conn()
        now_utc = datetime.now(timezone.utc)
        result  = _process_station_date(conn, args.station, args.date, now_utc)
        conn.close()
        print(result)
    else:
        run_cycle()
