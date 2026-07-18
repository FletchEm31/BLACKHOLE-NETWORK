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

from cp4_kelly_sizer import _is_settled, _settlement_dt

logger = logging.getLogger('bhn.trading.exit_audit')

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


# ---------------------------------------------------------------------------
# Part 1 — Signal capture (called by orchestrator at decision time)
# ---------------------------------------------------------------------------

# entry_edge_cents / entry_model_prob_no_cents / entry_predicted_tmax_f /
# entry_hours_to_settle / entry_sigma_used / entry_hours_to_avg_dailyhigh
# (added 2026-07-17/2026-07-17b/d/e): frozen at the same first-qualification
# moment as entry_no_ask_cents/entry_captured_at -- deliberately absent from
# the ON CONFLICT DO UPDATE SET below, same as those two.
#
# entry_hours_to_avg_dailyhigh specifically: computed by
# _entry_hours_to_avg_dailyhigh() below via a join against
# weather_station_climatology (station-level climatology, not duplicated
# per-row) -- see sql/migrations/2026-07-17e-... for the full definition
# and sign convention. NULL when no climatology row exists for that
# station/month (e.g. a station not yet populated by
# build_station_climatology_2026_07_17.py) -- never guessed at.
# edge_cents/model_prob_no_cents/predicted_tmax_f/hours_to_settle/sigma_used
# are NOT frozen (see UPDATE SET) and drift every cycle a signal keeps
# re-qualifying -- confirmed via same-night backtests that this drift is
# large enough to fabricate false "high edge", "high confidence", and
# "far outside the bucket" patterns out of trades that were unremarkable at
# entry and only look extreme after the market/forecast moved near
# settlement. sigma_used specifically also mechanically decays toward
# settlement via calculate_time_decayed_sigma() (sqrt(hours_remaining/24)),
# so a live-refreshed sigma_used reflects sigma near exit, not the
# uncertainty actually priced in at entry. Do not use the un-prefixed
# columns for any backtest or entry-time analysis -- use the entry_*
# versions. The un-prefixed columns remain live-refreshed by design, for
# "current state of an open position" monitoring -- not removed.
_RECORD_SQL = """
    INSERT INTO weather_position_exits (
        station_code, target_date, contract_ticker, real_market_ticker,
        bucket_label, bucket_floor, bucket_cap, side, decision_timestamp,
        predicted_tmax_f, model_prob_no_cents, no_ask_cents,
        edge_cents, contracts_recommended, stake_usd_recommended,
        hours_to_settle, sigma_used, is_paper_trade,
        entry_no_ask_cents, entry_captured_at,
        entry_edge_cents, entry_model_prob_no_cents,
        entry_predicted_tmax_f, entry_hours_to_settle, entry_sigma_used,
        entry_hours_to_avg_dailyhigh, fee_usd
    ) VALUES (
        %(station_code)s, %(target_date)s, %(contract_ticker)s, %(real_market_ticker)s,
        %(bucket_label)s, %(bucket_floor)s, %(bucket_cap)s, %(side)s, %(decision_timestamp)s,
        %(predicted_tmax_f)s, %(model_prob_no_cents)s, %(no_ask_cents)s,
        %(edge_cents)s, %(contracts_recommended)s, %(stake_usd_recommended)s,
        %(hours_to_settle)s, %(sigma_used)s, %(is_paper_trade)s,
        %(no_ask_cents)s, %(decision_timestamp)s,
        %(edge_cents)s, %(model_prob_no_cents)s,
        %(predicted_tmax_f)s, %(entry_hours_to_settle)s, %(sigma_used)s,
        %(entry_hours_to_avg_dailyhigh)s, %(fee_usd)s
    )
    ON CONFLICT (contract_ticker, side) DO UPDATE SET
        decision_timestamp    = EXCLUDED.decision_timestamp,
        real_market_ticker    = EXCLUDED.real_market_ticker,
        predicted_tmax_f      = EXCLUDED.predicted_tmax_f,
        model_prob_no_cents   = EXCLUDED.model_prob_no_cents,
        no_ask_cents          = EXCLUDED.no_ask_cents,
        edge_cents            = EXCLUDED.edge_cents,
        contracts_recommended = EXCLUDED.contracts_recommended,
        stake_usd_recommended = EXCLUDED.stake_usd_recommended,
        hours_to_settle       = EXCLUDED.hours_to_settle,
        sigma_used            = EXCLUDED.sigma_used,
        fee_usd               = EXCLUDED.fee_usd
    WHERE weather_position_exits.scored_at IS NULL
"""


