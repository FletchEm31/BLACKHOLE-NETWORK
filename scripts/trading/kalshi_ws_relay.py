#!/usr/bin/env python3
"""
Kalshi WebSocket relay -- NJ side (operator decision 2026-07-21: NJ is the
egress/capture point only, LA is the destination; no decision logic here).

Connects to Kalshi's PRODUCTION WebSocket API (public market-data channels
only: ticker, orderbook_delta/snapshot, trade, market_lifecycle_v2) for all
currently-active weather markets, and writes every message to LA's
Postgres over the WireGuard mesh. Prod, not demo -- this project's
established rule is that demo Kalshi is execution-mechanics-testing only,
never a signal/data source (see strat9_demo.env's own scope note).

Auth: NJ holds ONLY the public KALSHI_KEY_ID (no private key ever touches
this host). Before every (re)connect, calls kalshi_ws_signer.py running on
LA (10.8.0.1:8097, mesh-only, IP-allowlisted to this host) for a fresh
RSA-PSS signature over the WS handshake path -- the private key stays on
LA, reusing kalshi_client.py's existing signing code rather than
duplicating it.

Scope: capture only. Does NOT touch weather_bronze_kalshi_market_snapshots
/ weather_silver_market_conformed (the existing REST poller keeps running
exactly as before) or any CP4/trading decision logic -- purely additive
new bronze tables (sql/migrations/2026-07-21-kalshi-ws-bronze-tables.sql).
Wiring this into live trading logic is a deliberately separate future step.

Ticker discovery: reads the existing REST poller's own output (latest
snapshot's distinct active market_tickers from weather_bronze_kalshi_
market_snapshots) rather than re-deriving discovery via a fresh REST call
-- reuse, not re-derive. Refreshed every 15 minutes, and always on
reconnect (subscriptions aren't incrementally patched in this first cut --
simpler to fully resubscribe than implement add_markets/delete_markets
diffing, matching "well-scoped first step" scope).

Orderbook deltas are stored RAW, not reconstructed into live book state --
book reconstruction (cumulative sum per price level) can be built later as
a silver-layer view if ever needed, keeping this collector simple and
stateless per the project's bronze/silver separation convention.

Writes are batched (flush every ~2s or 500 rows, whichever first) rather
than one INSERT per message, since orderbook_delta can be high-frequency
across many simultaneous markets.

Usage:
    python3 kalshi_ws_relay.py

Environment:
    PG_HOST / PG_DB / PG_USER / PG_PASSWORD  (from /etc/bhn-trading/env,
        already configured on NJ as bhn_trader)
    KALSHI_KEY_ID  (public key id only -- no private key needed here)
"""
import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
import websockets

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("kalshi_ws_relay")

SIGNER_URL = "http://10.8.0.1:8097/kalshi-ws-signature"
WS_URL = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
CHANNELS = ("ticker", "orderbook_delta", "trade", "market_lifecycle_v2")
TICKER_REFRESH_SEC = 900   # 15 min -- full reconnect+resubscribe, not incremental
FLUSH_INTERVAL_SEC = 2.0
FLUSH_MAX_ROWS = 500
RECONNECT_BACKOFF_SEC = (2, 5, 10, 20, 30, 60)  # capped exponential-ish backoff


def _prime_env() -> None:
    for path in ("/etc/bhn-trading/env", "/etc/bhn-trading/kalshi-ws-public.env"):
        p = Path(path)
        if not p.is_file():
            continue
        for ln in p.read_text().splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_prime_env()

PG_HOST = os.environ.get("PG_HOST", "10.8.0.1")
PG_DB = os.environ.get("PG_DB", "eventhorizon")
PG_USER = os.environ.get("PG_USER", "bhn_trader")
PG_PASSWORD = os.environ.get("PG_PASSWORD", "")
KALSHI_KEY_ID = os.environ.get("KALSHI_KEY_ID", "").strip()


def _pg_dsn() -> str:
    if PG_PASSWORD:
        import urllib.parse
        return f"postgresql://{PG_USER}:{urllib.parse.quote(PG_PASSWORD)}@{PG_HOST}:5432/{PG_DB}"
    return f"postgresql://{PG_USER}@{PG_HOST}:5432/{PG_DB}"


