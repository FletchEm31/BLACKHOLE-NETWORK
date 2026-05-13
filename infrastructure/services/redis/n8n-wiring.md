# HORIZON Redis — n8n wiring guide

Companion to `docker-run.md` (deploys the cache) and `sql/redis-memory-schema.sql` (durable index). This doc covers the n8n-side changes that turn the deployed service into working memory for HORIZON.

Three additions to the HORIZON workflow + one new sub-workflow:

1. **Load working memory** node — runs before Sonnet, injects recent turns + running summary into the system prompt
2. **Write working memory** node — runs after Sonnet response, appends the new turn and optionally re-summarizes
3. **Session-close sweep** sub-workflow (cron) — drains stale Redis sessions into pgvector, frees the keys

Per the roadmap, **start with the chat channel only**. Voice and SMS layer on after a week of stable chat behavior.

---

## Session ID convention

Every entry into the HORIZON workflow needs a `session_id` (UUID v4). Generate at the top of the workflow if not already present:

| Channel | Where session_id comes from |
|---------|-----------------------------|
| Chat trigger | UUID of the n8n chat session (built-in `$input.body.sessionId`). If new, also INSERT into `conversation_sessions`. |
| Twilio SMS | Stable hash of `From` number for the rolling 12h window — or UUID minted on first message and persisted via Redis lookup (`horizon:phone:<E164>:current_session`) |
| Twilio voice | Twilio `CallSid` mapped to a UUID at call start; INSERT row at call start |
| Briefings (M2/M3) | UUID minted at cron firing, channel=`morning_brief` / `evening_brief` |

When a brand-new session_id is minted, also run:

```sql
INSERT INTO conversation_sessions (id, channel, metadata)
VALUES ($1, $2, $3)
ON CONFLICT (id) DO NOTHING;
```

---

## Node 1 — Load working memory

Position: immediately before the Sonnet "synthesize answer" node, after pgvector RAG retrieval.

**Type:** `Redis` node, operation `Execute Command` (or three separate Get/LRange/HGetAll calls — whichever the n8n version supports cleanly).

**Commands:**

```
GET     horizon:sess:{{$json.session_id}}:summary
LRANGE  horizon:sess:{{$json.session_id}}:turns 0 19
HGETALL horizon:sess:{{$json.session_id}}:meta
```

Then in a Function node downstream, sliding-TTL the keys:

```
EXPIRE horizon:sess:{{$json.session_id}}:summary 43200
EXPIRE horizon:sess:{{$json.session_id}}:turns   43200
EXPIRE horizon:sess:{{$json.session_id}}:meta    43200
```

(43200s = 12h. Use `EXPIRE` idempotently every read.)

**Output mapping into Sonnet system prompt:**

Add two placeholders to the existing HORIZON system prompt:

```
{{recent_turns}}     — last 20 turns, newest first, formatted as
                       "USER: ...\nHORIZON: ..." pairs
{{running_summary}}  — the GET result, or empty string if first turn
```

If the LRANGE result is empty AND the GET is empty, this is turn 1 — no working memory yet. The system prompt should handle that gracefully ("This is the start of the conversation.").

---

## Node 2 — Write working memory