def _entry_hours_to_avg_dailyhigh(conn, station_code: str, target_date: date,
                                  now_utc: datetime) -> Optional[float]:
    """Hours between now_utc and that station/month's climatological
    average-daily-high UTC instant on target_date. Positive = entry before
    the climatological peak (more uncertainty remaining); negative = after.

    Returns None (not 0.0 or a guess) if weather_station_climatology has no
    row for (station_code, month(target_date)) -- e.g. a station not yet
    populated by build_station_climatology_2026_07_17.py."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT average_dailyhigh_time_utc
            FROM weather_station_climatology
            WHERE station_code = %s AND month = %s
        """, (station_code, target_date.month))
        row = cur.fetchone()
    if row is None or row['average_dailyhigh_time_utc'] is None:
        return None
    peak_time = row['average_dailyhigh_time_utc']
    # average_dailyhigh_time_utc is a fixed clock time (LST-derived, never
    # DST-adjusted -- see migration file header), so combining it directly
    # with target_date gives the correct UTC instant. Peak hours for all
    # currently-populated stations fall in the 17-22 UTC range, same
    # calendar date as the local trading day, so no cross-midnight
    # adjustment is needed here.
    peak_instant = datetime.combine(target_date, peak_time, tzinfo=timezone.utc)
    return round((peak_instant - now_utc).total_seconds() / 3600.0, 3)


