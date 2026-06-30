#!/usr/bin/env python3
"""
CP4: Kelly position sizer for Kalshi weather contracts.

Four functions:
  1. calculate_time_decayed_sigma()  — compress sigma as settlement approaches
  2. calculate_bucket_probability()  — Gaussian (center) or Student-t (tails)
  3. run_cp4_kelly()                 — main engine: edge check + half-Kelly sizing
  4. write_to_ledger()               — upsert CP4 results into weather_gold_contract_ledger

Strategy: NO-side only by design ("Tail-No").
Kalshi weather markets systematically overprice extreme temperature buckets.
YES-side extension deferred until ≥60 live ledger entries validate NO-side calibration.

CRITICAL: Always reads no_ask from DB. NEVER derives no_price = 1 - yes_price.

Environment: DATABASE_URL (peer auth: postgresql:///eventhorizon)
"""
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import psycopg2
import psycopg2.extras
from scipy.stats import norm, t as student_t

# Settlement = 4PM local. UTC equivalents:
SETTLEMENT_UTC_HOUR = {
    'KLAX': 0,   # 4PM PDT = 00:00 UTC next day (midnight)
    'KDEN': 22,  # 4PM MDT = 22:00 UTC same day
    'KMIA': 20,  # 4PM EDT = 20:00 UTC same day
}
SIGMA_FLOOR_RATIO   = 0.20   # never compress below 20% of base_sigma
BANKROLL_CAP_PCT    = 0.10   # never stake more than 10% of bankroll on one contract
EDGE_THRESHOLD_LIQ  = 5.0    # cents — liquid (volume > 100)
EDGE_THRESHOLD_ILL  = 8.0    # cents — illiquid (volume <= 100 or unknown)
MIN_NO_ASK_CENTS    = 3.0    # skip buckets with no_ask below this — effectively dead market

CITY_MAP = {'KDEN': 'Denver', 'KLAX': 'Los Angeles', 'KMIA': 'Miami'}
STATION_TO_MARKET = {'KDEN': 'KXHIGHDEN', 'KLAX': 'KXHIGHLAX', 'KMIA': 'KXHIGHMIA'}


def _get_conn():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        host = os.environ.get("PG_HOST")
        port = os.environ.get("PG_PORT", "5432")
        db   = os.environ.get("PG_DB")
        user = os.environ.get("PG_USER")
        pwd  = os.environ.get("PG_PASSWORD", "")
        if host and db and user:
            import urllib.parse
            db_url = f"postgresql://{urllib.parse.quote(user)}:{urllib.parse.quote(pwd)}@{host}:{port}/{db}"
        else:
            sys.exit("ERROR: Neither DATABASE_URL nor PG_HOST/PG_DB/PG_USER are set")
    return psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor)


# ---------------------------------------------------------------------------
# Helpers — settlement filter, ticker builder, label functions
# ---------------------------------------------------------------------------

def _settlement_dt(station_code: str, target_date: date) -> datetime:
    """UTC settlement datetime for a given station/target_date."""
    settle_hour = SETTLEMENT_UTC_HOUR.get(station_code, 20)
    if settle_hour == 0:
        # KLAX: 4PM PDT = midnight UTC start of (target_date + 1 day)
        return (datetime(target_date.year, target_date.month, target_date.day,
                         0, 0, 0, tzinfo=timezone.utc) + timedelta(days=1))
    return datetime(target_date.year, target_date.month, target_date.day,
                    settle_hour, 0, 0, tzinfo=timezone.utc)


def _is_settled(station_code: str, target_date: date) -> bool:
    """True if the contract's settlement time has already passed."""
    return datetime.now(timezone.utc) >= _settlement_dt(station_code, target_date)


def _build_ticker(station_code: str, target_date: date, bucket_label: str) -> str:
    market = STATION_TO_MARKET[station_code]
    date_str = target_date.strftime('%y%b%d').upper()  # e.g. 26JUN29
    return f"{market}-{date_str}-{bucket_label}"


def _model_confidence(delta_f: float) -> str:
    """Confidence in the edge signal based on model vs NWS divergence."""
    abs_delta = abs(delta_f)
    if abs_delta < 1.5:  return 'HIGH'
    if abs_delta < 3.0:  return 'MEDIUM'
    return 'LOW'


