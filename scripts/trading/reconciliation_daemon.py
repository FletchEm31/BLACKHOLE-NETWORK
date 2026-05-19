#!/usr/bin/env python3
"""
reconciliation_daemon.py — BHN trading 3-way state reconciliation.

Runs every 5 min (configurable). Compares position state across three
independent sources:

  1. ALPACA       — live broker truth (get_positions API)
  2. LA PG        — canonical state (paper_trades WHERE status='open')
  3. NJ CACHE     — local SQLite mirror at /var/lib/bhn/trading/state.sqlite
                    written by trading_core.open_trade/close_trade as a
                    dual-write alongside LA PG INSERT

If any pair-wise comparison fails, the daemon flat-halts the entire
trading framework via master_killswitch.halt(). No severity tiers, no
recovery heuristics — a divergence between any two sources means SOMETHING
is wrong and operator must investigate before re-arming.

Per-cycle:
  - Acquire PG advisory lock (separate key from strategy locks) — blocks
    strategy place_order/close_trade for the duration of the compare
  - Fetch all 3 sources in parallel
  - Compare set membership (tickers) + per-ticker (qty, avg_entry_price)
  - On mismatch: write incident metadata, call killswitch.halt(reason=...,
    source='reconciliation_daemon'), exit with non-zero
  - Always: write reconciliation_heartbeat row (ok / mismatch / error)
  - Release lock

Run modes:
  python3 reconciliation_daemon.py --once          # single cycle, exit
  python3 reconciliation_daemon.py                 # loop, 5min interval
  python3 reconciliation_daemon.py --interval 60   # loop, custom interval
  python3 reconciliation_daemon.py --dry-run       # detect mismatches but
                                                    # do NOT halt (operator
                                                    # debug only — logs SMS
                                                    # body to stdout instead)

Deploy: systemd-units/bhn-reconciliation.service runs `--once` from a
5min timer. This keeps each cycle independent — if the daemon process
dies mid-cycle, the next timer fire restarts cleanly. Loop mode is for
operator debug / manual sessions only.

Cache file requirement: trading_core.py must dual-write open/close events
to /var/lib/bhn/trading/state.sqlite using the schema defined in
ensure_cache_schema() below. The daemon creates the schema if missing —
but only reads, never writes (writes are the strategies' responsibility).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import psycopg2.extras

import trading_core as tc
import master_killswitch


logger = tc.get_logger("reconciliation_daemon")

# Distinct advisory lock key — doesn't collide with strategy locks (which
# use abs(hash(strategy_id)) % 2^31).
RECONCILE_LOCK_KEY = 0x7E_C0_DE_DE  # 2,126,389,470 → fits in int32

NJ_CACHE_PATH = os.environ.get("BHN_NJ_CACHE_PATH",
                                "/var/lib/bhn/trading/state.sqlite")

# Tolerance for avg_entry_price comparison (cents). Alpaca uses 4 decimal
# place precision; PG uses NUMERIC; the cache stores TEXT-encoded Decimal.
# Anything tighter than $0.005 is precision noise, not a real divergence.
PRICE_TOLERANCE = Decimal("0.005")


# ─────────────────────────────────────────────────────────────────────────
# Mismatch dataclass
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class Mismatch:
    source_a: str
    source_b: str
    ticker: str
    field: str
    value_a: Optional[str]
    value_b: Optional[str]

    def as_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        return (f"{self.ticker}.{self.field}: "
                f"{self.source_a}={self.value_a} vs {self.source_b}={self.value_b}")


# ─────────────────────────────────────────────────────────────────────────
# NJ local cache (SQLite at /var/lib/bhn/trading/state.sqlite)
# ─────────────────────────────────────────────────────────────────────────

def ensure_cache_schema() -> None:
    """Idempotent schema bootstrap. Strategies write here via trading_core;
    daemon only reads. Schema published here so trading_core can mirror it."""
    parent = os.path.dirname(NJ_CACHE_PATH)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as e:
            logger.warning(f"Cache directory unavailable ({parent}): {e}")
    with sqlite3.connect(NJ_CACHE_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                ticker          TEXT PRIMARY KEY,
                qty             INTEGER NOT NULL,
                avg_entry_price TEXT NOT NULL,         -- Decimal as str
                strategy_id     TEXT NOT NULL,
                trade_id        INTEGER,                -- LA PG paper_trades.id
                opened_at       TEXT NOT NULL,          -- ISO timestamp
                last_synced_at  TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS positions_strategy_idx
            ON positions (strategy_id)
        """)


