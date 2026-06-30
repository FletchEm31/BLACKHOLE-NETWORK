#!/usr/bin/env python3
"""
CP3: XGBoost tmax inference.
Loads the trained model and returns a forecast for (station_code, target_date).
Emergency fallback: returns nws_tmax_calibrated_f if model is missing or corrupt.
Never returns None — always returns a forecast.

Standalone test:
    DATABASE_URL=postgresql:///eventhorizon python3 -c "
    from cp3_inference import run_cp3_inference
    from datetime import date
    print(run_cp3_inference('KLAX', date(2026, 6, 28)))
    "

Environment: DATABASE_URL (peer auth: postgresql:///eventhorizon)
"""
import os
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
import psycopg2
import psycopg2.extras

MODEL_PATH = Path("/opt/bhn/trading/models/weather_xgb_tmax.json")

STATION_ENC = {'KDEN': 0, 'KLAX': 1, 'KMIA': 2}
SEASON_ENC  = {'winter': 0, 'spring': 1, 'summer': 2, 'fall': 3}

FEATURE_COLS = [
    'nws_tmax_f',
    'om_tmax_f',
    'nws_tmax_mean_bias',
    'om_tmax_mean_bias',
    'nws_tmax_rmse',
    'om_tmax_rmse',
    'nws_tmax_calibrated_f',
    'forecast_spread_f',
    'station_enc',
    'season_enc',
]

_MODEL_CACHE = None


def _get_conn():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        host = os.environ.get("PG_HOST")
        port = os.environ.get("PG_PORT", "5432")
        db   = os.environ.get("PG_DB")
        user = os.environ.get("PG_USER")
        pwd  = os.environ.get("PG_PASSWORD", "")
        if host and db and user:
            import urllib.parse
            db_url = f"postgresql://{urllib.parse.quote(user)}:{urllib.parse.quote(pwd)}@{host}:{port}/{db}"
        else:
            sys.exit("ERROR: Neither DATABASE_URL nor PG_HOST/PG_DB/PG_USER are set")
    return psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor)


def _season_for(d: date) -> str:
    m = d.month
    if m in (12, 1, 2): return 'winter'
    if m in (3, 4, 5):  return 'spring'
    if m in (6, 7, 8):  return 'summer'
    return 'fall'


def _load_model():
    global _MODEL_CACHE
    if _MODEL_CACHE is None:
        from xgboost import XGBRegressor
        m = XGBRegressor()
        m.load_model(str(MODEL_PATH))
        _MODEL_CACHE = m
    return _MODEL_CACHE


