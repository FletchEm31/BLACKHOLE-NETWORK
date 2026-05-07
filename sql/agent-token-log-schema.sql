-- agent_token_log — captures Anthropic API token usage from each AI Agent v1.0
-- conversation turn. Populated by the workflow's "Log Tokens" Postgres node
-- (downstream of the AI Agent + Extract Token Usage Code node).
--
-- The agent's #TOKENS handler queries this alongside pulse_reports to give
-- the operator a 24h/7d cost summary on demand.
--
-- To apply:
--   psql -d eventhorizon -f agent-token-log-schema.sql
--
-- Pre-req: n8n_user role exists (created by the pulse-workflow installer).

CREATE TABLE IF NOT EXISTS agent_token_log (
    id              BIGSERIAL PRIMARY KEY,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    session_id      TEXT,
    execution_id    BIGINT,
    model           TEXT,
    input_tokens    INT NOT NULL DEFAULT 0,
    output_tokens   INT NOT NULL DEFAULT 0,
    user_message    TEXT
);

CREATE INDEX IF NOT EXISTS agent_token_log_occurred_at_idx
    ON agent_token_log (occurred_at DESC);

-- Workflow writes via this credential; agent (read-only) can SELECT for #TOKENS.
GRANT SELECT, INSERT ON agent_token_log TO n8n_user;
GRANT USAGE, SELECT ON SEQUENCE agent_token_log_id_seq TO n8n_user;
GRANT SELECT ON agent_token_log TO agent_reader;
