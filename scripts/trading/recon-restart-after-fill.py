#!/usr/bin/env python3
"""
recon-restart-after-fill.py — One-shot verifier that restarts
bhn-reconciliation.timer if and only if Tue 2026-05-19 13:30 UTC market
open cleared the queued JPST close on the default Alpaca account.

Fired once by bhn-recon-restart-after-fill.timer at 2026-05-19 14:00 UTC,
~30min after expected fill. Background: an emergency cleanup on
2026-05-19 ~03:54 UTC submitted close order 1303197b-e2ec-4c7f-b718-
21e8fc831391 (BUY 3920 JPST market, tif=day) to flatten a -3920 short
that had compounded on the default account due to a multi-account
routing bug. The reconciliation daemon was left stopped because my
post-fix reconcile_state correctly flags positions on the default
account as UNKNOWN_POSITION orphans — restarting before the close
filled would halt the framework.

Verification (all must pass before timer start):
  1. Close order 1303197b... is in 'filled' status
  2. No JPST position remains on the default Alpaca account

NOT checked (intentional):
  - NJ_CACHE row count: strategies fire from 13:00 UTC onward and may
    legitimately have open positions by 14:00 UTC.
  - reconcile_state() output: strat_2/6/8 currently share an Alpaca
    account with default (config issue surfaced during the cleanup),
    so reconcile_state will produce cross-strategy false-positives
    until rules.json broker config is fixed. Operator follow-up.

After this fires once, the timer self-deactivates (RemainAfterElapse=false).
Cleanup the units when convenient:
  systemctl disable --now bhn-recon-restart-after-fill.timer
  rm /etc/systemd/system/bhn-recon-restart-after-fill.{timer,service}

Exit codes:
  0  — all checks passed, bhn-reconciliation.timer started
  1  — JPST position still on default account
  3  — close order not in 'filled' status
  4  — systemctl start returned non-zero
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone

import trading_core as tc


JPST_CLOSE_ORDER_ID = "1303197b-e2ec-4c7f-b718-21e8fc831391"


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


def check_close_order_filled() -> int:
    try:
        o = tc.get_alpaca().get_order(JPST_CLOSE_ORDER_ID)
    except Exception as e:
        log(f"FAIL: could not fetch close order {JPST_CLOSE_ORDER_ID}: {e}")
        return 3
    log(f"close order status={o.status} filled_qty={o.filled_qty} "
        f"filled_avg_price={o.filled_avg_price}")
    if o.status != "filled":
        log(f"FAIL: close order status is {o.status!r}, expected 'filled'")
        return 3
    log("PASS: close order filled")
    return 0


def check_default_alpaca_jpst() -> int:
    jpst = [p for p in tc.get_alpaca().list_positions() if p.symbol == "JPST"]
    if jpst:
        log(f"FAIL: JPST still present on default account: "
            f"qty={jpst[0].qty} side={jpst[0].side} "
            f"avg_entry={jpst[0].avg_entry_price}")
        return 1
    log("PASS: no JPST position on default account")
    return 0


def start_reconciliation_timer() -> int:
    log("starting bhn-reconciliation.timer ...")
    r = subprocess.run(
        ["/usr/bin/systemctl", "start", "bhn-reconciliation.timer"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        log(f"FAIL: systemctl start returned {r.returncode}")
        log(f"  stdout: {r.stdout!r}")
        log(f"  stderr: {r.stderr!r}")
        return 4
    log("PASS: systemctl start bhn-reconciliation.timer succeeded")
    r2 = subprocess.run(
        ["/usr/bin/systemctl", "list-timers", "bhn-reconciliation.timer",
         "--no-pager"],
        capture_output=True, text=True,
    )
    log(f"list-timers output:\n{r2.stdout.rstrip()}")
    return 0


def main() -> int:
    log("=== recon-restart-after-fill verifier start ===")
    for fn in (check_close_order_filled, check_default_alpaca_jpst):
        rc = fn()
        if rc != 0:
            log(f"=== ABORT: {fn.__name__} returned {rc}; "
                f"NOT starting bhn-reconciliation.timer ===")
            return rc
    rc = start_reconciliation_timer()
    log(f"=== verifier exit rc={rc} ===")
    return rc


if __name__ == "__main__":
    sys.exit(main())
