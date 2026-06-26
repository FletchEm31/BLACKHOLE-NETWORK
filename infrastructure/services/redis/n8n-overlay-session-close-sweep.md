# HORIZON working-memory — session-close sweep sub-workflow

Cron-driven n8n sub-workflow that drains stale HORIZON sessions out of Redis
into long-term semantic storage. Companion to `n8n-overlay-chat.md` (which
fills Redis during live conversations); this one is the closer.

Builds the second half of Phase 5.5 working-memory. Layer on AFTER
`n8n-overlay-chat.md` has been chat-channel smoke-tested for at least 24h
so you have real stale sessions to drain.

Pairs with:
- `infrastructure/services/redis/n8n-overlay-chat.md` — the live-session wiring
- `infrastructure/services/redis/n8n-wiring.md` — full design (§ Session-close sweep)
- `sql/redis-memory-schema.sql` — `conversation_sessions` table
- `sql/memories-schema.sql` — `memories` table (target of the summarization write)

Pre-reqs:
- Chat overlay live and populating `conversation_sessions` rows + Redis keys
- `Postgres EventHorizon` and `EH HORIZON Redis (LA)` credentials already
  configured (reused from the chat overlay)
- HORIZON's existing `embed_text` tool endpoint URL captured — copy the URL
  from the live HORIZON workflow's `embed_text` HTTP Request tool node
  (typically `http://<BHN_WG_LA_IP>:8765/embed` or similar internal address)
- Sonnet credential `EventHorizonVPN-Claude` (reused)

> **Build this as a new standalone workflow, not nodes on the HORIZON
> workflow.** The sweep runs on its own cron timeline (every 15 min);
> wiring it into HORIZON's chat-trigger workflow would couple two unrelated
> execution flows.

---

## Workflow shell

In n8n UI: Workflows → New → name it **`HORIZON Session-Close Sweep`**.
Tag it `Claude-powered AI agent` to match the existing HORIZON workflow's tag.

---

## Topology

```
[ Cron: every 15 min ]
        │
        ▼
[ Find Stale Sessions ]         (Postgres SELECT — N rows, one per stale session)
        │
        ▼
[ Split In Batches (1) ]        (process one session at a time)
        │
        ▼
[ Load Turns from Redis ]       (Redis Execute Command — LRANGE)
        │
        ▼
[ Load Summary from Redis ]     (Redis, Get)
        │
        ▼
[ Build Summarization Input ]   (Code — chronological turns + prior summary)
        │
        ▼
[ Summarize via Sonnet ]        (LangChain Anthropic Chat — Sonnet 4.6, one-shot)
        │
        ▼
[ Embed Summary ]               (HTTP Request POST to /opt/eh-embed)
        │
        ▼
[ Insert into memories ]        (Postgres INSERT … RETURNING id)
        │
        ▼
[ Close Session Row ]           (Postgres UPDATE conversation_sessions)
        │
        ▼
[ Delete Redis Keys ]           (Redis Execute Command — DEL x3)
        │
        ▼
[ (loop back to Split In Batches until done) ]
```

No branches, no error handlers. Failure at any step leaves the session open
in PG + keys intact in Redis. The next cron firing finds the same session
and retries idempotently.

---

## Node-by-node UI parameters

### 1. Cron trigger

| Field | Value |
|-------|-------|
| Node type | **Schedule Trigger** |
| Trigger interval | Every 15 minutes |
| Position | x: 0, y: 0 |

### 2. Find Stale Sessions

| Field | Value |
|-------|-------|
| Node type | **Postgres** |
| Operation | Execute Query |
| Credential | `Postgres EventHorizon` |
| Position | x: 240, y: 0 |

```sql
SELECT id::text AS session_id,
       channel,
       turn_count,
       last_seen_at,
       started_at
FROM conversation_sessions
WHERE closed_at IS NULL
  AND last_seen_at < NOW() - INTERVAL '30 minutes'
  AND related_memory_id IS NULL
ORDER BY last_seen_at ASC
LIMIT 50;
```

Why these filters:
- `closed_at IS NULL` — only open sessions
- `last_seen_at < NOW() - 30 min` — "stale" threshold; matches the chat
  overlay's 12h Redis TTL but doesn't wait that long (most chat sessions
  finish well under 12h)
- `related_memory_id IS NULL` — idempotency. If we already wrote the
  memories row but crashed before closing the session, skip the
  Sonnet/embed/insert path on retry (only `Close Session Row` remains —
  see Idempotency section below)
- `LIMIT 50` — cap work per cron firing

### 3. Split In Batches

