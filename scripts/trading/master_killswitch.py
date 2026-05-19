#!/usr/bin/env python3
"""
master_killswitch.py — BHN trading framework emergency halt.

Single-button "stop everything now" for the trading framework. Can be invoked:

1. Manually by operator (CLI) — `python3 master_killswitch.py halt --reason "..."`
2. Automatically by reconciliation_daemon on Alpaca/PG mismatch (imports halt())
3. Automatically by circuit_breaker tripping at the strategy or system level
4. From HORIZON via SSH command (operator-confirmed only)

What "halt" does:
- Flips trading_strategies.halted=TRUE for every strategy + the 'system' row
- Cancels every open Alpaca order (across all strategies)
- Flattens every open position (market sell, unless --no-close-positions)
- Writes circuit_breaker_log row (SYSTEM_HALT type) with trigger context
- Sends SMS to operator via Twilio (whitelisted operator number only)
- Updates reconciliation_heartbeat with last_halt_at timestamp

Halt is sticky — strategies refuse to run while system row halted=true. Reset
requires explicit operator action: `python3 master_killswitch.py reset --confirm`
or direct PG update. The reset path also logs to circuit_breaker_log so the
audit trail is complete.

CLI:
    python3 master_killswitch.py halt --reason "manual halt"
    python3 master_killswitch.py halt --reason "..." --no-close-positions
    python3 master_killswitch.py halt --reason "..." --source reconciliation_daemon
    python3 master_killswitch.py status
    python3 master_killswitch.py reset --confirm

Module use:
    from master_killswitch import halt
    halt(reason="reconciliation mismatch on AAPL", source="reconciliation_daemon",
         close_positions=True)

Idempotent: running halt twice doesn't double-close or double-cancel. SMS
fires only on the FIRST successful halt of a contiguous halted state; reset
re-arms the SMS for future halts.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import requests
import psycopg2.extras

import trading_core as tc


logger = tc.get_logger("killswitch")


# ─────────────────────────────────────────────────────────────────────────
# Twilio SMS (operator-only, whitelisted)
# ─────────────────────────────────────────────────────────────────────────

def send_sms(message: str) -> bool:
    """Send SMS to the operator's whitelisted number via Twilio. Returns
    True on success, False on any failure (logged). Killswitch must not
    fail if SMS fails — the halt itself is more important."""
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    sender = os.environ.get("TWILIO_FROM_NUMBER")
    recipient = os.environ.get("TWILIO_OPERATOR_NUMBER")
    if not all([sid, token, sender, recipient]):
        logger.warning("Twilio env vars missing — skipping SMS notification")
        return False

    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    try:
        resp = requests.post(
            url, auth=(sid, token),
            data={"From": sender, "To": recipient,
                  "Body": message[:1500]},  # Twilio multi-segment cap
            timeout=10,
        )
        if resp.status_code in (200, 201):
            logger.info(f"SMS sent to operator: {message[:80]}...")
            return True
        logger.warning(f"Twilio API returned {resp.status_code}: {resp.text[:200]}")
        return False
    except requests.RequestException as e:
        logger.warning(f"Twilio request failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────
# Alpaca order/position teardown
# ─────────────────────────────────────────────────────────────────────────

def cancel_all_open_orders() -> dict:
    """Cancel every open order across every Alpaca account we own (default
    legacy account + each per-strategy account). Returns
    {"cancelled": [order_ids...], "failed": [(order_id, error)...]}"""
    cancelled: list[str] = []
    failed: list[tuple[str, str]] = []

    def _cancel_on(label: str, client) -> None:
        try:
            open_orders = client.list_orders(status="open", limit=500)
        except Exception as e:
            logger.error(f"Alpaca[{label}] list_orders failed: {e}")
            failed.append((f"list_orders[{label}]", str(e)))
            return
        for order in open_orders:
            try:
                client.cancel_order(order.id)
                cancelled.append(order.id)
                logger.info(f"Cancelled order {order.id} on {label} "
                            f"({order.symbol} {order.side} {order.qty})")
            except Exception as e:
                failed.append((order.id, str(e)))
                logger.warning(f"Failed to cancel order {order.id} on "
                               f"{label}: {e}")

    try:
        _cancel_on("default", tc.get_alpaca())
    except Exception as e:
        logger.warning(f"Alpaca[default] client unavailable: {e}")
        failed.append(("client[default]", str(e)))

    for sid, client in tc.iter_strategy_alpaca_clients():
        _cancel_on(sid, client)

    return {"cancelled": cancelled, "failed": failed}


def flatten_all_positions() -> dict:
    """Market-sell every open paper_trades row across all strategies.
    Each sell is routed to the strategy's OWN Alpaca account
    (tc.get_strategy_alpaca(strategy_id)) — NEVER the default singleton.
    Routing a flatten to the wrong account opens shorts on the wrong
    account; that bug class (a7ba358 fixed it for place_order) is exactly
    how the -3,920 JPST short on PRIMARY accumulated. Uses
    trading_core.close_trade() so signal/trade linkage stays clean and
    P&L gets recorded. Returns {"closed": [...], "failed": [...]}"""
    closed: list[dict] = []
    failed: list[tuple[int, str]] = []

    with tc.get_pg_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, strategy_id, ticker, qty, entry_price, entry_time, metadata
                FROM paper_trades WHERE status = 'open'
                """
            )
            open_trades = [dict(row) for row in cur.fetchall()]

    if not open_trades:
        logger.info("No open positions to flatten")
        return {"closed": closed, "failed": failed}

    logger.info(f"Flattening {len(open_trades)} open positions...")
    for t in open_trades:
        ticker = t["ticker"]
        qty = int(t["qty"])
        strategy_id = t["strategy_id"]
        try:
            try:
                alpaca = tc.get_strategy_alpaca(strategy_id)
            except Exception as e:
                # No per-strategy broker config — refuse to fall back to
                # the default singleton. Falling back is what created the
                # JPST short. Surface as a failure so operator can flatten
                # by hand on the correct account.
                raise RuntimeError(
                    f"no per-strategy Alpaca client for {strategy_id!r} "
                    f"({e}); refusing to route flatten to default account"
                )

            order = alpaca.submit_order(symbol=ticker, qty=qty, side="sell",
                                        type="market", time_in_force="day")
            # Wait briefly for fill (market orders fill fast during RTH)
            fill_price = None
            for _ in range(5):
                time.sleep(0.5)
                refreshed = alpaca.get_order(order.id)
                if refreshed.filled_avg_price:
                    fill_price = Decimal(str(refreshed.filled_avg_price))
                    break
            if fill_price is None:
                fill_price = Decimal(str(alpaca.get_latest_trade(ticker).price))

            result = tc.close_trade(
                trade_id=t["id"],
                exit_price=fill_price,
                exit_reason=tc.ExitReason.SYSTEM_HALT,
                alpaca_order_id_exit=order.id,
            )
            closed.append({
                "trade_id": t["id"], "ticker": ticker, "qty": qty,
                "account": strategy_id,
                "fill": float(fill_price),
                "pnl_dollar": float(result["pnl_dollar"]),
            })
            logger.info(f"FLATTENED {ticker} {qty}@${fill_price} on "
                        f"{strategy_id} P&L=${result['pnl_dollar']}")
        except Exception as e:
            failed.append((t["id"], str(e)))
            logger.error(f"Failed to flatten trade {t['id']} ({ticker}) on "
                         f"{strategy_id}: {e}")

    return {"closed": closed, "failed": failed}


