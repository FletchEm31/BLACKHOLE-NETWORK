#!/usr/bin/env python3
"""
bhn-coingecko-poller — poll CoinGecko top-N coins every 15 min, write to
crypto_market_data.

Cron (LA or NJ — operator's choice; both can reach internet via proxy):
  */15 * * * *  root  /usr/local/sbin/bhn-coingecko-poller.py

Config /root/.bhn-coingecko.env:
  BHN_COINGECKO_PG_DSN='postgresql://log_shipper:<PW>@10.8.0.1/eventhorizon'
  BHN_COINGECKO_TOP_N=10                    # default 10
  BHN_COINGECKO_API_KEY=''                  # optional — paid tier; demo works without

Free tier is rate-limited to ~10-50 req/min — 15-min cadence comfortably fits.
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path
import requests, psycopg2, psycopg2.extras


def die(msg, code=1):
    print(f"bhn-coingecko-poller: {msg}", file=sys.stderr); sys.exit(code)


def load_env(p):
    out = {}
    for ln in Path(p).read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith('#') or '=' not in ln: continue
        k, v = ln.split('=', 1); out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main():
    env_path = os.environ.get('BHN_COINGECKO_ENV', '/root/.bhn-coingecko.env')
    if not Path(env_path).is_file(): die(f"missing {env_path}")
    env = load_env(env_path)
    dsn = env.get('BHN_COINGECKO_PG_DSN', '')
    top_n = int(env.get('BHN_COINGECKO_TOP_N', '10'))
    api_key = env.get('BHN_COINGECKO_API_KEY', '')
    if not dsn: die("BHN_COINGECKO_PG_DSN missing")

    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {'vs_currency': 'usd', 'order': 'market_cap_desc',
              'per_page': str(top_n), 'page': '1', 'sparkline': 'false'}
    headers = {}
    if api_key:
        headers['x-cg-pro-api-key'] = api_key
        url = "https://pro-api.coingecko.com/api/v3/coins/markets"

    try:
        r = requests.get(url, params=params, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        die(f"HTTP failure: {e}", 3)

    rows = []
    for c in data or []:
        rows.append((
            (c.get('symbol') or '').upper(),
            c.get('name'),
            c.get('current_price'),
            c.get('market_cap'),
            c.get('total_volume'),
            c.get('price_change_percentage_24h'),
            c.get('market_cap_rank'),
            json.dumps(c)
        ))
    if not rows: print("bhn-coingecko-poller: empty response"); return 0

    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO crypto_market_data (symbol, name, price_usd, market_cap_usd, "
            "volume_24h_usd, change_24h_pct, rank, raw_payload) VALUES %s",
            rows
        )
    print(f"bhn-coingecko-poller: inserted {len(rows)} rows")
    return 0


if __name__ == '__main__':
    sys.exit(main())
