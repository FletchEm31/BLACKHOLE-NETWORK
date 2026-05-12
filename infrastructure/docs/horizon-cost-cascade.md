# HORIZON — Cost Cascade Query Architecture (Item 3)

Implementation blueprint for the RAG-first cost cascade that gates LLM API spend behind cheaper local lookups.

## Goal

Every operator-facing query HORIZON answers should pass through this cascade. Skipping straight to Sonnet wastes API spend. The cascade keeps Sonnet usage to ~5-10% of queries (genuinely-novel-synthesis cases) instead of 100%.

## The cascade

```
Operator query
   │
   ▼
Layer 0 — normalize        (free, ~1ms)         lowercase, strip punct, collapse whitespace, hash
   │
   ▼
Layer 1 — qa_cache exact   (free, ~5ms)         SELECT * FROM qa_cache WHERE question_hash = $1 AND (expires_at IS NULL OR expires_at > NOW())
   │ miss
   ▼
Layer 2 — qa_cache semantic (free, ~30ms)       SELECT … ORDER BY embedding <=> $1::vector LIMIT 1; return if similarity > 0.92
   │ miss
   ▼
Layer 3 — memories semantic (free, ~50ms)       SELECT … ORDER BY embedding <=> $1::vector LIMIT 5
   │ retrieved → forms context for LLM step (NOT a return-direct)
   │
   ▼
Layer 4 — Haiku classify    (~$0.0001)          "Given retrieved context, can a routine assistant produce the answer, or does this need genuine synthesis from Sonnet?"
   │ routine                                    │ synthesis
   ▼                                            ▼
Layer 5a — Haiku answer     (~$0.001)           Layer 5b — Sonnet answer  (~$0.015)
   │                                            │
   ▼                                            ▼
   answer + write to qa_cache (with appropriate expires_at)
```

## Per-layer detail

### Layer 0 — normalize

Single Code node. Compute a SHA-256 of the normalized question for `qa_cache.question_hash` lookup. Strip punctuation, lowercase, collapse whitespace, drop trailing question marks. The same operator question phrased two ways gets the same hash.

```javascript
const crypto = require('crypto');
const norm = (s) => s.toLowerCase()
  .replace(/[^\w\s]/g, '')
  .replace(/\s+/g, ' ')
  .trim();
const text = norm($json.user_query || '');
const hash = crypto.createHash('sha256').update(text).digest('hex');
return [{ json: { user_query: $json.user_query, normalized: text, question_hash: hash } }];
```

### Layer 1 — qa_cache exact

```sql
SELECT id, answer, model_used, confidence, hit_count, expires_at
FROM qa_cache
WHERE question_hash = $1
  AND (expires_at IS NULL OR expires_at > NOW())
LIMIT 1;
```

If hit: increment `hit_count`, set `last_hit_at = NOW()`, return the cached answer. **No LLM call.**

### Layer 2 — qa_cache semantic

If exact miss, embed the normalized question (local embedding service) and look up similar past questions:

```sql
SELECT id, question, answer, ROUND((1 - (embedding <=> $1::vector))::numeric, 4) AS similarity
FROM qa_cache
WHERE expires_at IS NULL OR expires_at > NOW()
ORDER BY embedding <=> $1::vector
LIMIT 1;
```

If similarity > **0.92** (high threshold to avoid wrong-answer reuse): return the cached answer + record the hit. The threshold is tunable as we learn — start strict.

### Layer 3 — memories semantic retrieval

The same retrieval HORIZON already does on every chat turn (already wired in `n8n-workflows/bhn-horizon.json`). Returns top-5 memories. **This is context, not an answer** — it feeds the LLM step.

### Layer 4 — Haiku classifier (cost gate)

Only reached if all cache layers missed. Calls Haiku 4.5 with a tight prompt:

```
You are HORIZON's query router. Decide if this query needs genuine synthesis
from a stronger model, or if a routine assistant can answer it given the
retrieved context.

Routine = factual lookup, status check, simple reformat, direct quote of
retrieved memory, restating known data.

Synthesis = novel reasoning, multi-source comparison, recommendation requiring
judgment, generating new prose (briefings, drafts, summaries longer than 2
sentences).

Reply with strictly one word: ROUTINE or SYNTHESIS.

Query: <user_query>
Retrieved context: <top-5 memory titles + snippets>
```

