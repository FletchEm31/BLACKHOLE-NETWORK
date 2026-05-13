#!/usr/bin/env python3
"""
bhn-fmp-poller — poll Financial Modeling Prep quotes for watchlist symbols,
write to market_signals.

Runs on NJ trading node every 15 min during market hours via cron. Reads
watchlist from rules.json (strat_4_momentum.universe + strat_2_value.universe
+ strat_3_mean_reversion.universe — unioned). Pulls real-time quote per
symbol, inserts one market_signals row per (symbol, signal_type='price').

Config:
  /root/.bhn-fmp.env (mode 0600):
    BHN_FMP_PG_DSN='postgresql://log_shipper:<PW>@10.8.0.1/eventhorizon'
    BHN_FMP_API_KEY='<from PM EH-FMP-APIKey>'
    BHN_FMP_RULES_PATH='/opt/bhn/trading/rules.json'    # optional

All outbound HTTP goes through https_proxy (set in /etc/environment per
LA egress lockdown — NJ should also be wired this way once that's deployed).

Cron (NJ):
  */15 9-16 * * 1-5  root  /usr/local/sbin/bhn-fmp-poller.py
"""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path

import requests
import psycopg2
import psycopg2.extras


def die(msg: str, code: int = 1) -> None:
    print(f"bhn-fmp-poller: {msg}", file=sys.stderr); sys.exit(code)


def load_env(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        k, v = line.split('=', 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def union_watchlist(rules_path: Path) -> list[str]:
    if not rules_path.exists():
        return []
    rules = json.loads(rules_path.read_text())
    syms: set[str] = set()
    for key in ('strat_2_value', 'strat_3_mean_reversion', 'strat_4_momentum'):
        block = rules.get(key, {})
        for s in block.get('universe', []) or []:
            syms.add(s.upper())
    return sorted(syms)


def main() -> int:
    env_path = os.environ.get('BHN_FMP_ENV', '/root/.bhn-fmp.env')
    if not Path(env_path).is_file(): die(f"missing {env_path}")
    env = load_env(env_path)
    dsn = env.get('BHN_FMP_PG_DSN', '')
    api_key = env.get('BHN_FMP_API_KEY', '')
    rules_path = Path(env.get('BHN_FMP_RULES_PATH', '/opt/bhn/trading/rules.json'))
    if not dsn or not api_key: die("BHN_FMP_PG_DSN and/or BHN_FMP_API_KEY missing")

    syms = union_watchlist(rules_path)
    if not syms:
        print("bhn-fmp-poller: empty watchlist — nothing to poll", file=sys.stderr); return 0

    # FMP supports multi-symbol quotes via comma-separated path.
    # Cap at 100 per call to stay polite.
    rows: list[tuple] = []
    for i in range(0, len(syms), 100):
        chunk = syms[i:i+100]
        url = f"https://financialmodelingprep.com/api/v3/quote/{','.join(chunk)}"
        try:
            r = requests.get(url, params={'apikey': api_key}, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"bhn-fmp-poller: HTTP failure for chunk {i}: {e}", file=sys.stderr)
            continue
        for q in data or []:
            sym = q.get('symbol')
            px = q.get('price')
            if sym is None or px is None: continue
            rows.append(('fmp', sym, 'price', float(px), json.dumps(q)))
            vol = q.get('volume')
            if vol is not None:
                rows.append(('fmp', sym, 'volume', float(vol), json.dumps({'symbol': sym, 'volume': vol})))
        time.sleep(0.2)   # be polite

    if not rows:
        print("bhn-fmp-poller: zero rows assembled", file=sys.stderr); return 0

    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO market_signals (source, symbol, signal_type, value, metadata) VALUES %s",
            rows
        )
    print(f"bhn-fmp-poller: inserted {len(rows)} rows for {len(syms)} symbols")
    return 0


if __name__ == '__main__':
    sys.exit(main())
