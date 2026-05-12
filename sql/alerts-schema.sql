-- alerts-schema.sql
-- BHN alerts audit log. Every Grafana alert that fires gets a row here so
-- HORIZON can answer "what alerted overnight?" / "show me the last alert"
-- via existing query_db. Also the basis for dedup (don't fire same alert
-- repeatedly in a short window).
--
-- Apply on LA hub:
--   sudo -u postgres psql -d eventhorizon -f sql/alerts-schema.sql

CREATE TABLE IF NOT EXISTS alerts (
    id              BIGSERIAL PRIMARY KEY,
    fired_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    rule_uid        TEXT NOT NULL,                  -- Grafana rule uid (stable id)
    rule_name       TEXT NOT NULL,                  -- human-readable title
    severity        TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    state           TEXT NOT NULL CHECK (state IN ('firing', 'resolved')),
    summary         TEXT,                           -- one-line description
    description     TEXT,                           -- full alert annotation
    value_at_fire   NUMERIC,                        -- the metric value when alert fired
    affected_nodes  TEXT[],                         -- which BHN nodes are involved (if applicable)
    dedup_key       TEXT NOT NULL,                  -- for the dedup window check
    sms_sent        BOOLEAN DEFAULT FALSE,
    sms_sid         TEXT,                           -- Twilio message SID when delivered
    ntfy_sent       BOOLEAN DEFAULT FALSE,
    raw_payload     JSONB,                          -- full Grafana webhook payload
    resolved_at     TIMESTAMPTZ                     -- populated when Grafana sends the resolved state
);

CREATE INDEX IF NOT EXISTS alerts_fired_idx       ON alerts (fired_at DESC);
CREATE INDEX IF NOT EXISTS alerts_dedup_idx       ON alerts (dedup_key, fired_at DESC);
CREATE INDEX IF NOT EXISTS alerts_state_idx       ON alerts (state, fired_at DESC);
CREATE INDEX IF NOT EXISTS alerts_rule_idx        ON alerts (rule_uid, fired_at DESC);

-- INSERT for the alert-router workflow
GRANT SELECT, INSERT, UPDATE ON alerts TO n8n_user;
GRANT USAGE, SELECT ON SEQUENCE alerts_id_seq TO n8n_user;

-- HORIZON reads
GRANT SELECT ON alerts TO agent_reader;

-- Grafana could query its own audit (optional, useful for a "recent alerts" panel)
GRANT SELECT ON alerts TO grafana_reader;

COMMENT ON TABLE alerts IS
    'BHN alert audit log. Populated by bhn-alert-router n8n workflow when Grafana fires a webhook.';
COMMENT ON COLUMN alerts.dedup_key IS
    'Stable identifier for the alert source. Same key in <dedup_window> = suppressed.';
