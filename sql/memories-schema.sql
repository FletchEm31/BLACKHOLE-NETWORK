-- EH long-term memory layer.
-- Stores semantic memories (incidents, deployments, operator preferences, etc.)
-- keyed by 384-dim vector embeddings produced by /opt/eh-embed (bge-small-en-v1.5).
--
-- Used by the EH Network Pulse workflow (Phase C — retrieval injection) and by any
-- future AI workflow that needs long-term context. Every memory survives across
-- pulse cycles and persists in the encrypted NVMe-backed PostgreSQL.
--
-- Privacy: lives entirely on the LA hub's encrypted volume. No external service
-- ever sees memory content. Embeddings are produced by a local ONNX model.
--
-- To apply:
--   psql -d eventhorizon -f memories-schema.sql
--
-- Pre-req: PGDG apt repo + postgresql-14-pgvector package installed.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS memories (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    memory_type     TEXT NOT NULL CHECK (memory_type IN (
        'incident',         -- security event aftermath, root cause, fix
        'operator_pref',    -- "operator prefers X over Y"
        'project_context',  -- ongoing initiative state, deadlines, blockers
        'deployment',       -- what was deployed when, why
        'observation',      -- pulse summary worth keeping
        'conversation',     -- Q&A with Claude (assistant or analyst)
        'reference'         -- pointer to external system
    )),
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    embedding       vector(384) NOT NULL,
    metadata        JSONB DEFAULT '{}',
    source          TEXT,                       -- 'pulse', 'manual', 'incident', 'chat'
    tags            TEXT[] DEFAULT '{}',
    importance      SMALLINT DEFAULT 5 CHECK (importance BETWEEN 1 AND 10),
    expires_at      TIMESTAMPTZ,                -- NULL = permanent
    superseded_by   BIGINT REFERENCES memories(id) ON DELETE SET NULL,
    related_ids     BIGINT[] DEFAULT '{}'       -- cross-reference other memories
);

-- HNSW for fast approximate nearest-neighbor retrieval.
-- m=16 / ef_construction=64 is a good default for <100k rows.
CREATE INDEX IF NOT EXISTS memories_embedding_idx
    ON memories USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS memories_type_created_idx ON memories (memory_type, created_at DESC);
CREATE INDEX IF NOT EXISTS memories_tags_idx         ON memories USING gin (tags);
CREATE INDEX IF NOT EXISTS memories_metadata_idx     ON memories USING gin (metadata);
CREATE INDEX IF NOT EXISTS memories_importance_idx   ON memories (importance DESC, created_at DESC);

-- Grant the n8n_user role (created by the installer for the pulse workflow)
-- read/write access. Insert/Update only — no DELETE — so memory is append-only
-- by default. To "remove" a memory, supersede it via superseded_by.
GRANT SELECT, INSERT, UPDATE ON memories TO n8n_user;
GRANT USAGE, SELECT ON SEQUENCE memories_id_seq TO n8n_user;