def record_paper_trade(conn, station_code: str, target_date: date,
                       predicted_tmax_f: float, buckets: list[dict],
                       is_paper_trade: bool = True, side: str = 'NO') -> int:
    """
    Upsert one row per qualifying bucket into weather_position_exits.

    Uses the caller's open connection — does not commit or close it.
    ON CONFLICT (contract_ticker, side) DO UPDATE refreshes signal cols each
    cycle so the row reflects the latest model view. The WHERE scored_at IS
    NULL guard prevents overwriting rows the exit scorer has already settled.

    side defaults to 'NO' since CP4 remains NO-side-only ("Tail-No") today —
    added 2026-07-18 as Yes-side trading infrastructure Phase 1 (schema +
    data-model only, no YES trigger/decision logic built yet). One side per
    call, same as station_code/target_date/predicted_tmax_f — a future
    YES-side qualification pass would be a separate call with side='YES',
    not mixed buckets within one call. Deliberately NOT in the ON CONFLICT
    DO UPDATE SET (same frozen-at-entry posture as the entry_* columns).
    weather_position_exits' UNIQUE constraint is on (contract_ticker, side)
    as of migration 2026-07-18b, so a NO and a YES bet on the literal same
    bucket contract now coexist as two distinct rows instead of colliding —
    see test_side_collision_2026_07_18.py for the proof.

    Returns number of rows inserted or updated.
    """
    now_utc    = datetime.now(timezone.utc)
    qualifying = [b for b in buckets if b.get('qualifies')]
    if not qualifying:
        return 0

    # Computed directly from _settlement_dt() (the same function
    # cp4_kelly_sizer.py uses for the live, drifting hours_to_settle) rather
    # than read from the bucket dict -- this is the frozen entry-time lead
    # time, exact by construction, not an approximation.
    entry_hours_to_settle = round(
        max((_settlement_dt(station_code, target_date) - now_utc).total_seconds() / 3600.0, 0.0), 2
    )

    # Same station/target_date for every bucket in this call -- one lookup,
    # not per-bucket. None if weather_station_climatology has no row yet
    # for this station/month (never guessed at -- see helper docstring).
    entry_hours_to_avg_dailyhigh = _entry_hours_to_avg_dailyhigh(
        conn, station_code, target_date, now_utc
    )

    inserted = 0
    with conn.cursor() as cur:
        for b in qualifying:
            # market_ticker is the real Kalshi ticker looked up in CP4 from the
            # snapshot table — never constructed synthetically. See TICKET-W1 /
            # WEATHERBHN-TICKER-ARCHITECTURE.md.
            real_ticker = b['market_ticker']
            cur.execute(_RECORD_SQL, {
                'station_code':          station_code,
                'target_date':           target_date,
                'contract_ticker':       real_ticker,
                'real_market_ticker':    real_ticker,
                'bucket_label':          b['bucket_label'],
                'bucket_floor':          b.get('bucket_floor'),
                'bucket_cap':            b.get('bucket_cap'),
                'side':                  side,
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
                'entry_hours_to_settle': entry_hours_to_settle,
                'entry_hours_to_avg_dailyhigh': entry_hours_to_avg_dailyhigh,
                'fee_usd':               b.get('fee_usd', 0.0),
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

    FIXED 2026-07-07: every boundary is inclusive on the bucket-wins side --
    confirmed against Kalshi's own rules_primary text ("...is between 90-91,
    then resolves Yes" -- both ends included) and independently confirmed
    against a real settled market record (KXHIGHNY-26JUN10-B81.5,
    settlement_temp_f=82.0 exactly equal to cap_strike=82, result=yes).
    Previously used >= cap / <= floor for the NO_WIN (bucket-loses) side,
    which silently excluded the exact boundary value from the bucket --
    wrong on every shape (between/T-low/T-high), not just the between-
    bucket cap. Confirmed real rows misclassified this way: 422, 523,
    709, 1495, 2397 (all actual_tmax_f == bucket_cap, recorded NO_WIN,
    truly NO_LOSS).

    Standard bucket (floor AND cap set), e.g. "90-91":
      NO_WIN  if actual < floor OR actual > cap   (tmax outside [floor, cap])
      NO_LOSS if floor <= actual <= cap           (tmax inside; YES won)

    T-low threshold (floor=None, cap=threshold, e.g. T65 '<=65°F'):
      YES wins if actual <= cap (temp at or below threshold)
      NO_WIN  if actual > cap
      NO_LOSS if actual <= cap

    T-high threshold (floor=threshold, cap=None, e.g. T95 '>=95°F'):
      YES wins if actual >= floor (temp at or above threshold)
      NO_WIN  if actual < floor
      NO_LOSS if actual >= floor
    """
    below_floor = bucket_floor is not None and actual_tmax_f < bucket_floor
    above_cap   = bucket_cap   is not None and actual_tmax_f > bucket_cap
    return 'NO_WIN' if (below_floor or above_cap) else 'NO_LOSS'


def score_settled_positions(dry_run: bool = False,
                            target_date_override: Optional[date] = None) -> dict:
    """
    Score unscored rows in weather_position_exits against NWS CLI actuals.

    1. Query rows WHERE target_date < CURRENT_DATE AND scored_at IS NULL
       AND side = 'NO'.
    2. For each row, look up final_tmax_f from weather_silver_actuals_conformed
       WHERE actual_source = 'nws_cli' AND is_final = TRUE.
       If no final actual yet: skip (will be retried on next run).
    3. Determine NO_WIN / NO_LOSS.
    4. Compute realized P&L (fee-adjusted, fixed 2026-07-17 -- see
       cp4_kelly_sizer.py's _maker_fee() docstring for the full bug writeup):
         NO_WIN:  contracts * (1.00 - entry_ask_c/100) - fee_usd
         NO_LOSS: contracts * (-entry_ask_c/100) - fee_usd
       fee_usd is charged at entry regardless of outcome, so it's subtracted
       in both branches, not just the loss side. Rows predating this fix
       have fee_usd = NULL -- COALESCE'd to 0.0 (no fee subtracted) rather
       than guessed at; those 101 rows were backfilled separately via
       sql/migrations/2026-07-17-backfill-position-exit-fees.sql, which
       computes and stores their fee_usd AND writes the fee-adjusted total
       to corrected_realized_pnl_usd (this function's own UPDATE below only
       ever touches realized_pnl_usd, so it can't retroactively fix rows it
       already scored without fee_usd -- the backfill migration is what
       makes weather_position_exits_clean.final_realized_pnl_usd correct for
       those rows via the corrected_realized_pnl_usd COALESCE).
    5. UPDATE row (skipped when dry_run=True).
    6. Log per-station summary and return summary dict.

    side = 'NO' filter added 2026-07-18b: the outcome/P&L logic above (step
    3-4) is hardcoded NO-side math -- a YES row would be scored with the
    wrong win condition and the wrong payout formula, not just a different
    label. This filter is a safety net (CP4 can't produce YES rows yet, so
    it's currently a no-op), not the real fix -- score_settled_positions()
    needs side-branched outcome/P&L logic before Phase 2 ever settles a real
    YES trade. Tracked, not built here (explicit scope: schema + data-model
    only this pass).
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            if target_date_override:
                cur.execute("""
                    SELECT id, station_code, target_date, contract_ticker,
                           bucket_label, bucket_floor, bucket_cap,
                           no_ask_cents, entry_no_ask_cents, contracts_recommended,
                           stake_usd_recommended, fee_usd
                    FROM weather_position_exits
                    WHERE target_date = %s
                      AND scored_at IS NULL
                      AND side = 'NO'
                    ORDER BY target_date, station_code
                """, (target_date_override,))
            else:
                cur.execute("""
                    SELECT id, station_code, target_date, contract_ticker,
                           bucket_label, bucket_floor, bucket_cap,
                           no_ask_cents, entry_no_ask_cents, contracts_recommended,
                           stake_usd_recommended, fee_usd
                    FROM weather_position_exits
                    WHERE scored_at IS NULL
                      AND side = 'NO'
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
            contracts   = int(row['contracts_recommended'])

            # P&L must be sized off entry_no_ask_cents (the price locked in at
            # first signal capture), not no_ask_cents (refreshed live every
            # orchestrator cycle via ON CONFLICT DO UPDATE — see _RECORD_SQL).
            # Identical bug to the one fixed in cp4_kelly_sizer.py's
            # _get_open_entry_prices() on 2026-07-03: using the live price
            # matches whatever the last refresh happened to see, not what was
            # actually paid to enter. Falls back to no_ask_cents only for rows
            # predating migration 003 (entry_no_ask_cents was never backfilled
            # for rows already scored at that time).
            if row['entry_no_ask_cents'] is not None:
                entry_ask_c = float(row['entry_no_ask_cents'])
            else:
                entry_ask_c = float(row['no_ask_cents'])
                logger.warning(
                    '%s: entry_no_ask_cents is NULL — falling back to live '
                    'no_ask_cents for P&L (pre-migration-003 row)',
                    row['contract_ticker'],
                )

            # fee_usd NULL for rows predating the 2026-07-17 fee fix -- those
            # were backfilled separately (see this function's docstring);
            # COALESCE to 0.0 here rather than guessing at a value for any
            # row this SELECT might somehow encounter without one.
            fee_usd = float(row['fee_usd']) if row['fee_usd'] is not None else 0.0

            outcome = _determine_outcome(actual_tmax, floor_val, cap_val)
            pnl = round(
                (contracts * (1.00 - entry_ask_c / 100.0) if outcome == 'NO_WIN'
                 else contracts * (-entry_ask_c / 100.0)) - fee_usd,
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
                        SET actual_tmax_f                     = %s,
                            actual_outcome                     = %s,
                            realized_pnl_usd                   = %s,
                            corrected_realized_pnl_usd         = %s,
                            corrected_contracts_recommended    = %s,
                            corrected_stake_usd_recommended    = %s,
                            scored_at                          = NOW()
                        WHERE id = %s
                    """, (actual_tmax, outcome, pnl, pnl,
                          row['contracts_recommended'], row['stake_usd_recommended'],
                          row['id']))
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
