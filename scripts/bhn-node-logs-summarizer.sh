#!/bin/bash
# bhn-node-logs-summarizer — aggregate node_logs into node_logs_summary windows.
#
# Per item 5 from operator: log_shipper already ships Suricata + CrowdSec
# alerts into node_logs (per scripts/bhn-log-shipper.py header). This script
# adds the periodic summary layer (alert_count, top_signatures, severity
# breakdown) for fast Grafana rendering.
#
# Runs on LA every 15 min. UPSERT per (node, source, window_start) so
# windows that gain late-arriving rows update cleanly.
#
# Reads PG DSN from /root/.bhn-node-logs-summary.env:
#   BHN_NLS_PG_DSN='postgresql://ehuser:<PW>@10.8.0.1/eventhorizon'
#
# Cron (LA): */15 * * * * root /usr/local/sbin/bhn-node-logs-summarizer.sh

set -euo pipefail

ENV_FILE=/root/.bhn-node-logs-summary.env
[[ -r "$ENV_FILE" ]] || { echo "bhn-nls: missing $ENV_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
[[ -n "${BHN_NLS_PG_DSN:-}" ]] || { echo "bhn-nls: BHN_NLS_PG_DSN empty" >&2; exit 1; }

# Pure SQL — single transaction. Generates 4 windows of 15 min each ending now
# (covers up to 1 hour back to catch late arrivals) and UPSERTs.
psql "$BHN_NLS_PG_DSN" -v ON_ERROR_STOP=1 >/dev/null <<'SQL' \
  || { echo "bhn-nls: PG operation failed" >&2; exit 2; }
INSERT INTO node_logs_summary (window_start, window_end, node_name, source, alert_count,
                                severity_critical, severity_high, severity_medium, severity_low,
                                top_signatures, unique_src_ips)
SELECT
    win.window_start,
    win.window_start + INTERVAL '15 minutes' AS window_end,
    nl.node_name,
    nl.source,
    COUNT(*)::int                                                 AS alert_count,
    COUNT(*) FILTER (WHERE severity = 'critical')::int            AS severity_critical,
    COUNT(*) FILTER (WHERE severity = 'high')::int                AS severity_high,
    COUNT(*) FILTER (WHERE severity = 'medium')::int              AS severity_medium,
    COUNT(*) FILTER (WHERE severity = 'low')::int                 AS severity_low,
    (
      SELECT jsonb_agg(jsonb_build_object('signature', signature, 'count', cnt))
      FROM (
        SELECT signature, COUNT(*)::int AS cnt
        FROM node_logs nl2
        WHERE nl2.node_name = nl.node_name AND nl2.source = nl.source
          AND nl2.event_time >= win.window_start
          AND nl2.event_time <  win.window_start + INTERVAL '15 minutes'
          AND nl2.signature IS NOT NULL AND nl2.signature <> ''
        GROUP BY signature
        ORDER BY cnt DESC
        LIMIT 10
      ) ts
    )                                                              AS top_signatures,
    COUNT(DISTINCT src_ip) FILTER (WHERE src_ip IS NOT NULL)::int AS unique_src_ips
FROM (
    SELECT generate_series(
        date_trunc('hour', NOW()) - INTERVAL '45 minutes',
        date_trunc('hour', NOW()) + INTERVAL '15 minutes',
        INTERVAL '15 minutes'
    ) AS window_start
) win
JOIN node_logs nl
  ON nl.event_time >= win.window_start
 AND nl.event_time <  win.window_start + INTERVAL '15 minutes'
WHERE nl.source IN ('suricata', 'crowdsec')
GROUP BY win.window_start, nl.node_name, nl.source
ON CONFLICT (node_name, source, window_start) DO UPDATE SET
    measured_at       = NOW(),
    window_end        = EXCLUDED.window_end,
    alert_count       = EXCLUDED.alert_count,
    severity_critical = EXCLUDED.severity_critical,
    severity_high     = EXCLUDED.severity_high,
    severity_medium   = EXCLUDED.severity_medium,
    severity_low      = EXCLUDED.severity_low,
    top_signatures    = EXCLUDED.top_signatures,
    unique_src_ips    = EXCLUDED.unique_src_ips;
SQL
