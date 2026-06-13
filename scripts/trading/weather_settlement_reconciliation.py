#!/usr/bin/env python3
"""
weather_settlement_reconciliation.py — WeatherBHN nightly settlement reconciliation.

After NWS CLI actuals publish each morning, this job joins every active
weather_gold_daily_edge_sheet recommendation against the final observed
temperature and writes the outcome to weather_model_accuracy.

Run nightly at 10:00 AM ET via bhn-weather-settlement-recon.timer, after the
NWS CLI report is expected to be published (~6-8 AM ET).

Edge-sheet rows with no matching actual are silently skipped (not yet settled).
Idempotent: existing weather_model_accuracy rows for a given contract_id are
not overwritten.

CLI:
  python3 weather_settlement_reconciliation.py
  python3 weather_settlement_reconciliation.py --dry-run
  python3 weather_settlement_reconciliation.py --date 2026-06-11
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional


def _prime_env() -> None:
    """Load /etc/bhn-trading/env and strat9.env into os.environ.

    Only sets vars that are not already present — systemd EnvironmentFile
    values take precedence when running under the timer. This allows the
    script to be run manually without pre-sourcing the env files.
    """
    for path in ("/etc/bhn-trading/env", "/etc/bhn-trading/strat9.env"):
        p = Path(path)
        if not p.is_file():
            continue
        for ln in p.read_text().splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            k = k.strip()
            if k not in os.environ:
                os.environ[k] = v.strip().strip('"').strip("'")


_prime_env()

import trading_core as tc  # noqa: E402 — must come after _prime_env()


logger = tc.get_logger("strat_9_weather_settlement_recon")


def _yes_settled(
    final_tmax_f: float,
    bucket_floor: Optional[float],
    bucket_cap: Optional[float],
) -> bool:
    """Return True if the YES side of a HIGH temperature contract settles."""
    if bucket_floor is not None and not (final_tmax_f >= bucket_floor):
        return False
    if bucket_cap is not None and not (final_tmax_f < bucket_cap):
        return False
    return True


def _accuracy_score(
    prob_yes: float,
    position_side: Optional[str],
    actual_yes: bool,
) -> float:
    """Position-aware Brier score. Near 1.0 = correct prediction, near 0.0 = wrong.

    For BET_NO positions the score is computed from NO's perspective so that a
    correct NO bet (YES did NOT settle) scores near 1.0 and a wrong NO bet scores
    near 0.0 — matching the semantics of bhn_was_correct.
    """
    if position_side == "no":
        # NO bet: p = probability NO wins = 1 - prob_yes; outcome = did NO win?
        p = 1.0 - prob_yes
        o = 0.0 if actual_yes else 1.0
    else:
        p = prob_yes
        o = 1.0 if actual_yes else 0.0
    return round(1.0 - (p - o) ** 2, 6)


def reconcile(target_date: Optional[date] = None, dry_run: bool = False) -> int:
    """Reconcile edge-sheet recommendations against actuals.

    Returns number of rows inserted into weather_model_accuracy.
    target_date: reconcile only this date; None = all settled dates.
    """
    rows_written = 0
    rows_skipped_no_actual = 0
    rows_skipped_exists = 0

    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            # 1. Pull all settled actuals (final=true), optionally filtered by date
            date_filter = "AND a.target_date = %s" if target_date else ""
            date_params = (target_date,) if target_date else ()

            cur.execute(f"""
                SELECT a.station_code,
                       a.target_date,
                       a.final_tmax_f,
                       a.report_issued_at
                FROM weather_silver_actuals_conformed a
                WHERE a.is_final = TRUE
                  AND a.final_tmax_f IS NOT NULL
                  {date_filter}
                ORDER BY a.target_date, a.station_code
            """, date_params)
            actuals = {(r[0], r[1]): (r[2], r[3]) for r in cur.fetchall()}

        if not actuals:
            logger.info("reconcile: no final actuals found — nothing to do")
            return 0

        logger.info(f"reconcile: {len(actuals)} (station, date) actuals to reconcile")

        with conn.cursor() as cur:
            for (station_code, act_date), (final_tmax_f, report_issued_at) in actuals.items():

                # 2. Fetch all active edge-sheet rows for this station+date
                cur.execute("""
                    SELECT g.contract_ticker,
                           g.city,
                           g.bucket_label,
                           g.contract_side,
                           g.bucket_floor,
                           g.bucket_cap,
                           g.calibrated_prob,
                           g.market_implied_prob,
                           g.edge,
                           g.recommended_action,
                           g.stake_usd
                    FROM weather_gold_daily_edge_sheet g
                    WHERE g.station_code = %s
                      AND g.target_date  = %s
                      AND g.is_active    = TRUE
                """, (station_code, act_date))
                edge_rows = cur.fetchall()

                if not edge_rows:
                    rows_skipped_no_actual += 1
                    continue

                for row in edge_rows:
                    (contract_ticker, city, bucket_label, contract_side,
                     bucket_floor, bucket_cap, calibrated_prob,
                     market_implied_prob, edge, recommended_action, stake_usd) = row

                    # 3. Determine settlement outcome
                    # All current contracts are HIGH temperature (tmax) contracts
                    actual_outcome = _yes_settled(
                        float(final_tmax_f),
                        float(bucket_floor) if bucket_floor is not None else None,
                        float(bucket_cap)   if bucket_cap   is not None else None,
                    )

                    # 4. Derive accuracy metrics
                    prob_for_scoring = float(calibrated_prob) if calibrated_prob is not None \
                        else (float(market_implied_prob) if market_implied_prob is not None else 0.5)

                    bhn_was_correct: Optional[bool] = None
                    bhn_position_side: Optional[str] = None
                    bhn_position_taken = recommended_action in ("BET_YES", "BET_NO")

                    if recommended_action == "BET_YES":
                        bhn_position_side = "yes"
                        bhn_was_correct = actual_outcome
                    elif recommended_action == "BET_NO":
                        bhn_position_side = "no"
                        bhn_was_correct = not actual_outcome

                    market_was_correct: Optional[bool] = None
                    if market_implied_prob is not None:
                        market_predicts_yes = float(market_implied_prob) > 0.5
                        market_was_correct = (market_predicts_yes == actual_outcome)

                    accuracy_score = _accuracy_score(
                        prob_for_scoring, bhn_position_side, actual_outcome
                    )

                    # P&L: entry_price * contracts = stake_usd; solve for contracts
                    pnl_dollar: Optional[float] = None
                    if bhn_position_taken and bhn_was_correct is not None and stake_usd is not None:
                        stake = float(stake_usd)
                        if bhn_position_side == "yes" and market_implied_prob is not None:
                            entry_price = float(market_implied_prob)
                            if entry_price > 0:
                                contracts = stake / entry_price
                                if bhn_was_correct:
                                    pnl_dollar = round((1.0 - entry_price) * contracts, 4)
                                else:
                                    pnl_dollar = round(-entry_price * contracts, 4)
                        elif bhn_position_side == "no" and market_implied_prob is not None:
                            entry_price = 1.0 - float(market_implied_prob)  # NO price
                            if entry_price > 0:
                                contracts = stake / entry_price
                                if bhn_was_correct:
                                    pnl_dollar = round((1.0 - entry_price) * contracts, 4)
                                else:
                                    pnl_dollar = round(-entry_price * contracts, 4)

                    if dry_run:
                        logger.info(
                            f"[DRY-RUN] {contract_ticker} {act_date} "
                            f"tmax={final_tmax_f}°F "
                            f"outcome={'YES' if actual_outcome else 'NO'} "
                            f"rec={recommended_action} "
                            f"correct={bhn_was_correct} "
                            f"accuracy={accuracy_score:.4f} "
                            f"pnl={pnl_dollar}"
                        )
                        rows_written += 1
                        continue

                    # 5. Upsert — skip if already reconciled for this contract
                    cur.execute("""
                        INSERT INTO weather_model_accuracy (
                            contract_id, contract_title, region,
                            variable,
                            bhn_predicted_probability, market_implied_probability, edge,
                            bhn_position_taken, bhn_position_value, bhn_position_side,
                            actual_outcome, bhn_was_correct, market_was_correct,
                            pnl_dollar, accuracy_score, resolved_at
                        )
                        SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        WHERE NOT EXISTS (
                            SELECT 1 FROM weather_model_accuracy
                            WHERE contract_id = %s
                              AND resolved_at IS NOT NULL
                        )
                    """, (
                        contract_ticker, bucket_label, city,
                        "tmax_f",
                        prob_for_scoring,
                        float(market_implied_prob) if market_implied_prob is not None else None,
                        float(edge) if edge is not None else None,
                        bhn_position_taken,
                        float(stake_usd) if stake_usd is not None else None,
                        bhn_position_side,
                        actual_outcome, bhn_was_correct, market_was_correct,
                        pnl_dollar, accuracy_score,
                        report_issued_at or datetime.now(timezone.utc),
                        # WHERE NOT EXISTS param:
                        contract_ticker,
                    ))

                    if cur.rowcount > 0:
                        rows_written += 1
                        logger.info(
                            f"{contract_ticker} {act_date}: "
                            f"tmax={final_tmax_f}°F → {'YES' if actual_outcome else 'NO'} | "
                            f"rec={recommended_action} correct={bhn_was_correct} "
                            f"accuracy={accuracy_score:.4f} pnl={pnl_dollar}"
                        )
                    else:
                        rows_skipped_exists += 1
                        logger.debug(f"{contract_ticker}: already reconciled — skipping")

    logger.info(
        f"reconcile: {rows_written} rows {'(dry-run)' if dry_run else 'written'} | "
        f"{rows_skipped_exists} already existed | "
        f"{rows_skipped_no_actual} stations had no edge rows"
    )
    return rows_written


def main() -> int:
    dry_run_env = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")

    parser = argparse.ArgumentParser(description="WeatherBHN settlement reconciliation")
    parser.add_argument(
        "--dry-run", action="store_true", default=dry_run_env,
        help="Log outcomes without writing to weather_model_accuracy",
    )
    parser.add_argument(
        "--date", metavar="YYYY-MM-DD",
        help="Reconcile only this target date (default: all settled dates)",
    )
    args = parser.parse_args()

    target_date: Optional[date] = None
    if args.date:
        try:
            target_date = date.fromisoformat(args.date)
        except ValueError:
            print(f"Invalid date: {args.date}", file=sys.stderr)
            return 1

    logger.info(
        f"=== weather-settlement-recon start "
        f"(date={target_date or 'all'}, dry_run={args.dry_run}) ==="
    )
    n = reconcile(target_date=target_date, dry_run=args.dry_run)
    logger.info(f"=== weather-settlement-recon end (rows={n}) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
