#!/usr/bin/env python3
"""
weather_calibration.py — BHN Strategy 9 Phase 2 bias-correction calibration.

PHASE 2 SCAFFOLD. Phase 1 (data collection) lands rows into weather_forecasts
+ weather_observations. Once 4-6 weeks of paired forecast↔observation data
has accumulated, Phase 2 rebuilds model_calibration weekly with the
bias-correction lookup the strategy code uses at prediction time.

Calibration flow (Phase 2 build target — NOT YET IMPLEMENTED):

  For each (station_code, variable, season, lead_time_hours, source_model):
    1. Pull matched pairs from the rolling window (e.g. trailing 90 days):
       SELECT
         f.predicted_value, o.observed_value,
         f.predicted_at, o.observed_at, f.target_date
       FROM weather_forecasts f
       JOIN weather_observations o
         ON  f.station_code = o.station_code
         AND f.variable     = o.variable
         AND f.target_date  = o.observed_at::date
       WHERE f.station_code = $1
         AND f.variable     = $2
         AND f.season       = $3
         AND f.lead_time_hours = $4
         AND f.source_model = $5
         AND o.source = 'asos'
         AND f.predicted_at > NOW() - INTERVAL '90 days';

    2. Compute residuals:
       residuals = observed_value - predicted_value   (per row)

    3. Aggregate:
       mean_bias = mean(residuals)
       rmse      = sqrt(mean(residuals^2))
       mae       = mean(abs(residuals))
       sample_n  = count(*)

    4. For ensemble sources (NOMADS GFS GEFS) also compute:
       crps              = continuous ranked probability score
       reliability_score = how well calibrated the ensemble spread is

    5. UPSERT into model_calibration with calibrated_at = NOW().

Strategy code at prediction time (Phase 3+):
    1. Pull raw forecast for upcoming target_date.
    2. Lookup model_calibration row for (station, var, season, lead, model).
    3. Apply: corrected_value = predicted_value + mean_bias.
    4. Bet only if corrected_value implies edge ≥ 8% over Kalshi/Polymarket
       price AND ensemble confidence (1 - normalized RMSE) ≥ 0.65.

This file is intentionally a stub: methods are defined with full type
signatures + SQL templates in docstrings, but execute() is a no-op until
Phase 2 ships. Importable now so the systemd / cron layer can wire it.

CLI (all current commands no-op; reserved for Phase 2):
  python3 weather_calibration.py --rebuild
  python3 weather_calibration.py --station KNYC --variable tmax_f
  python3 weather_calibration.py --status
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Optional

import trading_core as tc


logger = tc.get_logger("strat_9_weather_alpha_calibration")


# Rolling window for residual aggregation (Phase 2)
DEFAULT_LOOKBACK_DAYS = 90

# Minimum sample size for calibration to be considered valid
MIN_SAMPLE_SIZE = 30


@dataclass
class CalibrationRow:
    station_code:    str
    variable:        str
    season:          str
    lead_time_hours: int
    source_model:    str
    sample_size:     int
    mean_bias:       float
    rmse:            float
    mae:             float
    crps:            Optional[float] = None
    reliability:     Optional[float] = None


def collect_residuals(station_code: str, variable: str, season: str,
                       lead_time_hours: int, source_model: str,
                       lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> list:
    """Phase 2 implementation will return a list of (predicted, observed)
    tuples from the JOIN documented in the module docstring. Phase 1 stub
    returns empty list."""
    logger.debug(f"collect_residuals scaffold called: "
                 f"{station_code}/{variable}/{season}/lead={lead_time_hours}h/{source_model}")
    return []


def compute_calibration(pairs: list) -> Optional[CalibrationRow]:
    """Phase 2: compute mean_bias / RMSE / MAE / CRPS / reliability from
    residuals and return a CalibrationRow. Phase 1 stub returns None."""
    if len(pairs) < MIN_SAMPLE_SIZE:
        return None
    # Phase 2: implement aggregation. Mean bias = mean(observed - predicted).
    return None


def upsert_calibration(row: CalibrationRow) -> None:
    """UPSERT a calibration row. Wired now so Phase 2 implementation is one
    function-body change away from working."""
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO model_calibration
                    (station_code, variable, season, lead_time_hours,
                     source_model, sample_size, mean_bias, rmse, mae,
                     crps, reliability_score)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (station_code, variable, season, lead_time_hours, source_model)
                DO UPDATE SET
                    sample_size       = EXCLUDED.sample_size,
                    mean_bias         = EXCLUDED.mean_bias,
                    rmse              = EXCLUDED.rmse,
                    mae               = EXCLUDED.mae,
                    crps              = EXCLUDED.crps,
                    reliability_score = EXCLUDED.reliability_score,
                    calibrated_at     = NOW()
            """, (row.station_code, row.variable, row.season,
                  row.lead_time_hours, row.source_model, row.sample_size,
                  row.mean_bias, row.rmse, row.mae,
                  row.crps, row.reliability))


def rebuild_all() -> int:
    """Phase 2: iterate all (station, variable, season, lead, model) combos
    and rebuild every model_calibration row from the rolling window. Phase 1
    stub returns 0."""
    logger.info("weather_calibration.rebuild_all — SCAFFOLD (Phase 2)")
    logger.info("Phase 2 build target: iterate all 10 cities × 4 vars × 4 seasons × "
                "8 lead times × 3 models ≈ 3,840 cells; UPSERT into model_calibration")
    return 0


def status() -> int:
    """Print current model_calibration coverage — useful for Phase 2 monitoring."""
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    source_model,
                    COUNT(*) AS n_cells,
                    MIN(calibrated_at) AS oldest_calibration,
                    MAX(calibrated_at) AS newest_calibration,
                    AVG(sample_size)::int AS avg_sample_size,
                    AVG(rmse) AS avg_rmse,
                    AVG(mae) AS avg_mae
                FROM model_calibration
                GROUP BY source_model
                ORDER BY source_model
            """)
            rows = cur.fetchall()
    if not rows:
        print("model_calibration is empty — Phase 2 hasn't run yet")
        return 0
    print(f"{'model':30s} {'cells':>6s} {'avg_n':>6s} {'avg_rmse':>10s} {'avg_mae':>10s}")
    for r in rows:
        print(f"{r[0]:30s} {r[1]:>6d} {r[4]:>6d} {r[5] or 0:>10.3f} {r[6] or 0:>10.3f}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="BHN Strat 9 calibration (Phase 2 scaffold)")
    parser.add_argument("--rebuild", action="store_true",
                        help="Rebuild model_calibration from rolling residual window")
    parser.add_argument("--station", help="Limit rebuild to one station")
    parser.add_argument("--variable", help="Limit rebuild to one variable")
    parser.add_argument("--status", action="store_true",
                        help="Print current calibration coverage")
    args = parser.parse_args()

    if args.status:
        return status()
    if args.rebuild:
        return rebuild_all()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
