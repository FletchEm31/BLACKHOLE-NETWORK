#!/usr/bin/env bash
# BHN — WeatherBHN calibration milestone check.
#
# Per-city alert: writes a flag file the day EACH individual HIGH rollout
# candidate reaches >=30 paired forecast/observation days (the go-live
# threshold documented in infrastructure/docs/metabase/METABASE_SETUP_GUIDE.md).
# Does NOT flip anything in STATION_STATUS (scripts/weather/core_trading_orchestrator.py)
# or strat_9_prediction_alpha.enabled in /etc/bhn/rules.json — those are
# manual, deliberate actions for a human to take, one city at a time, per
# the rollout checklist in STATION_STATUS's comments (calibration clearing
# 30 pairs is necessary but not sufficient — ticker mapping must also be
# verified and the city watched for a few real cycles before flipping).
#
# UPDATED 2026-07-02: rewritten from an all-8-cities/all-or-nothing check
# to per-city alerts on exactly the 3 real HIGH rollout candidates.
#   - Confirmed HIGH ceiling is 6 cities, not 8: KDEN/KLAX/KMIA already
#     enabled; KPHX/KDFW have NO HIGH market on Kalshi at all (confirmed
#     live — zero markets under every ticker variant, zero HIGH contracts
#     ever logged historically), so checking their calibration pairs for
#     HIGH-readiness is meaningless — they're excluded from this script
#     entirely. Checking them would eventually show "ready" once their
#     forecast-vs-actual pairs accumulate (that collection isn't gated by
#     market existence), which would be actively misleading.
#   - The old script waited for ALL 8 (effectively unreachable given the
#     above) before firing a single combined alert. Austin/NYC/Chicago
#     won't necessarily clear 30 pairs on the same day, and the rollout
#     is explicitly one city at a time (see STATION_STATUS), so the alert
#     needs to be per-city.
#   - No longer self-disables after firing once — keeps running until all
#     3 target cities have individually been flagged, since each flip is
#     a separate manual decision that happens on its own schedule.
#
# Explicitly NOT in scope: any Low-side (tmin_f) calibration tracking.
# CP3/CP4 don't support Low at all yet — that's a fully separate,
# sequentially-later project, not something this script watches for.

set -euo pipefail

FLAG_DIR="/etc/bhn"
STATIONS="KAUS KNYC KORD"

ALL_FLAGGED=true

for station in $STATIONS; do
    FLAG_FILE="$FLAG_DIR/weatherbhn-calibration-ready-${station}.flag"

    if [ -f "$FLAG_FILE" ]; then
        continue
    fi
    ALL_FLAGGED=false

    pairs=$(sudo -u postgres HOME=/tmp psql -t -A -d eventhorizon -c "
        SELECT COUNT(DISTINCT sfe.target_date)
        FROM weather_silver_forecast_conformed sfc
        LEFT JOIN weather_silver_forecast_error sfe
            ON sfe.station_code = sfc.station_code
           AND sfe.target_date  = sfc.target_date
           AND sfe.feature_name = 'tmax_f'
        WHERE sfc.is_latest_run = TRUE
          AND sfc.source_name   = 'nws'
          AND sfc.station_code  = '$station';
    " | tr -d ' ')

    if [ -n "$pairs" ] && [ "$pairs" -ge 30 ]; then
        {
            echo "WeatherBHN calibration reached 30 paired days for $station on $(date -u +%Y-%m-%dT%H:%M:%SZ)"
            echo "error_pairs=$pairs"
            echo ""
            echo "$station is now eligible for the HIGH rollout checklist in"
            echo "scripts/weather/core_trading_orchestrator.py's STATION_STATUS comments:"
            echo "  1. Calibration cleared (this alert)."
            echo "  2. Verify ticker mapping is correct (don't assume — Austin's was"
            echo "     found completely missing once already)."
            echo "  3. Flip this city's 'high' status to 'enabled' ALONE, watch a few"
            echo "     real orchestrator cycles, confirm sane signals."
            echo "  4. Only then move to the next not_ready city."
            echo ""
            echo "This script does NOT flip STATION_STATUS or rules.json — that's a"
            echo "manual, deliberate action for a human to take."
        } > "$FLAG_FILE"
        logger -t weatherbhn-calibration-check "MILESTONE: $station reached 30 paired days — see $FLAG_FILE"
    fi
done

# Re-check flagged state after this run's writes, so the timer disables
# itself on the same run that completes the last flag rather than one run late.
if [ "$ALL_FLAGGED" = false ]; then
    ALL_FLAGGED=true
    for station in $STATIONS; do
        [ -f "$FLAG_DIR/weatherbhn-calibration-ready-${station}.flag" ] || ALL_FLAGGED=false
    done
fi

if [ "$ALL_FLAGGED" = true ]; then
    logger -t weatherbhn-calibration-check "All 3 HIGH rollout candidates (KAUS, KNYC, KORD) have individually reached 30 paired days — disabling timer"
    systemctl disable --now weatherbhn-calibration-check.timer || true
fi
