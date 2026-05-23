#!/bin/bash
# Single-shot HORIZON publish fix.
# Sets workflow_entity.activeVersionId to point at HORIZON's history versionId,
# restarts n8n container, verifies published classification, smoke-tests chat.

set -uo pipefail

WORKFLOW_ID='fTFjaf2Q2aQrOPsY'
HISTORY_VERSION_ID='1c3cf029-394c-4ae3-a68b-b071acb6355a'
WEBHOOK_PATH='ec1592c6-8715-4b0f-8ee8-5bc02f551a27/chat'
DB='/root/.n8n/database.sqlite'

echo '======================================================================'
echo 'HORIZON publish fix — single-shot script'
echo '======================================================================'
echo
echo '--- 1. Pre-fix state of workflow_entity ---'
sqlite3 "$DB" "SELECT id, name, activeVersionId FROM workflow_entity WHERE name='HORIZON';"
echo
echo '--- 2. Verify history versionId exists (must be non-empty for FK) ---'
sqlite3 "$DB" "SELECT versionId, autosaved FROM workflow_history WHERE versionId='$HISTORY_VERSION_ID';"
echo
echo '--- 3. Snapshot current DB before mutation (rollback point) ---'
SNAPSHOT="/root/.n8n/database.sqlite.snap-pre-activeversion-$(date +%s)"
cp "$DB" "$SNAPSHOT"
ls -la "$SNAPSHOT"
echo
echo '--- 4. UPDATE activeVersionId ---'
sqlite3 "$DB" "UPDATE workflow_entity SET activeVersionId='$HISTORY_VERSION_ID' WHERE id='$WORKFLOW_ID';"
echo 'UPDATE issued.'
echo
echo '--- 5. Verify the value landed ---'
sqlite3 "$DB" "SELECT id, name, activeVersionId FROM workflow_entity WHERE name='HORIZON';"
echo
echo '--- 6. Restart n8n container ---'
sudo docker restart n8n
sleep 12
echo
echo '--- 7. Container status ---'
sudo docker ps --filter name=n8n --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
echo
echo '--- 8. Published/draft classification (looking for "1 published") ---'
sudo docker logs n8n --tail 40 2>&1 | grep -iE 'published|draft|migration error' | tail -10
echo
echo '--- 9. Smoke test: POST to chat webhook ---'
curl -sS -w '\n--- HTTP %{http_code} ---\n' -X POST "http://10.8.0.1:5678/webhook/$WEBHOOK_PATH" \
  -H 'Content-Type: application/json' \
  -d '{"chatInput":"What is the current network status?","sessionId":"recovery-final"}' \
  --max-time 30 | head -80
echo
echo '--- 10. Did an execution row get created? ---'
sqlite3 "$DB" "SELECT id, status, mode, startedAt FROM execution_entity WHERE workflowId='$WORKFLOW_ID' ORDER BY startedAt DESC LIMIT 3;"
echo
echo '======================================================================'
echo 'Done. If step 8 shows "1 published" and step 9 returns HTTP 200 with'
echo 'content, HORIZON is recovered.'
echo
echo 'Snapshot the win:'
echo "  cp $DB $DB.snap-RECOVERED-\$(date +%s)"
echo '======================================================================'
