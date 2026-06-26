#!/usr/bin/env python3
"""
bhn-polymarket-poller — poll Polymarket gamma-api for top active markets,
write to prediction_market_data (venue='polymarket').

Cron (NJ, every 10 min, 24/7):
  */10 * * * *  root  /usr/local/sbin/bhn-polymarket-poller.py

Config /root/.bhn-polymarket.env:
  BHN_POLYMARKET_PG_DSN='postgresql://log_shipper:<PW>@<BHN_WG_LA_IP>/eventhorizon'
  BHN_POLYMARKET_TOP_N=50

No auth required.
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path
import requests, psycopg2, psycopg2.extras


def die(msg, code=1):
    print(f"bhn-polymarket-poller: {msg}", file=sys.stderr); sys.exit(code)


def load_env(p):
    out = {}
    for ln in Path(p).read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith('#') or '=' not in ln: continue
        k, v = ln.split('=', 1); out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main():
    env_path = os.environ.get('BHN_POLYMARKET_ENV', '/root/.bhn-polymarket.env')
    if not Path(env_path).is_file():
        print(f"bhn-polymarket-poller: missing {env_path} — skipping", file=sys.stderr); return 0
    env = load_env(env_path)
    dsn = env.get('BHN_POLYMARKET_PG_DSN', '')
    top_n = int(env.get('BHN_POLYMARKET_TOP_N', '50'))
    if not dsn:
        print("bhn-polymarket-poller: BHN_POLYMARKET_PG_DSN missing — skipping", file=sys.stderr); return 0
    # No API key required — Polymarket gamma-api is public.

    url = 'https://gamma-api.polymarket.com/markets'
    params = {'active': 'true', 'closed': 'false', 'limit': str(top_n), 'order': 'volume', 'ascending': 'false'}
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        markets = r.json()
    except Exception as e:
        die(f"HTTP failure: {e}", 3)

    rows = []
    for m in markets or []:
        market_id = str(m.get('id') or m.get('conditionId') or '')
        if not market_id: continue
        title = m.get('question') or m.get('title') or ''
        # outcomePrices is a stringified list like '["0.65","0.35"]'
        prices = m.get('outcomePrices')
        if isinstance(prices, str):
            try: prices = json.loads(prices)
            except Exception: prices = []
        outcomes = m.get('outcomes')
        if isinstance(outcomes, str):
            try: outcomes = json.loads(outcomes)
            except Exception: outcomes = []
        # One row per outcome
        for i, oc in enumerate(outcomes or []):
            px = None
            if prices and i < len(prices):
                try: px = float(prices[i])
                except Exception: px = None
            rows.append((
                'polymarket', market_id, title, oc, px,
                None, None,
                float(m.get('volume') or m.get('volumeNum') or 0),
                float(m.get('liquidity') or m.get('liquidityNum') or 0),
                None,
                json.dumps(m)
            ))

    if not rows: print("bhn-polymarket-poller: empty response"); return 0
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO prediction_market_data (venue, market_id, market_title, outcome, price, yes_bid, yes_ask, volume_24h, liquidity, open_interest, raw_payload) VALUES %s",
            rows
        )
    print(f"bhn-polymarket-poller: inserted {len(rows)} outcome rows from {len({r[1] for r in rows})} markets")
    return 0


if __name__ == '__main__':
    sys.exit(main())
