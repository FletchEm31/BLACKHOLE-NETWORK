#!/usr/bin/env python3
"""
strategy_prediction_alpha.py — BHN Strategy 9 (BHN-PREDICTION-ALPHA) orchestrator.

Glues the four Strat 9 components into a cron-runnable cycle:

  weather_data_collector  → forecasts + observations  (separate cron, 6h)
  kalshi_client            → RSA-PSS auth + poll loop  (this file uses it)
  prediction_signal        → edge calc + Kelly + decision
  prediction_settlement    → resolve bets after Kalshi settles them

One invocation = one GFS-publish window:
  1. Compute next GFS publish window (or use the most recent if --window-now)
  2. Sleep until window opens
  3. KalshiClient.poll_weather_prices_aggressive(
        on_market_update = PredictionSignal(...).on_market_update,
     )
  4. After window close: prediction_settlement.settle_all_open_bets()
  5. Exit (systemd timer re-fires at the next GFS window)

Designed to be wired to a systemd timer at 03:30 / 09:30 / 15:30 / 21:30
UTC (each is +3.5h after a GFS cycle start). The script can also be
invoked manually with --window-now to test the polling loop outside of
the cron-driven cadence, or --settle-only to run a settlement pass
without any polling.

PHASE 1 SAFETY — by design:
  - Hardcoded STRATEGY_ID = 'strat_9_prediction_alpha' — does NOT depend
    on trading_core.StrategyId enum (no enum entry yet) or
    trading_strategies registry row. Strategy is fully standalone until
    operator wants to wire it into the framework.
  - Gating in this order:
        (a) PG-level: rules.json strat_9_prediction_alpha.enabled=true
        (b) Kalshi:   paper_only=True (refuses production URL)
        (c) Signal:   PredictionSignal.dry_run=True until rules.json
                       enabled AND calibration data exists
  - If ANY gate is false, the cycle runs but PLACE_ORDER calls are
    suppressed. weather_bets rows are still inserted as audit records.

Operator flips to live (Phase 3 activation):
  1. UPDATE rules.json strat_9_prediction_alpha.enabled = true
  2. rsync rules.json LA → NJ (or wherever this runs)
  3. Wait for ≥30 days of weather_observations × weather_forecasts pairs
     to accumulate so Phase 2 calibration job populates model_calibration
     with reliability_score ≥ 0.65 for at least some (city, var, lead) tuples
  4. PredictionSignal will start passing the confidence gate and bets
     will actually flow

CLI:
  python3 strategy_prediction_alpha.py                # default — sleep to
                                                       # next GFS window, run
                                                       # polling + settlement
  python3 strategy_prediction_alpha.py --window-now   # skip sleep; poll now
  python3 strategy_prediction_alpha.py --settle-only  # settlement pass only
  python3 strategy_prediction_alpha.py --duration N   # override poll duration
  python3 strategy_prediction_alpha.py --dry-run      # force dry-run regardless
                                                       # of rules.json
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import trading_core as tc
import kalshi_client as kc
import prediction_signal as ps
import prediction_settlement as pset


STRATEGY_ID = "strat_9_prediction_alpha"
logger = tc.get_logger(STRATEGY_ID)

# Defaults for one cycle
DEFAULT_WINDOW_DURATION_SECONDS = 1800   # 30 min — operator-spec
GFS_LAG_HOURS = kc.DEFAULT_GFS_LAG_HOURS


# ─────────────────────────────────────────────────────────────────────────
# Gating helpers
# ─────────────────────────────────────────────────────────────────────────

def _strat_block() -> dict:
    """Return rules.json[strat_9_prediction_alpha] block, or {} if missing."""
    try:
        rules = tc.load_rules() or {}
        block = rules.get(STRATEGY_ID)
        return block if isinstance(block, dict) else {}
    except Exception as e:
        logger.warning(f"_strat_block: load_rules failed (non-fatal): {e}")
        return {}


def _is_enabled() -> bool:
    """True iff rules.json explicitly sets enabled=true. Default off."""
    return bool(_strat_block().get("enabled", False))


def _is_halted_at_system() -> bool:
    """Mirror trading_core.is_system_halted() but without raising when the
    'system' row doesn't exist (Phase 1 may be on a host without the trading
    framework deployed)."""
    try:
        return tc.is_system_halted()
    except Exception as e:
        logger.debug(f"is_system_halted check failed (treating as not-halted): {e}")
        return False


def _should_run_window() -> tuple[bool, str]:
    """Composite gate for whether to run the polling window. Returns
    (allowed, reason_if_blocked). Even when blocked, we may still want to
    run the settlement pass — that's gated separately."""
    if _is_halted_at_system():
        return False, "system halted (killswitch)"
    # rules.json enabled=false → run DRY (operator sees what we'd have done)
    # but don't bail entirely; this is the Phase 1 mode.
    return True, ""


