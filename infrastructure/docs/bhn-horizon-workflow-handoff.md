# BHN HORIZON workflow expansion — operator handoff

Items 27, 28, 30, 34 from the 2026-05-13 monitoring expansion involve editing the HORIZON workflow JSON in n8n (workflow ID `fTFjaf2Q2aQrOPsY`, repo copy `n8n-workflows/bhn-horizon.json`). The data pipeline + supporting scripts are committed; what's left is the n8n UI work.

This doc is the specification — node-by-node + SQL-by-SQL. Operator does the UI work in n8n, exports the updated workflow JSON, commits the JSON back to `n8n-workflows/`.

---

## Item 28 — fix existing known issues

| Issue | Fix |
|---|---|
| **Token logging: rewire as parallel branch** | The Token Logging node currently sits in-line and blocks the response on a PG write. Move it to a parallel branch off the same trigger so failures don't block HORIZON's reply. In n8n: split the trigger output → main reply path + token-log path, set token-log node to `continueOnFail = true`. |
| **Format Memory Block: Code node bug** | Open the Code node. Confirm: are you using `$json.foo` or `items[0].json.foo`? n8n's Code node uses the latter idiom in current versions. Common breakage: undefined check on optional fields. Wrap field accesses with `?.` (`items[0].json.memory?.content`) and provide defaults. |
| **chatTrigger: set public: true** | Open the chatTrigger node → Options → toggle `Make Chat Publicly Available` on. Confirm the public URL is reachable (it's a per-workflow path under `/webhook/chat/...`). Use a long random token in the URL since `public` removes auth. |

After all three: save workflow, export JSON, replace `n8n-workflows/bhn-horizon.json` in repo, commit.

---

## Item 27 — Wire SearXNG into HORIZON

Replace any "web search" tool HORIZON uses (or add a new one) with an HTTP Request node pointing at Frankfurt's SearXNG instance at `http://10.9.0.2:8089`.

**n8n HTTP Request node config:**

```
Method:    GET
URL:       http://10.9.0.2:8089/search
Query Parameters:
  q          = {{ $json.query }}
  format     = json
  categories = general,news
  pageno     = 1
Headers:
  User-Agent = bhn-horizon/1.0
Authentication: None
Response Format: JSON
```

Wire into HORIZON's tool-list via a Tool node, with description: `Search the web privately via Frankfurt SearXNG. Use for current events, fact-checking, anything not in BHN's memory.`

Parse the response: `$json.results[0..9]` returns `{title, url, content, engine}`.

---

## Item 30 — Security alert automation

A separate n8n workflow (NOT inside HORIZON's chat workflow). Polls `node_logs_summary` every 15 min; classifies by severity; routes immediate SMS for P1/P2 (critical/high), digests P3 into hourly batches.

**Workflow shape:**

```
Cron (every 15 min)
  → PostgreSQL Execute: see Query 1 below
    → IF: severity_critical > 0 OR severity_high > 0   (P1/P2)
      → Twilio SMS: per-event SMS to operator
      → PostgreSQL UPDATE: mark events as auto-acknowledged
    → ELSE                                              (P3)
      → Set node: append to /tmp/bhn-p3-digest.json (or PG staging table)
Cron (every hour)
  → PostgreSQL Execute: see Query 2 below
    → IF rows > 0
      → Twilio SMS: P3 digest
      → PostgreSQL UPDATE: mark digested events as auto-acknowledged
```

**Query 1 — P1/P2 events not yet acknowledged (run every 15 min):**

```sql
SELECT id, node_name, source, signature, severity, event_time, src_ip, meta
FROM node_logs
WHERE severity IN ('critical', 'high')
  AND event_time > NOW() - INTERVAL '20 minutes'
  AND meta->>'horizon_acked' IS NULL
ORDER BY event_time DESC;
```

After SMS, update each row:

```sql
UPDATE node_logs
SET meta = meta || jsonb_build_object('horizon_acked', NOW()::text)
WHERE id = $event_id;
```

**Query 2 — P3 hourly digest:**

```sql
SELECT node_name, source, COUNT(*) AS n,
       array_agg(DISTINCT signature) FILTER (WHERE signature IS NOT NULL) AS sigs
FROM node_logs
WHERE severity IN ('medium', 'low')
  AND event_time > NOW() - INTERVAL '1 hour'
  AND (meta->>'horizon_acked') IS NULL
GROUP BY node_name, source
HAVING COUNT(*) > 0;
```

SMS template (≤300 chars):

```
BHN P3 digest {{NOW}}:
{{ for each row }}- {{node}} {{source}}: {{n}} ({{sigs|join(',')}}){{end}}
```

---

## Item 34 — Operator SMS command expansion

Add a webhook receiver workflow that listens for inbound Twilio SMS, parses the first word as a command, executes, replies with the result.

**Workflow shape:**

```
Webhook Trigger (POST, path = /sms-command/<token>)
  → Set: extract Body = first-word.toUpperCase()
  → Switch (route by first word):
      "STATUS"      → Execute Command: /usr/local/sbin/bhn-horizon-briefing.py
                    → Twilio SMS reply with output
      "TRADES"      → PG query: SELECT … FROM strategy_performance WHERE date = CURRENT_DATE
                    → Format → Twilio reply
      "SECURITY"    → PG query: SELECT … FROM node_logs_summary WHERE window_start > NOW() - INTERVAL '24h'
                    → Format → Twilio reply
      "HALT"        → SSH Execute: python3 /opt/bhn/trading/master_killswitch.py halt --reason "SMS HALT from operator"
                    → Twilio reply confirming halt
      "NODE <name>" → PG query: SELECT … FROM nodes JOIN tor_relay_stats … WHERE name LIKE '%name%'
                    → Format → Twilio reply
      "TOR"         → Execute Command: psql -c "SELECT DISTINCT ON (node) node, …"
      "BANDWIDTH"   → PG query against node_bandwidth_stats top-3 consumers
      "MARKET"      → PG query: top symbols by signal volume + latest prices
      "CRYPTO"      → PG query: SELECT … FROM crypto_market_data WHERE measured_at > NOW() - INTERVAL '20 min'
      "PREDICT"     → PG query: SELECT … FROM prediction_market_data ORDER BY volume_24h DESC LIMIT 5
      default       → Twilio reply: "Unknown command. Try STATUS / TRADES / SECURITY / HALT / NODE <name> / TOR / BANDWIDTH / MARKET / CRYPTO / PREDICT"
```

**Twilio inbound configuration:** in Twilio console, set the operator number's "SMS messaging webhook" to the n8n workflow's public URL with the per-workflow token. Verify the auth-token header in the workflow's Webhook node so only Twilio-originating requests succeed (`X-Twilio-Signature` HMAC verification — see Twilio's webhook security docs).

**Operator-source verification:** the Switch node should also gate on `$json.From == '<operator-phone-from-PM>'` — any SMS from a non-operator number gets dropped without reply (don't let strangers `HALT` the trading framework by texting the inbound number).

---

## Item 33 — HORIZON memory expansion (already done, just to record)

All new monitoring + market-data tables have `GRANT SELECT TO agent_reader` via `sql/horizon-agent-reader-grants-verify.sql`. HORIZON's `query_db` tool can read them as-is.

For HORIZON to know which tables to query for which question, update the system prompt or tool-description schema in the HORIZON workflow:

```
Available BHN monitoring tables (LA PG, eventhorizon DB, read via query_db):

- nodes                      → 4-node registry + last_seen heartbeat
- wg_peer_stats              → WG peer bandwidth + handshake (5-min snapshots)
- wg_sessions                → per-peer session boundaries
- tor_relay_stats            → Tor relay accounting + flags + circuits (per node + consensus)
- node_bandwidth_stats       → vnstat hour/day/month per interface per node
- node_resource_stats        → CPU/RAM/load per node
- node_disk_stats            → per-mount usage
- node_logs                  → Suricata + CrowdSec alerts per node
- node_logs_summary          → 15-min aggregated counts + top signatures
- crowdsec_decisions         → active bans/captchas
- fail2ban_events            → per-jail banned IPs
- connection_snapshots       → conntrack table (high volume, 14-day retention)
- iptables_stats             → per-rule counters
- container_stats            → docker per-container CPU/mem
- pg_activity_snapshots, pg_query_stats, pg_table_stats → PG workload
- n8n_execution_stats        → workflow run history
- ssh_sessions, ssh_commands → SSH audit
- proxy_request_logs         → tinyproxy CONNECT events (Hillsboro)
- dns_query_log              → DNS queries from dnscrypt-proxy
- market_bars, market_ticks  → Alpaca prices (partitioned by timeframe)
- order_events               → trading order lifecycle
- prediction_market_data     → Kalshi + Polymarket
- crypto_market_data         → CoinGecko top-N
- macro_indicators           → FRED series
- analyst_data, earnings_data → Finnhub
- energy_prices, agriculture_prices → EIA / USDA
- corporate_actions, alpaca_news, options_chain_snapshots → Alpaca REST extras
- market_signals             → FMP + Quiver feeds
- strategy_performance, paper_trades, circuit_breaker_log → trading audit

Use DISTINCT ON (node) ORDER BY measured_at DESC for "current" per-node queries.
```

---

## Item 32 — Node offline detection (already done)

Committed in `ef830eb` (recover script) + `800b89c` (10-min alert). No workflow editing required — Grafana → existing webhook contact point handles the routing.

---

## What's left for operator's next n8n session

1. Open n8n at `http://10.8.0.1:5678`
2. Item 28 fixes (1-2 min each)
3. Add SearXNG HTTP node to HORIZON (item 27)
4. Create new "Security alert automation" workflow (item 30)
5. Create new "SMS command receiver" workflow (item 34)
6. Update HORIZON's system prompt with the table catalog (item 33)
7. Wire the two new scripts (item 29 briefing + item 35 weekly report) into cron-triggered workflows
8. Export all touched workflows to JSON, commit to `n8n-workflows/`

All other monitoring infrastructure (35 schemas, 32 collectors, 9 dashboards, multiple alerts) is committed + pushed.
