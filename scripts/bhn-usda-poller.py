#!/usr/bin/env python3
"""
bhn-usda-poller — poll USDA NASS commodity prices.

Cron (LA, aligned to USDA release times in ET; safety-poll +1min after each):
  CRON_TZ=America/New_York
  30,31 8 * * 1-5  root  /usr/local/sbin/bhn-usda-poller.py   # 08:30 ET daily releases + safety
  0,1  15 * * 5    root  /usr/local/sbin/bhn-usda-poller.py   # 15:00 ET Fri crop progress + safety

The +1min safety poll catches data that posts 1-2 minutes after the
official release time. UPSERT semantics (UNIQUE(commodity, short_desc,
period_start, state_alpha) → ON CONFLICT DO NOTHING) make the safety
poll idempotent.

Deploy via /etc/cron.d/bhn-usda-poller with CRON_TZ at top.

Config /root/.bhn-usda.env (mode 0600):
  BHN_USDA_PG_DSN='postgresql://log_shipper:<PW>@10.8.0.1/eventhorizon'
  # API key from Proton Pass entry: BHN-USDA-APIKey
  # Sign up: https://quickstats.nass.usda.gov/api
  BHN_USDA_API_KEY='<your-key-or-empty>'
  BHN_USDA_COMMODITIES='CORN,SOYBEANS,WHEAT,CATTLE,HOGS,COTTON'
  BHN_USDA_YEAR_FROM='2024'   # rolling window

Graceful: missing/empty key = log + exit 0.
"""
from __future__ import annotations
import json, os, sys
from datetime import date
from pathlib import Path
import requests, psycopg2, psycopg2.extras


def log(msg): print(f"bhn-usda-poller: {msg}", file=sys.stderr)


def load_env(p):
    out = {}
    for ln in Path(p).read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith('#') or '=' not in ln: continue
        k, v = ln.split('=', 1); out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main():
    env_path = os.environ.get('BHN_USDA_ENV', '/root/.bhn-usda.env')
    if not Path(env_path).is_file(): log(f"missing {env_path} — skipping"); return 0
    env = load_env(env_path)
    dsn = env.get('BHN_USDA_PG_DSN', '')
    api_key = env.get('BHN_USDA_API_KEY', '')
    commodities = [c.strip() for c in env.get('BHN_USDA_COMMODITIES', 'CORN,SOYBEANS,WHEAT,CATTLE,HOGS,COTTON').split(',') if c.strip()]
    year_from = env.get('BHN_USDA_YEAR_FROM', str(date.today().year - 1))
    if not dsn: log("BHN_USDA_PG_DSN missing — skipping"); return 0
    if not api_key: log("BHN_USDA_API_KEY not configured (PM: BHN-USDA-APIKey) — skipping"); return 0

    rows = []
    for c in commodities:
        try:
            r = requests.get('https://quickstats.nass.usda.gov/api/api_GET/',
                params={'key': api_key, 'commodity_desc': c,
                        'statisticcat_desc': 'PRICE RECEIVED', 'agg_level_desc': 'NATIONAL',
                        'year__GE': year_from, 'format': 'JSON'},
                timeout=30)
            r.raise_for_status()
            recs = r.json().get('data', [])
        except Exception as e:
            log(f"{c}: {e}"); continue
        for d in recs:
            # period_start derived from year + month or end_code
            try:
                year = int(d.get('year') or 0)
                # USDA uses end_code (month) or "year" with reference_period_desc
                month = d.get('reference_period_desc') or ''
                # Best-effort date — fall back to Jan 1 of year if no month info
                period = f"{year}-01-01"
                v = float(str(d.get('Value') or '0').replace(',', ''))
            except Exception:
                continue
            rows.append((c, d.get('statisticcat_desc'), d.get('short_desc'),
                         v, d.get('unit_desc'), period, d.get('reference_period_desc'),
                         d.get('state_alpha') or 'US', json.dumps(d)))

    if not rows: log("no rows"); return 0
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        psycopg2.extras.execute_values(cur,
            """INSERT INTO agriculture_prices (commodity, statisticcat, short_desc, value, units,
                                                 period_start, period_label, state_alpha, raw_payload)
               VALUES %s ON CONFLICT (commodity, short_desc, period_start, state_alpha) DO NOTHING""",
            rows)
    log(f"inserted up to {len(rows)} rows across {len(commodities)} commodities")
    return 0


if __name__ == '__main__':
    sys.exit(main())