def run_cp3_inference(station_code: str, target_date: date,
                      conn=None) -> dict:
    """
    Returns:
    {
        'pass': bool,
        'mode': 'xgboost' | 'emergency_fallback',
        'predicted_tmax_f': float | None,
        'nws_forecast_f': float | None,
        'calibrated_forecast_f': float | None,
        'model_rmse': float,   # from model_calibration at lead_hours=24
        'reason': str
    }
    """
    close_conn = conn is None
    if conn is None:
        conn = _get_conn()

    try:
        season = _season_for(target_date)

        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    nws_tmax_f, om_tmax_f,
                    nws_tmax_mean_bias, om_tmax_mean_bias,
                    nws_tmax_rmse, om_tmax_rmse,
                    nws_tmax_calibrated_f
                FROM weather_gold_city_day_features
                WHERE station_code = %s AND target_date = %s
            """, (station_code, target_date))
            gold = cur.fetchone()

        # If no gold row, fall back to bronze live snapshots.
        # The gold table is a settled-only training table; for pre-settlement
        # trading target_dates the bronze tables are the authoritative source.
        if gold is None:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (station_code)
                        tmax_f AS nws_tmax_f
                    FROM weather_bronze_nws_forecast_snapshots
                    WHERE station_code = %s AND target_date = %s AND tmax_f IS NOT NULL
                    ORDER BY station_code, retrieved_at DESC
                """, (station_code, target_date))
                nws_row = cur.fetchone()

                cur.execute("""
                    SELECT DISTINCT ON (station_code)
                        tmax_f AS om_tmax_f
                    FROM weather_bronze_openmeteo_forecast_snapshots
                    WHERE station_code = %s AND target_date = %s AND tmax_f IS NOT NULL
                    ORDER BY station_code, retrieved_at DESC
                """, (station_code, target_date))
                om_row = cur.fetchone()

                cur.execute("""
                    SELECT mean_bias, rmse
                    FROM model_calibration
                    WHERE station_code = %s AND variable = 'tmax_f'
                      AND source_model = 'nws' AND lead_time_hours = 24 AND season = %s
                    LIMIT 1
                """, (station_code, season))
                nws_cal_row = cur.fetchone()

                cur.execute("""
                    SELECT mean_bias, rmse
                    FROM model_calibration
                    WHERE station_code = %s AND variable = 'tmax_f'
                      AND source_model = 'open_meteo_gfs_seamless' AND lead_time_hours = 24 AND season = %s
                    LIMIT 1
                """, (station_code, season))
                om_cal_row = cur.fetchone()

            if nws_row is None and om_row is None:
                return {
                    'pass': False, 'mode': 'emergency_fallback',
                    'predicted_tmax_f': None, 'nws_forecast_f': None,
                    'om_tmax_f': None, 'calibrated_forecast_f': None,
                    'model_rmse': 3.5,
                    'reason': f'No NWS or OM bronze forecast for {station_code}/{target_date}',
                }

            nws_v   = float(nws_row['nws_tmax_f']) if nws_row else None
            om_v    = float(om_row['om_tmax_f'])  if om_row  else None
            bias    = float(nws_cal_row['mean_bias']) if (nws_cal_row and nws_cal_row['mean_bias']) else 0.0
            nws_rmse= float(nws_cal_row['rmse'])    if (nws_cal_row and nws_cal_row['rmse'])    else 0.0
            om_bias = float(om_cal_row['mean_bias']) if (om_cal_row  and om_cal_row['mean_bias']) else 0.0
            om_rmse = float(om_cal_row['rmse'])     if (om_cal_row  and om_cal_row['rmse'])     else 0.0
            cal_f   = round(nws_v - bias, 4) if nws_v is not None else None

            # Synthesise a pseudo-gold dict so the rest of the function works unchanged
            gold = {
                'nws_tmax_f': nws_v, 'om_tmax_f': om_v,
                'nws_tmax_mean_bias': bias, 'om_tmax_mean_bias': om_bias,
                'nws_tmax_rmse': nws_rmse, 'om_tmax_rmse': om_rmse,
                'nws_tmax_calibrated_f': cal_f,
                '_from_bronze': True,
            }

        with conn.cursor() as cur:
            cur.execute("""
                SELECT rmse
                FROM model_calibration
                WHERE station_code = %s
                  AND variable = 'tmax_f'
                  AND source_model = 'nws'
                  AND lead_time_hours = 24
                  AND season = %s
                LIMIT 1
            """, (station_code, season))
            cal_row = cur.fetchone()

    finally:
        if close_conn:
            conn.close()

    # Base sigma fallback — 3.5F is conservative but safe
    model_rmse = float(cal_row['rmse']) if (cal_row and cal_row['rmse']) else 3.5

    nws_tmax_f    = float(gold['nws_tmax_f'])            if gold['nws_tmax_f']            is not None else None
    om_tmax_f_raw = float(gold['om_tmax_f'])             if gold['om_tmax_f']             is not None else None
    calibrated_f  = float(gold['nws_tmax_calibrated_f']) if gold['nws_tmax_calibrated_f'] is not None else nws_tmax_f

    fallback_reason: Optional[str] = None

    if not MODEL_PATH.exists():
        fallback_reason = f'Model file not found at {MODEL_PATH}'
    else:
        try:
            model = _load_model()

            om_f      = om_tmax_f_raw if om_tmax_f_raw is not None else (nws_tmax_f or 0.0)
            nws_bias  = float(gold['nws_tmax_mean_bias']) if gold['nws_tmax_mean_bias'] is not None else 0.0
            om_bias   = float(gold['om_tmax_mean_bias'])  if gold['om_tmax_mean_bias']  is not None else 0.0
            nws_rmse  = float(gold['nws_tmax_rmse'])      if gold['nws_tmax_rmse']      is not None else 0.0
            om_rmse   = float(gold['om_tmax_rmse'])       if gold['om_tmax_rmse']       is not None else 0.0
            cal_f_val = calibrated_f                      if calibrated_f               is not None else (nws_tmax_f or 0.0)
            nws_val   = nws_tmax_f                        if nws_tmax_f                 is not None else 0.0

            features = np.array([[
                nws_val,
                om_f,
                nws_bias,
                om_bias,
                nws_rmse,
                om_rmse,
                cal_f_val,
                nws_val - om_f,                           # forecast_spread_f
                float(STATION_ENC.get(station_code, 0)),
                float(SEASON_ENC.get(season, 2)),
            ]], dtype=float)

            predicted = float(model.predict(features)[0])
            return {
                'pass': True,
                'mode': 'xgboost',
                'predicted_tmax_f': round(predicted, 2),
                'nws_forecast_f': nws_tmax_f,
                'om_tmax_f': om_tmax_f_raw,
                'calibrated_forecast_f': calibrated_f,
                'model_rmse': model_rmse,
                'reason': 'XGBoost inference successful',
            }

        except Exception as exc:
            fallback_reason = f'XGBoost failed ({exc}), falling back to calibrated NWS'

    # Emergency fallback path
    if calibrated_f is None:
        return {
            'pass': False,
            'mode': 'emergency_fallback',
            'predicted_tmax_f': None,
            'nws_forecast_f': nws_tmax_f,
            'calibrated_forecast_f': None,
            'model_rmse': model_rmse,
            'reason': f'{fallback_reason} — calibrated forecast also unavailable',
        }

    return {
        'pass': True,
        'mode': 'emergency_fallback',
        'predicted_tmax_f': round(calibrated_f, 2),
        'nws_forecast_f': nws_tmax_f,
        'om_tmax_f': om_tmax_f_raw,
        'calibrated_forecast_f': calibrated_f,
        'model_rmse': model_rmse,
        'reason': fallback_reason,
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="CP3 inference")
    p.add_argument('--station', required=True)
    p.add_argument('--date', required=True, type=date.fromisoformat)
    args = p.parse_args()
    result = run_cp3_inference(args.station, args.date)
    for k, v in result.items():
        print(f"  {k}: {v}")
