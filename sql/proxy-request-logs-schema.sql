-- proxy-request-logs-schema.sql
-- BHN — tinyproxy CONNECT events (LA outbound API calls via Hillsboro).

CREATE TABLE IF NOT EXISTS proxy_request_logs (
    id            BIGSERIAL PRIMARY KEY,
    measured_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    log_time      TIMESTAMPTZ NOT NULL,
    node_name     TEXT NOT NULL,           -- 'BHN-HILLSBORO-US3' for the canonical proxy
    pid           INTEGER,                  -- tinyproxy worker PID (lets you pair connect/close)
    src_ip        INET,                     -- the LA-side client behind the proxy
    dst_host      TEXT,                     -- 'api.anthropic.com'
    dst_port      INTEGER,                  -- 443 for HTTPS CONNECT
    response_code INTEGER,                  -- NULL — tinyproxy default log doesn't expose this
    bytes_sent    BIGINT,                   -- NULL — needs paired close-line parsing
    raw_line      TEXT,
    UNIQUE (node_name, log_time, pid, dst_host)
);

CREATE INDEX IF NOT EXISTS prl_node_time_idx ON proxy_request_logs (node_name, log_time DESC);
CREATE INDEX IF NOT EXISTS prl_dst_idx       ON proxy_request_logs (dst_host);
CREATE INDEX IF NOT EXISTS prl_src_idx       ON proxy_request_logs (src_ip);

GRANT INSERT ON proxy_request_logs TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE proxy_request_logs_id_seq TO log_shipper;
GRANT SELECT ON proxy_request_logs TO agent_reader, grafana_reader, ehuser;

COMMENT ON TABLE proxy_request_logs IS
    'Tinyproxy CONNECT events parsed from /var/log/tinyproxy/tinyproxy.log. response_code + bytes_sent intentionally NULL in v1 (tinyproxy default log doesn''t expose them; would require LogLevel Connect + paired close-line parsing).';
