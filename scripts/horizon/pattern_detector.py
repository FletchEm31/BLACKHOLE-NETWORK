#!/usr/bin/env python3
"""
pattern_detector.py — HORIZON pattern discovery (SCAFFOLD).

Discovers correlations between market state and strategy outcomes, writes
findings to pattern_library. Read-only consumed by HORIZON via
get_pattern_matches tool.

DATA SUFFICIENCY GATE:
  Self-gates if any ticker in market_daily has fewer than 63 rows
  (~3 months of trading days). Until that gate clears, logs status and
  exits cleanly with rc=0. This is the SCAFFOLD posture for now.

Once data is sufficient, the live methodology will be:
  1. Win-rate diffs bucketed by (regime × VIX_bucket × macro_state).
     Minimum sample size = 20 per bucket; smaller buckets are skipped.
  2. Cross-strategy Pearson correlations on daily P&L series, 60d window.
     Stored as pattern_type='cross_strategy_correlation'.
  3. No p-value gating in v1 — sample sizes will be too small early on.
     Operator can prune via active=FALSE once a pattern looks like noise.

Analytics-only contract (per [[feedback_self_contained_strategies]]):
  pattern_library is READ-ONLY for HORIZON consumption. No feedback into
  strategy execution. Strategies remain self-contained per the operator's
  hard line.

Cadence: invoke manually OR via a future weekly Sunday 21:00 ET timer
(not yet added — scaffold posture).

CLI:
  python3 pattern_detector.py                 # gated run; exits if data insufficient
  python3 pattern_detector.py --status        # report data sufficiency, no compute
  python3 pattern_detector.py --force         # run compute even if gated (for testing)
  python3 pattern_detector.py --dry-run       # log only, no PG writes
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "trading"))
import trading_core as tc


logger = tc.get_logger("horizon_pattern_detector")


# ─────────────────────────────────────────────────────────────────────────
# Data sufficiency gate
# ─────────────────────────────────────────────────────────────────────────

MIN_ROWS_PER_TICKER = 63        # ~3 months of trading days
MIN_SAMPLE_SIZE = 20            # per regime/VIX/macro bucket
CROSS_STRAT_WINDOW_DAYS = 60    # Pearson on daily P&L series


def check_data_sufficiency() -> tuple[bool, dict[str, int]]:
    """Returns (sufficient, {ticker: row_count}). Sufficient = every ticker
    in the universe has >= MIN_ROWS_PER_TICKER rows in market_daily."""
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ticker, COUNT(*) AS n
                FROM market_daily
                GROUP BY ticker
            """)
            counts = {r[0]: int(r[1]) for r in cur.fetchall()}
    if not counts:
        return False, {}
    short = {t: n for t, n in counts.items() if n < MIN_ROWS_PER_TICKER}
    return (len(short) == 0), counts


# ─────────────────────────────────────────────────────────────────────────
# Compute logic (scaffold — pseudo-implementation; activated post-gate)
# ─────────────────────────────────────────────────────────────────────────

def compute_regime_buckets(dry_run: bool = False) -> int:
    """For each (regime, VIX bucket) discovered in history, compute win-rate
    diffs across paper_trades that opened during that bucket. SCAFFOLD —
    real implementation joins paper_trades.entry_time to market_regimes.date
    and aggregates pnl_pct. Returns count of patterns written."""
    # Bucket definitions match regime_classifier thresholds
    vix_buckets = ("low", "elevated", "high")    # <15, 15-25, >=25
    regimes = ("BULL_CALM", "BULL_VOLATILE", "BULL_STRESSED",
                "BEAR_PANIC", "BEAR_GRIND")

    # SCAFFOLD: skeleton query, not yet upserting real patterns
    n_written = 0
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            for regime in regimes:
                for vix_bucket in vix_buckets:
                    # Skeleton query — will be filled in once data is sufficient
                    cur.execute("""
                        SELECT COUNT(*) AS n,
                               AVG(pt.pnl_pct) AS avg_return,
                               AVG(CASE WHEN pt.pnl_pct > 0 THEN 1.0 ELSE 0.0 END) AS win_rate
                        FROM paper_trades pt
                        JOIN market_regimes mr ON mr.date = pt.entry_time::date
                        WHERE mr.regime = %s
                          AND mr.vix >= %s AND mr.vix < %s
                          AND pt.status = 'closed'
                    """, (regime,
                          0 if vix_bucket == "low" else 15 if vix_bucket == "elevated" else 25,
                          15 if vix_bucket == "low" else 25 if vix_bucket == "elevated" else 9999))
                    row = cur.fetchone()
                    if not row or row[0] is None or int(row[0]) < MIN_SAMPLE_SIZE:
                        continue
                    n, avg_ret, win_rate = int(row[0]), float(row[1] or 0), float(row[2] or 0)
                    desc = (f"{regime} + VIX {vix_bucket}: closed trades show "
                            f"avg return {avg_ret*100:+.2f}%, win rate {win_rate*100:.1f}%")
                    conditions = {"regime": regime, "vix_bucket": vix_bucket}
                    if dry_run:
                        logger.info(f"dry-run pattern: {desc} (n={n})")
                    else:
                        cur.execute("""
                            INSERT INTO pattern_library
                                (pattern_type, description, conditions, sample_size,
                                 win_rate, avg_return, confidence_score, active)
                            VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, TRUE)
                            ON CONFLICT DO NOTHING
                        """, ("regime_vix_bucket", desc,
                              str(conditions).replace("'", '"'),
                              n, win_rate, avg_ret, min(1.0, n / 100.0)))
                    n_written += 1
    return n_written


