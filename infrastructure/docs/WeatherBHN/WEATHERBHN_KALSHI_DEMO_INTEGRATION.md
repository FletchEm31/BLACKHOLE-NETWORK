# WeatherBHN — Kalshi Demo API Integration

**Status:** execution-mechanics testing only. Not wired into any signal or
sizing logic. Not a green light for live trading.

## What this is for

Fletch has a funded Kalshi demo account. This integration exists to prove
that the code paths which will *eventually* place real orders — auth,
order placement, cancellation, fill-tracking, position reads — actually
work correctly, before any of them are ever exercised with real money.

That's it. Execution mechanics only.

## What this is explicitly NOT for

**Demo market data must never be used as a market-data source for CP1-4's
signal generation or edge calculation.** Per Kalshi's own documentation
([Test in the Demo Environment](https://docs.kalshi.com/concepts/test-in-the-demo-environment)):

> "The price and behavior of markets in the demo environment may not be
> reflective of those in real markets."

This is Kalshi's own stated position, not an inference — demo pricing is
officially unreliable for strategy validation. Production market data
(`weather_bronze_kalshi_market_snapshots`, fed by the live production
Kalshi key) remains the only source CP1-4 ever learns from or calculates
edge against, even while `strat_9_prediction_alpha.enabled=false` in
`/etc/bhn-trading/rules.json`.

## Components

| Piece | Location | Notes |
|---|---|---|
| Demo private key | `/etc/bhn-trading/kalshi_demo_private.pem` | chmod 600, root-owned. Separate from the production key at `/etc/bhn-trading/kalshi_private.pem` — never touched. |
| Demo API Key ID | `0beb858e-2c79-41d8-b468-fb40bc0bdb70` | Registered on Kalshi's demo account, 2026-07-02. |
| Demo env config | `/etc/bhn-trading/strat9_demo.env` | `KALSHI_ENV=demo`, `KALSHI_DEMO_HOST`, `KALSHI_KEY_ID`, `KALSHI_PRIVATE_KEY_PATH`. Separate file — production `strat9.env` is never modified. |
| Demo host override | `KALSHI_DEMO_HOST` env var, `kalshi_client.py` | Overrides the client's default `DEMO_HOST` constant (left untouched) to point at Kalshi's currently-recommended demo endpoint (`external-api.demo.kalshi.co`) rather than the legacy one. |
| Execution test harness | `scripts/trading/demo_execution_test.py` | Standalone script. Imports `KalshiClient` directly, constructs it with `env='demo'` explicitly. Never imports `prediction_signal.py`, `cp4_kelly_sizer.py`, or `core_trading_orchestrator.py`. |

## Running the smoke test

```bash
cd /opt/bhn/trading
set -a; source /etc/bhn-trading/env; source /etc/bhn-trading/strat9_demo.env; set +a
python3 kalshi_client.py balance
python3 kalshi_client.py markets --series KXHIGHMIA --status open
python3 demo_execution_test.py --ticker <a real, currently-open demo ticker>
```

`demo_execution_test.py` exercises two flows against real demo orders:
1. **Cancellation flow** — places a deliberately unfillable limit order (1c),
   confirms it rests, cancels it, confirms it's gone.
2. **Fill-tracking flow** — places an order that crosses the existing book,
   confirms it appears in `/portfolio/fills` and the resulting position.

Last verified run (2026-07-02): both flows passed against a real demo
order (Miami, `KXHIGHMIA-26JUL02-T94`) — real fill at $0.25, correct
resulting position and fee accounting.

## Kalshi order API V1→V2 migration (found via this harness)

Building this harness caught a real, production-relevant bug: Kalshi's
order-creation endpoint (`POST /portfolio/orders`) is deprecated and
returns HTTP 410 as of 2026 (stated sunset 2026-05-06) — this affects
`kalshi_client.py`'s `place_order()`/`cancel_order()` for **both** demo
and any eventual production use, not a demo-only issue. Fixed in place:

| | Old (broken, HTTP 410) | V2 (current) |
|---|---|---|
| Create | `POST /portfolio/orders` | `POST /portfolio/events/orders` |
| Cancel | `DELETE /portfolio/orders/{id}` | `DELETE /portfolio/events/orders/{id}` |
| Side model | `side: yes/no` + `action: buy/sell` | single-book `side: bid/ask`, quoted from the YES leg only |
| Count/price | integers, price in cents | fixed-point strings, price in dollars |
| New required field | — | `self_trade_prevention_type` (defaults to `taker_at_cross`) |

`place_order()`'s external signature (`side='yes'/'no'`, `action='buy'/'sell'`,
price in cents) is unchanged — the V2 translation happens entirely inside
the method, so no caller (`prediction_signal.py`, `weather_position_monitor.py`)
needed to change.

**Known follow-up, not chased further here:** `get_orders(status=...)`'s
server-side status filter returned empty results even for a genuinely
resting order during this harness's testing — a separate, minor V2
surface issue. Worked around in the harness by fetching all orders and
filtering client-side on the `status` field. Worth a proper fix later if
`get_orders(status=...)` is ever relied on elsewhere.

**Market orders are not implemented** — `place_order(order_type='market')`
raises `NotImplementedError`. Nothing in the codebase currently uses
market orders (`prediction_signal.py` and `weather_position_monitor.py`
both always pass `order_type='limit'`), and Kalshi's V2 create-order
schema has no documented field distinguishing limit/market order types —
that mechanism hasn't been verified and shouldn't be guessed at in code
that will eventually touch real money.
