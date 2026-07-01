#!/usr/bin/env python3
"""
exit_audit_logger.py — BHN WeatherBHN paper trade recorder + exit scorer.

Two responsibilities:
  record_paper_trade()      — called by core_trading_orchestrator at signal time;
                              inserts one row per qualifying BET_NO bucket into
                              weather_position_exits.
  score_settled_positions() — runs daily at 00:30 UTC (bhn-exit-audit.timer);
                              scores unscored rows against NWS CLI actuals from
                              weather_silver_actuals_conformed.

Scope: KDEN, KLAX, KMIA only.
Strategy: NO-side only ("Tail-No").
Ground truth: NWS CLI actuals (actual_source='nws_cli') — Kalshi settles on this.
"""

import argparse
import logging
import os
import sys
from datetime import date, datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras

from cp4_kelly_sizer import _is_settled

logger = logging.getLogger('bhn.trading.exit_audit')

STATION_TO_MARKET = {'KDEN': 'KXHIGHDEN', 'KLAX': 'KXHIGHLAX', 'KMIA': 'KXHIGHMIA'}


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


def _build_ticker(station_code: str, target_date: date, bucket_label: str) -> str:
    market   = STATION_TO_MARKET[station_code]
    date_str = target_date.strftime('%y%b%d').upper()
    return f'{market}-{date_str}-{bucket_label}'


# ---------------------------------------------------------------------------
# Part 1 — Signal capture (called by orchestrator at decision time)
# ---------------------------------------------------------------------------

_RECORD_SQL = """
    INSERT INTO weather_position_exits (
        station_code, target_date, contract_ticker, bucket_label,
        bucket_floor, bucket_cap, decision_timestamp,
        predicted_tmax_f, model_prob_no_cents, no_ask_cents,
        edge_cents, contracts_recommended, stake_usd_recommended,
        hours_to_settle, sigma_used, is_paper_trade
    ) VALUES (
        %(station_code)s, %(target_date)s, %(contract_ticker)s, %(bucket_label)s,
        %(bucket_floor)s, %(bucket_cap)s, %(decision_timestamp)s,
        %(predicted_tmax_f)s, %(model_prob_no_cents)s, %(no_ask_cents)s,
        %(edge_cents)s, %(contracts_recommended)s, %(stake_usd_recommended)s,
        %(hours_to_settle)s, %(sigma_used)s, %(is_paper_trade)s
    )
    ON CONFLICT (contract_ticker) DO UPDATE SET
        decision_timestamp    = EXCLUDED.decision_timestamp,
        predicted_tmax_f      = EXCLUDED.predicted_tmax_f,
        model_prob_no_cents   = EXCLUDED.model_prob_no_cents,
        no_ask_cents          = EXCLUDED.no_ask_cents,
        edge_cents            = EXCLUDED.edge_cents,
        contracts_recommended = EXCLUDED.contracts_recommended,
        stake_usd_recommended = EXCLUDED.stake_usd_recommended,
        hours_to_settle       = EXCLUDED.hours_to_settle,
        sigma_used            = EXCLUDED.sigma_used
    WHERE weather_position_exits.scored_at IS NULL
"""


def record_paper_trade(conn, station_code: str, target_date: date,
                       predicted_tmax_f: float, buckets: list[dict],
                       is_paper_trade: bool = True) -> int:
    """
    Upsert one row per qualifying BET_NO bucket into weather_position_exits.

    Uses the caller's open connection — does not commit or close it.
    ON CONFLICT (contract_ticker) DO UPDATE refreshes signal cols each cycle
    so the row reflects the latest model view. The WHERE scored_at IS NULL
    guard prevents overwriting rows the exit scorer has already settled.

    Returns number of rows inserted or updated.
    """
    now_utc    = datetime.now(timezone.utc)
    qualifying = [b for b in buckets if b.get('qualifies')]
    if not qualifying:
        return 0

    inserted = 0
    with conn.cursor() as cur:
        for b in qualifying:
            cur.execute(_RECORD_SQL, {
                'station_code':          station_code,
                'target_date':           target_date,
                'contract_ticker':       _build_ticker(station_code, target_date,
                                                       b['bucket_label']),
                'bucket_label':          b['bucket_label'],
                'bucket_floor':          b.get('bucket_floor'),
                'bucket_cap':            b.get('bucket_cap'),
                'decision_timestamp':    now_utc,
                'predicted_tmax_f':      predicted_tmax_f,
                'model_prob_no_cents':   b['model_prob_cents'],
                'no_ask_cents':          b['no_ask_cents'],
                'edge_cents':            b['edge_cents'],
                'contracts_recommended': b['contracts'],
                'stake_usd_recommended': b['stake_usd'],
                'hours_to_settle':       b.get('hours_to_settle'),
                'sigma_used':            b.get('sigma_used'),
                'is_paper_trade':        is_paper_trade,
            })
            inserted += cur.rowcount
    return inserted


