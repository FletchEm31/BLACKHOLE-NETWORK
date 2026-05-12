# BHN Alerting Architecture

How BHN gets paged when something breaks. Grafana fires alert rules against PostgreSQL data source, posts to an n8n webhook, the `bhn-alert-router` workflow deduplicates + logs to PG + notifies the operator via ntfy push (primary today) and Twilio SMS (once A2P campaign approves).

**Phase split:**
- **v1 (committed today):** 2 alerts (node down, PG unreachable) + full plumbing
- **v2 (next session):** 3 more alerts (NVMe %, HDD %, n8n failure rate) — needs new host_metrics + n8n_execution_stats tables

---

## Architecture

```
┌────────────┐                                            ┌────────────────┐
│  Grafana   │ alert rules run every 1m vs PG datasource │ PostgreSQL     │
│  (LA)      │◄─────────────────────────────────────────►│ eventhorizon   │
│            │                                            └────────────────┘
└──────┬─────┘
       │ alert fires → webhook POST
       │   (token-authed; tunnel-bound URL)
       ▼
┌──────────────────────┐
│  n8n on LA           │
│  bhn-alert-router    │
│  workflow            │
│  ┌────────────────┐  │
│  │ Webhook        │  │ accept POST
│  │ Code: parse +  │  │ extract rule_uid, severity, value, dedup_key
│  │   dedup key    │  │
│  │ PG: dedup check│  │ skip if same dedup_key fired in last 15m
│  │ HTTP: ntfy     │  │ always — works today
│  │ HTTP: Twilio   │  │ try; ignore failure (A2P pending)
│  │ PG: INSERT     │  │ alerts table audit row
│  └────────────────┘  │
└──────┬───────┬───────┘
       │       │
       ▼       ▼
┌──────────┐  ┌──────────────┐
│ ntfy.sh  │  │ Twilio API   │
│ topic    │  │ (queued      │
│ eh-alerts│  │  pending A2P)│
│-hayden-  │  └──────────────┘
│ x7k2     │           │
└────┬─────┘           │ once approved →
     │                 ▼
     ▼              SMS to +1 ____
operator's phone    operator's cell
(immediately,
 today)
```

---

## v1 deliverables (this commit)

| File | Purpose |
|------|---------|
| `sql/alerts-schema.sql` | New `alerts` audit table. Columns for state (firing/resolved), severity, dedup_key, sms_sent, ntfy_sent, raw_payload, resolved_at. INSERT for n8n_user; SELECT for agent_reader + grafana_reader. |
| `infrastructure/grafana/provisioning/alerting/bhn-alerts.yaml` | Provisioned alert rules: `bhn-node-down` + `bhn-pg-unreachable`. Plus contact point pointing at the n8n webhook, plus notification policy with severity-based routing + repeat intervals. |
| `infrastructure/docs/bhn-alerting-architecture.md` | This doc. |

Plus the **n8n workflow spec below** that the operator builds in the n8n UI (n8n 2.8.4 CLI/JSON import unsafe per `feedback_n8n_editor_hazards` memory).

---

## Deploy steps (operator-side)

### Step 1 — Apply the schema on LA

```bash
ssh root@10.8.0.1
sudo -u postgres psql -d eventhorizon -f /path/to/repo/sql/alerts-schema.sql
sudo -u postgres psql -d eventhorizon -c "\d alerts"
```

### Step 2 — Generate the webhook token

```bash
# On LA — generate the 32-hex secret that authenticates Grafana → n8n
openssl rand -hex 32 > /root/.bhn-alert-webhook-token
chmod 600 /root/.bhn-alert-webhook-token
echo "Webhook token: $(cat /root/.bhn-alert-webhook-token)"
```

Keep this value handy — it goes into BOTH the Grafana provisioning file AND the n8n webhook node URL path.

### Step 3 — Build the `bhn-alert-router` workflow in n8n UI

In n8n at `http://10.8.0.1:5678`, create a new workflow named `bhn-alert-router` per the spec below. **Don't refresh the browser tab** mid-edit (n8n 2.8.4 hazard). After every save, snapshot the DB:

```bash
cp /root/.n8n/database.sqlite /root/.n8n/database.sqlite.snap-$(date +%s)
```

