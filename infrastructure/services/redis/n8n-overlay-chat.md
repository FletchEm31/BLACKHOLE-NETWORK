# HORIZON working-memory — n8n overlay for the chat channel

Step-by-step UI checklist to add Redis short-term memory to the live `HORIZON`
n8n workflow. Implements the chat-channel slice of `n8n-wiring.md`. SMS / voice
channels and the session-close sweep sub-workflow are deferred to follow-on
sessions per the rollout sequence in that doc.

Pairs with:
- `infrastructure/services/redis/docker-compose.yml` — the deployed Redis
- `infrastructure/services/redis/docker-run.md` — Redis service deploy
- `sql/redis-memory-schema.sql` — `conversation_sessions` table
- `infrastructure/services/redis/n8n-wiring.md` — full design

Pre-reqs (verify before starting):
- `bhn-horizon-redis` container is up on LA (`docker ps | grep horizon-redis`)
- `conversation_sessions` exists (`psql -d eventhorizon -c "\d conversation_sessions"`)
- New n8n credential **`EH HORIZON Redis (LA)`** created per `docker-run.md`,
  test-connection green
- `Postgres EventHorizon` (rw) credential already exists — reused for the
  open/bump session-row writes

> **Test on a workflow COPY first.** In n8n: Workflows → HORIZON → Duplicate.
> Rename the copy `HORIZON (working memory candidate)`. Apply this overlay to
> the copy. Smoke-test 10 chat turns. Only then deactivate the original and
> activate the candidate.

---

## Overlay topology

```
[ When chat message received ]
        │
        ▼
[ Open Session Row ]            (Postgres, INSERT … ON CONFLICT DO NOTHING)
        │
        ▼
[ Load WM: GET summary ]        (Redis, Get)
        │
        ▼
[ Load WM: LRANGE turns ]       (Redis, Execute Command — LRANGE)
        │
        ▼
[ Load WM: HGETALL meta ]       (Redis, Execute Command — HGETALL)
        │
        ▼
[ Build WM Context ]            (Code, JS — flatten into recent_turns_md +
        │                        running_summary; emit session_id passthrough)
        ▼
[ AI Agent ]                    (existing, system prompt edited)
        │
        ▼
[ Write WM: LPUSH turn ]        (Redis, Execute Command — LPUSH+LTRIM)
        │
        ▼
[ Write WM: HSET meta ]         (Redis, Execute Command — HSET+HSETNX)
        │
        ▼
[ Bump Session Row ]            (Postgres, UPDATE turn_count, last_seen_at)
        │
        ▼
[ Extract Token Usage ] → [ Log Tokens ]   (existing, unchanged)
```

The existing orphaned `Embed Chat Query → Retrieve Chat Memories →
Format Memory Block` branch stays as-is (it's the disabled pgvector recall
path the system prompt already references). No edits to those three nodes.

---

## New nodes — exact UI parameters

For each node below: in n8n UI, click `+` on the canvas, search for the type,
configure the parameters listed, then connect inputs/outputs as in the
topology diagram above.

### 1. Open Session Row

| Field | Value |
|-------|-------|
| Node type | **Postgres** |
| Operation | Execute Query |
| Credential | `Postgres EventHorizon` |
| Query | see SQL below |
| Position (canvas hint) | x: 100, y: 120 |

```sql
INSERT INTO conversation_sessions (id, channel, metadata)
VALUES (
  '{{ $json.sessionId }}'::uuid,
  'chat',
  jsonb_build_object('source', 'n8n-chat-trigger')
)
ON CONFLICT (id) DO NOTHING
RETURNING id;
```

Notes:
- `$json.sessionId` comes from n8n's chat trigger output (built-in field)
- `ON CONFLICT DO NOTHING` makes this idempotent across every turn
- The `RETURNING id` keeps the row threaded into the next node's `$json`

### 2. Load WM: GET summary

| Field | Value |
|-------|-------|
| Node type | **Redis** |
| Operation | Get |
| Credential | `EH HORIZON Redis (LA)` |
| Key | `horizon:sess:{{ $('Open Session Row').item.json.id }}:summary` |
| Key Type | Automatic |
| Property Name | `running_summary` |
| Position | x: 100, y: 240 |

### 3. Load WM: LRANGE turns

