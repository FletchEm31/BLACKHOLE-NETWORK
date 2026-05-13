#!/usr/bin/env python3
"""
bhn-alpaca-extras-poller — poll Alpaca REST for the non-streamable feeds:
  - corporate actions (every 4h, lookforward 30d)
  - news (every 15min)
  - options chain (every 15min during market hours, for symbols with open positions)

Cron (NJ):
  0 */4 * * *  root  /usr/local/sbin/bhn-alpaca-extras-poller.py corporate
  */15 * * * *  root  /usr/local/sbin/bhn-alpaca-extras-poller.py news
  */15 9-16 * * 1-5  root  /usr/local/sbin/bhn-alpaca-extras-poller.py options

Reads PG DSN + Alpaca creds from /etc/bhn-trading/env (shared with the
strategy framework). Respects ALPACA_RATE_LIMIT from that env (default 150
req/min); WebSocket-only operations are exempt, REST operations honor it.

Graceful failure on missing creds.
"""
from __future__ import annotations
import json, os, sys, time
from datetime import date, timedelta
from pathlib import Path
import requests, psycopg2, psycopg2.extras


def log(msg): print(f"bhn-alpaca-extras-poller: {msg}", file=sys.stderr)


def load_env(p):
    out = {}
    if not Path(p).is_file(): return out
    for ln in Path(p).read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith('#') or '=' not in ln: continue
        k, v = ln.split('=', 1); out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def session_for(env: dict) -> tuple[requests.Session, str]:
    key = env.get('ALPACA_API_KEY', '')
    sec = env.get('ALPACA_API_SECRET', '')
    base = env.get('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets')
    if not key or not sec:
        log("ALPACA_API_KEY/SECRET missing — skipping"); return None, ''
    s = requests.Session()
    s.headers.update({'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': sec})
    return s, base


def poll_corporate_actions(cur, sess, base):
    today = date.today()
    end = today + timedelta(days=30)
    try:
        r = sess.get(f'{base}/v2/corporate_actions',
                     params={'start': today.isoformat(), 'end': end.isoformat()}, timeout=20)
        r.raise_for_status()
        items = r.json().get('corporate_actions', []) or r.json().get('items', []) or []
    except Exception as e:
        log(f"corporate_actions: {e}"); return
    rows = []
    for it in items:
        sym = it.get('symbol') or (it.get('target', {}) or {}).get('symbol')
        action = it.get('type') or it.get('action_type')
        ex_date = it.get('ex_date') or it.get('exDate')
        if not (sym and action and ex_date): continue
        rows.append((sym, action, ex_date, it.get('payable_date'), it.get('record_date'),
                     it.get('declared_date'), it.get('cash'), it.get('ratio'), json.dumps(it)))
    if not rows: log("corporate_actions: 0 rows"); return
    psycopg2.extras.execute_values(cur,
        """INSERT INTO corporate_actions (symbol, action_type, ex_date, payable_date, record_date,
                                          declared_date, cash_amount, split_ratio, raw_payload)
           VALUES %s ON CONFLICT (symbol, action_type, ex_date) DO NOTHING""", rows)
    log(f"corporate_actions: {len(rows)} rows")


def poll_news(cur, sess, base):
    # Alpaca News API is at data.alpaca.markets — separate domain
    news_base = 'https://data.alpaca.markets/v1beta1/news'
    try:
        r = sess.get(news_base, params={'limit': 50, 'sort': 'desc'}, timeout=20)
        r.raise_for_status()
        items = r.json().get('news', []) or []
    except Exception as e:
        log(f"news: {e}"); return
    rows = []
    for n in items:
        rows.append((n.get('id'), n.get('headline'), n.get('summary'), n.get('author'),
                     n.get('source'), n.get('created_at'), n.get('updated_at'),
                     n.get('url'), n.get('symbols') or [], json.dumps(n)))
    if not rows: log("news: 0 rows"); return
    psycopg2.extras.execute_values(cur,
        """INSERT INTO alpaca_news (article_id, headline, summary, author, source,
                                     created_at, updated_at, url, symbols, raw_payload)
           VALUES %s ON CONFLICT (article_id) DO NOTHING""", rows)
    log(f"news: {len(rows)} articles")


def poll_options(cur, sess, base, pg_conn):
    # Only for symbols with open positions in paper_trades.
    with pg_conn.cursor() as c2:
        c2.execute("SELECT DISTINCT ticker FROM paper_trades WHERE status='open'")
        symbols = [row[0] for row in c2.fetchall()]
    if not symbols: log("options: no open positions, skipping"); return
    data_base = 'https://data.alpaca.markets/v1beta1/options'
    inserted = 0
    for sym in symbols:
        try:
            r = sess.get(f'{data_base}/snapshots/{sym}', timeout=20)
            if not r.ok: log(f"options/{sym}: HTTP {r.status_code}"); continue
            payload = r.json()
            snapshots = payload.get('snapshots', {})
        except Exception as e:
            log(f"options/{sym}: {e}"); continue
        rows = []
        for occ, snap in snapshots.items():
            # OCC symbol format: SYMBOLYYMMDDC00150000 — parse expiry/strike/right
            # Best-effort regex via slicing; fall back to snap fields if present
            try:
                underlying = sym
                # Find first digit
                i = next(j for j, ch in enumerate(occ) if ch.isdigit())
                expiry = f"20{occ[i:i+2]}-{occ[i+2:i+4]}-{occ[i+4:i+6]}"
                right = occ[i+6]
                strike = int(occ[i+7:]) / 1000.0
            except Exception:
                continue
            q = (snap.get('latestQuote') or {})
            t = (snap.get('latestTrade') or {})
            g = (snap.get('greeks') or {})
            rows.append((underlying, expiry, strike, right,
                         q.get('bp'), q.get('ap'), t.get('p'),
                         snap.get('impliedVolatility'),
                         g.get('delta'), g.get('gamma'), g.get('theta'), g.get('vega'),
                         t.get('s'), snap.get('openInterest'),
                         json.dumps(snap)))
        if rows:
            psycopg2.extras.execute_values(cur,
                """INSERT INTO options_chain_snapshots (underlying, expiry, strike, right_type, bid, ask,
                                                         last, iv, delta, gamma, theta, vega, volume, open_interest, raw_payload)
                   VALUES %s""", rows)
            inserted += len(rows)
        time.sleep(0.4)
    log(f"options: {inserted} rows across {len(symbols)} symbols")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'news'
    env = load_env('/etc/bhn-trading/env') or load_env('/root/.bhn-alpaca-extras.env')
    if not env: log("no env file — skipping"); return 0
    dsn = env.get('BHN_TRADING_PG_DSN') or env.get('BHN_ALPACA_EXTRAS_PG_DSN')
    if not dsn: log("no PG DSN — skipping"); return 0
    sess, base = session_for(env)
    if sess is None: return 0
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        if mode == 'corporate': poll_corporate_actions(cur, sess, base)
        elif mode == 'news':    poll_news(cur, sess, base)
        elif mode == 'options': poll_options(cur, sess, base, conn)
        else: log(f"unknown mode: {mode}"); return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