#### Workflow spec — 7 nodes in a line

```
[1] Webhook  →  [2] Code: extract+dedup  →  [3] Postgres: dedup check
                                              │
                                              ▼ (if not deduped)
                                          [4] HTTP: ntfy POST
                                              │
                                              ▼
                                          [5] HTTP: Twilio (try; on error continue)
                                              │
                                              ▼
                                          [6] Postgres: INSERT alerts row
                                              │
                                              ▼
                                          [7] (terminal — no further nodes)
```

##### Node [1] — Webhook

- Type: `n8n-nodes-base.webhook`
- HTTP Method: `POST`
- Path: `<BHN_ALERT_WEBHOOK_TOKEN>/grafana-alert` (use the 32-hex token from Step 2)
- Response Mode: `When Last Node Finishes`
- Response Code: `200`

##### Node [2] — Code: extract + dedup key

- Type: `n8n-nodes-base.code`
- Language: JavaScript
- Run mode: `Run Once for All Items`

```javascript
// Grafana webhook payload — see https://grafana.com/docs/grafana/latest/alerting/configure-notifications/template-notifications/reference/
const body = $input.first().json;
const alerts = body.alerts || [];

return alerts.map(a => {
  const labels = a.labels || {};
  const annotations = a.annotations || {};
  const state = a.status === 'resolved' ? 'resolved' : 'firing';
  const severity = labels.severity || 'warning';
  const dedupKey = labels.dedup_key || labels.alertname || 'unknown';
  // values is an object of refId -> {Value, Labels}
  const value = (a.values && Object.values(a.values)[0]) || null;

  return {
    json: {
      rule_uid:        labels.__alert_rule_uid__ || labels.alertname || 'unknown',
      rule_name:       labels.alertname || annotations.summary || 'BHN alert',
      severity:        severity,
      state:           state,
      summary:         annotations.summary || '',
      description:     annotations.description || '',
      value_at_fire:   typeof value === 'number' ? value : null,
      dedup_key:       dedupKey,
      raw_payload:     a,
      ntfy_topic:      'eh-alerts-hayden-x7k2',     // existing operator topic from Pulse
      ntfy_server:     'https://ntfy.sh',
      twilio_to:       $env.TWILIO_OPERATOR_CELL || '',
      twilio_from:     $env.TWILIO_FROM_NUMBER || '',
    },
  };
});
```

##### Node [3] — Postgres: dedup check

- Type: `n8n-nodes-base.postgres`
- Credential: `Postgres EventHorizon` (rw)
- Operation: `Execute Query`
- Query:

```sql
SELECT COUNT(*)::int AS recent_count
FROM alerts
WHERE dedup_key = $1
  AND state = 'firing'
  AND fired_at > NOW() - INTERVAL '15 minutes';
```

- Query Parameters: `={{ [$json.dedup_key] }}`

##### Node [3.5] — IF (skip if deduped)