def _signal_strength(edge_cents: float) -> str:
    if edge_cents >= 15:  return 'STRONG'
    if edge_cents >= 10:  return 'MODERATE'
    return 'WEAK'


# ---------------------------------------------------------------------------
# Function 1 — Time-decayed sigma
# ---------------------------------------------------------------------------

def calculate_time_decayed_sigma(base_sigma: float, station_code: str,
                                  evaluation_time_utc: datetime,
                                  target_date: Optional[date] = None) -> float:
    """
    sigma_decayed = base_sigma * sqrt(hours_remaining / 24)
    Floor: 20% of base_sigma (never fully collapse uncertainty).

    Must pass target_date to get the correct settlement time for that contract.
    KLAX settles at midnight UTC the day AFTER target_date (4PM PDT).
    KDEN/KMIA settle at 22:00/20:00 UTC on target_date itself.

    Without target_date falls back to the old "next settlement from now"
    logic — only correct for same-day contracts.
    """
    if evaluation_time_utc.tzinfo is None:
        now = evaluation_time_utc.replace(tzinfo=timezone.utc)
    else:
        now = evaluation_time_utc.astimezone(timezone.utc)

    if target_date is not None:
        settle_dt = _settlement_dt(station_code, target_date)
    else:
        # Legacy path: "next settlement from now" — only correct for same-day contracts.
        settle_hour = SETTLEMENT_UTC_HOUR.get(station_code, 20)
        settle_dt = datetime(now.year, now.month, now.day,
                             settle_hour, 0, 0, tzinfo=timezone.utc)
        if settle_dt <= now:
            settle_dt += timedelta(days=1)

    hours_remaining = max((settle_dt - now).total_seconds() / 3600.0, 0.0)
    decay_factor = math.sqrt(min(hours_remaining, 24.0) / 24.0)
    return max(base_sigma * decay_factor, base_sigma * SIGMA_FLOOR_RATIO)


# ---------------------------------------------------------------------------
# Function 2 — Bucket probability
# ---------------------------------------------------------------------------

def calculate_bucket_probability(predicted_tmax_f: float,
                                  sigma: float,
                                  bucket_floor: Optional[float],
                                  bucket_cap: Optional[float],
                                  blended_mean: float) -> tuple[float, str]:
    """
    Returns (prob_yes, distribution_used).
    prob_yes = P(tmax falls in [bucket_floor, bucket_cap]).

    Within 2-sigma of blended_mean → Gaussian CDF.
    Beyond 2-sigma (tail bracket)  → Student-t CDF (df=5, heavier tails).
    """
    eff_floor = bucket_floor if bucket_floor is not None else -9999.0
    eff_cap   = bucket_cap   if bucket_cap   is not None else  9999.0
    sigma     = max(sigma, 0.01)

    bucket_mid = (eff_floor + eff_cap) / 2.0
    sigma_dist = abs(bucket_mid - blended_mean) / sigma

    if sigma_dist > 2.0:
        prob = (student_t.cdf(eff_cap,   df=5, loc=blended_mean, scale=sigma)
              - student_t.cdf(eff_floor, df=5, loc=blended_mean, scale=sigma))
        dist = 'student_t'
    else:
        prob = (norm.cdf(eff_cap,   loc=blended_mean, scale=sigma)
              - norm.cdf(eff_floor, loc=blended_mean, scale=sigma))
        dist = 'gaussian'

    return max(0.0, min(1.0, prob)), dist


# ---------------------------------------------------------------------------
# Function 3 — Main Kelly engine
# ---------------------------------------------------------------------------