Token cost per classification: ~500 input + 1 output = ~$0.0001 with Haiku 4.5 input $0.80/Mtok output $4/Mtok. **At scale: 1000 queries/month routes for ~$0.10/mo.**

### Layer 5a — Haiku answer

If router said ROUTINE, Haiku produces the answer with the retrieved context already in the prompt. ~$0.001 per answer (1K input, 200 output).

### Layer 5b — Sonnet answer

If router said SYNTHESIS, Sonnet 4.6 with full system prompt + retrieved context. ~$0.015 per answer (the same cost as a Pulse run — comparable scope).

### Cache the answer

After Layer 5a or 5b, write to `qa_cache`:
- `question` = original user question
- `question_hash` = computed in Layer 0
- `answer` = the model's response
- `embedding` = embedding of the normalized question
- `model_used` = `'haiku'` or `'sonnet'`
- `confidence` = 8 for Haiku-routine, 9 for Sonnet-synthesis (operator can tune)
- `expires_at`:
  - Time-sensitive answers (weather, market price, today's calendar) → 1 hour
  - Status answers (current network state, recent events) → 24 hours
  - Durable answers (architecture, operator prefs, how-to) → NULL (never expires)
  - Classification done by another small Haiku call OR rule-based on question keywords

## Implementation as an n8n workflow

Recommend ONE shared subworkflow `HORIZON_query_cascade` that:
- Inputs: `user_query` (string)
- Outputs: `{ answer, model_used, layer_hit, cost_estimate_usd }`

Other workflows (HORIZON main chat agent, morning briefing dynamic-Q paths, HORIZON-via-SMS) call this subworkflow via the `Execute Workflow` node. Single source of truth for cascade logic.

Workflow shape:
```
Trigger (webhook or sub-call)
  → Normalize Code
  → IF qa_cache_exact_hit THEN return ELSE
  → Embed Code (call local embedding service)
  → IF qa_cache_semantic_hit THEN return ELSE
  → Retrieve Memories (Postgres)
  → Haiku Classify (HTTP)
  → SWITCH on classification
    → Haiku Answer (HTTP) → Write to qa_cache → return
    → Sonnet Answer (HTTP) → Write to qa_cache → return
```

## Cost projection

| Volume | Assumed mix | Monthly cost |
|--------|-------------|--------------|
| 100 queries/mo | 60% cache hit, 30% Haiku, 10% Sonnet | $0.10 |
| 1000 queries/mo | same mix | $1.00 |
| 10000 queries/mo | same mix | $10.00 |

(Cache hit % grows over time as the qa_cache populates. Initial-state cost is higher because cache is empty; converges to the above ratios after ~200 queries.)

Compare to skipping the cascade entirely: 1000 queries/mo × $0.015 (Sonnet) = $15.00. **Cascade saves ~93% of LLM spend at steady state.**

## Why a separate Haiku classifier instead of just letting Sonnet decide

Sonnet is the answerer; having Sonnet self-classify ("can I answer this without thinking hard?") wastes the input tokens before the decision. Haiku at ~$0.0001 is 150x cheaper and competent at this binary classification task. Routing decision should be the cheapest possible model, even if the eventual answer comes from Sonnet.

## Build sequence

1. **Done — schema:** `qa_cache` table in `sql/horizon-schema.sql` (committed `cfe163d`).
2. **Next:** build `HORIZON_query_cascade` subworkflow in n8n. ~12 nodes, all using existing Anthropic credential. Activate as inactive=false initially while testing.
3. **Then:** wire it into HORIZON's chat workflow as an additional step (replacing or augmenting the existing direct-to-Sonnet path).
4. **Telemetry:** extend `agent_token_log` with `layer_hit` column so we can see cascade efficiency over time. Grafana panel: cache hit rate / Haiku route rate / Sonnet route rate / cost-per-day.

## When this becomes essential

Right now HORIZON has very low chat volume — a cascade isn't urgent. **Becomes high-leverage when:**
- M2 morning briefing fires daily and asks Claude many small questions to compose the briefing → cascade saves 80%+ on briefing assembly cost
- M4 intraday alerts need quick "should I notify Hayden?" judgment calls → cache + Haiku route covers most of these
- M7 outbound calling has HORIZON respond live during a call → speed matters; cache hit at ~30ms beats round-trip-to-Sonnet at ~2s

Build during Session 2 alongside M2 (morning briefing) so both land together.
