"""
WeatherBHN Checkpoint 2 — Post-Fee Arbitrage Scanner.

Takes CP1-passed contracts and checks for genuine risk-free arbitrage after
accounting for Kalshi maker fees on both sides.

Correct arb condition (Phase A doc had the inequality direction wrong):

    combined_fee = maker_fee(yes_ask, n) + maker_fee(no_ask, n)
    if yes_ask + no_ask + combined_fee < 1.00:
        # Genuine arb: buying YES at yes_ask + NO at no_ask nets > $1 at settlement

Size: min(yes_ask_depth, no_ask_depth, bankroll * 0.10)
  Depth proxy: open_interest (full LOB depth not available in snapshot).

Action: BUY_BOTH_PAPER_ONLY — no real execution.

Fires are logged to stdout and optionally to a log file. Every cycle logs
whether arb was found or not (for audit trail during paper-trading period).
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone

import pandas as pd
import psycopg2

from fee_calculator import maker_fee
from weatherbhn_cp1_sanity import run_cp1

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DSN      = os.environ.get("BHN_PG_DSN", "host=/var/run/postgresql dbname=bhn user=ehuser")
BANKROLL = float(os.environ.get("BHN_BANKROLL", "500"))

# Minimum profit per contract to bother logging (filters noise near 0)
MIN_PROFIT_PER_CONTRACT = 0.001


def _arb_size(yes_ask: float, no_ask: float,
              open_interest: float, bankroll: float,
              n_contracts: int = 1) -> int:
    """Conservative contract count: can't exceed 10% of bankroll or available OI.

    open_interest is used as a proxy for available depth — we can't fill
    more than the thinner side's outstanding OI.
    """
    oi_cap      = int(open_interest) if open_interest and open_interest > 0 else 1
    cost_per_pair = yes_ask + no_ask        # cost to buy 1 YES + 1 NO
    bankroll_cap  = int(math.floor((bankroll * 0.10) / cost_per_pair)) if cost_per_pair > 0 else 0
    return max(1, min(oi_cap, bankroll_cap))


def scan_arb(cp1_df: pd.DataFrame, bankroll: float = BANKROLL) -> list[dict]:
    """Evaluate each CP1-passed contract for post-fee arb.

    Returns a list of fire dicts for contracts where arb exists.
    All fires are BUY_BOTH_PAPER_ONLY — no real execution.
    """
    fires = []
    passed = cp1_df[cp1_df["sanity_passed"]].copy()

    for _, row in passed.iterrows():
        ticker    = row["market_ticker"]
        yes_ask   = float(row["yes_ask"])
        no_ask    = float(row["no_ask"])
        oi        = float(row.get("open_interest") or 0)

        # Post-fee arb condition (using maker_fee — we place limit orders)
        n = 1  # evaluate per-contract first
        combined_fee = maker_fee(yes_ask, n) + maker_fee(no_ask, n)
        total_cost   = yes_ask + no_ask + combined_fee

        if total_cost < 1.00:
            profit_per = 1.00 - total_cost
            if profit_per < MIN_PROFIT_PER_CONTRACT:
                continue

            size = _arb_size(yes_ask, no_ask, oi, bankroll)

            fire = {
                "timestamp":          datetime.now(timezone.utc).isoformat(),
                "ticker":             ticker,
                "station_code":       row.get("station_code"),
                "target_date":        str(row.get("target_date")),
                "contract_type":      row.get("contract_type"),
                "bucket_floor":       row.get("bucket_floor"),
                "bucket_cap":         row.get("bucket_cap"),
                "yes_ask":            round(yes_ask, 4),
                "no_ask":             round(no_ask, 4),
                "maker_fee_yes":      round(maker_fee(yes_ask, size), 4),
                "maker_fee_no":       round(maker_fee(no_ask, size), 4),
                "combined_fee":       round(combined_fee, 4),
                "total_cost":         round(total_cost, 4),
                "profit_per_contract": round(profit_per, 4),
                "size":               size,
                "total_profit":       round(profit_per * size, 4),
                "open_interest":      oi,
                "action":             "BUY_BOTH_PAPER_ONLY",
            }
            fires.append(fire)

            log.info(
                "ARB FIRE %s: yes_ask=%.3f no_ask=%.3f fee=%.4f → "
                "profit=%.4f/contract × %d = $%.4f [PAPER ONLY]",
                ticker, yes_ask, no_ask, combined_fee,
                profit_per, size, profit_per * size,
            )
        else:
            log.debug(
                "no-arb %s: yes_ask=%.3f + no_ask=%.3f + fee=%.4f = %.4f ≥ 1.00",
                ticker, yes_ask, no_ask, combined_fee, total_cost,
            )

    return fires


def run_cp2(conn, bankroll: float = BANKROLL) -> tuple[pd.DataFrame, list[dict]]:
    """Run CP1 then CP2. Returns (cp1_df, fires).

    cp1_df: full CP1 output (use for audit)
    fires:  list of arb opportunities found (empty if none)
    """
    cp1_df = run_cp1(conn)
    fires  = scan_arb(cp1_df, bankroll=bankroll)

    if fires:
        log.info(
            "CP2: %d ARB OPPORTUNITY(IES) FOUND across %d CP1-passed contracts",
            len(fires), cp1_df["sanity_passed"].sum(),
        )
    else:
        log.info(
            "CP2: no arb found across %d CP1-passed contracts",
            cp1_df["sanity_passed"].sum(),
        )

    return cp1_df, fires


# ─────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info(
        "WeatherBHN CP2 — post-fee arb scanner (PAPER ONLY, bankroll=$%.0f)",
        BANKROLL,
    )

    with psycopg2.connect(DSN) as conn:
        cp1_df, fires = run_cp2(conn, bankroll=BANKROLL)

    print("\n=== CP1 → CP2 PIPELINE SUMMARY ===")
    total   = len(cp1_df)
    passed  = int(cp1_df["sanity_passed"].sum())
    failed  = total - passed
    print(f"  Total contracts seen:    {total}")
    print(f"  CP1 passed:              {passed}")
    print(f"  CP1 failed:              {failed}")
    print(f"  CP2 arb fires (paper):   {len(fires)}")

    if fires:
        print("\n=== ARB OPPORTUNITIES (BUY_BOTH_PAPER_ONLY) ===")
        for f in fires:
            print(
                f"  {f['ticker']}: "
                f"yes_ask={f['yes_ask']:.3f} + no_ask={f['no_ask']:.3f} + "
                f"fee={f['combined_fee']:.4f} = {f['total_cost']:.4f} | "
                f"profit={f['profit_per_contract']:.4f}/contract × {f['size']} = "
                f"${f['total_profit']:.4f} [{f['action']}]"
            )
        print()
        print("Full fire details (JSON):")
        print(json.dumps(fires, indent=2, default=str))
    else:
        print("\n  No arb found this cycle — logged for paper-trading audit trail.")


if __name__ == "__main__":
    main()