Position: immediately after the Sonnet "synthesize answer" node, in the same lane as the response delivery (don't gate the response on this write — fire-and-forget is fine).

**Commands:**

```
LPUSH  horizon:sess:{{$json.session_id}}:turns  {{JSON.stringify({user: $json.user_text, horizon: $json.horizon_text, ts: $now.toISO()})}}
LTRIM  horizon:sess:{{$json.session_id}}:turns 0 19
HSET   horizon:sess:{{$json.session_id}}:meta last_seen {{$now.toISO()}} channel {{$json.channel}}
HSETNX horizon:sess:{{$json.session_id}}:meta started_at {{$now.toISO()}}
EXPIRE horizon:sess:{{$json.session_id}}:summary 43200
EXPIRE horizon:sess:{{$json.session_id}}:turns   43200
EXPIRE horizon:sess:{{$json.session_id}}:meta    43200
```

Then also bump the PG side:

```sql
UPDATE conversation_sessions
SET last_seen_at = NOW(),
    turn_count   = turn_count + 1
WHERE id = $1;
```

**Re-summarize every K=5 turns:**

After LPUSH, an IF node checks `LLEN horizon:sess:<id>:turns MOD 5 == 0`. If yes, branch into a cheap Haiku 4.5 call that rewrites the summary from the existing summary + the 5 new turns, then:

```
SET    horizon:sess:{{$json.session_id}}:summary  {{$json.new_summary}}
EXPIRE horizon:sess:{{$json.session_id}}:summary 43200
```

The point of the running summary is to keep Sonnet's effective context window flat as the session grows past 20 turns — older turns drop out of `turns`, but their gist persists in `summary`. Without this, long sessions silently fall back to "last 20 turns only" memory.

---

## Sub-workflow — Session-close sweep

New n8n workflow `horizon-session-close-sweep`. Cron trigger every 15 min.

**Flow:**

1. **Find stale sessions.** Redis: `SCAN 0 MATCH horizon:sess:*:meta COUNT 200`, then for each match `HGET ... last_seen`. Treat as stale if `now() - last_seen > 30 min`.

   Cheaper alternative once `conversation_sessions` is populated reliably: query PG directly for `last_seen_at < NOW() - INTERVAL '30 min' AND closed_at IS NULL`. Prefer this when available.

2. **Pull session contents.** For each stale `session_id`:
   ```
   LRANGE horizon:sess:<id>:turns 0 -1
   GET    horizon:sess:<id>:summary
   HGETALL horizon:sess:<id>:meta
   ```

3. **Summarize via Sonnet.** Input = existing `summary` + all turns (reversed to chronological). Output = 1-2 paragraph session summary in operator's tone-matched voice.

4. **Embed via `/opt/eh-embed`.** HTTP POST to the local embedder (bge-small-en-v1.5, 384-dim) — same endpoint the pulse workflow uses. Output = `vector(384)`.

5. **Write to `memories`.**
   ```sql
   INSERT INTO memories (memory_type, title, content, embedding, source, metadata, importance)
   VALUES ('conversation',
           'HORIZON session ' || $session_id::text,
           $summary_text,
           $embedding,
           'horizon-session-close',
           jsonb_build_object('session_id', $session_id, 'channel', $channel, 'turn_count', $turn_count),
           5)
   RETURNING id;
   ```

6. **Close the row in `conversation_sessions`.**
   ```sql
   UPDATE conversation_sessions
   SET closed_at         = NOW(),
       summary           = $summary_text,
       summary_embedding = $embedding,
       related_memory_id = $memories_id,
       turn_count        = $turn_count
   WHERE id = $session_id;
   ```

7. **Delete the Redis keys.**
   ```
   DEL horizon:sess:<id>:turns horizon:sess:<id>:summary horizon:sess:<id>:meta
   ```

Idempotency: each step is safe to re-run. If the sweep crashes between step 5 and step 6, the next sweep finds the same session, re-summarizes (cost ~1 Haiku call wasted), and proceeds — no duplicate `memories` row if you add `ON CONFLICT (session_id) DO NOTHING` upstream by checking `conversation_sessions.related_memory_id` IS NULL before inserting.

---

## Rollout sequence

1. **Deploy Redis** per `docker-run.md`. Confirm PING from n8n.
2. **Apply schema** — `psql -f sql/redis-memory-schema.sql`. Confirm `conversation_sessions` exists, grants are correct.
3. **Add Node 1 + Node 2** to a *copy* of the HORIZON workflow, channel restricted to chat only. Smoke-test a 10-turn chat session, watch keys appear in `redis-cli KEYS`.
4. **Add the close sweep sub-workflow.** Run it manually once with a closed-then-orphaned session to verify the full PG write path. Then enable the cron.
5. **Soak for 7 days** chat-only. Watch:
   - `redis-cli INFO memory` — used_memory_human stays bounded (well under 128 MB)
   - `SELECT count(*) FROM conversation_sessions WHERE closed_at IS NULL` — open sessions stay in single digits, not unbounded growth
   - Sonnet input token count per turn — should stay flat as session grows, not climb linearly
6. **Layer on SMS and voice** channels once chat is stable. Briefings (M2/M3) add later — those are short single-shot exchanges where working memory matters less.
7. **Add to M2 morning briefing** — surface `redis_hit_ratio` (= summaries served / turns answered) and open-session count as a health signal.

## Failure modes

| Failure | Effect | Recovery |
|---------|--------|----------|
| Redis container down | HORIZON loses working memory; each turn behaves as turn 1 of a fresh session. Long-term recall via pgvector still works. | `docker compose up -d` brings it back. No PG-side correction needed. |
| Sweep workflow not firing | Live Redis keys accumulate; eventually hit `--maxmemory 128mb` and LRU-evict oldest. Closed sessions never get their `memories` row. | Manually run sweep workflow. Add a Grafana alert on `conversation_sessions WHERE closed_at IS NULL AND last_seen_at < NOW() - INTERVAL '1h'`. |
| Embedder down (`/opt/eh-embed` 500s) | Sweep step 4 fails. Session stays open in PG, Redis keys keep growing. | Same as above. Sweep is idempotent. |
| Sonnet rate-limited mid-sweep | Step 3 fails. Same idempotent retry next cycle. | None needed. |
