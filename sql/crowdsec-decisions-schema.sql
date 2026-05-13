-- crowdsec-decisions-schema.sql
-- BHN — active CrowdSec decisions (bans/captchas/throttles) per node.
-- Populated by scripts/bhn-crowdsec-collector.sh (cron */5 on each node).
-- Distinct from node_logs which carries CrowdSec ALERTS (events that
-- caused decisions); this table tracks the DECISIONS currently active.

CREATE TABLE IF NOT EXISTS crowdsec_decisions (
    id            BIGSERIAL PRIMARY KEY,
    measured_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    node_name     TEXT NOT NULL,
    decision_id   BIGINT NOT NULL,        -- CrowdSec internal id
    origin        TEXT,                    -- 'crowdsec' | 'cscli' | 'lists:firehol_level1' | ...
    scenario      TEXT,                    -- 'crowdsecurity/ssh-bf' | ...
    type          TEXT,                    -- 'ban' | 'captcha' | 'throttle'
    value         TEXT NOT NULL,           -- the target — IP, range, or country code
    scope         TEXT,                    -- 'Ip' | 'Range' | 'Country'
    duration_s    BIGINT,                  -- seconds remaining at observation time
    expires_at    TIMESTAMPTZ,
    raw_payload   JSONB,
    UNIQUE (node_name, decision_id, measured_at)
);

CREATE INDEX IF NOT EXISTS cs_dec_node_time_idx  ON crowdsec_decisions (node_name, measured_at DESC);
CREATE INDEX IF NOT EXISTS cs_dec_value_idx      ON crowdsec_decisions (value);
CREATE INDEX IF NOT EXISTS cs_dec_scenario_idx   ON crowdsec_decisions (scenario);

GRANT INSERT ON crowdsec_decisions TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE crowdsec_decisions_id_seq TO log_shipper;
GRANT SELECT ON crowdsec_decisions TO agent_reader;
GRANT SELECT ON crowdsec_decisions TO grafana_reader;
GRANT SELECT ON crowdsec_decisions TO ehuser;

COMMENT ON TABLE crowdsec_decisions IS
    'Active CrowdSec decisions per node. Snapshot per cron run; (node, decision_id, measured_at) lets you slice "what was active when".';
