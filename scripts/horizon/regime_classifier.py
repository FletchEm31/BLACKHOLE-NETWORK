#!/usr/bin/env python3
"""
regime_classifier.py — HORIZON market regime classification.

Reads SPY close + SMA200 from market_daily and VIX + yield_curve_10y2y from
macro_daily for the target date, classifies into one of 5 regimes, writes
to market_regimes with confidence score + auto-generated rationale.

Cadence: systemd timer at 17:15 ET daily (after market_data_collector at
16:30 and macro_collector at 17:00).

Regimes (operator spec):
  BULL_CALM       spy > 200ma  AND  vix < 15
  BULL_VOLATILE   spy > 200ma  AND  15 <= vix < 25
  BULL_STRESSED   spy > 200ma  AND  vix >= 25
  BEAR_PANIC      spy < 200ma  AND  vix >= 25
  BEAR_GRIND      everything else (spy < 200ma AND vix < 25)

Confidence: distance-from-boundary composite. Low values flag days near a
regime transition; high values flag days comfortably inside a bucket.

Backfill mode reclassifies every day where market_daily has SPY + macro_daily
has VIX, so the regime time series matches the upstream history.

Env (/etc/bhn-trading/env):
  PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PASSWORD

CLI:
  python3 regime_classifier.py                # classify latest available date
  python3 regime_classifier.py --date 2026-05-13
  python3 regime_classifier.py --backfill     # all dates with required inputs
  python3 regime_classifier.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from datetime import date as _date
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "trading"))
import trading_core as tc


logger = tc.get_logger("horizon_regime_classifier")


# ─────────────────────────────────────────────────────────────────────────
# Thresholds — single source of truth, also drive confidence scoring
# ─────────────────────────────────────────────────────────────────────────

VIX_LOW_THRESHOLD       = 15.0   # below = vix_low
VIX_HIGH_THRESHOLD      = 25.0   # at-or-above = vix_high
SPY_PCT_NORM            = 0.02   # normalize spy_vs_200ma distance against 2%


# ─────────────────────────────────────────────────────────────────────────
# Pure classification function (operator spec, verbatim — high_yield_spread
# dropped from signature since it was unused in the spec branches)
# ─────────────────────────────────────────────────────────────────────────

def classify_regime(spy_close: float, spy_sma200: float, vix: float,
                     yield_curve: float) -> str:
    """Pure rule-based regime classification. yield_curve passed for forward
    compatibility (may gate BEAR_GRIND vs BEAR_PANIC in a future revision)."""
    _ = yield_curve  # currently informational only; surfaced in notes
    spy_trend     = spy_close > spy_sma200
    vix_low       = vix < VIX_LOW_THRESHOLD
    vix_elevated  = VIX_LOW_THRESHOLD <= vix < VIX_HIGH_THRESHOLD
    vix_high      = vix >= VIX_HIGH_THRESHOLD

    if spy_trend and vix_low:
        return "BULL_CALM"
    if spy_trend and vix_elevated:
        return "BULL_VOLATILE"
    if spy_trend and vix_high:
        return "BULL_STRESSED"
    if not spy_trend and vix_high:
        return "BEAR_PANIC"
    return "BEAR_GRIND"


# ─────────────────────────────────────────────────────────────────────────
# Confidence scoring
# ─────────────────────────────────────────────────────────────────────────

def confidence_score(spy_pct: float, vix: float) -> float:
    """Distance-from-nearest-boundary, clamped 0-1.

    spy boundary is 0 (SPY at 200MA). vix boundaries are 15 and 25.
    Confidence = min(spy_distance_normalized, vix_distance_normalized), so a
    day that's marginal on EITHER axis gets a low score. Days deep inside a
    bucket on both axes get a high score.
    """
    spy_dist = min(abs(spy_pct) / SPY_PCT_NORM, 1.0)
    vix_dist_low  = abs(vix - VIX_LOW_THRESHOLD)  / VIX_LOW_THRESHOLD
    vix_dist_high = abs(vix - VIX_HIGH_THRESHOLD) / VIX_HIGH_THRESHOLD
    vix_dist = min(min(vix_dist_low, vix_dist_high), 1.0)
    return round(min(spy_dist, vix_dist), 4)


def build_notes(regime: str, spy_pct: float, vix: float, yield_curve: float) -> str:
    """Auto-generated rationale string, deterministic from inputs."""
    direction = "above" if spy_pct >= 0 else "below"
    curve_label = "inverted" if yield_curve < 0 else "normal"
    return (f"SPY {abs(spy_pct)*100:.1f}% {direction} 200MA, "
            f"VIX={vix:.1f}, curve={yield_curve:+.2f}% ({curve_label}) -> {regime}")


# ─────────────────────────────────────────────────────────────────────────
# PG I/O
# ─────────────────────────────────────────────────────────────────────────

UPSERT_SQL = """
    INSERT INTO market_regimes
        (date, regime, spy_close, spy_vs_200ma, vix, yield_curve,
         confidence_score, notes)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (date) DO UPDATE SET
        regime              = EXCLUDED.regime,
        spy_close           = EXCLUDED.spy_close,
        spy_vs_200ma        = EXCLUDED.spy_vs_200ma,
        vix                 = EXCLUDED.vix,
        yield_curve         = EXCLUDED.yield_curve,
        confidence_score    = EXCLUDED.confidence_score,
        notes               = EXCLUDED.notes,
        classified_at       = NOW()