# ─────────────────────────────────────────────────────────────────────────
# One-cycle runner
# ─────────────────────────────────────────────────────────────────────────

def _sleep_until(target: datetime) -> None:
    """Sleep in 30s chunks until target, so SIGTERM from systemd shutdown
    can interrupt quickly. Logs progress every 5 minutes."""
    last_progress = 0.0
    while True:
        now = datetime.now(timezone.utc)
        delta = (target - now).total_seconds()
        if delta <= 0:
            return
        if delta - last_progress >= 300 or last_progress == 0:
            mins = int(delta // 60)
            logger.info(f"sleeping {mins} min until GFS window opens at "
                        f"{target.isoformat()}")
            last_progress = delta
        time.sleep(min(30, delta))


def run_one_window(*,
                    window_now: bool = False,
                    duration_seconds: int = DEFAULT_WINDOW_DURATION_SECONDS,
                    dry_run_override: Optional[bool] = None,
                    skip_settlement: bool = False) -> dict:
    """One full Strat 9 cycle: (optional sleep) → poll → settle.

    Returns dict summary {window: {...}, settlement: {...}}.
    """
    allowed, reason = _should_run_window()
    if not allowed:
        logger.info(f"Skipping polling window: {reason}")
        if not skip_settlement:
            return {"window": None,
                    "settlement": pset.settle_all_open_bets()}
        return {"window": None, "settlement": None}

    # Determine GFS window timing
    window_start, window_end, gfs_run_hour = kc.next_gfs_publish_window_utc(
        lag_hours=GFS_LAG_HOURS,
        watch_duration_minutes=int(duration_seconds / 60),
    )
    if window_now:
        now = datetime.now(timezone.utc)
        # Use the MOST RECENT cycle as the gfs_run_at attribution for stats,
        # since we're polling "now" rather than waiting
        today = now.date()
        most_recent_hour = max(
            (h for h in kc.GFS_CYCLE_HOURS
             if datetime(today.year, today.month, today.day, h, 0, 0, 0,
                          tzinfo=timezone.utc) <= now),
            default=None,
        )
        if most_recent_hour is None:
            # Pre-00z UTC; use yesterday's 18z
            from datetime import timedelta
            y = today - timedelta(days=1)
            gfs_run_at = datetime(y.year, y.month, y.day, 18, 0, 0, 0,
                                    tzinfo=timezone.utc)
            gfs_run_hour = 18
        else:
            gfs_run_at = datetime(today.year, today.month, today.day,
                                    most_recent_hour, 0, 0, 0, tzinfo=timezone.utc)
            gfs_run_hour = most_recent_hour
    else:
        _sleep_until(window_start)
        gfs_run_at = window_start.replace(
            hour=gfs_run_hour, minute=0, second=0, microsecond=0,
        )

    logger.info(f"=== {STRATEGY_ID} window start "
                f"gfs_run_hour={gfs_run_hour}z duration={duration_seconds}s ===")

    # Construct the Kalshi client + signal callback
    client = kc.KalshiClient(paper_only=True)
    # Phase 1 safety: rules.json enabled=false → dry_run forced True
    # Operator override via CLI --dry-run also possible
    if dry_run_override is True:
        signal_dry_run: Optional[bool] = True
    elif dry_run_override is False and _is_enabled():
        signal_dry_run = False
    else:
        signal_dry_run = None  # PredictionSignal resolves from rules.json
    signal = ps.PredictionSignal(client, dry_run=signal_dry_run)

    # Run the polling loop (writes gfs_window_stats + SMS on completion)
    window_summary: dict
    try:
        window_summary = client.poll_weather_prices_aggressive(
            duration_seconds=duration_seconds,
            gfs_run_at=gfs_run_at,
            gfs_run_hour=gfs_run_hour,
            on_market_update=signal.on_market_update,
        )
    except Exception:
        logger.exception("polling loop crashed; will still attempt settlement")
        window_summary = {"error": "polling loop crashed; see log"}

    logger.info(f"=== {STRATEGY_ID} window end "
                f"polls={window_summary.get('poll_count', 0)} "
                f"opps={window_summary.get('opportunities_found', 0)} "
                f"bets={window_summary.get('bets_placed', 0)} ===")

    # Post-window settlement pass
    settlement_summary: Optional[dict] = None
    if not skip_settlement:
        try:
            settlement_summary = pset.settle_all_open_bets(client=client)
        except Exception:
            logger.exception("settlement pass crashed")
            settlement_summary = {"error": "settlement crashed; see log"}

    return {"window": window_summary, "settlement": settlement_summary}


def run_settlement_only() -> dict:
    """Skip the polling loop entirely; just settle any resolved bets.
    Used by a separate daily cron to catch any settlements Kalshi published
    overnight."""
    logger.info(f"=== {STRATEGY_ID} settlement-only pass ===")
    if _is_halted_at_system():
        logger.info("system halted — skipping settlement")
        return {"settlement": None}
    return {"settlement": pset.settle_all_open_bets()}


# ─────────────────────────────────────────────────────────────────────────
# PG advisory lock — prevent concurrent invocations
# ─────────────────────────────────────────────────────────────────────────

def _lock_key() -> int:
    """Stable hash of STRATEGY_ID as a 31-bit int for pg_advisory_lock."""
    return abs(hash(STRATEGY_ID)) % (2**31)


# ─────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="BHN Strategy 9 — BHN-PREDICTION-ALPHA orchestrator",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--window-now", action="store_true",
                      help="Skip the sleep-until-GFS-window step; poll "
                      "immediately for --duration seconds.")
    mode.add_argument("--settle-only", action="store_true",
                      help="Run only the settlement pass; no polling.")
    parser.add_argument("--duration", type=int,
                        default=DEFAULT_WINDOW_DURATION_SECONDS,
                        help="Polling window duration in seconds "
                        "(default 1800 = 30 min)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Force dry-run regardless of rules.json. "
                        "Decisions logged + audited to weather_bets, but "
                        "no place_order calls fire.")
    parser.add_argument("--skip-settlement", action="store_true",
                        help="Skip the post-window settlement pass.")
    parser.add_argument("--no-lock", action="store_true",
                        help="Bypass the PG advisory lock (debugging only).")
    args = parser.parse_args()

    logger.info(f"--- {STRATEGY_ID} invoke "
                f"window_now={args.window_now} "
                f"settle_only={args.settle_only} "
                f"duration={args.duration} dry_run={args.dry_run} ---")

    # Diagnostics: log the gates we're about to evaluate
    enabled = _is_enabled()
    halted = _is_halted_at_system()
    logger.info(f"gates: rules.json.enabled={enabled} system.halted={halted}")
    if not enabled and not args.dry_run:
        logger.info("rules.json strat_9 enabled=false — running in dry-run "
                    "(decisions audited to weather_bets, no orders placed)")

    # PG advisory lock — prevents two cron-driven invocations from racing
    if args.no_lock:
        return _dispatch(args)

    try:
        with tc.pg_advisory_lock(_lock_key()):
            return _dispatch(args)
    except Exception:
        logger.exception("advisory lock acquisition or dispatch failed")
        return 1


def _dispatch(args) -> int:
    try:
        if args.settle_only:
            run_settlement_only()
        else:
            run_one_window(
                window_now=args.window_now,
                duration_seconds=args.duration,
                dry_run_override=(True if args.dry_run else None),
                skip_settlement=args.skip_settlement,
            )
        return 0
    except KeyboardInterrupt:
        logger.warning("interrupted by SIGINT — exiting cleanly")
        return 130
    except Exception:
        logger.exception(f"{STRATEGY_ID} cycle crashed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
