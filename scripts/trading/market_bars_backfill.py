#!/usr/bin/env python3
"""
market_bars_backfill — pull 30 days of bars from Alpaca for the watchlist
across all 5 timeframes (1Min/5Min/15Min/1Hour/1Day), INSERT to market_bars.

One-shot script — operator runs once at trading framework deploy time, then
the live aggregation in market_stream.py (item 18) keeps the table current.

Usage:
  python3 market_bars_backfill.py                  # all timeframes, default 30d
  python3 market_bars_backfill.py --days 14        # custom window
  python3 market_bars_backfill.py --timeframe 1Day # single timeframe
  python3 market_bars_backfill.py --symbols AAPL,MSFT   # restricted symbol list

Reads creds from /etc/bhn-trading/env (BHN_TRADING_PG_DSN + ALPACA_*).
Idempotent — ON CONFLICT (symbol, timeframe, bar_start) DO NOTHING.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests, psycopg2, psycopg2.extras


def log(msg): print(f"market_bars_backfill: {msg}", file=sys.stderr)


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
    syms = set()
    for k in ('strat_2_value', 'strat_3_mean_reversion', 'strat_4_momentum'):
        for s in (rules.get(k, {}).get('universe') or []):
            syms.add(s.upper())
    return sorted(syms)


def fetch_bars(sess, base, sym, tf, start, end):
    """Alpaca v2 bars endpoint, paginated."""
    url = f'{base}/v2/stocks/{sym}/bars'
    out = []
    page_token = None
    while True:
        params = {'start': start, 'end': end, 'timeframe': tf, 'limit': 10000, 'adjustment': 'all'}
        if page_token: params['page_token'] = page_token
        r = sess.get(url, params=params, timeout=30)
        if r.status_code == 429:
            log(f"{sym}/{tf}: rate-limited, sleeping 60s"); time.sleep(60); continue
        if not r.ok: log(f"{sym}/{tf}: HTTP {r.status_code}: {r.text[:200]}"); break
        body = r.json()
        bars = body.get('bars', []) or []
        out.extend(bars)
        page_token = body.get('next_page_token')
        if not page_token: break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--timeframe', default='all',
                    help='1Min, 5Min, 15Min, 1Hour, 1Day, or "all"')
    ap.add_argument('--symbols', default='', help='comma-separated override (default: watchlist)')
    ap.add_argument('--env', default='/etc/bhn-trading/env')
    args = ap.parse_args()

    env = load_env(args.env)
    if not env: log(f"no env at {args.env}"); return 2
    dsn = env.get('BHN_TRADING_PG_DSN')
    key = env.get('ALPACA_API_KEY', '')
    sec = env.get('ALPACA_API_SECRET', '')
    if not (dsn and key and sec): log("missing PG DSN or Alpaca creds — skipping"); return 0

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    else:
        rules_path = Path(env.get('BHN_TRADING_RULES_PATH', '/opt/bhn/trading/rules.json'))
        symbols = watchlist_from_rules(rules_path)
    if not symbols: log("empty symbol list"); return 0

    if args.timeframe == 'all':
        timeframes = ['1Day', '1Hour', '15Min', '5Min', '1Min']   # heaviest last
    else:
        timeframes = [args.timeframe]

    end = datetime.now(timezone.utc).isoformat()
    start = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()

    sess = requests.Session()
    sess.headers.update({'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': sec})
    data_base = 'https://data.alpaca.markets'

    total = 0
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        for tf in timeframes:
            for sym in symbols:
                bars = fetch_bars(sess, data_base, sym, tf, start, end)
                if not bars: continue
                rows = []
                for b in bars:
                    rows.append((sym, tf, b.get('t'),
                                 b.get('o'), b.get('h'), b.get('l'), b.get('c'),
                                 b.get('v'), b.get('vw'), b.get('n'),
                                 json.dumps(b)))
                if rows:
                    psycopg2.extras.execute_values(cur,
                        """INSERT INTO market_bars (symbol, timeframe, bar_start, open_price, high_price,
                                                     low_price, close_price, volume, vwap, trade_count, raw_payload)
                           VALUES %s ON CONFLICT (symbol, timeframe, bar_start) DO NOTHING""", rows)
                    total += len(rows)
                time.sleep(0.2)
            log(f"timeframe {tf}: done, running total {total}")
    log(f"backfill complete: {total} bars across {len(symbols)} symbols × {len(timeframes)} timeframes")
    return 0


if __name__ == '__main__':
    sys.exit(main())