# ─────────────────────────────────────────────────────────────────────────
# Halt state + breaker logging
# ─────────────────────────────────────────────────────────────────────────

def get_halt_state() -> dict:
    """Returns {halted: bool, halt_reason: str, halt_at: dt, system_row: dict,
    open_positions: int, open_orders: int}"""
    with tc.get_pg_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM trading_strategies WHERE id = 'system'")
            system_row = cur.fetchone()
            cur.execute("SELECT COUNT(*) AS n FROM paper_trades WHERE status='open'")
            open_pos = cur.fetchone()["n"]

    # Sum open orders across every account (default + per-strategy). -1
    # means "we couldn't query any account at all"; partial errors are
    # logged but we return the running total from the accounts we did
    # reach (better than reporting unknown when most of the picture is
    # available).
    open_orders = 0
    any_ok = False
    try:
        open_orders += len(tc.get_alpaca().list_orders(status="open", limit=500))
        any_ok = True
    except Exception as e:
        logger.warning(f"Could not query Alpaca[default] open orders: {e}")
    for sid, client in tc.iter_strategy_alpaca_clients():
        try:
            open_orders += len(client.list_orders(status="open", limit=500))
            any_ok = True
        except Exception as e:
            logger.warning(f"Could not query Alpaca[{sid}] open orders: {e}")
    if not any_ok:
        open_orders = -1  # unknown

    return {
        "halted": bool(system_row["halted"]) if system_row else False,
        "halt_reason": (system_row.get("halt_reason") if system_row else None),
        "halt_at": (system_row.get("halted_at") if system_row else None),
        "system_row": dict(system_row) if system_row else None,
        "open_positions": open_pos,
        "open_orders": open_orders,
    }


