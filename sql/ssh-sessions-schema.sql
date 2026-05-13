-- ssh-sessions-schema.sql
-- BHN — SSH session log + per-session command audit per node.

CREATE TABLE IF NOT EXISTS ssh_sessions (
    id              BIGSERIAL PRIMARY KEY,
    measured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    node_name       TEXT NOT NULL,
    user_name       TEXT NOT NULL,
    source_ip       INET,
    tty             TEXT,
    login_at        TIMESTAMPTZ NOT NULL,
    logout_at       TIMESTAMPTZ,
    duration_s      INTEGER,
    raw_line        TEXT,
    UNIQUE (node_name, user_name, source_ip, login_at)
);

CREATE INDEX IF NOT EXISTS ssh_node_time_idx  ON ssh_sessions (node_name, login_at DESC);
CREATE INDEX IF NOT EXISTS ssh_source_idx     ON ssh_sessions (source_ip);
CREATE INDEX IF NOT EXISTS ssh_open_idx       ON ssh_sessions (node_name) WHERE logout_at IS NULL;

-- Commands within an SSH session (sourced via auditd execve audit; see
-- infrastructure/audit/bhn-ssh-audit.rules for the audit rules file).
CREATE TABLE IF NOT EXISTS ssh_commands (
    id              BIGSERIAL PRIMARY KEY,
    measured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    node_name       TEXT NOT NULL,
    ses_id          INTEGER,                  -- auditd session id (matches /proc/<pid>/sessionid)
    auid            INTEGER,                  -- audit user id (caller uid even after su/sudo)
    uid             INTEGER,                  -- effective uid
    command_time    TIMESTAMPTZ NOT NULL,
    executable      TEXT,
    args            TEXT,
    cwd             TEXT,
    raw_line        TEXT
);

CREATE INDEX IF NOT EXISTS shc_node_time_idx ON ssh_commands (node_name, command_time DESC);
CREATE INDEX IF NOT EXISTS shc_ses_idx       ON ssh_commands (ses_id);
CREATE INDEX IF NOT EXISTS shc_exe_idx       ON ssh_commands (executable);

GRANT INSERT ON ssh_sessions, ssh_commands TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE ssh_sessions_id_seq, ssh_commands_id_seq TO log_shipper;
GRANT SELECT ON ssh_sessions, ssh_commands TO agent_reader, grafana_reader, ehuser;

COMMENT ON TABLE ssh_sessions  IS 'SSH session log per node from `last -F` wtmp parse + journalctl sshd events. UNIQUE for idempotent re-shipping.';
COMMENT ON TABLE ssh_commands  IS 'Per-session execve audit from auditd. Requires bhn-ssh-audit.rules deployed.';