def compute_cross_strategy_correlations(dry_run: bool = False) -> int:
    """Daily P&L Pearson correlations between strategy pairs over the trailing
    CROSS_STRAT_WINDOW_DAYS. SCAFFOLD — depends on strategy_performance having
    enough rows."""
    n_written = 0
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT strategy_id, date, daily_pnl
                FROM strategy_performance
                WHERE date >= CURRENT_DATE - INTERVAL '%s days'
                  AND strategy_id != 'system'
                ORDER BY strategy_id, date
            """ % CROSS_STRAT_WINDOW_DAYS)
            rows = cur.fetchall()

    # Build per-strategy daily series
    by_strat: dict[str, dict[date, float]] = defaultdict(dict)
    for sid, d, pnl in rows:
        by_strat[sid][d] = float(pnl) if pnl is not None else 0.0

    sids = sorted(by_strat.keys())
    for i, sa in enumerate(sids):
        for sb in sids[i + 1:]:
            common_dates = sorted(set(by_strat[sa].keys()) & set(by_strat[sb].keys()))
            if len(common_dates) < MIN_SAMPLE_SIZE:
                continue
            xs = [by_strat[sa][d] for d in common_dates]
            ys = [by_strat[sb][d] for d in common_dates]
            r = _pearson(xs, ys)
            if r is None:
                continue
            desc = (f"P&L correlation between {sa} and {sb} over "
                    f"{len(common_dates)}d window: r = {r:+.3f}")
            conditions = {"strategy_a": sa, "strategy_b": sb,
                           "window_days": CROSS_STRAT_WINDOW_DAYS}
            if dry_run:
                logger.info(f"dry-run pattern: {desc}")
            else:
                with tc.get_pg_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO pattern_library
                                (pattern_type, description, conditions, sample_size,
                                 confidence_score, strategies_affected, active)
                            VALUES (%s, %s, %s::jsonb, %s, %s, %s, TRUE)
                            ON CONFLICT DO NOTHING
                        """, ("cross_strategy_correlation", desc,
                              str(conditions).replace("'", '"'),
                              len(common_dates), abs(r), [sa, sb]))
            n_written += 1
    return n_written


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    var_x = sum((xs[i] - mean_x) ** 2 for i in range(n))
    var_y = sum((ys[i] - mean_y) ** 2 for i in range(n))
    den = math.sqrt(var_x * var_y)
    if den == 0:
        return None
    return num / den


# ─────────────────────────────────────────────────────────────────────────
# Top-level + CLI
# ─────────────────────────────────────────────────────────────────────────

def run(force: bool, dry_run: bool) -> int:
    sufficient, counts = check_data_sufficiency()
    if not counts:
        logger.info("market_daily is empty — run market_data_collector --backfill first")
        return 0
    if not sufficient and not force:
        short = {t: n for t, n in counts.items() if n < MIN_ROWS_PER_TICKER}
        logger.info(
            f"data insufficiency gate active — {len(short)} ticker(s) have < "
            f"{MIN_ROWS_PER_TICKER} rows. Pattern detector remains SCAFFOLD-ONLY. "
            f"Worst case: {min(short.values())} rows. Exiting cleanly."
        )
        return 0

    logger.info("data sufficient — beginning pattern compute")
    n_regime = compute_regime_buckets(dry_run=dry_run)
    n_cross  = compute_cross_strategy_correlations(dry_run=dry_run)
    logger.info(f"patterns written: {n_regime} regime/VIX buckets, "
                f"{n_cross} cross-strategy correlations")
    return 0


def report_status() -> int:
    sufficient, counts = check_data_sufficiency()
    print(f"market_daily ticker counts:")
    for t, n in sorted(counts.items()):
        marker = "✓" if n >= MIN_ROWS_PER_TICKER else "✗"
        print(f"  {marker} {t:8s}  {n} rows")
    print(f"\ngate threshold: {MIN_ROWS_PER_TICKER} rows/ticker")
    print(f"status: {'SUFFICIENT — pattern compute will run' if sufficient else 'SCAFFOLD — gate active, compute blocked'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="HORIZON pattern detector (scaffold)")
    parser.add_argument("--status", action="store_true",
                        help="Report data sufficiency, no compute.")
    parser.add_argument("--force", action="store_true",
                        help="Run compute even if gate is active (testing).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log only, no PG writes.")
    args = parser.parse_args()

    if args.status:
        return report_status()

    logger.info(f"=== pattern-detector start (force={args.force}, dry_run={args.dry_run}) ===")
    try:
        rc = run(force=args.force, dry_run=args.dry_run)
    except Exception:
        logger.exception("pattern-detector failed")
        return 1
    logger.info("=== pattern-detector end ===")
    return rc


if __name__ == "__main__":
    sys.exit(main())