def record_killswitch_event(reason: str, source: str, action: str,
                             cancelled_orders: int, flattened_positions: int,
                             failed_count: int) -> int:
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO circuit_breaker_log
                    (breaker_type, strategy_id, action, reason, triggered_by,
                     metadata)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                RETURNING id
                """,
                (tc.BreakerType.SYSTEM_HALT.value, "system", action, reason,
                 source,
                 tc.json_safe({
                     "cancelled_orders": cancelled_orders,
                     "flattened_positions": flattened_positions,
                     "failed_count": failed_count,
                     "triggered_at": datetime.now(timezone.utc).isoformat(),
                 })),
            )
            return cur.fetchone()[0]


# ─────────────────────────────────────────────────────────────────────────
# Public API — halt + reset
# ─────────────────────────────────────────────────────────────────────────

def halt(reason: str, source: str = "manual",
         close_positions: bool = True) -> dict:
    """
    Halt the entire trading framework. Idempotent.

    Args:
        reason: Human-readable cause (free text, recorded in PG + SMS)
        source: Trigger identifier (e.g. "manual", "reconciliation_daemon",
                "circuit_breaker_daily_loss", "horizon_operator_command")
        close_positions: If False, only halt new trading; existing
                positions stay open. Default True (full kill).

    Returns dict with halt summary.
    """
    logger.warning(f"=== KILLSWITCH ENGAGED ({source}): {reason} ===")
    started_at = datetime.now(timezone.utc)

    prior_state = get_halt_state()
    first_halt = not prior_state["halted"]

    # 1. Flip halt flags FIRST so any in-flight strategy cycle aborts on its
    #    next should_run() check before placing new orders.
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE trading_strategies
                SET halted = TRUE,
                    halt_reason = %s,
                    halted_at = COALESCE(halted_at, NOW())
                WHERE halted = FALSE OR halted IS NULL
                """,
                (reason,),
            )
            cur.execute(
                """
                UPDATE trading_strategies
                SET halt_reason = %s,
                    halted_at = COALESCE(halted_at, NOW())
                WHERE id = 'system'
                """,
                (reason,),
            )
            cur.execute(
                """
                UPDATE trading_strategies
                SET halted = TRUE, halt_reason = %s, halted_at = NOW()
                WHERE id = 'system'
                """,
                (reason,),
            )

    # 2. Cancel open orders. Always — even if --no-close-positions, we don't
    #    want pending bracket orders firing against an arrested account.
    cancel_result = cancel_all_open_orders()

    # 3. Optionally flatten positions
    flatten_result = {"closed": [], "failed": []}
    action = "halt_only"
    if close_positions:
        flatten_result = flatten_all_positions()
        action = "halt_and_flatten"

    cancelled_n = len(cancel_result["cancelled"])
    flattened_n = len(flatten_result["closed"])
    failed_n = len(cancel_result["failed"]) + len(flatten_result["failed"])

    # 4. Log circuit-breaker event
    breaker_id = record_killswitch_event(
        reason=reason, source=source, action=action,
        cancelled_orders=cancelled_n,
        flattened_positions=flattened_n,
        failed_count=failed_n,
    )

    # 5. Update heartbeat
    try:
        with tc.get_pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO reconciliation_heartbeat
                        (last_halt_at, last_halt_reason, last_halt_source)
                    VALUES (NOW(), %s, %s)
                    ON CONFLICT (id) DO UPDATE
                    SET last_halt_at = NOW(),
                        last_halt_reason = EXCLUDED.last_halt_reason,
                        last_halt_source = EXCLUDED.last_halt_source
                    """,
                    (reason, source),
                )
    except Exception as e:
        # Heartbeat schema may not yet exist if reconciliation_daemon hasn't shipped
        logger.debug(f"Heartbeat update skipped: {e}")

    # 6. SMS — only on FIRST halt of a contiguous halted state
    duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
    if first_halt:
        sms_body = (
            f"BHN KILLSWITCH ({source})\n"
            f"Reason: {reason[:200]}\n"
            f"Cancelled orders: {cancelled_n}\n"
            f"Flattened positions: {flattened_n}\n"
            f"Failures: {failed_n}\n"
            f"Breaker ID: {breaker_id}\n"
            f"Reset: ssh nj 'python3 /opt/bhn/trading/master_killswitch.py reset --confirm'"
        )
        send_sms(sms_body)
    else:
        logger.info("Halt was already active — skipping duplicate SMS")

    summary = {
        "halted": True,
        "first_halt": first_halt,
        "reason": reason,
        "source": source,
        "action": action,
        "cancelled_orders": cancelled_n,
        "flattened_positions": flattened_n,
        "failed_count": failed_n,
        "breaker_log_id": breaker_id,
        "duration_ms": duration_ms,
    }
    logger.warning(f"Killswitch complete: {summary}")
    return summary


def reset(confirmation_token: bool = False, operator_note: str = "") -> dict:
    """
    Clear the system halt. Requires explicit confirm. Open positions are
    NOT reopened — they were flattened (or left open per halt flag); reset
    just re-arms strategy runs.

    Operator confirmation is required because mistakenly clearing halt
    when the underlying issue (e.g. reconciliation mismatch) is still
    present would re-arm a broken system.
    """
    if not confirmation_token:
        logger.error("Reset called without --confirm — aborting")
        return {"reset": False, "reason": "confirmation_required"}

    state = get_halt_state()
    if not state["halted"]:
        logger.info("System was not halted — reset is a no-op")
        return {"reset": False, "reason": "not_halted"}

    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE trading_strategies
                SET halted = FALSE,
                    halt_reason = NULL,
                    halted_at = NULL
                """
            )

    record_killswitch_event(
        reason=f"reset by operator: {operator_note[:200]}" if operator_note
               else "reset by operator",
        source="manual_reset",
        action="reset",
        cancelled_orders=0, flattened_positions=0, failed_count=0,
    )

    send_sms(
        f"BHN KILLSWITCH RESET\n"
        f"Previous halt reason: {state['halt_reason']}\n"
        f"Operator note: {operator_note or '(none)'}\n"
        f"Strategies re-armed — next cron tick will resume normal operation."
    )

    logger.warning("=== KILLSWITCH RESET (system re-armed) ===")
    return {"reset": True, "previous_reason": state["halt_reason"]}