| Field | Value |
|-------|-------|
| Node type | **Split In Batches** |
| Batch Size | `1` |
| Position | x: 480, y: 0 |

Process one stale session at a time so the downstream Redis/PG/Sonnet
nodes can reference a single `session_id`.

### 4. Load Turns from Redis

| Field | Value |
|-------|-------|
| Node type | **Redis** |
| Operation | Execute Command |
| Credential | `EH HORIZON Redis (LA)` |
| Command | `LRANGE horizon:sess:{{ $json.session_id }}:turns 0 -1` |
| Output property | `turns_raw` |
| Position | x: 720, y: 0 |

`0 -1` returns the entire list. If the list is empty (Redis TTL'd it),
`turns_raw` will be `[]` — handled in Build Summarization Input below.

### 5. Load Summary from Redis

| Field | Value |
|-------|-------|
| Node type | **Redis** |
| Operation | Get |
| Credential | `EH HORIZON Redis (LA)` |
| Key | `horizon:sess:{{ $json.session_id }}:summary` |
| Key Type | Automatic |
| Property Name | `running_summary` |
| Position | x: 960, y: 0 |

### 6. Build Summarization Input

| Field | Value |
|-------|-------|
| Node type | **Code** |
| Language | JavaScript |
| Position | x: 1200, y: 0 |

```javascript
// Build the Sonnet prompt input from Redis turns + summary.
// turnsRaw is newest-first (from LPUSH). Reverse to chronological for the
// LLM so the conversation reads top-to-bottom.

const sessionId = $('Find Stale Sessions').item.json.session_id
                  || $('Split In Batches').item.json.session_id;
const channel = $('Find Stale Sessions').item.json.channel
                || $('Split In Batches').item.json.channel
                || 'unknown';
const turnCount = $('Find Stale Sessions').item.json.turn_count
                  || $('Split In Batches').item.json.turn_count
                  || 0;
const startedAt = $('Find Stale Sessions').item.json.started_at
                  || $('Split In Batches').item.json.started_at;
const lastSeenAt = $('Find Stale Sessions').item.json.last_seen_at
                   || $('Split In Batches').item.json.last_seen_at;

const turnsRaw = $('Load Turns from Redis').item.json.turns_raw || [];
const runningSummary = $('Load Summary from Redis').item.json.running_summary || '';

const turns = Array.isArray(turnsRaw) ? [...turnsRaw].reverse() : [];
let turnsMd = '';
for (const raw of turns) {
  try {
    const t = JSON.parse(raw);
    turnsMd += `USER: ${t.user}\nHORIZON: ${t.horizon}\n\n`;
  } catch (_) {
    // skip malformed
  }
}

// If both Redis stores TTL'd before sweep got to them, fall back to a
// stub summary so the memories row still captures session metadata.
const haveContent = (turns.length > 0) || runningSummary.length > 0;

return [{
  json: {
    session_id: sessionId,
    channel,
    turn_count: turnCount,
    started_at: startedAt,
    last_seen_at: lastSeenAt,
    running_summary: runningSummary,
    turns_md: turnsMd,
    have_content: haveContent,
    sonnet_input: haveContent
      ? `## Prior running summary\n${runningSummary || '(none)'}\n\n## Full turns (chronological)\n${turnsMd || '(none recovered)'}`
      : `(Session ${sessionId} on channel ${channel} expired before sweep; no content recoverable. Capture as metadata-only memory.)`,
  },
}];
```

### 7. Summarize via Sonnet

| Field | Value |
|-------|-------|
| Node type | **Anthropic Chat Model** (LangChain) |
| Model | `claude-sonnet-4-6` |
| Credential | `EventHorizonVPN-Claude` |
| Position | x: 1440, y: 0 |
| Connection target | direct call, not via AI Agent |

System message:

```
You are HORIZON, summarizing a closed conversation session for long-term operator memory.

Output a 1-2 paragraph summary in the operator's terse cadence. Lead with what was decided, fixed, or learned. Then context: who, what, when, why. No filler ("In this session…", "The user asked…"). No headings. Plain prose.

If the session is metadata-only (no turn content recovered), write one sentence capturing channel + duration + turn_count only — flag this with the prefix "[metadata-only]".

Match the operator's voice: direct, technical, comfortable with abbreviations. Use backticks for file paths, IPs, command names. Don't moralize, don't summarize the summary itself, don't add a "to remember" coda.
```

User message:

```
{{ $json.sonnet_input }}