def get_signature() -> dict:
    """Fetch a fresh signed handshake header set from LA. Never caches --
    Kalshi requires a recent timestamp, and a stale signature would just
    fail the handshake, so always ask fresh right before connecting."""
    with urllib.request.urlopen(SIGNER_URL, timeout=8) as resp:
        return json.loads(resp.read())


def get_active_tickers(conn) -> list[str]:
    """Latest REST-poller snapshot's distinct non-closed weather tickers --
    reuses the existing poller's own discovery, doesn't re-derive it."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT market_ticker
            FROM weather_bronze_kalshi_market_snapshots
            WHERE retrieved_at >= NOW() - INTERVAL '1 hour'
              AND market_status != 'closed'
        """)
        return [r[0] for r in cur.fetchall()]


class BatchWriter:
    """Accumulates rows per target table, flushes on a timer or row-count
    threshold via execute_values -- batched inserts instead of one round-
    trip per WS message."""

    def __init__(self, conn):
        self.conn = conn
        self.buffers: dict[str, list[tuple]] = {
            "ticker": [], "orderbook": [], "trade": [], "lifecycle": [],
        }
        self._lock = asyncio.Lock()

    async def add(self, table: str, row: tuple) -> None:
        async with self._lock:
            self.buffers[table].append(row)
            total = sum(len(v) for v in self.buffers.values())
        if total >= FLUSH_MAX_ROWS:
            await self.flush()

    async def flush(self) -> None:
        async with self._lock:
            pending = {k: v for k, v in self.buffers.items() if v}
            for k in self.buffers:
                self.buffers[k] = []
        if not pending:
            return
        try:
            with self.conn.cursor() as cur:
                if pending.get("ticker"):
                    psycopg2.extras.execute_values(cur, """
                        INSERT INTO weather_bronze_kalshi_ws_ticker
                            (market_ticker, price_dollars, yes_bid_dollars, yes_ask_dollars,
                             volume_fp, open_interest_fp, dollar_volume, dollar_open_interest,
                             yes_bid_size_fp, yes_ask_size_fp, last_trade_size_fp, event_ts_ms)
                        VALUES %s
                    """, pending["ticker"])
                if pending.get("orderbook"):
                    psycopg2.extras.execute_values(cur, """
                        INSERT INTO weather_bronze_kalshi_ws_orderbook
                            (market_ticker, is_snapshot, side, price_dollars, count_or_delta_fp, event_ts_ms)
                        VALUES %s
                    """, pending["orderbook"])
                if pending.get("trade"):
                    psycopg2.extras.execute_values(cur, """
                        INSERT INTO weather_bronze_kalshi_ws_trade
                            (trade_id, market_ticker, yes_price_dollars, no_price_dollars, count_fp, taker_side, event_ts_ms)
                        VALUES %s
                        ON CONFLICT (trade_id) DO NOTHING
                    """, pending["trade"])
                if pending.get("lifecycle"):
                    psycopg2.extras.execute_values(cur, """
                        INSERT INTO weather_bronze_kalshi_ws_lifecycle
                            (market_ticker, event_type, open_ts, close_ts, raw_msg_json)
                        VALUES %s
                    """, pending["lifecycle"])
            self.conn.commit()
            n = sum(len(v) for v in pending.values())
            logger.debug(f"flushed {n} rows")
        except Exception as e:
            logger.warning(f"flush failed, rows dropped: {e}")
            self.conn.rollback()