"""


def fetch_inputs(target_date: Optional[_date] = None) -> list[tuple]:
    """Fetch (date, spy_close, spy_sma200, vix, yield_curve_10y2y) for one
    date or all dates with both inputs available. Returns rows oldest-first."""
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            if target_date is not None:
                cur.execute("""
                    SELECT md.date, md.close, md.sma_200,
                           mac.vix, mac.yield_curve_10y2y
                    FROM market_daily md
                    JOIN macro_daily   mac ON mac.date = md.date
                    WHERE md.ticker = 'SPY' AND md.date = %s
                      AND md.sma_200 IS NOT NULL
                      AND mac.vix IS NOT NULL
                """, (target_date,))
            else:
                cur.execute("""
                    SELECT md.date, md.close, md.sma_200,
                           mac.vix, mac.yield_curve_10y2y
                    FROM market_daily md
                    JOIN macro_daily   mac ON mac.date = md.date
                    WHERE md.ticker = 'SPY'
                      AND md.sma_200 IS NOT NULL
                      AND mac.vix IS NOT NULL
                    ORDER BY md.date
                """)
            return cur.fetchall()


def classify_and_upsert(rows: list[tuple], dry_run: bool = False) -> int:
    n = 0
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            for d, spy_close, spy_sma200, vix, yield_curve in rows:
                spy_close   = float(spy_close)
                spy_sma200  = float(spy_sma200)
                vix         = float(vix)
                yc          = float(yield_curve) if yield_curve is not None else 0.0
                spy_pct     = (spy_close - spy_sma200) / spy_sma200
                regime      = classify_regime(spy_close, spy_sma200, vix, yc)
                conf        = confidence_score(spy_pct, vix)
                notes       = build_notes(regime, spy_pct, vix, yc)
                if dry_run:
                    logger.info(f"{d}: {regime} (conf={conf:.2f}) — {notes}")
                else:
                    cur.execute(UPSERT_SQL,
                                (d, regime, spy_close, spy_pct, vix, yc, conf, notes))
                n += 1
    return n


# ─────────────────────────────────────────────────────────────────────────
# Top-level
# ─────────────────────────────────────────────────────────────────────────

def latest_classifiable_date() -> Optional[_date]:
    """Most recent date where both SPY+sma_200 in market_daily AND vix in
    macro_daily exist. Returns None if no overlap."""
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT MAX(md.date)
                FROM market_daily md
                JOIN macro_daily   mac ON mac.date = md.date
                WHERE md.ticker = 'SPY'
                  AND md.sma_200 IS NOT NULL
                  AND mac.vix IS NOT NULL
            """)
            row = cur.fetchone()
            return row[0] if row else None


def run(target_date: Optional[_date], backfill: bool, dry_run: bool) -> int:
    if backfill:
        rows = fetch_inputs(target_date=None)
        logger.info(f"backfill: {len(rows)} (date) rows with full inputs")
    elif target_date is not None:
        rows = fetch_inputs(target_date=target_date)
        if not rows:
            logger.warning(f"no inputs available for {target_date} — skipping")
            return 0
    else:
        d = latest_classifiable_date()
        if d is None:
            logger.warning("no overlap between market_daily(SPY) and macro_daily(vix). "
                           "Run market_data_collector and macro_collector first.")
            return 0
        rows = fetch_inputs(target_date=d)

    n = classify_and_upsert(rows, dry_run=dry_run)
    logger.info(f"regimes: {n} rows {'(dry-run)' if dry_run else 'upserted'}")
    return n


# ─────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="HORIZON regime classifier")
    parser.add_argument("--date", default=None,
                        help="Target date YYYY-MM-DD. Default = latest available.")
    parser.add_argument("--backfill", action="store_true",
                        help="Reclassify every date with full inputs.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log only, no PG writes.")
    args = parser.parse_args()

    target = None
    if args.date:
        try:
            target = _date.fromisoformat(args.date)
        except ValueError:
            logger.error(f"invalid --date {args.date!r}, must be YYYY-MM-DD")
            return 2

    logger.info(f"=== regime-classifier start (target={target}, "
                f"backfill={args.backfill}, dry_run={args.dry_run}) ===")
    try:
        run(target_date=target, backfill=args.backfill, dry_run=args.dry_run)
    except Exception:
        logger.exception("regime-classifier failed")
        return 1
    logger.info("=== regime-classifier end ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