# ---------------------------------------------------------------------------
# Part 2 — Exit scorer (runs daily at 00:30 UTC)
# ---------------------------------------------------------------------------

def _determine_outcome(actual_tmax_f: float,
                       bucket_floor: Optional[float],
                       bucket_cap: Optional[float]) -> str:
    """
    Determine NO-side outcome for a settled contract.

    Standard bucket (floor AND cap set):
      NO_WIN  if actual < floor OR actual >= cap  (tmax outside bucket)
      NO_LOSS if floor <= actual < cap            (tmax inside bucket; YES won)

    T-low threshold (floor=None, cap=threshold, e.g. T65 '<=65°F'):
      YES wins if actual < cap (temp stayed below)
      NO_WIN  if actual >= cap
      NO_LOSS if actual < cap

    T-high threshold (floor=threshold, cap=None, e.g. T95 '>=95°F'):
      YES wins if actual > floor (temp hit threshold)
      NO_WIN  if actual <= floor
      NO_LOSS if actual > floor
    """
    if bucket_floor is not None and bucket_cap is not None:
        return ('NO_WIN' if (actual_tmax_f < bucket_floor or actual_tmax_f >= bucket_cap)
                else 'NO_LOSS')
    elif bucket_cap is not None:
        return 'NO_WIN' if actual_tmax_f >= bucket_cap else 'NO_LOSS'
    else:
        return 'NO_WIN' if actual_tmax_f <= bucket_floor else 'NO_LOSS'