- Type: `n8n-nodes-base.if`
- Condition: `$('Postgres: dedup check').first().json.recent_count` `<=` `0`
- True branch → continue to node 4
- False branch → terminate (alert was just fired recently, don't spam)

##### Node [4] — HTTP: ntfy POST

- Type: `n8n-nodes-base.httpRequest`
- Method: `POST`
- URL: `={{ $('Code: extract+dedup').first().json.ntfy_server }}/{{ $('Code: extract+dedup').first().json.ntfy_topic }}`
- Send Headers:
  - `Title`: `={{ "BHN " + $('Code: extract+dedup').first().json.severity.toUpperCase() + ": " + $('Code: extract+dedup').first().json.rule_name }}`
  - `Priority`: `={{ $('Code: extract+dedup').first().json.severity === 'critical' ? 'urgent' : 'high' }}`
  - `Tags`: `={{ $('Code: extract+dedup').first().json.severity === 'critical' ? 'rotating_light' : 'warning' }}`
- Body (raw, text/plain): `={{ $('Code: extract+dedup').first().json.summary + "\n\n" + $('Code: extract+dedup').first().json.description }}`
- On Error: `Continue Regular Output` (don't fail the whole workflow if ntfy times out)

##### Node [5] — HTTP: Twilio SMS

- Type: `n8n-nodes-base.httpRequest`
- Method: `POST`
- URL: `https://api.twilio.com/2010-04-01/Accounts/{{ $env.TWILIO_ACCOUNT_SID }}/Messages.json`
- Authentication: `Generic Credential Type` → `Basic Auth` → credential `EH-Twilio`
- Send Body: `Form-Data`
- Body Parameters:
  - `To`: `={{ $('Code: extract+dedup').first().json.twilio_to }}`
  - `From`: `={{ $('Code: extract+dedup').first().json.twilio_from }}`
  - `Body`: `={{ "BHN " + $('Code: extract+dedup').first().json.severity.toUpperCase() + ": " + $('Code: extract+dedup').first().json.summary }}`
- On Error: `Continue Regular Output` (A2P pending — Twilio will return error until campaign approved)

##### Node [6] — Postgres: INSERT alert audit row

- Type: `n8n-nodes-base.postgres`
- Credential: `Postgres EventHorizon` (rw)
- Operation: `Execute Query`
- Query:

```sql
INSERT INTO alerts (
  rule_uid, rule_name, severity, state,
  summary, description, value_at_fire,
  dedup_key, sms_sent, sms_sid, ntfy_sent,
  raw_payload
) VALUES (
  $1, $2, $3, $4,
  $5, $6, $7,
  $8, $9, $10, $11,
  $12::jsonb
) RETURNING id;
```

- Query Parameters:

```
={{ [
  $('Code: extract+dedup').first().json.rule_uid,
  $('Code: extract+dedup').first().json.rule_name,
  $('Code: extract+dedup').first().json.severity,
  $('Code: extract+dedup').first().json.state,
  $('Code: extract+dedup').first().json.summary,
  $('Code: extract+dedup').first().json.description,
  $('Code: extract+dedup').first().json.value_at_fire,
  $('Code: extract+dedup').first().json.dedup_key,
  Boolean($('HTTP: Twilio SMS').first()?.json?.sid),
  $('HTTP: Twilio SMS').first()?.json?.sid || null,
  $('HTTP: ntfy POST').first()?.json !== undefined,
  JSON.stringify($('Code: extract+dedup').first().json.raw_payload)
] }}
```

##### Save + Publish + Activate

After all 7 nodes wired:
1. Save (Ctrl+S)
2. **Publish** (n8n 2.8.4 quirk — saving alone doesn't activate scheduled runs)
3. Activate toggle ON
4. Take a DB snapshot per the n8n hazards memory

### Step 4 — Deploy Grafana alert config + reload

```bash
# Copy provisioning YAML to LA
scp infrastructure/grafana/provisioning/alerting/bhn-alerts.yaml root@10.8.0.1:/etc/grafana/provisioning/alerting/

# On LA — substitute the webhook token placeholder
TOKEN=$(cat /root/.bhn-alert-webhook-token)
sed -i "s|<BHN_ALERT_WEBHOOK_TOKEN>|$TOKEN|g" /etc/grafana/provisioning/alerting/bhn-alerts.yaml

# Reload Grafana to pick up the new rules
systemctl restart grafana-server
sleep 5
systemctl is-active grafana-server

# Verify rules loaded
curl -fsS -u admin:$(cat /etc/grafana/admin-password 2>/dev/null) \
  http://10.8.0.1:3000/api/ruler/grafana/api/v1/rules 2>/dev/null \
  | python3 -m json.tool | head -30
```

### Step 5 — Set environment variables for the n8n workflow

In n8n's container env:

```bash
TWILIO_ACCOUNT_SID=<from Proton Pass: EH-Twilio-AccountSID>
TWILIO_FROM_NUMBER=+13109296201
TWILIO_OPERATOR_CELL=<operator's cell, from Proton Pass>
```

These get added via the standard n8n container recreate (per `reference_n8n_docker_patterns` memory — `docker stop n8n && docker rm n8n && docker run -d ... -e TWILIO_ACCOUNT_SID=... ...`).

### Step 6 — End-to-end test

Once all the above is in place:

```bash
# Trigger a synthetic alert by stopping PG briefly:
ssh root@10.8.0.1 'systemctl stop postgresql'
sleep 60   # wait for Grafana's 1m eval cycle + 2m for: clause to expire
ssh root@10.8.0.1 'systemctl start postgresql'

# Verify the alert flow:
sudo -u postgres psql -d eventhorizon -c "SELECT id, fired_at, rule_uid, state, sms_sent, ntfy_sent FROM alerts ORDER BY fired_at DESC LIMIT 5;"
# Should see a row for bhn-pg-unreachable with ntfy_sent=true; sms_sent=false (A2P pending)

# Phone should have received an ntfy push titled "BHN CRITICAL: BHN PostgreSQL unreachable"
```

**WARNING:** stopping PG breaks pulse + weather + news writes + HORIZON. Only do the test during a known idle window OR use a different trigger (e.g., temporarily stop `bhn-heartbeat.sh` to trigger the node-down alert instead — less destructive).

---

## v2 follow-up scope (next session)

For the 3 metric-collection alerts:

1. **New schemas:**
   - `sql/host-metrics-schema.sql` — `(id, node, measured_at, nvme_pct, hdd_pct, load_avg_1m, mem_pct)`
   - `sql/n8n-execution-stats-schema.sql` — `(measured_at, window_minutes, total_execs, failed_execs, pct_fail)`

2. **New scripts:**
   - `scripts/bhn-host-metrics.sh` — runs every 5 min via cron on each node, INSERTs into PG via tunneled psql
   - New n8n workflow `bhn-n8n-stats-sync` — reads its own SQLite, writes to PG every 5 min

3. **Three more Grafana rules** added to `bhn-alerts.yaml`:
   - `bhn-nvme-high` (>80%)
   - `bhn-hdd-high` (>80%)
   - `bhn-n8n-failure-rate` (>10% over last 30 min, min 10 executions)

4. **HORIZON awareness** — system prompt addition so HORIZON knows about the new tables + can answer "show me last night's NVMe trend" type queries.

---

## Security posture

- **Webhook token**: 32-hex random, stored in `/root/.bhn-alert-webhook-token` (mode 0600) on LA, NOT committed to repo. Acts as bearer auth on the Grafana → n8n hop.
- **Webhook binding**: n8n listens on the tunnel IP `10.8.0.1:5678` (already VPN-only per existing setup). External attackers cannot reach the webhook even if they knew the token, because LA's UFW blocks public 5678.
- **Twilio credentials**: existing `EH-Twilio` n8n credential — never in config files or alerts table.
- **ntfy topic**: `eh-alerts-hayden-x7k2` — the existing operator-only topic from Pulse. Anyone who learns the topic name can publish to it, but ntfy.sh's subscribe model means only the operator's phone (with the topic pre-subscribed) receives pushes.
- **alerts table payload**: `raw_payload` contains the full Grafana webhook body, including label values. Not sensitive enough to encrypt; protected by PG access control (only n8n_user can write, only agent_reader + grafana_reader can read).

---

## Why this design

| Decision | Rationale |
|----------|-----------|
| Grafana as the alert source (not n8n directly polling) | Grafana already runs, already has the PG data source, already evaluates time-series queries. Adding cron + Bash for the same job is reinventing. |
| n8n as the routing/dedup layer (not direct Grafana → Twilio) | Twilio API needs auth handling + retries + dedup tracking + audit logging. n8n already handles all that pattern. |
| Audit row in `alerts` table | Operator can ask HORIZON "what alerted overnight?" via existing query_db. Also gives Grafana a "recent alerts" panel data source if desired. |
| ntfy as primary channel today | Works immediately, no A2P gate, already wired in Pulse. SMS adds redundancy once A2P approves. |
| Dedup at 15 min default + per-severity override | Stops spam during a sustained outage. `repeat_interval` in the notification policy controls the re-fire cadence. |
| Webhook on tunnel-bound 10.8.0.1:5678 | Public exposure of n8n's webhook endpoint would be a real risk. Tunnel-bound limits it to authenticated peers. |
| HMAC vs bearer token in URL | Grafana doesn't support HMAC signing natively. Token-in-URL is the standard pattern that Grafana supports out of the box. Combined with tunnel-only binding, the security posture is equivalent. |
