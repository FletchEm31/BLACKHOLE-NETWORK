-- dns-query-log-schema.sql
-- BHN — DNS queries parsed from dnscrypt-proxy's TSV query log.
--
-- POLICY NOTE: STATUS.md previously documented "DNS query persistence
-- intentionally disabled — domains are content" (external-observer
-- principle). This schema reverses that decision per operator request.
-- Retention: rolling 30 days, then compressed to HDD COLD by eh-purge.
-- See scripts/eh-purge-monitoring-retention.conf (when added).

CREATE TABLE IF NOT EXISTS dns_query_log (
    id              BIGSERIAL PRIMARY KEY,
    measured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    log_time        TIMESTAMPTZ NOT NULL,
    node_name       TEXT NOT NULL,
    client_ip       INET,
    qname           TEXT NOT NULL,
    qtype           TEXT,
    status          TEXT,                  -- 'PASS', 'BLOCK', 'CLOAK', 'SYNTH', ...
    response_time_ms INTEGER,
    resolver        TEXT,
    UNIQUE (node_name, log_time, client_ip, qname, qtype)
);

CREATE INDEX IF NOT EXISTS dql_node_time_idx ON dns_query_log (node_name, log_time DESC);
CREATE INDEX IF NOT EXISTS dql_client_idx    ON dns_query_log (client_ip);
CREATE INDEX IF NOT EXISTS dql_qname_idx     ON dns_query_log (qname);
CREATE INDEX IF NOT EXISTS dql_status_idx    ON dns_query_log (status);

GRANT INSERT ON dns_query_log TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE dns_query_log_id_seq TO log_shipper;
GRANT SELECT ON dns_query_log TO agent_reader, grafana_reader, ehuser;

COMMENT ON TABLE dns_query_log IS
    'DNS queries from dnscrypt-proxy TSV log per node. Operator reversed prior "no DNS persistence" policy. 30d rolling retention.';