Session metadata:
- session_id: {{ $json.session_id }}
- channel: {{ $json.channel }}
- turn_count: {{ $json.turn_count }}
- started_at: {{ $json.started_at }}
- last_seen_at: {{ $json.last_seen_at }}
```

Max tokens: 400 (1-2 paragraphs cap). Temperature: 0.3 (consistency over creativity).

### 8. Embed Summary

| Field | Value |
|-------|-------|
| Node type | **HTTP Request** |
| Method | POST |
| URL | (copy from `embed_text` tool in HORIZON workflow) |
| Body Content-Type | JSON |
| Body | `{ "text": "{{ $('Summarize via Sonnet').item.json.text }}" }` |
| Output property | `embed_response` |
| Position | x: 1680, y: 0 |

Expected response shape (verify against your `/opt/eh-embed` deployment):
```json
{ "embedding": [0.123, ..., 0.789] }
```

If the embedder returns a string-formatted pgvector literal instead of a
raw array (`embed_text` tool currently does this for ease of pasting into
SQL), keep that as-is and let the next node bind it directly.

### 9. Insert into memories

| Field | Value |
|-------|-------|
| Node type | **Postgres** |
| Operation | Execute Query |
| Credential | `Postgres EventHorizon` |
| Position | x: 1920, y: 0 |

```sql
INSERT INTO memories
  (memory_type, title, content, embedding, source, metadata, importance)
VALUES
  ('conversation',
   'HORIZON session ' || '{{ $('Build Summarization Input').item.json.session_id }}',
   $${{ $('Summarize via Sonnet').item.json.text }}$$,
   '{{ $('Embed Summary').item.json.embedding }}'::vector,
   'horizon-session-close',
   jsonb_build_object(
     'session_id', '{{ $('Build Summarization Input').item.json.session_id }}',
     'channel',    '{{ $('Build Summarization Input').item.json.channel }}',
     'turn_count', {{ $('Build Summarization Input').item.json.turn_count }},
     'started_at', '{{ $('Build Summarization Input').item.json.started_at }}',
     'closed_at',  NOW()
   ),
   5)
RETURNING id;
```

Notes:
- `$$ ... $$` dollar-quoting wraps the summary text so apostrophes/quotes
  in the Sonnet output don't break the SQL
- Importance 5 is mid — session memories aren't load-bearing like
  `operator_pref` (importance 8) but more useful than `observation` (3-4)
- The pgvector literal binding depends on the embedder response shape; if
  it returns a raw array, you may need to format it as
  `'[0.123,0.456,...]'::vector` in a Code node before this INSERT

### 10. Close Session Row

| Field | Value |
|-------|-------|
| Node type | **Postgres** |
| Operation | Execute Query |
| Credential | `Postgres EventHorizon` |
| Position | x: 2160, y: 0 |

```sql
UPDATE conversation_sessions
SET closed_at         = NOW(),
    summary           = $${{ $('Summarize via Sonnet').item.json.text }}$$,
    summary_embedding = '{{ $('Embed Summary').item.json.embedding }}'::vector,
    related_memory_id = {{ $('Insert into memories').item.json.id }},
    turn_count        = {{ $('Build Summarization Input').item.json.turn_count }}
WHERE id = '{{ $('Build Summarization Input').item.json.session_id }}'::uuid;
```

### 11. Delete Redis Keys

| Field | Value |
|-------|-------|
| Node type | **Redis** |
| Operation | Execute Command |
| Credential | `EH HORIZON Redis (LA)` |
| Command | `DEL horizon:sess:{{ $('Build Summarization Input').item.json.session_id }}:turns horizon:sess:{{ $('Build Summarization Input').item.json.session_id }}:summary horizon:sess:{{ $('Build Summarization Input').item.json.session_id }}:meta` |
| Position | x: 2400, y: 0 |

Loop output back to Split In Batches' input for the next stale session.

---

## Idempotency notes

The `Find Stale Sessions` query already filters `related_memory_id IS NULL`,
so any session that previously made it through `Insert into memories` won't
re-enter the pipeline even if `Close Session Row` failed before completing.

For partial-failure recovery in step 10 (`Close Session Row`): the next
sweep cycle filters that row out (it has `related_memory_id` now), so the
operator can manually run:

```sql
UPDATE conversation_sessions
SET closed_at = (SELECT created_at FROM memories WHERE id = related_memory_id),
    summary           = (SELECT content FROM memories WHERE id = related_memory_id),
    summary_embedding = (SELECT embedding FROM memories WHERE id = related_memory_id)
WHERE closed_at IS NULL
  AND related_memory_id IS NOT NULL;