| Field | Value |
|-------|-------|
| Node type | **Redis** |
| Operation | Execute Command |
| Credential | `EH HORIZON Redis (LA)` |
| Command | `LRANGE horizon:sess:{{ $('Open Session Row').item.json.id }}:turns 0 19` |
| Output property | `recent_turns_raw` |
| Position | x: 100, y: 360 |

If your n8n version doesn't surface "Execute Command", install the Redis
Tools community node OR fall back to one of:
- Set `--enable-execute-command` on the Redis credential if exposed
- Use a Code node + ioredis (requires `NODE_FUNCTION_ALLOW_EXTERNAL=ioredis`
  on the n8n container env)

### 4. Load WM: HGETALL meta

| Field | Value |
|-------|-------|
| Node type | **Redis** |
| Operation | Execute Command |
| Credential | `EH HORIZON Redis (LA)` |
| Command | `HGETALL horizon:sess:{{ $('Open Session Row').item.json.id }}:meta` |
| Output property | `session_meta` |
| Position | x: 100, y: 480 |

### 5. Build WM Context

| Field | Value |
|-------|-------|
| Node type | **Code** |
| Language | JavaScript |
| Position | x: 100, y: 600 |

```javascript
// Flatten the three Redis loads into AI-Agent-injectable strings.
// Output passes through to AI Agent; system prompt references
// {{ $json.recent_turns_md }} and {{ $json.running_summary }}.

const sessionId = $('Open Session Row').item.json.id;
const summary   = $('Load WM: GET summary').item.json.running_summary || '';
const turnsRaw  = $('Load WM: LRANGE turns').item.json.recent_turns_raw || [];
const userMsg   = $('When chat message received').item.json.chatInput
                  || $('When chat message received').item.json.message
                  || '';

// turnsRaw is newest-first from LPUSH+LRANGE 0 19. Reverse to chronological
// so the system prompt reads naturally top-to-bottom.
const turns = Array.isArray(turnsRaw) ? [...turnsRaw].reverse() : [];

let recent_turns_md = '';
for (const raw of turns) {
  try {
    const t = JSON.parse(raw);
    recent_turns_md += `USER: ${t.user}\nHORIZON: ${t.horizon}\n\n`;
  } catch (_) {
    // skip malformed
  }
}
if (!recent_turns_md) {
  recent_turns_md = '(no prior turns in this session)';
}

const running_summary = summary || '(no running summary yet)';

return [{
  json: {
    sessionId,
    chatInput: userMsg,
    recent_turns_md,
    running_summary,
    turn_count_loaded: turns.length,
  },
}];
```

### 6. AI Agent system-prompt edit

Open the existing `AI Agent` node. In the system message, add this block
near the top of the prompt (suggested: right after `## Voice & posture`
and before `## Threefold role`):

```
## Working memory for this session

The two blocks below are the operator's CURRENT session context — restore them
into your reasoning every turn. They live in Redis, expire 12h after last
activity, and persist long-term in PostgreSQL only after the session closes.

### Running summary
{{ $json.running_summary }}

### Recent turns (most recent last)
{{ $json.recent_turns_md }}
```

No other edits to the AI Agent node.

### 7. Write WM: LPUSH turn

| Field | Value |
|-------|-------|
| Node type | **Redis** |
| Operation | Execute Command |
| Credential | `EH HORIZON Redis (LA)` |
| Command | see below |
| Position | x: 420, y: 120 |

```
LPUSH horizon:sess:{{ $('Build WM Context').item.json.sessionId }}:turns {{ JSON.stringify({ user: $('Build WM Context').item.json.chatInput, horizon: $('AI Agent').item.json.output, ts: $now.toISO() }) }}
```

Then in the SAME node (or chain a second Redis-Execute node immediately
after), add:

```
LTRIM horizon:sess:{{ $('Build WM Context').item.json.sessionId }}:turns 0 19
```

```
EXPIRE horizon:sess:{{ $('Build WM Context').item.json.sessionId }}:turns 43200
EXPIRE horizon:sess:{{ $('Build WM Context').item.json.sessionId }}:summary 43200
EXPIRE horizon:sess:{{ $('Build WM Context').item.json.sessionId }}:meta 43200
```

(43200s = 12h sliding TTL.)

### 8. Write WM: HSET meta

| Field | Value |
|-------|-------|
| Node type | **Redis** |
| Operation | Execute Command |
| Credential | `EH HORIZON Redis (LA)` |
| Position | x: 420, y: 240 |