def status() -> dict:
    s = get_halt_state()
    print("=" * 60)
    print(f"BHN trading framework status — {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)
    print(f"Halted:          {s['halted']}")
    if s["halted"]:
        print(f"Halt reason:     {s['halt_reason']}")
        print(f"Halted at:       {s['halt_at']}")
    print(f"Open positions:  {s['open_positions']}")
    print(f"Open orders:     {s['open_orders']} (-1 = query failed)")
    print("=" * 60)
    # Per-strategy summary
    with tc.get_pg_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, name, halted, capital_allocation, last_run_at
                FROM trading_strategies WHERE id <> 'system'
                ORDER BY id
                """
            )
            for r in cur.fetchall():
                marker = "HALTED" if r["halted"] else "ACTIVE"
                last = r["last_run_at"].isoformat() if r["last_run_at"] else "(never)"
                print(f"  {r['id']:25s} {marker:7s} "
                      f"${r['capital_allocation']:>8} last_run={last}")
    return s


# ─────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="BHN trading framework master killswitch"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    halt_p = sub.add_parser("halt", help="Engage emergency halt")
    halt_p.add_argument("--reason", required=True, help="Reason for halt")
    halt_p.add_argument("--source", default="manual",
                        help="Trigger source identifier")
    halt_p.add_argument("--no-close-positions", action="store_true",
                        help="Halt new trading but leave open positions")

    reset_p = sub.add_parser("reset", help="Clear halt and re-arm strategies")
    reset_p.add_argument("--confirm", action="store_true",
                         help="Required: explicit operator confirmation")
    reset_p.add_argument("--note", default="",
                         help="Optional operator note (logged)")

    sub.add_parser("status", help="Show current halt state")

    args = parser.parse_args()

    if args.cmd == "halt":
        result = halt(reason=args.reason, source=args.source,
                      close_positions=not args.no_close_positions)
        return 0 if result["halted"] else 1
    if args.cmd == "reset":
        result = reset(confirmation_token=args.confirm,
                       operator_note=args.note)
        return 0 if result["reset"] else 1
    if args.cmd == "status":
        status()
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
