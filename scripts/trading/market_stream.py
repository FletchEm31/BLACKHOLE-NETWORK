#!/usr/bin/env python3
"""
market_stream.py — always-on Alpaca WebSocket daemon (NJ).

Maintains TWO concurrent WebSocket connections:
  1. Market-data stream  (wss://stream.data.alpaca.markets/v2/{feed})
       - Subscribed to: bars on full watchlist
                        trades + quotes on open-position symbols only
  2. Trading-updates stream  (wss://paper-api.alpaca.markets/stream)
       - Subscribed to: trade_updates (order lifecycle events)

Persistence policy (operator addendum, storage-bounded):
  - 1-min bars: every bar event → market_bars (UPSERT, partition '1Min')
  - Trades/quotes (ticks): persisted ONLY if symbol in open positions →
                           market_ticks (48hr rolling purge via eh-purge)
  - Order events: every event → order_events (forever, audit)

Auto-reconnect: exponential backoff capped at 30s. Reconnect within 5s
on clean disconnects.

Health: in-memory dict latest[symbol] = {'price', 'ts'} surfaced for
strategy consumers via a future shared-memory IPC. v1 = in-process only;
strategies that need live data can be loaded as plugins or read recent
bars from PG.

Run via systemd: scripts/trading/systemd-units/bhn-market-stream.service
(provided separately).

Env (/etc/bhn-trading/env):
  BHN_TRADING_PG_DSN, ALPACA_API_KEY, ALPACA_API_SECRET, ALPACA_BASE_URL
  ALPACA_RATE_LIMIT=150         # respected by REST callers; WebSocket is push-based and exempt
  ALPACA_STREAM_FEED=iex        # 'iex' for free, 'sip' for paid

Reqs: pip install alpaca-py websockets psycopg2-binary
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import signal
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
import psycopg2.pool
import websockets

ENV_FILE = '/etc/bhn-trading/env'
RECONNECT_BACKOFF_INITIAL = 1.0
RECONNECT_BACKOFF_MAX = 30.0
WATCHLIST_REFRESH_SEC = 300       # re-read rules.json + open positions every 5 min
HEARTBEAT_LOG_SEC = 60

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    stream=sys.stderr)
log = logging.getLogger('market_stream')


def load_env(p):
    out = {}
    if not Path(p).is_file(): return out
    for ln in Path(p).read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith('#') or '=' not in ln: continue
        k, v = ln.split('=', 1); out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def watchlist_from_rules(p: Path) -> list[str]:
    if not p.exists(): return []
    rules = json.loads(p.read_text())
    syms: set[str] = set()
    for k in ('strat_2_value', 'strat_3_mean_reversion', 'strat_4_momentum'):
        for s in (rules.get(k, {}).get('universe') or []):
            syms.add(s.upper())
    return sorted(syms)


class State:
    """Shared mutable state across coroutines."""
    def __init__(self, env: dict):
        self.env = env
        self.dsn = env.get('BHN_TRADING_PG_DSN', '')
        self.key = env.get('ALPACA_API_KEY', '')
        self.sec = env.get('ALPACA_API_SECRET', '')
        self.feed = env.get('ALPACA_STREAM_FEED', 'iex')
        self.rules_path = Path(env.get('BHN_TRADING_RULES_PATH', '/opt/bhn/trading/rules.json'))
        self.watchlist: list[str] = []
        self.open_positions: set[str] = set()
        self.latest: dict[str, dict] = {}      # in-memory cache: symbol -> {price, ts}
        self.shutdown = asyncio.Event()
        self.pg_pool = None
        if self.dsn:
            self.pg_pool = psycopg2.pool.SimpleConnectionPool(1, 4, self.dsn)

    def pg(self):
        return self.pg_pool.getconn()

    def put_pg(self, conn):
        self.pg_pool.putconn(conn)


async def refresh_subscriptions(state: State):
    """Periodically reread rules.json + open positions; adjust strategy stream subs if changed."""
    last_watchlist: list[str] = []
    last_open: set[str] = set()
    while not state.shutdown.is_set():
        state.watchlist = watchlist_from_rules(state.rules_path)
        # Open positions from PG
        try:
            conn = state.pg()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT DISTINCT ticker FROM paper_trades WHERE status='open'")
                    state.open_positions = {r[0] for r in cur.fetchall()}
            finally:
                state.put_pg(conn)
        except Exception as e:
            log.warning(f"refresh_subscriptions PG read failed: {e}")
        if state.watchlist != last_watchlist or state.open_positions != last_open:
            log.info(f"subscriptions changed — watchlist={len(state.watchlist)} open_positions={sorted(state.open_positions)}")
            last_watchlist = list(state.watchlist)
            last_open = set(state.open_positions)
        try:
            await asyncio.wait_for(state.shutdown.wait(), timeout=WATCHLIST_REFRESH_SEC)
        except asyncio.TimeoutError:
            pass


async def market_data_stream(state: State):
    """Connect, authenticate, subscribe, and loop on messages.
    Reconnects on disconnect with exponential backoff."""
    url = f"wss://stream.data.alpaca.markets/v2/{state.feed}"
    backoff = RECONNECT_BACKOFF_INITIAL
    while not state.shutdown.is_set():
        try:
            log.info(f"market_data: connecting to {url}")
            async with websockets.connect(url, ping_interval=15, ping_timeout=10) as ws:
                # 1. auth
                await ws.send(json.dumps({"action": "auth", "key": state.key, "secret": state.sec}))
                resp = json.loads(await ws.recv())
                log.info(f"market_data: auth response {resp}")
                # 2. subscribe
                sub_msg = {"action": "subscribe", "bars": state.watchlist or ['*']}
                if state.open_positions:
                    sub_msg["trades"] = list(state.open_positions)
                    sub_msg["quotes"] = list(state.open_positions)
                await ws.send(json.dumps(sub_msg))
                log.info(f"market_data: subscribed bars={len(state.watchlist)} trades+quotes={len(state.open_positions)}")
                backoff = RECONNECT_BACKOFF_INITIAL   # reset on successful connect

                last_heartbeat = time.monotonic()
                async for raw in ws:
                    msgs = json.loads(raw)
                    if not isinstance(msgs, list): msgs = [msgs]
                    handle_market_messages(state, msgs)
                    if time.monotonic() - last_heartbeat > HEARTBEAT_LOG_SEC:
                        log.info(f"market_data: alive, latest cache size={len(state.latest)}")
                        last_heartbeat = time.monotonic()
        except Exception as e:
            log.warning(f"market_data: disconnected ({e}); reconnecting in {backoff:.1f}s")
            try: await asyncio.wait_for(state.shutdown.wait(), timeout=backoff)
            except asyncio.TimeoutError: pass
            backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)


def handle_market_messages(state: State, msgs: list):
    """Process a batch of market-data messages: persist bars, conditional ticks, update cache."""
    bar_rows, tick_rows = [], []
    for m in msgs:
        t = m.get('T')
        sym = m.get('S')
        if not sym: continue
        if t == 'b':   # bar
            bar_rows.append((sym, '1Min', m.get('t'),
                             m.get('o'), m.get('h'), m.get('l'), m.get('c'),
                             m.get('v'), m.get('vw'), m.get('n'), json.dumps(m)))
            state.latest[sym] = {'price': m.get('c'), 'ts': m.get('t')}
        elif t == 't':   # trade
            if sym not in state.open_positions: continue   # storage scope
            tick_rows.append((sym, 'trade', m.get('p'), m.get('s'),
                              None, None, None, None,
                              m.get('x'), m.get('c'),
                              m.get('t'), json.dumps(m)))
            state.latest[sym] = {'price': m.get('p'), 'ts': m.get('t')}
        elif t == 'q':   # quote
            if sym not in state.open_positions: continue
            tick_rows.append((sym, 'quote', None, None,
                              m.get('bp'), m.get('bs'), m.get('ap'), m.get('as'),
                              m.get('x'), None,
                              m.get('t'), json.dumps(m)))
        # 'error', 'subscription', 'success' etc. — ignore here

    if not (bar_rows or tick_rows): return
    try:
        conn = state.pg()
        try:
            with conn, conn.cursor() as cur:
                if bar_rows:
                    psycopg2.extras.execute_values(cur,
                        """INSERT INTO market_bars (symbol, timeframe, bar_start, open_price, high_price,
                                                     low_price, close_price, volume, vwap, trade_count, raw_payload)
                           VALUES %s ON CONFLICT (symbol, timeframe, bar_start) DO NOTHING""", bar_rows)
                if tick_rows:
                    psycopg2.extras.execute_values(cur,
                        """INSERT INTO market_ticks (symbol, tick_type, price, size,
                                                      bid_price, bid_size, ask_price, ask_size,
                                                      exchange, conditions, timestamp_ns, raw_payload)
                           VALUES %s""", tick_rows)
        finally:
            state.put_pg(conn)
    except Exception as e:
        log.error(f"market_data PG insert failed: {e}")


async def trading_updates_stream(state: State):
    """Connect to the paper-trading account updates feed."""
    base = state.env.get('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets')
    # Trading WS URL derived from REST base: same host, /stream path
    ws_url = base.replace('https://', 'wss://').replace('http://', 'ws://').rstrip('/') + '/stream'
    backoff = RECONNECT_BACKOFF_INITIAL
    while not state.shutdown.is_set():
        try:
            log.info(f"trading_updates: connecting to {ws_url}")
            async with websockets.connect(ws_url, ping_interval=15, ping_timeout=10) as ws:
                await ws.send(json.dumps({"action": "auth", "key": state.key, "secret": state.sec}))
                resp = json.loads(await ws.recv())
                log.info(f"trading_updates: auth response {resp}")
                await ws.send(json.dumps({"action": "listen", "data": {"streams": ["trade_updates"]}}))
                backoff = RECONNECT_BACKOFF_INITIAL

                async for raw in ws:
                    msg = json.loads(raw)
                    data = msg.get('data') or msg
                    if msg.get('stream') != 'trade_updates': continue
                    handle_trading_update(state, data)
        except Exception as e:
            log.warning(f"trading_updates: disconnected ({e}); reconnecting in {backoff:.1f}s")
            try: await asyncio.wait_for(state.shutdown.wait(), timeout=backoff)
            except asyncio.TimeoutError: pass
            backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)


def handle_trading_update(state: State, data: dict):
    """Persist one order-event row per trade_updates message."""
    order = data.get('order') or {}
    if not order: return
    row = (
        order.get('id'),
        data.get('event'),                          # 'new', 'fill', 'partial_fill', 'canceled', 'rejected', 'expired'
        order.get('symbol'),
        order.get('side'),
        order.get('qty'),
        order.get('filled_qty'),
        order.get('filled_avg_price'),
        order.get('status'),
        order.get('client_order_id'),               # strategies set this; carries strategy_id prefix
        json.dumps(data),
    )
    try:
        conn = state.pg()
        try:
            with conn, conn.cursor() as cur:
                cur.execute("""INSERT INTO order_events
                    (order_id, event_type, symbol, side, qty, filled_qty,
                     filled_avg_price, status, strategy_id, raw_payload)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", row)
        finally:
            state.put_pg(conn)
    except Exception as e:
        log.error(f"trading_updates PG insert failed: {e}")


async def main_async():
    env = load_env(ENV_FILE)
    if not env or not env.get('ALPACA_API_KEY') or not env.get('BHN_TRADING_PG_DSN'):
        log.error(f"missing required env in {ENV_FILE} — exiting"); return 2

    state = State(env)

    # Initial sub set BEFORE either stream connects so subscription messages are correct
    state.watchlist = watchlist_from_rules(state.rules_path)
    try:
        conn = state.pg()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT ticker FROM paper_trades WHERE status='open'")
                state.open_positions = {r[0] for r in cur.fetchall()}
        finally: state.put_pg(conn)
    except Exception as e:
        log.warning(f"initial open_positions read failed: {e}")

    # Signal handlers for clean shutdown
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, state.shutdown.set)

    log.info(f"starting: watchlist={len(state.watchlist)} open_positions={len(state.open_positions)}")
    await asyncio.gather(
        refresh_subscriptions(state),
        market_data_stream(state),
        trading_updates_stream(state),
        return_exceptions=True,
    )
    log.info("shutdown complete"); return 0


def main():
    try: return asyncio.run(main_async())
    except KeyboardInterrupt: return 0


if __name__ == '__main__':
    sys.exit(main())