```
HSET horizon:sess:{{ $('Build WM Context').item.json.sessionId }}:meta last_seen {{ $now.toISO() }} channel chat
HSETNX horizon:sess:{{ $('Build WM Context').item.json.sessionId }}:meta started_at {{ $now.toISO() }}
```

### 9. Bump Session Row

| Field | Value |
|-------|-------|
| Node type | **Postgres** |
| Operation | Execute Query |
| Credential | `Postgres EventHorizon` |
| Position | x: 420, y: 360 |

```sql
UPDATE conversation_sessions
SET last_seen_at = NOW(),
    turn_count   = turn_count + 1
WHERE id = '{{ $('Build WM Context').item.json.sessionId }}'::uuid;
```

---

## Connection edits

In n8n UI, drag connections to match the topology diagram. The diff from the
live workflow is:

| Before | After |
|--------|-------|
| `When chat message received` → `AI Agent` | `When chat message received` → `Open Session Row` |
| (new) | `Open Session Row` → `Load WM: GET summary` → `Load WM: LRANGE turns` → `Load WM: HGETALL meta` → `Build WM Context` → `AI Agent` |
| `AI Agent` → `Extract Token Usage` | `AI Agent` → `Write WM: LPUSH turn` → `Write WM: HSET meta` → `Bump Session Row` → `Extract Token Usage` |

Leave untouched:
- `Anthropic Chat Model` → AI Agent (ai_languageModel)
- `embed_text` → AI Agent (ai_tool)
- `query_db` → AI Agent (ai_tool)
- `Embed Chat Query` / `Retrieve Chat Memories` / `Format Memory Block`
  (the orphaned pgvector-recall branch — not wired into the chat trigger
  anyway; leave it alone)
- `Extract Token Usage` → `Log Tokens`

---

## Smoke test (after import + connect, before activating live)

1. **Open the candidate workflow's Chat panel in n8n UI.** Send: `hi`.
   Expect: HORIZON responds normally. Behind the scenes, after the response:
   - `redis-cli -a $REDIS_PASSWORD KEYS 'horizon:sess:*'` shows three keys
     for the new session id (turns, meta — summary appears only after the
     re-summarize step is added later).
   - `psql -d eventhorizon -c "SELECT id, channel, turn_count, last_seen_at
     FROM conversation_sessions ORDER BY started_at DESC LIMIT 1;"` shows
     the row with turn_count=1.

2. **Send 4 more turns.** Each time:
   - `LLEN horizon:sess:<id>:turns` increments
   - `conversation_sessions.turn_count` increments to 5
   - The HORIZON response should reference prior turns by content (test:
     mention "the kalshi poller" on turn 2, then on turn 5 ask "what did I
     bring up earlier?" — it should recall kalshi)

3. **Send a 21st turn.** `LLEN` should cap at 20 (LTRIM working).

4. **Wait 12h+ without activity, then send a turn.** New `:turns` list
   starts fresh (TTL expired); the session row in PG still exists with
   `closed_at IS NULL` — that's expected, the session-close sweep workflow
   (not in this overlay) is what closes it.

If any step fails: deactivate candidate, leave original HORIZON workflow
running, debug. Original is untouched throughout.

---

## What's NOT in this overlay (deferred)

- **Re-summarize every K=5 turns.** The branch that calls Haiku to compress
  the running summary. Adds 1 IF node + 1 LangChain Haiku node + 1 Redis SET
  node. Layer on after the basic LPUSH path is stable.
- **Session-close sweep sub-workflow.** A separate cron-triggered n8n
  workflow that drains stale sessions into `memories` + closes
  `conversation_sessions`. Full spec in `n8n-wiring.md` § Session-close sweep.
- **SMS / voice channels.** Same node topology, different session-id
  derivation (Twilio CallSid / phone-number hash). Wire after chat is stable
  for a week.
- **morning-briefing health surfacing** of `redis_hit_ratio` and open-session
  count. Adds to M2 briefing build, not here.

---

## Rollback

If the candidate workflow misbehaves: deactivate it, re-activate the
original HORIZON workflow. No data loss — Redis cache is ephemeral by
design; `conversation_sessions` rows remain (operator can `DELETE` test
sessions if desired). Nothing in `memories` is written by this overlay
(that's the session-close sweep's job, deferred).