def run_cp4_kelly(station_code: str, target_date: date,
                  predicted_tmax_f: float, model_rmse: float,
                  bankroll_usd: float = 1000.0) -> list[dict]:
    """
    For each Kalshi bucket for station/date: compute edge and half-Kelly stake.

    Reads real no_ask and yes_bid from weather_bronze_kalshi_market_snapshots.
    NEVER derives no_price = 1 - yes_price.

    Strategy: NO side.
      model_prob_no_cents = (1 - P(YES)) * 100   [our internal model estimate]
      edge_cents = model_prob_no_cents - no_ask_cents
      kelly_fraction = edge_cents / (100 - no_ask_cents)
      stake = bankroll * half_kelly, capped at 10% of bankroll

    Returns list of dicts — one per bucket, sorted by bucket_floor.
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT bucket_label, bucket_type, bucket_floor, bucket_cap,
                       yes_bid, yes_ask, no_bid, no_ask
                FROM weather_bronze_kalshi_market_snapshots
                WHERE station_code = %s
                  AND target_date = %s
                  AND retrieved_at = (
                      SELECT MAX(retrieved_at)
                      FROM weather_bronze_kalshi_market_snapshots
                      WHERE station_code = %s AND target_date = %s
                  )
                  AND yes_bid  IS NOT NULL
                  AND no_ask   IS NOT NULL
                ORDER BY bucket_floor NULLS LAST
            """, (station_code, target_date, station_code, target_date))
            buckets = cur.fetchall()
    finally:
        conn.close()

    if not buckets:
        return []

    now_utc = datetime.now(timezone.utc)
    sigma = calculate_time_decayed_sigma(model_rmse, station_code, now_utc, target_date)
    settle_dt = _settlement_dt(station_code, target_date)
    hours_to_settle = round(max((settle_dt - now_utc).total_seconds() / 3600.0, 0.0), 2)

    # Determine threshold bucket directions.
    # Kalshi stores both T66 (≤66) and T73 (≥73) with floor=cap=threshold_value.
    # Sort threshold values: the smaller one is the bottom (≤), larger is the top (≥).
    thresh_vals = sorted(
        float(b['bucket_floor'])
        for b in buckets
        if b['bucket_type'] == 'threshold'
        and b['bucket_floor'] is not None
        and b['bucket_cap']   is not None
        and float(b['bucket_floor']) == float(b['bucket_cap'])
    )
    bottom_thresh = thresh_vals[0]  if len(thresh_vals) >= 1 else None
    top_thresh    = thresh_vals[-1] if len(thresh_vals) >= 2 else None

    results = []
    for b in buckets:
        bucket_floor = float(b['bucket_floor']) if b['bucket_floor'] is not None else None
        bucket_cap   = float(b['bucket_cap'])   if b['bucket_cap']   is not None else None

        # Fix threshold buckets: open the correct end so CDF integrates to ±∞.
        # T66 (≤66): floor → -∞  (pass None → eff_floor = -9999)
        # T73 (≥73): cap  → +∞  (pass None → eff_cap  = +9999)
        if b['bucket_type'] == 'threshold' and bucket_floor == bucket_cap:
            val = bucket_floor
            if val == bottom_thresh:
                bucket_floor = None   # integrate from -∞ to val
            elif val == top_thresh:
                bucket_cap = None     # integrate from val to +∞

        yes_bid_dec = float(b['yes_bid'])                                          # decimal 0-1
        yes_ask_dec = float(b['yes_ask']) if b['yes_ask'] is not None else None
        no_bid_dec  = float(b['no_bid'])  if b['no_bid']  is not None else None
        no_ask_dec  = float(b['no_ask'])                                           # NEVER derived

        yes_bid_cents = round(yes_bid_dec * 100, 2)
        yes_ask_cents = round(yes_ask_dec * 100, 2) if yes_ask_dec is not None else None
        no_bid_cents  = round(no_bid_dec  * 100, 2) if no_bid_dec  is not None else None
        no_ask_cents  = round(no_ask_dec  * 100, 2)

        # P(YES): probability tmax lands in this bucket per our model
        prob_yes, dist = calculate_bucket_probability(
            predicted_tmax_f, sigma, bucket_floor, bucket_cap, predicted_tmax_f
        )
        # P(NO): our model's probability this bucket does NOT win
        # (internal model calc — not derived from market price)
        prob_no = 1.0 - prob_yes
        model_prob_no_cents = round(float(prob_no) * 100, 2)

        # Edge on NO trade
        edge_cents = round(model_prob_no_cents - no_ask_cents, 2)

        # Volume data not in snapshot table — conservative illiquid threshold
        edge_threshold = EDGE_THRESHOLD_ILL

        no_ask_thin = no_ask_cents < MIN_NO_ASK_CENTS
        valid_price  = 0 < no_ask_cents < 100
        qualifies    = bool(valid_price and not no_ask_thin and edge_cents >= edge_threshold)

        contracts = 0
        stake_usd = 0.0
        if qualifies:
            win_cents = 100.0 - no_ask_cents  # cents profit per winning NO contract
            if win_cents > 0:
                kelly_fraction = edge_cents / win_cents
                half_kelly = kelly_fraction * 0.5
                # Cap at 10% of bankroll
                max_stake = bankroll_usd * min(half_kelly, BANKROLL_CAP_PCT)
                cost_per_contract = no_ask_cents / 100.0  # dollars
                contracts = max(0, int(max_stake // cost_per_contract))
                stake_usd = round(contracts * cost_per_contract, 2)

        results.append({
            'bucket_label':      b['bucket_label'],
            'bucket_floor':      bucket_floor,
            'bucket_cap':        bucket_cap,
            'yes_bid_cents':     yes_bid_cents,
            'yes_ask_cents':     yes_ask_cents,
            'no_bid_cents':      no_bid_cents,
            'no_ask_cents':      no_ask_cents,
            'model_prob_cents':  model_prob_no_cents,
            'edge_cents':        edge_cents,
            'qualifies':         qualifies,
            'contracts':         contracts,
            'stake_usd':         stake_usd,
            'sigma_used':        round(sigma, 4),
            'distribution_used': dist,
            'hours_to_settle':   hours_to_settle,
            'no_ask_thin':       no_ask_thin,
        })

    return results


# ---------------------------------------------------------------------------
# Function 4 — Ledger write
# ---------------------------------------------------------------------------

_LEDGER_UPSERT = """
INSERT INTO weather_gold_contract_ledger (
    city, station_code, target_date, contract_side, contract_ticker,
    bucket_floor, bucket_cap, bucket_label,
    nws_forecast_f, gfs_forecast_f,
    calibrated_prob, raw_model_prob,
    model_delta_f, model_confidence, model_delta_flag,
    ensemble_spread,
    market_implied_prob, market_yes_mid,
    edge, edge_pct, edge_rank,
    recommended_action, signal_strength,
    stake_fraction, stake_usd,
    skip_reason, is_active, signal_generated_at,
    yes_bid, yes_ask, no_bid, no_ask,
    market_liquidity
) VALUES (
    %(city)s, %(station_code)s, %(target_date)s, %(contract_side)s, %(contract_ticker)s,
    %(bucket_floor)s, %(bucket_cap)s, %(bucket_label)s,
    %(nws_forecast_f)s, %(gfs_forecast_f)s,
    %(calibrated_prob)s, %(raw_model_prob)s,
    %(model_delta_f)s, %(model_confidence)s, %(model_delta_flag)s,
    %(ensemble_spread)s,
    %(market_implied_prob)s, %(market_yes_mid)s,
    %(edge)s, %(edge_pct)s, %(edge_rank)s,
    %(recommended_action)s, %(signal_strength)s,
    %(stake_fraction)s, %(stake_usd)s,
    %(skip_reason)s, %(is_active)s, %(signal_generated_at)s,
    %(yes_bid)s, %(yes_ask)s, %(no_bid)s, %(no_ask)s,
    %(market_liquidity)s
)
ON CONFLICT (contract_ticker) DO UPDATE SET
    calibrated_prob      = EXCLUDED.calibrated_prob,
    raw_model_prob       = EXCLUDED.raw_model_prob,
    model_delta_f        = EXCLUDED.model_delta_f,
    model_confidence     = EXCLUDED.model_confidence,
    model_delta_flag     = EXCLUDED.model_delta_flag,
    ensemble_spread      = EXCLUDED.ensemble_spread,
    market_implied_prob  = EXCLUDED.market_implied_prob,
    market_yes_mid       = EXCLUDED.market_yes_mid,
    edge                 = EXCLUDED.edge,
    edge_pct             = EXCLUDED.edge_pct,
    edge_rank            = EXCLUDED.edge_rank,
    recommended_action   = EXCLUDED.recommended_action,
    signal_strength      = EXCLUDED.signal_strength,
    stake_fraction       = EXCLUDED.stake_fraction,
    stake_usd            = EXCLUDED.stake_usd,
    skip_reason          = EXCLUDED.skip_reason,
    is_active            = EXCLUDED.is_active,
    signal_generated_at  = EXCLUDED.signal_generated_at,
    yes_bid              = EXCLUDED.yes_bid,
    yes_ask              = EXCLUDED.yes_ask,
    no_bid               = EXCLUDED.no_bid,
    no_ask               = EXCLUDED.no_ask,
    market_liquidity     = EXCLUDED.market_liquidity,
    ledger_updated_at    = NOW()
"""
# Settlement actuals (actual_tmax_f, settled_at, contract_resolved_yes, paper_pnl, etc.)
# are written by the settlement reconciliation job, never overwritten here.


def write_to_ledger(conn, station_code: str, target_date: date,
                    predicted_tmax_f: float,
                    nws_tmax_f: Optional[float],
                    om_tmax_f: Optional[float],
                    model_rmse: float,
                    bankroll_usd: float = 1000.0,
                    dry_run: bool = False,
                    _precomputed_buckets: Optional[list] = None) -> dict:
    """
    Run CP4 sizing for station/target_date and upsert into weather_gold_contract_ledger.

    Skips the entire station/date if the contract has already settled.
    Writes one row per Kalshi bucket (BET_NO if edge qualifies, else SKIP).
    ON CONFLICT (contract_ticker) DO UPDATE — re-runs update signal, never touch actuals.

    Returns {'settled': bool, 'written': int, 'bet_no': int, 'skipped': int}.
    """
    if _is_settled(station_code, target_date):
        return {'settled': True, 'written': 0, 'bet_no': 0, 'skipped': 0}

    buckets = (_precomputed_buckets if _precomputed_buckets is not None
               else run_cp4_kelly(station_code, target_date, predicted_tmax_f,
                                  model_rmse, bankroll_usd))
    if not buckets:
        return {'settled': False, 'written': 0, 'bet_no': 0, 'skipped': 0}

    ref_f = nws_tmax_f if nws_tmax_f is not None else om_tmax_f
    model_delta_f = round(predicted_tmax_f - ref_f, 2) if ref_f is not None else 0.0
    ensemble_spread = (round(abs(nws_tmax_f - om_tmax_f), 2)
                       if nws_tmax_f is not None and om_tmax_f is not None else None)
    now = datetime.now(timezone.utc)

    # Edge rank: 1 = best qualifying bucket (by edge_cents desc)
    qualifying_sorted = sorted(
        [b for b in buckets if b['qualifies']],
        key=lambda x: x['edge_cents'], reverse=True,
    )
    edge_rank_map = {b['bucket_label']: i + 1 for i, b in enumerate(qualifying_sorted)}

    rows = []
    for b in buckets:
        edge_cents   = b['edge_cents']
        no_ask_cents = b['no_ask_cents']
        qualifies    = b['qualifies']

        stake_fraction = 0.0
        if qualifies and no_ask_cents > 0:
            win_cents = 100.0 - no_ask_cents
            if win_cents > 0:
                kelly = edge_cents / win_cents
                stake_fraction = round(min(kelly * 0.5, BANKROLL_CAP_PCT), 6)

        if not qualifies:
            if not (0 < no_ask_cents < 100):
                skip_reason = 'INVALID_PRICE'
            elif b.get('no_ask_thin'):
                skip_reason = 'NO_ASK_TOO_THIN'
            else:
                skip_reason = 'EDGE_TOO_LOW'
        else:
            skip_reason = None

        no_ask_dec  = no_ask_cents / 100.0
        yes_bid_dec = b['yes_bid_cents'] / 100.0
        yes_ask_dec = (b['yes_ask_cents'] / 100.0
                       if b['yes_ask_cents'] is not None else None)
        no_bid_dec  = (b['no_bid_cents']  / 100.0
                       if b['no_bid_cents']  is not None else None)

        # YES mid = midpoint of (yes_bid, 1 - no_ask) — best available estimate
        yes_mid = round((yes_bid_dec + (1.0 - no_ask_dec)) / 2.0, 6)
        edge_pct = round(edge_cents / no_ask_cents, 4) if no_ask_cents > 0 else None

        rows.append({
            'city':               CITY_MAP.get(station_code, station_code),
            'station_code':       station_code,
            'target_date':        target_date,
            'contract_side':      'high',
            'contract_ticker':    _build_ticker(station_code, target_date, b['bucket_label']),
            'bucket_floor':       b['bucket_floor'],
            'bucket_cap':         b['bucket_cap'],
            'bucket_label':       b['bucket_label'],
            'nws_forecast_f':     nws_tmax_f,
            'gfs_forecast_f':     om_tmax_f,
            'calibrated_prob':    round(b['model_prob_cents'] / 100.0, 6),
            'raw_model_prob':     round(b['model_prob_cents'] / 100.0, 6),
            'model_delta_f':      model_delta_f,
            'model_confidence':   _model_confidence(model_delta_f),
            'model_delta_flag':   'DIVERGE' if abs(model_delta_f) >= 1.5 else 'CONVERGE',
            'ensemble_spread':    ensemble_spread,
            'market_implied_prob': round(no_ask_dec, 6),
            'market_yes_mid':     yes_mid,
            'edge':               round(edge_cents / 100.0, 6),
            'edge_pct':           edge_pct,
            'edge_rank':          edge_rank_map.get(b['bucket_label']),
            'recommended_action': 'BET_NO' if qualifies else 'SKIP',
            'signal_strength':    _signal_strength(edge_cents) if qualifies else None,
            'stake_fraction':     stake_fraction,
            'stake_usd':          b['stake_usd'],
            'skip_reason':        skip_reason,
            'is_active':          True,
            'signal_generated_at': now,
            'yes_bid':            round(yes_bid_dec, 6),
            'yes_ask':            round(yes_ask_dec, 6) if yes_ask_dec is not None else None,
            'no_bid':             round(no_bid_dec,  6) if no_bid_dec  is not None else None,
            'no_ask':             round(no_ask_dec,  6),
            'market_liquidity':   'ILLIQUID',
        })

    if dry_run:
        hrs = buckets[0].get('hours_to_settle', '?') if buckets else '?'
        sigma_used = buckets[0].get('sigma_used', '?') if buckets else '?'
        print(f"[DRY RUN] {station_code} {target_date}: {hrs}h to settle  "
              f"sigma={sigma_used}°F  {len(rows)} buckets — "
              f"{sum(1 for r in rows if r['recommended_action'] == 'BET_NO')} BET_NO, "
              f"{sum(1 for r in rows if r['skip_reason'] == 'NO_ASK_TOO_THIN')} THIN, "
              f"{sum(1 for r in rows if r['recommended_action'] == 'SKIP')} SKIP")
        for r in rows:
            if r['recommended_action'] == 'BET_NO':
                print(f"  BET_NO  {r['contract_ticker']:40} "
                      f"edge={r['edge']*100:.1f}¢  stake=${r['stake_usd']:.2f}  "
                      f"rank={r['edge_rank']}  strength={r['signal_strength']}")
        return {'settled': False, 'written': 0, 'bet_no': 0,
                'skipped': len(rows), 'dry_run': True}

    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, _LEDGER_UPSERT, rows, page_size=50)
    conn.commit()

    bet_no = sum(1 for r in rows if r['recommended_action'] == 'BET_NO')
    return {'settled': False, 'written': len(rows),
            'bet_no': bet_no, 'skipped': len(rows) - bet_no}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="CP4 Kelly sizer standalone test")
    p.add_argument('--station', required=True)
    p.add_argument('--date', required=True, type=date.fromisoformat)
    p.add_argument('--predicted-tmax', type=float, required=True)
    p.add_argument('--model-rmse', type=float, required=True)
    p.add_argument('--bankroll', type=float, default=1000.0)
    args = p.parse_args()

    opps = run_cp4_kelly(args.station, args.date, args.predicted_tmax,
                         args.model_rmse, args.bankroll)
    print(f"{'Bucket':10} {'Floor':>6} {'Cap':>6} "
          f"{'no_ask¢':>8} {'yes_bid¢':>9} {'model¢':>7} {'edge¢':>7} "
          f"{'qual':>5} {'contracts':>9} {'stake$':>7} {'sigma':>7} {'dist'}")
    print("-" * 105)
    for o in opps:
        print(f"{o['bucket_label']:10} "
              f"{(o['bucket_floor'] or 0):>6.0f} {(o['bucket_cap'] or 0):>6.0f} "
              f"{o['no_ask_cents']:>8.2f} {o['yes_bid_cents']:>9.2f} "
              f"{o['model_prob_cents']:>7.2f} {o['edge_cents']:>7.2f} "
              f"{'Y' if o['qualifies'] else 'N':>5} "
              f"{o['contracts']:>9} {o['stake_usd']:>7.2f} "
              f"{o['sigma_used']:>7.4f} {o['distribution_used']}")
