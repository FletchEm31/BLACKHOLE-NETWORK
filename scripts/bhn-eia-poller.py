#!/usr/bin/env python3
"""
bhn-eia-poller — poll EIA (Energy Information Administration) commodity series.

Cron (LA, aligned to EIA release times in ET; safety-poll +1min after each):
  CRON_TZ=America/New_York
  30,31 10 * * 1-5  root  /usr/local/sbin/bhn-eia-poller.py   # 10:30 ET daily releases + safety
  35,36 10 * * 3    root  /usr/local/sbin/bhn-eia-poller.py   # 10:35 ET Wed weekly inventory + safety

The +1min safety poll catches data that posts 1-2 minutes after the
official release time. UPSERT semantics (ON CONFLICT (series_id,
period_start) DO NOTHING) make the safety poll idempotent.

Deploy via /etc/cron.d/bhn-eia-poller with CRON_TZ at top.

Config /root/.bhn-eia.env (mode 0600):
  BHN_EIA_PG_DSN='postgresql://log_shipper:<PW>@10.8.0.1/eventhorizon'
  # API key from Proton Pass entry: BHN-EIA-APIKey
  # Sign up: https://www.eia.gov/opendata/register.php
  BHN_EIA_API_KEY='<your-key-or-empty>'
  BHN_EIA_SERIES='PET.RWTC.D,PET.RBRTE.D,NG.RNGWHHD.D,PET.EMD_EPMR_PTE_NUS_DPG.W'
  # Defaults: WTI crude (daily), Brent crude (daily), Henry Hub natgas (daily),
  # US retail gasoline (weekly).

Graceful: missing/empty BHN_EIA_API_KEY = log + exit 0.
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path
import requests, psycopg2, psycopg2.extras


def log(msg): print(f"bhn-eia-poller: {msg}", file=sys.stderr)


def load_env(p):
    out = {}
    for ln in Path(p).read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith('#') or '=' not in ln: continue
        k, v = ln.split('=', 1); out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main():
    env_path = os.environ.get('BHN_EIA_ENV', '/root/.bhn-eia.env')
    if not Path(env_path).is_file(): log(f"missing {env_path} — skipping"); return 0
    env = load_env(env_path)
    dsn = env.get('BHN_EIA_PG_DSN', '')
    api_key = env.get('BHN_EIA_API_KEY', '')
    series_csv = env.get('BHN_EIA_SERIES', 'PET.RWTC.D,PET.RBRTE.D,NG.RNGWHHD.D')
    if not dsn: log("BHN_EIA_PG_DSN missing — skipping"); return 0
    if not api_key: log("BHN_EIA_API_KEY not configured (PM: BHN-EIA-APIKey) — skipping"); return 0

    series_list = [s.strip() for s in series_csv.split(',') if s.strip()]
    rows = []
    for sid in series_list:
        try:
            # EIA v2 API: GET https://api.eia.gov/v2/seriesid/{series_id}?api_key=...
            r = requests.get(f'https://api.eia.gov/v2/seriesid/{sid}',
                params={'api_key': api_key, 'length': 30}, timeout=20)
            r.raise_for_status()
            payload = r.json()
            data = (payload.get('response', {}) or {}).get('data', [])
        except Exception as e:
            log(f"{sid}: {e}"); continue
        for d in data:
            period = d.get('period')
            v = d.get('value')
            if period is None or v is None: continue
            try: val = float(v)
            except Exception: continue
            rows.append((sid, d.get('series-description') or d.get('name'),
                         val, d.get('units'), period, json.dumps(d)))

    if not rows: log("no observations"); return 0
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        psycopg2.extras.execute_values(cur,
            """INSERT INTO energy_prices (series_id, series_title, value, units, period_start, raw_payload)
               VALUES %s ON CONFLICT (series_id, period_start) DO NOTHING""", rows)
    log(f"inserted up to {len(rows)} rows across {len(series_list)} series")
    return 0


if __name__ == '__main__':
    sys.exit(main())
