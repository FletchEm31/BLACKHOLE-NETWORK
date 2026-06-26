#!/usr/bin/env bash
# deploy-noaa-actuals.sh — SCP NOAA CSVs to LA and run the loader.
#
# Run from operator PC (on WireGuard, can reach LA at <BHN_WG_LA_IP>).
#
# Usage:
#   bash scripts/deploy-noaa-actuals.sh
#
# Requires:
#   - WireGuard tunnel up (<BHN_WG_LA_IP> reachable)
#   - SSH key auth to root@<BHN_WG_LA_IP>
#   - NOAA CSV files in the repo at infrastructure/docs/WeatherBHN/
#
# What it does:
#   1. Snapshots DB on LA
#   2. Applies the migration (creates tables + grants)
#   3. SCPs all 7 NOAA CSV files to LA /tmp/noaa-csvs/
#   4. SCPs the loader script
#   5. Runs the loader on LA via SSH
#   6. Prints row counts per station
#   7. Cleans up temp files on LA

set -euo pipefail

LA="root@<BHN_WG_LA_IP>"
SSH="ssh $LA"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NOAA_DIR="$REPO_ROOT/infrastructure/docs/WeatherBHN"
REMOTE_CSV_DIR="/tmp/noaa-csvs"
LOADER="$REPO_ROOT/scripts/load-noaa-actuals.py"
MIGRATION="$REPO_ROOT/sql/migrations/2026-06-25-noaa-actuals.sql"

echo "=== BHN NOAA Actuals Deploy ==="
echo "Source CSVs: $NOAA_DIR"
echo "Target:      $LA"

# Step 1: snapshot DB
echo ""
echo "[1/6] Snapshotting eventhorizon DB..."
$SSH 'sudo -u postgres pg_dump eventhorizon > /mnt/eh-nvme-hot/backups/pre-noaa-actuals-$(date +%Y%m%d-%H%M).sql && echo "Snapshot OK"'

# Step 2: apply migration
echo ""
echo "[2/6] Applying schema migration..."
scp "$MIGRATION" "$LA:/tmp/2026-06-25-noaa-actuals.sql"
$SSH 'sudo -u postgres psql -d eventhorizon -f /tmp/2026-06-25-noaa-actuals.sql && echo "Migration OK"'

# Step 3: SCP CSV files
echo ""
echo "[3/6] Copying NOAA CSV files to LA ($REMOTE_CSV_DIR)..."
$SSH "mkdir -p $REMOTE_CSV_DIR"
scp \
  "$NOAA_DIR/NOAA Daily Summary - Miami International Airport 1948-2026.csv" \
  "$NOAA_DIR/NOAA Daily Summary - Chicago International Airports 1928-2026.csv" \
  "$NOAA_DIR/NOAA Daily Summary - Los Angeles International Airport 1944-2026.csv" \
  "$NOAA_DIR/NOAA Daily Summary - New York (JFK) International Airport 1947-2026.csv" \
  "$NOAA_DIR/NOAA Daily Summary - Denver Internaitonal Airport 1994-2026.csv" \
  "$NOAA_DIR/NOAA Hourly Weather - Miami International Airport 2010.csv" \
  "$NOAA_DIR/NOAA Hourly Weather - Denver-Aurora Buckley Airfield 2010.csv" \
  "$LA:$REMOTE_CSV_DIR/"
echo "CSV transfer complete."

# Step 4: SCP loader script
echo ""
echo "[4/6] Deploying loader script..."
scp "$LOADER" "$LA:/tmp/load-noaa-actuals.py"

# Step 5: Run loader
# Prefer ehuser with PGPASSWORD; if not set, try as postgres (peer auth).
echo ""
echo "[5/6] Running loader on LA..."
if [[ -n "${BHN_EHUSER_PG_PASS:-}" ]]; then
  $SSH "PGHOST=localhost PGUSER=ehuser PGPASSWORD='$BHN_EHUSER_PG_PASS' python3 /tmp/load-noaa-actuals.py --dir $REMOTE_CSV_DIR"
else
  echo "BHN_EHUSER_PG_PASS not set — running as postgres (peer auth)..."
  $SSH "sudo -u postgres python3 /tmp/load-noaa-actuals.py --dir $REMOTE_CSV_DIR"
fi

# Step 6: Cleanup
echo ""
echo "[6/6] Cleaning up temp files on LA..."
$SSH "rm -rf $REMOTE_CSV_DIR /tmp/load-noaa-actuals.py /tmp/2026-06-25-noaa-actuals.sql"

echo ""
echo "=== Deploy complete ==="
echo "Next: verify in Grafana or via psql:"
echo "  sudo -u postgres psql -d eventhorizon -c \\"
echo "    'SELECT icao_code, COUNT(*), MIN(date), MAX(date) FROM weather_bronze_noaa_daily_actuals GROUP BY 1 ORDER BY 1;'"