def fetch_nj_cache_state() -> dict[str, dict]:
    """Read all open positions from NJ local SQLite. Returns
    {ticker: {qty, avg_entry_price, strategy_id, trade_id, opened_at}}.
    Empty dict if cache file missing — but logs a warning since absent
    cache during reconciliation is itself suspicious."""
    if not os.path.exists(NJ_CACHE_PATH):
        logger.warning(f"NJ cache file missing: {NJ_CACHE_PATH}")
        return {}
    with sqlite3.connect(NJ_CACHE_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT ticker, qty, avg_entry_price, strategy_id, trade_id, opened_at "
            "FROM positions"
        ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        out[r["ticker"]] = {
            "ticker": r["ticker"],
            "qty": int(r["qty"]),
            "avg_entry_price": Decimal(r["avg_entry_price"]),
            "strategy_id": r["strategy_id"],
            "trade_id": r["trade_id"],
            "opened_at": r["opened_at"],
        }
    return out


# ─────────────────────────────────────────────────────────────────────────
# State fetchers — Alpaca + LA PG
# ─────────────────────────────────────────────────────────────────────────

def fetch_alpaca_state() -> dict[str, dict]:
    """Returns {ticker: {qty, avg_entry_price}} aggregated across ALL
    Alpaca accounts the framework owns (every per-strategy account plus
    the legacy/default ALPACA_API_KEY account).

    Why aggregate: PG (the compare-target) aggregates open paper_trades by
    ticker across all strategies, since multiple strategies CAN legitimately
    hold the same ticker. Matching that shape on the Alpaca side means
    summing each ticker's qty across every account we look at.

    Account labels (which account a position came from) are preserved in
    a per-ticker `accounts` list inside the value dict — useful for
    diagnostics and for spotting orphan positions on the default account
    (which should be empty under multi-account routing).
    """
    out: dict[str, dict] = {}

    def _merge(ticker: str, qty: int, avg: Decimal, account: str) -> None:
        if ticker in out:
            prev = out[ticker]
            total_qty = prev["qty"] + qty
            if total_qty == 0:
                weighted = prev["avg_entry_price"]
            else:
                weighted = (
                    (prev["avg_entry_price"] * prev["qty"] + avg * qty)
                    / total_qty
                )
            prev["qty"] = total_qty
            prev["avg_entry_price"] = weighted
            prev["accounts"].append(account)
        else:
            out[ticker] = {
                "ticker": ticker, "qty": qty,
                "avg_entry_price": avg,
                "accounts": [account],
            }

    # Default / legacy account first — anything here under multi-account
    # routing is misrouted, but the daemon's job is to surface state
    # truthfully and let the comparison flag the divergence vs PG.
    saw_any = False
    try:
        for p in tc.get_alpaca().list_positions():
            saw_any = True
            _merge(p.symbol, int(p.qty),
                   Decimal(str(p.avg_entry_price)), "default")
    except Exception as e:
        logger.warning(f"Alpaca[default] list_positions failed: {e}")

    # Per-strategy accounts
    any_strategy_ok = False
    for sid, client in tc.iter_strategy_alpaca_clients():
        try:
            positions = client.list_positions()
        except Exception as e:
            logger.warning(f"Alpaca[{sid}] list_positions failed: {e}")
            continue
        any_strategy_ok = True
        for p in positions:
            saw_any = True
            _merge(p.symbol, int(p.qty),
                   Decimal(str(p.avg_entry_price)), sid)

    # If neither default nor any strategy could be queried, the daemon
    # can't make a trustworthy compare — propagate so run_once records
    # an 'error' heartbeat instead of falsely reporting "all clean".
    if not any_strategy_ok and not saw_any:
        # Distinguish "all accounts reachable but empty" from "no account
        # was queryable at all" by re-checking the default — if even the
        # default raised, we have nothing.
        try:
            tc.get_alpaca().get_account()
        except Exception as e:
            raise RuntimeError(
                f"fetch_alpaca_state: no Alpaca account reachable "
                f"(default error: {e})"
            )

    return out


def fetch_pg_state() -> dict[str, dict]:
    """Returns {ticker: {qty, avg_entry_price, strategy_id, trade_id}}.
    Aggregates multiple open paper_trades rows for the same ticker by
    qty-weighted avg entry price (multiple strategies can hold the same
    ticker, and Alpaca only sees the net position)."""
    out: dict[str, dict] = {}
    with tc.get_pg_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, strategy_id, ticker, qty, entry_price
                FROM paper_trades WHERE status='open'
                """
            )
            for r in cur.fetchall():
                ticker = r["ticker"]
                qty = int(r["qty"])
                price = Decimal(str(r["entry_price"]))
                if ticker in out:
                    prev = out[ticker]
                    total_qty = prev["qty"] + qty
                    weighted = (prev["avg_entry_price"] * prev["qty"]
                                + price * qty) / total_qty
                    out[ticker] = {
                        "ticker": ticker, "qty": total_qty,
                        "avg_entry_price": weighted,
                        "strategy_ids": prev["strategy_ids"] + [r["strategy_id"]],
                        "trade_ids": prev["trade_ids"] + [r["id"]],
                    }
                else:
                    out[ticker] = {
                        "ticker": ticker, "qty": qty,
                        "avg_entry_price": price,
                        "strategy_ids": [r["strategy_id"]],
                        "trade_ids": [r["id"]],
                    }
    return out


# ─────────────────────────────────────────────────────────────────────────
# Comparison
# ─────────────────────────────────────────────────────────────────────────

def compare_pair(name_a: str, state_a: dict, name_b: str,
                 state_b: dict) -> list[Mismatch]:
    """Pair-wise comparison: tickers held, per-ticker qty, per-ticker
    avg_entry_price within PRICE_TOLERANCE."""
    mismatches: list[Mismatch] = []
    tickers_a = set(state_a.keys())
    tickers_b = set(state_b.keys())

    for ticker in tickers_a - tickers_b:
        mismatches.append(Mismatch(
            source_a=name_a, source_b=name_b, ticker=ticker,
            field="exists", value_a="present", value_b="absent"))
    for ticker in tickers_b - tickers_a:
        mismatches.append(Mismatch(
            source_a=name_a, source_b=name_b, ticker=ticker,
            field="exists", value_a="absent", value_b="present"))

    for ticker in tickers_a & tickers_b:
        a = state_a[ticker]
        b = state_b[ticker]
        if a["qty"] != b["qty"]:
            mismatches.append(Mismatch(
                source_a=name_a, source_b=name_b, ticker=ticker,
                field="qty", value_a=str(a["qty"]), value_b=str(b["qty"])))
        price_diff = abs(a["avg_entry_price"] - b["avg_entry_price"])
        if price_diff > PRICE_TOLERANCE:
            mismatches.append(Mismatch(
                source_a=name_a, source_b=name_b, ticker=ticker,
                field="avg_entry_price",
                value_a=f"{a['avg_entry_price']:.4f}",
                value_b=f"{b['avg_entry_price']:.4f}"))
    return mismatches


def compare_three_sources(alpaca: dict, pg: dict, nj: dict) -> list[Mismatch]:
    """Run all 3 pair-wise comparisons. Returns the union — flat list of
    every mismatch across pairs (no dedup; same divergence may show up in
    two pairs and that's useful diagnostic info)."""
    out: list[Mismatch] = []
    out.extend(compare_pair("alpaca", alpaca, "pg", pg))
    out.extend(compare_pair("alpaca", alpaca, "nj_cache", nj))
    out.extend(compare_pair("pg", pg, "nj_cache", nj))
    return out


# ─────────────────────────────────────────────────────────────────────────
# Heartbeat
# ─────────────────────────────────────────────────────────────────────────

def record_heartbeat(status: str, mismatches: list[Mismatch],
                     duration_ms: int, source_counts: dict) -> None:
    """status ∈ {'ok', 'mismatch', 'error'}."""
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO reconciliation_heartbeat
                    (last_run_at, status, mismatch_count, duration_ms, metadata)
                VALUES (NOW(), %s, %s, %s, %s::jsonb)
                ON CONFLICT (id) DO UPDATE
                SET last_run_at = NOW(),
                    status = EXCLUDED.status,
                    mismatch_count = EXCLUDED.mismatch_count,
                    duration_ms = EXCLUDED.duration_ms,
                    metadata = EXCLUDED.metadata
                """,
                (status, len(mismatches), duration_ms,
                 json.dumps({
                     "source_counts": source_counts,
                     "mismatches": [m.as_dict() for m in mismatches[:50]],
                 })),
            )


# ─────────────────────────────────────────────────────────────────────────
# Cycle
# ─────────────────────────────────────────────────────────────────────────

def run_once(dry_run: bool = False) -> dict:
    """Returns {status, mismatches_count, duration_ms, sources}."""
    started = datetime.now(timezone.utc)
    logger.info("=== reconciliation cycle start ===")

    ensure_cache_schema()

    with tc.pg_advisory_lock(RECONCILE_LOCK_KEY):
        try:
            alpaca_state = fetch_alpaca_state()
            pg_state = fetch_pg_state()
            nj_state = fetch_nj_cache_state()
        except Exception as e:
            logger.exception("State fetch failed")
            duration_ms = int((datetime.now(timezone.utc) - started)
                              .total_seconds() * 1000)
            try:
                record_heartbeat("error", [], duration_ms,
                                 source_counts={"error": str(e)[:200]})
            except Exception:
                pass
            return {"status": "error", "error": str(e),
                    "duration_ms": duration_ms}

        source_counts = {
            "alpaca": len(alpaca_state),
            "pg": len(pg_state),
            "nj_cache": len(nj_state),
        }
        logger.info(f"Source counts: {source_counts}")

        mismatches = compare_three_sources(alpaca_state, pg_state, nj_state)
        duration_ms = int((datetime.now(timezone.utc) - started)
                          .total_seconds() * 1000)

        if not mismatches:
            logger.info("All 3 sources agree ✓")
            record_heartbeat("ok", [], duration_ms, source_counts)
            return {"status": "ok", "mismatches": 0,
                    "duration_ms": duration_ms, "sources": source_counts}

        # MISMATCH PATH
        for m in mismatches:
            logger.error(f"MISMATCH: {m}")
        record_heartbeat("mismatch", mismatches, duration_ms, source_counts)

        reason = f"reconciliation: {len(mismatches)} divergence(s) — " + \
                 "; ".join(str(m) for m in mismatches[:3])
        if len(mismatches) > 3:
            reason += f" (+{len(mismatches)-3} more)"

        if dry_run:
            logger.warning(f"DRY RUN — would halt with reason: {reason}")
            return {"status": "mismatch_dry_run",
                    "mismatches": len(mismatches),
                    "duration_ms": duration_ms,
                    "sources": source_counts,
                    "would_halt_reason": reason,
                    "mismatch_details": [m.as_dict() for m in mismatches]}

        logger.critical(f"Halting framework: {reason}")
        try:
            master_killswitch.halt(
                reason=reason[:1000],
                source="reconciliation_daemon",
                close_positions=True,
            )
        except Exception as e:
            logger.exception(f"Killswitch invocation FAILED: {e}")
            return {"status": "halt_failed", "error": str(e),
                    "mismatches": len(mismatches),
                    "duration_ms": duration_ms,
                    "sources": source_counts}

        return {"status": "halted",
                "mismatches": len(mismatches),
                "duration_ms": duration_ms,
                "sources": source_counts,
                "mismatch_details": [m.as_dict() for m in mismatches]}


# ─────────────────────────────────────────────────────────────────────────
# Loop runner
# ─────────────────────────────────────────────────────────────────────────

def run_loop(interval: int, dry_run: bool) -> int:
    """Loop mode: run_once() every interval seconds. Continues even after
    'halted' status because if killswitch fired the halt flag is now set
    and strategies will refuse to run; the daemon's job is to keep
    detecting and re-confirming, not to give up. Halt is sticky and only
    operator reset clears it."""
    logger.info(f"Loop mode: every {interval}s, dry_run={dry_run}")
    while True:
        try:
            result = run_once(dry_run=dry_run)
            logger.info(f"Cycle: {result['status']} "
                        f"({result.get('mismatches', 0)} mismatches, "
                        f"{result.get('duration_ms', 0)}ms)")
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt — exiting loop")
            return 0
        except Exception:
            logger.exception("Cycle threw uncaught exception")
        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="BHN reconciliation daemon (3-way state check)"
    )
    parser.add_argument("--once", action="store_true",
                        help="Run a single cycle and exit (systemd timer mode)")
    parser.add_argument("--interval", type=int, default=300,
                        help="Loop interval seconds (default 300 = 5min)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Detect mismatches but skip the halt action")
    args = parser.parse_args()

    if args.once:
        result = run_once(dry_run=args.dry_run)
        print(json.dumps(result, default=str, indent=2))
        return 0 if result["status"] in ("ok", "mismatch_dry_run") else 1

    return run_loop(args.interval, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
