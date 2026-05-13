#!/usr/bin/env python3
"""
bhn-fred-poller — poll FRED (Federal Reserve Economic Data) series → macro_indicators.

Cron (LA, daily): 0 6 * * * root /usr/local/sbin/bhn-fred-poller.py

Config /root/.bhn-fred.env (mode 0600):
  BHN_FRED_PG_DSN='postgresql://log_shipper:<PW>@10.8.0.1/eventhorizon'
  # API key from Proton Pass entry: BHN-FRED-APIKey
  # Sign up: https://fred.stlouisfed.org/docs/api/api_key.html
  BHN_FRED_API_KEY='<your-key-or-empty>'
  # Comma-separated FRED series to poll. Defaults to a sensible macro basket.
  BHN_FRED_SERIES='DGS10,DGS2,CPIAUCSL,UNRATE,GDP,DFF,M2SL,VIXCLS,SP500'

Graceful failure: if BHN_FRED_API_KEY is empty/missing, logs and exits 0
(does not error out cron). Same for transient HTTP failures.
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path
import requests, psycopg2, psycopg2.extras


def log(msg): print(f"bhn-fred-poller: {msg}", file=sys.stderr)


def load_env(p):
    out = {}
    for ln in Path(p).read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith('#') or '=' not in ln: continue
        k, v = ln.split('=', 1); out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main():
    env_path = os.environ.get('BHN_FRED_ENV', '/root/.bhn-fred.env')
    if not Path(env_path).is_file():
        log(f"missing {env_path} — skipping"); return 0
    env = load_env(env_path)
    dsn = env.get('BHN_FRED_PG_DSN', '')
    api_key = env.get('BHN_FRED_API_KEY', '')
    series_csv = env.get('BHN_FRED_SERIES', 'DGS10,DGS2,CPIAUCSL,UNRATE,GDP,DFF,M2SL,VIXCLS,SP500')
    if not dsn:
        log("BHN_FRED_PG_DSN missing — skipping"); return 0
    if not api_key:
        log("BHN_FRED_API_KEY not configured (PM entry BHN-FRED-APIKey) — skipping"); return 0

    series_list = [s.strip() for s in series_csv.split(',') if s.strip()]
    rows = []
    for sid in series_list:
        try:
            # Recent observations only (last 30 days for daily, more for lower-freq series).
            r = requests.get('https://api.stlouisfed.org/fred/series/observations',
                params={'series_id': sid, 'api_key': api_key, 'file_type': 'json',
                        'sort_order': 'desc', 'limit': 30},
                timeout=20)
            r.raise_for_status()
            obs = r.json().get('observations', [])
            # Metadata for series title + units
            m = requests.get('https://api.stlouisfed.org/fred/series',
                params={'series_id': sid, 'api_key': api_key, 'file_type': 'json'},
                timeout=20)
            meta_series = (m.json().get('seriess') or [{}])[0] if m.ok else {}
        except Exception as e:
            log(f"{sid}: HTTP failure: {e}"); continue
        title = meta_series.get('title')
        units = meta_series.get('units')
        freq = meta_series.get('frequency')
        for o in obs:
            v = o.get('value')
            if v in (None, '.', ''): continue
            try: val = float(v)
            except Exception: continue
            rows.append((sid, title, val, units, o['date'] + 'T00:00:00Z', freq, json.dumps(o)))

    if not rows:
        log("no observations to insert"); return 0
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO macro_indicators (series_id, series_title, value, units, period_start, frequency, raw_payload)
               VALUES %s ON CONFLICT (series_id, period_start) DO NOTHING""",
            rows
        )
    log(f"inserted up to {len(rows)} rows across {len(series_list)} series")
    return 0


if __name__ == '__main__':
    sys.exit(main())
