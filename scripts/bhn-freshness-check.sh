#!/bin/bash
# bhn-freshness-check.sh
#
# Detects silent data-flow failures by reading v_table_freshness and writing
# an alert row for any table that's stale beyond its per-table threshold
# (and not in its dedup window, and not silenced).
#
# Lives on:    LA at /usr/local/sbin/bhn-freshness-check.sh
# Schedule:    /etc/cron.d/bhn-freshness-check  (every 6h)
# Auth:        Unix-socket peer auth as the postgres user (no password file).
#              Same pattern as our other root-cron sql tools.
#
# Adds to:
#   freshness_alerts          - one row per detected staleness event
#   table_freshness_targets   - bumps last_alert_at to dedup repeats
#
# Future work (next session):
#   POST a webhook to n8n after the INSERT so HORIZON can SMS the operator.
#   For tonight, alerts land in the table only. Operator can SELECT to inspect.
#
# Exit codes:
#   0  success (regardless of whether any alerts were written)
#   1  generic failure (see stderr)

set -euo pipefail

LOG_PREFIX="[$(date -u --iso-8601=seconds)] bhn-freshness-check"
echo "$LOG_PREFIX: starting"

# One psql session does pre-check listing + atomic alert+update.
sudo -u postgres psql -d eventhorizon -v ON_ERROR_STOP=1 <<'SQL'
\pset border 1
\pset format aligned

-- 1. Show what's currently stale (informational; gets logged via stdout)
SELECT
    table_name,
    hours_stale,
    max_stale_hours AS threshold,
    latest_event_at,
    producer,
    CASE
        WHEN last_alert_at IS NULL                                                  THEN 'first alert'
        WHEN last_alert_at < NOW() - (alert_dedup_hours || ' hours')::interval      THEN 'past dedup window - re-alerting'
        ELSE                                                                             'within dedup - skip'
    END AS dedup_state
FROM v_table_freshness
WHERE is_stale AND enabled AND NOT is_silenced
ORDER BY hours_stale DESC NULLS FIRST;

-- 2. Atomic: insert alerts + update last_alert_at, only for rows that need alerting
WITH new_alerts AS (
    INSERT INTO freshness_alerts
        (table_name, hours_stale, threshold_hours, latest_event_at, producer, notes)
    SELECT
        table_name, hours_stale, max_stale_hours, latest_event_at, producer,
        'auto-detected by bhn-freshness-check.sh'
    FROM v_table_freshness
    WHERE needs_alert
    RETURNING table_name
),
bumped AS (
    UPDATE table_freshness_targets t
       SET last_alert_at = NOW(), updated_at = NOW()
      FROM new_alerts a
     WHERE t.table_name = a.table_name
    RETURNING t.table_name
)
SELECT COUNT(*) || ' alert(s) recorded' AS result FROM bumped;
SQL

echo "$LOG_PREFIX: done"
exit 0
