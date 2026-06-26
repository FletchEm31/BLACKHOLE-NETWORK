#!/usr/bin/env python3
"""
bhn-quiver-poller — poll Quiver Quantitative congressional-trading endpoint,
write each fresh disclosure to market_signals (source='quiver').

Cron (NJ, 15 min, 24/7):
  */15 * * * *  root  /usr/local/sbin/bhn-quiver-poller.py

Config /root/.bhn-quiver.env (mode 0600):
  BHN_QUIVER_PG_DSN='postgresql://log_shipper:<PW>@<BHN_WG_LA_IP>/eventhorizon'
  BHN_QUIVER_API_KEY='<from PM EH-Quiver-APIKey>'
  BHN_QUIVER_ENDPOINT='https://api.quiverquant.com/beta/live/congresstrading'

Dedup via metadata->>'disclosure_id' — only INSERT rows whose
disclosure_id is not already in the table for the past 30 days.
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path
import requests, psycopg2, psycopg2.extras


def die(msg, code=1):
    print(f"bhn-quiver-poller: {msg}", file=sys.stderr); sys.exit(code)


def load_env(p):
    out = {}
    for ln in Path(p).read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith('#') or '=' not in ln: continue
        k, v = ln.split('=', 1); out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main():
    env_path = os.environ.get('BHN_QUIVER_ENV', '/root/.bhn-quiver.env')
    if not Path(env_path).is_file():
        print(f"bhn-quiver-poller: missing {env_path} — skipping", file=sys.stderr); return 0
    env = load_env(env_path)
    dsn = env.get('BHN_QUIVER_PG_DSN', '')
    api_key = env.get('BHN_QUIVER_API_KEY', '')
    endpoint = env.get('BHN_QUIVER_ENDPOINT', 'https://api.quiverquant.com/beta/live/congresstrading')
    if not dsn:
        print("bhn-quiver-poller: BHN_QUIVER_PG_DSN missing — skipping", file=sys.stderr); return 0
    if not api_key:
        print("bhn-quiver-poller: BHN_QUIVER_API_KEY not configured (PM: BHN-Quiver-APIKey) — paid tier; skipping", file=sys.stderr); return 0

    try:
        r = requests.get(endpoint, headers={'Authorization': f'Token {api_key}'}, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        die(f"HTTP failure: {e}", 3)

    if not data: print("bhn-quiver-poller: empty response"); return 0

    # Each disclosure shape: {ReportDate, TransactionDate, Ticker, Representative, Transaction, Amount, ...}
    rows = []
    for d in data:
        ticker = d.get('Ticker') or d.get('ticker')
        if not ticker: continue
        disclosure_id = f"{d.get('ReportDate','')}-{d.get('Representative','')}-{ticker}-{d.get('TransactionDate','')}"
        amt = d.get('Amount') or d.get('amount') or 0
        try: val = float(amt)
        except Exception: val = 0.0
        meta = dict(d); meta['disclosure_id'] = disclosure_id
        rows.append(('quiver', ticker, 'congress_trade', val, json.dumps(meta)))

    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        # Dedup via disclosure_id present in metadata; only INSERT new ones from last 30 days.
        ids = [r[4] for r in rows]
        existing_ids = set()
        if ids:
            cur.execute("""
                SELECT DISTINCT metadata->>'disclosure_id'
                FROM market_signals
                WHERE source='quiver' AND captured_at > NOW() - INTERVAL '30 days'
            """)
            existing_ids = {row[0] for row in cur.fetchall() if row[0]}
        new_rows = [r for r in rows if json.loads(r[4]).get('disclosure_id') not in existing_ids]
        if not new_rows:
            print(f"bhn-quiver-poller: {len(rows)} fetched, 0 new"); return 0
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO market_signals (source, symbol, signal_type, value, metadata) VALUES %s",
            new_rows
        )
        print(f"bhn-quiver-poller: inserted {len(new_rows)} new of {len(rows)} fetched")
    return 0


if __name__ == '__main__':
    sys.exit(main())
