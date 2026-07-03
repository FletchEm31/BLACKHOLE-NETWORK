#!/usr/bin/env python3
"""BHN — Kalshi DEMO execution-mechanics test harness.

*** DEMO ENVIRONMENT ONLY. NOT A TRADING SCRIPT. NOT A DATA SOURCE. ***

Purpose: prove that the KalshiClient code paths that will eventually place
real orders (auth, order placement, cancellation, fill-tracking, position
reads) actually work correctly, using Kalshi's demo environment and a
demo-only API key. This is execution-mechanics testing exclusively.

Explicitly NOT in scope, and never touched by this script:
  - CP1-4 signal generation, edge calculation, or Kelly sizing
  - prediction_signal.py / cp4_kelly_sizer.py / core_trading_orchestrator.py
  - Anything reading demo market prices as if they were real market data.
    Per Kalshi's own docs, demo market pricing/behavior is not reflective
    of real markets and must never inform CP1-4's edge/probability logic.

This script is intentionally standalone. It imports KalshiClient directly
and constructs it with env='demo' explicitly — it does not import or call
anything from the production signal/sizing modules.

Requires strat9_demo.env sourced first (KALSHI_ENV=demo, KALSHI_KEY_ID,
KALSHI_PRIVATE_KEY_PATH, KALSHI_DEMO_HOST), plus the base env for logging.

Usage:
    python3 demo_execution_test.py --ticker KXHIGHMIA-26JUL02-T94
"""
import argparse
import sys
import time

from kalshi_client import KalshiClient, KalshiAPIError


def _line(msg: str) -> None:
    print(f"[demo-execution-test] {msg}")


def test_auth(client: KalshiClient) -> None:
    _line("=== Auth check ===")
    balance = client.get("/portfolio/balance")
    cents = balance.get("balance", 0)
    _line(f"balance: ${cents / 100:.2f} (raw cents={cents})")


def test_cancellation_flow(client: KalshiClient, ticker: str) -> None:
    """Place a deliberately unfillable order, confirm it rests, cancel it,
    confirm it's gone. Proves the place -> observe -> cancel path."""
    _line("=== Cancellation flow ===")
    order = client.place_order(
        ticker=ticker, side="yes", count=1, price=1,
        order_type="limit", action="buy",
        client_order_id=f"bhn-demo-cancel-test-{int(time.time())}",
    )
    order_id = order.get("order", {}).get("order_id") or order.get("order_id")
    _line(f"placed unfillable order (yes @ 1c): order_id={order_id}")

    time.sleep(2)  # brief propagation delay before the order appears in GET /portfolio/orders

    # Filtering client-side rather than via get_orders(status=...) — that
    # server-side filter came back empty even for a genuinely resting order
    # during this harness's own testing (2026-07-02), a separate minor V2
    # surface issue unrelated to the create/cancel fix this harness exists
    # to prove. Not chased further here; flagged in the docs as a follow-up.
    orders = client.get_orders()
    resting_ids = [o.get("order_id") for o in orders.get("orders", [])
                   if o.get("status") == "resting"]
    _line(f"resting orders after placement: {resting_ids}")
    assert order_id in resting_ids, "order not found resting — cannot verify cancellation path"

    client.cancel_order(order_id)
    _line(f"cancel_order({order_id}) called")

    time.sleep(2)  # brief propagation delay before the cancel is reflected in GET /portfolio/orders

    orders_after = client.get_orders()
    resting_ids_after = [o.get("order_id") for o in orders_after.get("orders", [])
                          if o.get("status") == "resting"]
    _line(f"resting orders after cancel: {resting_ids_after}")
    assert order_id not in resting_ids_after, "order still resting after cancel — cancellation did not take effect"
    _line("cancellation flow: PASS")


def test_fill_flow(client: KalshiClient, ticker: str) -> None:
    """Place an order that crosses the existing book (should fill
    immediately), then confirm it shows up in fills and positions.
    Proves the place -> fill -> position/fill-tracking path."""
    _line("=== Fill-tracking flow ===")
    book = client.get_orderbook(ticker).get("orderbook_fp", {})
    no_bids = book.get("no_dollars") or []
    if not no_bids:
        _line("no resting 'no' bids on this ticker right now — skipping fill test "
              "(pick a different --ticker with visible book depth)")
        return

    best_no_bid_price = float(no_bids[0][0])
    cross_price_cents = max(1, min(99, round(best_no_bid_price * 100)))
    _line(f"best resting no_bid=${best_no_bid_price:.2f}; "
          f"selling 'no' at {cross_price_cents}c to cross it")

    order = client.place_order(
        ticker=ticker, side="no", count=1, price=cross_price_cents,
        order_type="limit", action="sell",
        client_order_id=f"bhn-demo-fill-test-{int(time.time())}",
    )
    order_id = order.get("order", {}).get("order_id") or order.get("order_id")
    _line(f"placed crossing order (no sell @ {cross_price_cents}c): order_id={order_id}")

    time.sleep(2)  # give the match engine a moment

    fills = client.list_fills(ticker=ticker, limit=10)
    matching_fills = [f for f in fills.get("fills", []) if f.get("order_id") == order_id]
    _line(f"fills matching order_id={order_id}: {len(matching_fills)}")
    for f in matching_fills:
        _line(f"  fill: {f}")

    positions = client.get_positions(limit=50)
    ticker_positions = [p for p in positions.get("market_positions", [])
                        if p.get("ticker") == ticker]
    _line(f"positions on {ticker}: {ticker_positions}")

    if matching_fills:
        _line("fill-tracking flow: PASS (order filled and appeared in /portfolio/fills)")
    else:
        _line("fill-tracking flow: NO FILL OBSERVED — order may be resting instead "
              "(book may have moved). Check get_orders(status='resting') manually.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", required=True,
                         help="Real, currently-open demo market ticker to test against")
    args = parser.parse_args()

    client = KalshiClient(env="demo")
    if client._base != "https://external-api.demo.kalshi.co" and \
       "demo" not in client._base:
        _line(f"WARNING: client base is {client._base!r} — does not look like a demo host. Aborting.")
        return 1
    _line(f"using demo host: {client._base}")

    try:
        test_auth(client)
        test_cancellation_flow(client, args.ticker)
        test_fill_flow(client, args.ticker)
    except KalshiAPIError as e:
        _line(f"KalshiAPIError: {e}")
        return 1
    except AssertionError as e:
        _line(f"ASSERTION FAILED: {e}")
        return 1

    _line("=== All execution-mechanics tests completed ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