def handle_message(raw: str, writer_buf: dict) -> None:
    """Parse one WS frame and stage rows into the in-memory buffers dict
    (table_name -> list[tuple]) -- called synchronously from the async
    receive loop, actual DB flush happens separately via BatchWriter."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return
    mtype = msg.get("type")
    body = msg.get("msg") or {}

    if mtype == "ticker":
        writer_buf["ticker"].append((
            body.get("market_ticker"),
            body.get("price_dollars"), body.get("yes_bid_dollars"), body.get("yes_ask_dollars"),
            body.get("volume_fp"), body.get("open_interest_fp"),
            body.get("dollar_volume"), body.get("dollar_open_interest"),
            body.get("yes_bid_size_fp"), body.get("yes_ask_size_fp"), body.get("last_trade_size_fp"),
            body.get("ts_ms"),
        ))
    elif mtype == "orderbook_snapshot":
        ticker = body.get("market_ticker")
        for side, key in (("yes", "yes_dollars_fp"), ("no", "no_dollars_fp")):
            for level in (body.get(key) or []):
                price, count = level[0], level[1]
                writer_buf["orderbook"].append((ticker, True, side, price, count, None))
    elif mtype == "orderbook_delta":
        writer_buf["orderbook"].append((
            body.get("market_ticker"), False, body.get("side"),
            body.get("price_dollars"), body.get("delta_fp"), body.get("ts_ms"),
        ))
    elif mtype == "trade":
        writer_buf["trade"].append((
            body.get("trade_id"), body.get("market_ticker"),
            body.get("yes_price_dollars"), body.get("no_price_dollars"),
            body.get("count_fp"), body.get("taker_side"), body.get("ts_ms"),
        ))
    elif mtype == "market_lifecycle_v2":
        writer_buf["lifecycle"].append((
            body.get("market_ticker"), body.get("event_type"),
            body.get("open_ts"), body.get("close_ts"), json.dumps(body),
        ))
    elif mtype == "error":
        logger.warning(f"Kalshi WS error: {msg}")
    # "subscribed"/"ok"/"unsubscribed" -- no row to write, just logged at debug level.
    else:
        logger.debug(f"unhandled message type: {mtype}")


async def flush_loop(writer: BatchWriter):
    while True:
        await asyncio.sleep(FLUSH_INTERVAL_SEC)
        await writer.flush()


async def run_connection(pg_conn) -> None:
    tickers = get_active_tickers(pg_conn)
    if not tickers:
        logger.warning("no active weather tickers found -- retrying in 60s")
        await asyncio.sleep(60)
        return

    sig = get_signature()
    headers = {
        "KALSHI-ACCESS-KEY": KALSHI_KEY_ID,
        "KALSHI-ACCESS-SIGNATURE": sig["signature"],
        "KALSHI-ACCESS-TIMESTAMP": str(sig["timestamp_ms"]),
    }

    writer = BatchWriter(pg_conn)
    flush_task = asyncio.create_task(flush_loop(writer))
    connected_at = time.monotonic()

    try:
        async with websockets.connect(WS_URL, extra_headers=headers, ping_interval=None) as ws:
            sub_msg = {
                "id": 1, "cmd": "subscribe",
                "params": {
                    "channels": list(CHANNELS),
                    "market_tickers": tickers,
                    "send_initial_snapshot": True,
                },
            }
            await ws.send(json.dumps(sub_msg))
            logger.info(f"subscribed to {len(tickers)} tickers across {CHANNELS}")

            async for raw in ws:
                handle_message(raw, writer.buffers)
                if time.monotonic() - connected_at > TICKER_REFRESH_SEC:
                    logger.info("ticker-refresh window elapsed, reconnecting to resubscribe")
                    break
    finally:
        flush_task.cancel()
        await writer.flush()


async def main() -> None:
    if not KALSHI_KEY_ID:
        raise SystemExit("KALSHI_KEY_ID not set")

    pg_conn = psycopg2.connect(_pg_dsn())
    backoff_idx = 0
    while True:
        try:
            await run_connection(pg_conn)
            backoff_idx = 0  # clean exit (ticker refresh) -- reset backoff
        except (websockets.WebSocketException, ConnectionError,
                urllib.error.URLError, OSError) as e:
            wait = RECONNECT_BACKOFF_SEC[min(backoff_idx, len(RECONNECT_BACKOFF_SEC) - 1)]
            logger.warning(f"connection lost ({e}), reconnecting in {wait}s")
            backoff_idx += 1
            await asyncio.sleep(wait)
        except Exception as e:
            logger.exception(f"unexpected error: {e}")
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
