#!/usr/bin/env python3
"""
bhn-tor-metrics-poller — poll onionoo.torproject.org for BHN relays' consensus
data and INSERT to tor_relay_stats (consensus fields populated, source='onionoo').

Cron (LA, daily):
  15 7 * * *  root  /usr/local/sbin/bhn-tor-metrics-poller.py

Config /root/.bhn-tor-metrics.env:
  BHN_TOR_METRICS_PG_DSN='postgresql://log_shipper:<PW>@<BHN_WG_LA_IP>/eventhorizon'

Fingerprints are discovered from tor_relay_stats — whatever the per-node
collector has shipped most recently. No hardcoding.
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path
import requests, psycopg2, psycopg2.extras


def die(msg, code=1):
    print(f"bhn-tor-metrics-poller: {msg}", file=sys.stderr); sys.exit(code)


def load_env(p):
    out = {}
    for ln in Path(p).read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith('#') or '=' not in ln: continue
        k, v = ln.split('=', 1); out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main():
    env_path = os.environ.get('BHN_TOR_METRICS_ENV', '/root/.bhn-tor-metrics.env')
    if not Path(env_path).is_file(): die(f"missing {env_path}")
    env = load_env(env_path)
    dsn = env.get('BHN_TOR_METRICS_PG_DSN', '')
    if not dsn: die("BHN_TOR_METRICS_PG_DSN missing")

    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (node) node, fingerprint
            FROM tor_relay_stats
            WHERE fingerprint IS NOT NULL AND fingerprint <> ''
            ORDER BY node, measured_at DESC
        """)
        relays = [(row[0], row[1]) for row in cur.fetchall()]
    if not relays: print("bhn-tor-metrics-poller: no relays in tor_relay_stats yet"); return 0

    lookup = ','.join(fp for _, fp in relays)
    url = 'https://onionoo.torproject.org/details'
    try:
        r = requests.get(url, params={'lookup': lookup, 'fields':
            'fingerprint,nickname,consensus_weight,flags,observed_bandwidth,advertised_bandwidth,country,as_name,first_seen,last_restarted,running'},
            timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        die(f"HTTP failure: {e}", 3)

    by_fp = {item.get('fingerprint'): item for item in (data.get('relays') or [])}

    rows = []
    for node, fp in relays:
        item = by_fp.get(fp)
        if not item:
            print(f"bhn-tor-metrics-poller: {node} ({fp[:8]}...) not in consensus")
            continue
        rows.append((
            node,
            None,  # bytes_read — onionoo doesn't expose this
            None,  # bytes_written
            None,  # circuits_built
            None,  # relay_bandwidth_rate
            None,  # relay_bandwidth_burst
            None,  # uptime_seconds — derive from first_seen + last_restarted if needed
            fp,
            json.dumps(item),
            item.get('consensus_weight'),
            item.get('flags') or [],
            item.get('observed_bandwidth'),
            item.get('advertised_bandwidth'),
            item.get('country'),
            item.get('as_name'),
            item.get('first_seen'),
            item.get('last_restarted'),
            'onionoo'
        ))

    if not rows: print("bhn-tor-metrics-poller: zero matched rows"); return 0
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO tor_relay_stats
               (node, bytes_read, bytes_written, circuits_built, relay_bandwidth_rate, relay_bandwidth_burst,
                uptime_seconds, fingerprint, raw_payload,
                consensus_weight, flags, observed_bandwidth, advertised_bandwidth, country, as_name,
                first_seen, last_restarted, source)
               VALUES %s""",
            rows
        )
    print(f"bhn-tor-metrics-poller: inserted {len(rows)} onionoo rows")
    return 0


if __name__ == '__main__':
    sys.exit(main())
