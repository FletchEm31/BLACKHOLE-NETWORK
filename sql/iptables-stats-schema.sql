-- iptables-stats-schema.sql
-- BHN — per-rule iptables packet/byte counters per node.

CREATE TABLE IF NOT EXISTS iptables_stats (
    id             BIGSERIAL PRIMARY KEY,
    measured_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    node_name      TEXT NOT NULL,
    chain          TEXT NOT NULL,         -- INPUT, FORWARD, OUTPUT, ufw-after-input, ...
    rule_idx       INTEGER NOT NULL,      -- position within chain (1-based)
    packets        BIGINT NOT NULL,       -- cumulative since boot
    bytes          BIGINT NOT NULL,       -- cumulative since boot
    target         TEXT,                  -- ACCEPT, DROP, REJECT, ufw-skip-to-policy-input, ...
    proto          TEXT,
    in_iface       TEXT,
    out_iface      TEXT,
    source_spec    TEXT,
    dest_spec      TEXT,
    rule_spec      TEXT                   -- full rule line for reference
);

CREATE INDEX IF NOT EXISTS ipt_node_time_idx  ON iptables_stats (node_name, measured_at DESC);
CREATE INDEX IF NOT EXISTS ipt_chain_idx      ON iptables_stats (node_name, chain, rule_idx);

GRANT INSERT ON iptables_stats TO log_shipper;
GRANT USAGE, SELECT ON SEQUENCE iptables_stats_id_seq TO log_shipper;
GRANT SELECT ON iptables_stats TO agent_reader, grafana_reader, ehuser;

COMMENT ON TABLE iptables_stats IS
    'Per-rule iptables packets/bytes counters per node. Cumulative since boot — compute deltas with LAG() over (node, chain, rule_idx).';
