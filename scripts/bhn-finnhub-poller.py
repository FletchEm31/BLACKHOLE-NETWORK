#!/usr/bin/env python3
"""
bhn-finnhub-poller — poll Finnhub for analyst recommendations + earnings.

Cron (NJ, daily): 30 6 * * * root /usr/local/sbin/bhn-finnhub-poller.py

Config /root/.bhn-finnhub.env (mode 0600):
  BHN_FINNHUB_PG_DSN='postgresql://log_shipper:<PW>@10.8.0.1/eventhorizon'
  # API key from Proton Pass entry: BHN-Finnhub-APIKey
  # Sign up: https://finnhub.io/register
  BHN_FINNHUB_API_KEY='<your-key-or-empty>'
  BHN_FINNHUB_RULES_PATH='/opt/bhn/trading/rules.json'

Graceful: missing key = log + exit 0 (cron not flagged as failure).
"""
from __future__ import annotations
import json, os, sys, time
from pathlib import Path
import requests, psycopg2, psycopg2.extras


def log(msg): print(f"bhn-finnhub-poller: {msg}", file=sys.stderr)


def load_env(p):
    out = {}
    for ln in Path(p).read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith('#') or '=' not in ln: continue
        k, v = ln.split('=', 1); out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def watchlist(rules_path: Path) -> list[str]:
    if not rules_path.exists(): return []
    rules = json.loads(rules_path.read_text())
    syms: set[str] = set()
    for key in ('strat_2_value', 'strat_3_mean_reversion', 'strat_4_momentum'):
        for s in (rules.get(key, {}).get('universe') or []):
            syms.add(s.upper())
    return sorted(syms)


def main():
    env_path = os.environ.get('BHN_FINNHUB_ENV', '/root/.bhn-finnhub.env')
    if not Path(env_path).is_file(): log(f"missing {env_path} — skipping"); return 0
    env = load_env(env_path)
    dsn = env.get('BHN_FINNHUB_PG_DSN', '')
    api_key = env.get('BHN_FINNHUB_API_KEY', '')
    rules_path = Path(env.get('BHN_FINNHUB_RULES_PATH', '/opt/bhn/trading/rules.json'))
    if not dsn: log("BHN_FINNHUB_PG_DSN missing — skipping"); return 0
    if not api_key: log("BHN_FINNHUB_API_KEY not configured (PM: BHN-Finnhub-APIKey) — skipping"); return 0

    syms = watchlist(rules_path)
    if not syms: log("empty watchlist — skipping"); return 0

    analyst_rows, earnings_rows = [], []
    for sym in syms:
        try:
            ra = requests.get('https://finnhub.io/api/v1/stock/recommendation',
                params={'symbol': sym, 'token': api_key}, timeout=15)
            if ra.ok:
                for rec in ra.json() or []:
                    analyst_rows.append((sym, rec.get('period'),
                        rec.get('buy'), rec.get('strongBuy'), rec.get('hold'),
                        rec.get('sell'), rec.get('strongSell'),
                        None, None, None, None, json.dumps(rec)))
            time.sleep(0.5)   # free tier ~60 req/min
            re_ = requests.get('https://finnhub.io/api/v1/stock/earnings',
                params={'symbol': sym, 'token': api_key, 'limit': 8}, timeout=15)
            if re_.ok:
                for e in re_.json() or []:
                    surprise_pct = None
                    if e.get('estimate') and e.get('actual') is not None:
                        try:
                            est = float(e['estimate']); act = float(e['actual'])
                            if est != 0: surprise_pct = (act - est) / abs(est) * 100
                        except Exception: pass
                    earnings_rows.append((sym, e.get('period'),
                        e.get('actual'), e.get('estimate'),
                        None, None, surprise_pct, json.dumps(e)))
            time.sleep(0.5)
        except Exception as ex:
            log(f"{sym}: {ex}"); continue

    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        if analyst_rows:
            psycopg2.extras.execute_values(cur,
                """INSERT INTO analyst_data (symbol, period, buy, strong_buy, hold, sell, strong_sell,
                                              target_high, target_low, target_mean, target_median, raw_payload)
                   VALUES %s ON CONFLICT (symbol, period) DO NOTHING""", analyst_rows)
        if earnings_rows:
            psycopg2.extras.execute_values(cur,
                """INSERT INTO earnings_data (symbol, period, eps_actual, eps_estimate,
                                               revenue_actual, revenue_estimate, surprise_pct, raw_payload)
                   VALUES %s ON CONFLICT (symbol, period) DO NOTHING""", earnings_rows)
    log(f"inserted {len(analyst_rows)} analyst + {len(earnings_rows)} earnings rows")
    return 0


if __name__ == '__main__':
    sys.exit(main())
