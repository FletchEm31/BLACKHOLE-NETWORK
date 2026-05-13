#!/bin/bash
# bhn-wg-session-tracker — open/close WG sessions based on wg_peer_stats transitions.
#
# Runs on LA every 5 min via cron, AFTER bhn-wg-stats.sh has produced the
# latest snapshot. Looks at the most recent 2 measurements per peer:
#   prev_stale + curr_active  →  OPEN new session (or skip if one is already open)
#   prev_active + curr_stale  →  CLOSE the currently open session
#   prev never seen + curr active → OPEN new session
# Else no-op.
#
# Reads PG DSN from /root/.bhn-wg-sessions.env:
#   BHN_WG_SESSIONS_PG_DSN='postgresql://log_shipper:<PW>@10.8.0.1/eventhorizon'
#
# Cron (LA): */5 * * * * root /usr/local/sbin/bhn-wg-session-tracker.sh

set -euo pipefail

ENV_FILE=/root/.bhn-wg-sessions.env
[[ -r "$ENV_FILE" ]] || { echo "bhn-wg-sessions: missing $ENV_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
[[ -n "${BHN_WG_SESSIONS_PG_DSN:-}" ]] || { echo "bhn-wg-sessions: BHN_WG_SESSIONS_PG_DSN empty" >&2; exit 1; }

# All logic in SQL — atomic per cycle.
psql "$BHN_WG_SESSIONS_PG_DSN" -v ON_ERROR_STOP=1 >/dev/null <<'SQL' \
  || { echo "bhn-wg-sessions: PG operation failed" >&2; exit 2; }
BEGIN;

WITH last_two AS (
  SELECT peer_ip, peer_label, peer_pubkey, measured_at, is_stale, bytes_received, bytes_sent, endpoint,
         ROW_NUMBER() OVER (PARTITION BY peer_ip ORDER BY measured_at DESC) AS rn
  FROM wg_peer_stats
  WHERE measured_at > NOW() - INTERVAL '30 minutes'   -- window large enough to catch any 5-min cycle
), pivoted AS (
  SELECT
    p1.peer_ip, p1.peer_label, p1.peer_pubkey, p1.measured_at AS curr_t,
    p1.is_stale AS curr_stale, p1.bytes_received AS curr_rx, p1.bytes_sent AS curr_tx,
    p1.endpoint AS curr_endpoint,
    p2.measured_at AS prev_t, p2.is_stale AS prev_stale
  FROM last_two p1
  LEFT JOIN last_two p2 ON p1.peer_ip = p2.peer_ip AND p2.rn = 2
  WHERE p1.rn = 1
)
-- 1. OPEN: curr active AND (prev stale OR prev never seen) AND no open session
INSERT INTO wg_sessions (peer_ip, peer_label, peer_pubkey, session_start,
                         bytes_received_session, bytes_sent_session, endpoints_seen)
SELECT pv.peer_ip, pv.peer_label, pv.peer_pubkey, pv.curr_t, 0, 0,
       CASE WHEN pv.curr_endpoint IS NOT NULL THEN ARRAY[pv.curr_endpoint] ELSE ARRAY[]::TEXT[] END
FROM pivoted pv
WHERE pv.curr_stale = FALSE
  AND (pv.prev_stale IS TRUE OR pv.prev_stale IS NULL)
  AND NOT EXISTS (
    SELECT 1 FROM wg_sessions ws
    WHERE ws.peer_ip = pv.peer_ip AND ws.session_end IS NULL
  );

-- 2. CLOSE: curr stale AND prev active AND there IS an open session
UPDATE wg_sessions ws
SET session_end = pv.curr_t,
    duration_seconds = EXTRACT(EPOCH FROM (pv.curr_t - ws.session_start))::int,
    bytes_received_session = pv.curr_rx,   -- final delta computed below
    bytes_sent_session = pv.curr_tx
FROM pivoted pv
WHERE ws.peer_ip = pv.peer_ip
  AND ws.session_end IS NULL
  AND pv.curr_stale = TRUE AND pv.prev_stale = FALSE;

-- 3. ACCUMULATE: for open sessions, update bytes from latest sample as
--    a running total (delta vs session start). Simplification: keep raw latest
--    cumulative-counter value; the analyst computes delta vs the first sample.
UPDATE wg_sessions ws
SET bytes_received_session = pv.curr_rx,
    bytes_sent_session = pv.curr_tx,
    endpoints_seen = CASE
      WHEN pv.curr_endpoint IS NOT NULL AND NOT (pv.curr_endpoint = ANY(ws.endpoints_seen))
      THEN array_append(ws.endpoints_seen, pv.curr_endpoint)
      ELSE ws.endpoints_seen
    END
FROM pivoted pv
WHERE ws.peer_ip = pv.peer_ip
  AND ws.session_end IS NULL
  AND pv.curr_stale = FALSE;

COMMIT;
SQL