```

…to reconcile orphans, OR just leave them — the `closed_at IS NULL`
+ `related_memory_id IS NOT NULL` state is detectable for cleanup later.

For step 11 partial failure (Redis keys not deleted): no harm, Redis TTL
will evict them at 12h regardless. If you want eager cleanup, run a one-off:

```bash
redis-cli -a "$REDIS_PASSWORD" --scan --pattern 'horizon:sess:*' | while read k; do
  sess_id=$(echo "$k" | cut -d: -f3)
  closed=$(psql -d eventhorizon -tAc "SELECT closed_at FROM conversation_sessions WHERE id = '$sess_id'::uuid")
  if [ -n "$closed" ]; then redis-cli -a "$REDIS_PASSWORD" DEL "$k"; fi
done
```

---

## Smoke test

### Manual trigger before enabling cron

1. **Create a stale session by hand** (in the live HORIZON candidate workflow,
   send one chat turn, then wait 30+ minutes — or shortcut by setting
   `last_seen_at = NOW() - INTERVAL '31 minutes'` on a test row in
   `conversation_sessions`):

   ```sql
   UPDATE conversation_sessions
   SET last_seen_at = NOW() - INTERVAL '31 minutes'
   WHERE id = '<your-test-session-id>'::uuid;
   ```

2. **Click "Execute Workflow"** on the sweep workflow in n8n UI.

3. **Expect:**
   - `Find Stale Sessions` returns 1 row
   - `Summarize via Sonnet` runs, produces 1-2 paragraph summary
   - `Insert into memories` returns a new `id`
   - `Close Session Row` updates the conversation_sessions row
     (`closed_at` populated, `related_memory_id` set, `summary` populated)
   - `Delete Redis Keys` removes the three keys

4. **Verify post-run:**

   ```sql
   SELECT id, closed_at, related_memory_id, length(summary) AS sum_len
   FROM conversation_sessions
   WHERE id = '<test-session-id>'::uuid;

   SELECT id, title, memory_type, importance, length(content) AS content_len
   FROM memories
   WHERE source = 'horizon-session-close'
   ORDER BY id DESC LIMIT 1;
   ```

   ```bash
   redis-cli -a "$REDIS_PASSWORD" KEYS 'horizon:sess:<test-session-id>:*'
   # expect: empty
   ```

5. **Re-execute the workflow manually.** The same test session should now
   NOT appear in `Find Stale Sessions` (filtered by `related_memory_id IS
   NULL`). Other stale sessions are picked up. Confirms idempotency.

### Enable cron

Workflows → HORIZON Session-Close Sweep → Activate (toggle top right).
Schedule trigger fires every 15 min thereafter.

---

## Observability

Add to your 2026-05-12-style HORIZON health monitoring (morning briefing M2,
eventually):

```sql
-- Open sessions count (should stay in single digits)
SELECT count(*) FROM conversation_sessions WHERE closed_at IS NULL;

-- Sessions awaiting close (stale > 30min, no memories row yet)
SELECT count(*) FROM conversation_sessions
WHERE closed_at IS NULL
  AND last_seen_at < NOW() - INTERVAL '30 minutes'
  AND related_memory_id IS NULL;

-- Sweep throughput (closures per day)
SELECT date_trunc('day', closed_at) AS day, count(*)
FROM conversation_sessions
WHERE closed_at > NOW() - INTERVAL '7 days'
GROUP BY 1 ORDER BY 1 DESC;

-- Average summary length (proxy for content density)
SELECT avg(length(content))::int AS avg_summary_chars,
       avg(turn_count)::numeric(10,2) AS avg_turns
FROM conversation_sessions cs
JOIN memories m ON m.id = cs.related_memory_id
WHERE cs.closed_at > NOW() - INTERVAL '7 days';
```

If "awaiting close" creeps above ~10, sweep is failing somewhere — check
the n8n workflow execution log. Common causes:
- `/opt/eh-embed` is down → embed step 500s
- Sonnet rate-limited → step 7 times out
- Redis keys TTL'd before sweep got to them → `have_content=false` path
  fires, produces metadata-only memory (acceptable)

---

## What this overlay does NOT cover

- **Cross-session topic search.** `conversation_sessions.summary_embedding`
  lets you query "find past sessions about X" via pgvector. No dashboard
  surfaces this yet; add to HORIZON's `query_db` tool description once
  the table has 10+ closed sessions of accumulated content.
- **Per-channel sweep thresholds.** Single 30-min threshold for all
  channels right now. Voice/SMS may want shorter (e.g., 10 min) since
  those sessions are inherently shorter. Tune after observing real
  distributions.
- **Re-summarization of mid-session running summary.** That belongs in
  the LIVE chat overlay (every K=5 turns), not the sweep. See
  `n8n-wiring.md` § "Re-summarize every K=5 turns" for that branch
  (deferred until basic chat overlay is stable).