def score_settled_positions(dry_run: bool = False,
                            target_date_override: Optional[date] = None) -> dict:
    """
    Score unscored rows in weather_position_exits against NWS CLI actuals.

    1. Query rows WHERE target_date < CURRENT_DATE AND scored_at IS NULL.
    2. For each row, look up final_tmax_f from weather_silver_actuals_conformed
       WHERE actual_source = 'nws_cli' AND is_final = TRUE.
       If no final actual yet: skip (will be retried on next run).
    3. Determine NO_WIN / NO_LOSS.
    4. Compute realized P&L:
         NO_WIN:  contracts * (1.00 - no_ask_cents/100)
         NO_LOSS: contracts * (-no_ask_cents/100)
    5. UPDATE row (skipped when dry_run=True).
    6. Log per-station summary and return summary dict.
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            if target_date_override:
                cur.execute("""
                    SELECT id, station_code, target_date, contract_ticker,
                           bucket_label, bucket_floor, bucket_cap,
                           no_ask_cents, contracts_recommended
                    FROM weather_position_exits
                    WHERE target_date = %s
                      AND scored_at IS NULL
                    ORDER BY target_date, station_code
                """, (target_date_override,))
            else:
                cur.execute("""
                    SELECT id, station_code, target_date, contract_ticker,
                           bucket_label, bucket_floor, bucket_cap,
                           no_ask_cents, contracts_recommended
                    FROM weather_position_exits
                    WHERE scored_at IS NULL
                    ORDER BY target_date, station_code
                """)
            # Filter in Python using _is_settled() so KDEN/KMIA same-day settlements
            # (e.g. KMIA settles 20:00 UTC, KDEN 22:00 UTC) are scored the same evening
            # rather than waiting until the date rolls over.
            all_rows = cur.fetchall()

        rows = [r for r in all_rows
                if target_date_override or _is_settled(r['station_code'], r['target_date'])]

        if not rows:
            logger.info('No unscored positions to process')
            return {'scored': 0, 'wins': 0, 'losses': 0,
                    'total_pnl_usd': 0.0, 'skipped_no_actual': 0}

        scored = wins = losses = skipped = 0
        total_pnl = 0.0
        station_summary: dict = {}

        for row in rows:
            station = row['station_code']
            tdate   = row['target_date']

            with conn.cursor() as cur:
                cur.execute("""
                    SELECT final_tmax_f
                    FROM weather_silver_actuals_conformed
                    WHERE station_code  = %s
                      AND target_date   = %s
                      AND actual_source = 'nws_cli'
                      AND is_final      = TRUE
                    LIMIT 1
                """, (station, tdate))
                act_row = cur.fetchone()

            if act_row is None or act_row['final_tmax_f'] is None:
                skipped += 1
                logger.debug('%s %s: no final NWS CLI actual yet — deferring', station, tdate)
                continue

            actual_tmax = float(act_row['final_tmax_f'])
            floor_val   = float(row['bucket_floor']) if row['bucket_floor'] is not None else None
            cap_val     = float(row['bucket_cap'])   if row['bucket_cap']   is not None else None
            no_ask_c    = float(row['no_ask_cents'])
            contracts   = int(row['contracts_recommended'])

            outcome = _determine_outcome(actual_tmax, floor_val, cap_val)
            pnl = round(
                contracts * (1.00 - no_ask_c / 100.0) if outcome == 'NO_WIN'
                else contracts * (-no_ask_c / 100.0),
                4,
            )

            if outcome == 'NO_WIN':
                wins += 1
            else:
                losses += 1
            total_pnl = round(total_pnl + pnl, 4)
            scored += 1

            st = station_summary.setdefault(station, {'wins': 0, 'losses': 0, 'pnl': 0.0})
            st['wins' if outcome == 'NO_WIN' else 'losses'] += 1
            st['pnl'] = round(st['pnl'] + pnl, 4)

            if dry_run:
                logger.info('[DRY RUN] %s %s %s: actual=%.1f°F → %s  pnl=$%+.4f',
                            station, tdate, row['bucket_label'], actual_tmax, outcome, pnl)
            else:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE weather_position_exits
                        SET actual_tmax_f    = %s,
                            actual_outcome   = %s,
                            realized_pnl_usd = %s,
                            scored_at        = NOW()
                        WHERE id = %s
                    """, (actual_tmax, outcome, pnl, row['id']))
                conn.commit()
                logger.info('%s %s %s: actual=%.1f°F → %s  pnl=$%+.4f',
                            station, tdate, row['bucket_label'], actual_tmax, outcome, pnl)

        for station, st in sorted(station_summary.items()):
            logger.info('%s: %d scored (%d WIN, %d LOSS) | P&L: %+.2f',
                        station,
                        st['wins'] + st['losses'],
                        st['wins'], st['losses'],
                        st['pnl'])

        return {
            'scored':            scored,
            'wins':              wins,
            'losses':            losses,
            'total_pnl_usd':     total_pnl,
            'skipped_no_actual': skipped,
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)sZ %(levelname)s %(message)s',
        datefmt='%Y-%m-%dT%H:%M:%S',
    )

    p = argparse.ArgumentParser(description='BHN WeatherBHN exit audit scorer')
    p.add_argument('--dry-run', action='store_true',
                   help='Print what would be scored without writing to DB')
    p.add_argument('--date', type=date.fromisoformat, dest='target_date',
                   help='Score a specific date manually (YYYY-MM-DD)')
    args = p.parse_args()

    result = score_settled_positions(dry_run=args.dry_run,
                                     target_date_override=args.target_date)
    logger.info('Done: %d scored (%d WIN / %d LOSS)  total P&L: $%+.4f  skipped: %d',
                result['scored'], result['wins'], result['losses'],
                result['total_pnl_usd'], result['skipped_no_actual'])
