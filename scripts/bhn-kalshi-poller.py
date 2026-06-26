#!/usr/bin/env python3
"""
bhn-kalshi-poller — poll Kalshi top-N markets, write to prediction_market_data.

Cron (NJ, every 10 min, 24/7):
  */10 * * * *  root  /usr/local/sbin/bhn-kalshi-poller.py

Config /root/.bhn-kalshi.env:
  BHN_KALSHI_PG_DSN='postgresql://log_shipper:<PW>@<BHN_WG_LA_IP>/eventhorizon'
  BHN_KALSHI_API_KEY_ID='<from PM EH-Kalshi-APIKeyID>'
  BHN_KALSHI_API_PRIVATE_KEY='<PEM-encoded RSA private key>'
  BHN_KALSHI_TOP_N=50

Kalshi auth is RSA-signed request signatures. See
https://trading-api.readme.io/reference/authentication for the canonical
header format. This script does a minimal subset for the public
/markets endpoint.
"""
from __future__ import annotations
import base64, json, os, sys, time
from pathlib import Path
import requests, psycopg2, psycopg2.extras
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def die(msg, code=1):
    print(f"bhn-kalshi-poller: {msg}", file=sys.stderr); sys.exit(code)


def load_env(p):
    out = {}
    for ln in Path(p).read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith('#') or '=' not in ln: continue
        k, v = ln.split('=', 1); out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def sign(priv_key_pem: str, msg: str) -> str:
    key = serialization.load_pem_private_key(priv_key_pem.encode(), password=None)
    sig = key.sign(msg.encode(), padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
    return base64.b64encode(sig).decode()


def main():
    env_path = os.environ.get('BHN_KALSHI_ENV', '/root/.bhn-kalshi.env')
    if not Path(env_path).is_file():
        print(f"bhn-kalshi-poller: missing {env_path} — skipping", file=sys.stderr); return 0
    env = load_env(env_path)
    dsn = env.get('BHN_KALSHI_PG_DSN', '')
    key_id = env.get('BHN_KALSHI_API_KEY_ID', '')
    priv = env.get('BHN_KALSHI_API_PRIVATE_KEY', '').replace('\\n', '\n')
    top_n = int(env.get('BHN_KALSHI_TOP_N', '50'))
    if not dsn:
        print("bhn-kalshi-poller: BHN_KALSHI_PG_DSN missing — skipping", file=sys.stderr); return 0
    if not key_id or not priv:
        print("bhn-kalshi-poller: BHN_KALSHI_API_KEY_ID and/or BHN_KALSHI_API_PRIVATE_KEY not configured (PM: BHN-Kalshi-APIKey + BHN-Kalshi-PrivKey) — paid tier; skipping", file=sys.stderr); return 0

    ts = str(int(time.time() * 1000))
    method = 'GET'
    path = '/trade-api/v2/markets'
    msg = ts + method + path
    sig = sign(priv, msg)

    url = 'https://api.elections.kalshi.com' + path
    headers = {
        'KALSHI-ACCESS-KEY': key_id,
        'KALSHI-ACCESS-TIMESTAMP': ts,
        'KALSHI-ACCESS-SIGNATURE': sig,
    }
    params = {'limit': str(top_n), 'status': 'open'}

    try:
        r = requests.get(url, headers=headers, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        die(f"HTTP failure: {e}", 3)

    markets = data.get('markets', [])
    rows = []
    for m in markets:
        # Kalshi market shape: ticker, event_ticker, title, yes_bid, yes_ask, last_price, volume, liquidity, open_interest, ...
        rows.append((
            'kalshi',
            m.get('ticker') or m.get('event_ticker') or '',
            m.get('title') or m.get('subtitle') or '',
            'YES',
            float(m.get('last_price', 0)) / 100.0 if m.get('last_price') is not None else None,
            float(m.get('yes_bid', 0)) / 100.0 if m.get('yes_bid') is not None else None,
            float(m.get('yes_ask', 0)) / 100.0 if m.get('yes_ask') is not None else None,
            float(m.get('volume', 0)),
            float(m.get('liquidity', 0)),
            float(m.get('open_interest', 0)),
            json.dumps(m)
        ))

    if not rows: print("bhn-kalshi-poller: empty response"); return 0
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO prediction_market_data (venue, market_id, market_title, outcome, price, yes_bid, yes_ask, volume_24h, liquidity, open_interest, raw_payload) VALUES %s",
            rows
        )
    print(f"bhn-kalshi-poller: inserted {len(rows)} markets")
    return 0


if __name__ == '__main__':
    sys.exit(main())
