#!/usr/bin/env python3
"""
CP3: XGBoost tmax regressor training.
Trains on weather_gold_city_day_features (KDEN/KLAX/KMIA),
exports model to /opt/bhn/trading/models/weather_xgb_tmax.json.

Sample weights: live rows = 3.0x, historical_backfill rows = 1.0x.
Test set is drawn from live rows ONLY to ensure honest live validation.
Historical rows are used for training only.

Run:
    DATABASE_URL=postgresql:///eventhorizon python3 cp3_train_model.py

Environment: DATABASE_URL (peer auth: postgresql:///eventhorizon)
"""
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from sklearn.metrics import mean_squared_error
from xgboost import XGBRegressor

MODEL_PATH = Path("/opt/bhn/trading/models/weather_xgb_tmax.json")
TRADEABLE_STATIONS = ['KDEN', 'KLAX', 'KMIA', 'KAUS', 'KNYC', 'KORD']

STATION_ENC = {'KDEN': 0, 'KLAX': 1, 'KMIA': 2, 'KAUS': 3, 'KNYC': 4, 'KORD': 5}
SEASON_ENC  = {'winter': 0, 'spring': 1, 'summer': 2, 'fall': 3}

WEIGHT_LIVE       = 3.0
WEIGHT_HISTORICAL = 1.0

# Exact order must match cp3_inference.py
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

XGB_PARAMS = dict(
    n_estimators=200,
    max_depth=4,
    learning_rate=0.05,
    subsample=0.8,
    colsample_bytree=0.8,
    random_state=42,
    tree_method='hist',
    enable_categorical=False,
)


def _get_conn():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("ERROR: DATABASE_URL not set")
    return psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor)


def _load_data(conn) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                station_code, target_date, season,
                nws_tmax_f, om_tmax_f,
                nws_tmax_mean_bias, om_tmax_mean_bias,
                nws_tmax_rmse, om_tmax_rmse,
                nws_tmax_calibrated_f,
                actual_tmax_f,
                data_source
            FROM weather_gold_city_day_features
            WHERE station_code = ANY(%s)
              AND actual_tmax_f IS NOT NULL
              AND (nws_tmax_f IS NOT NULL OR om_tmax_f IS NOT NULL)
            ORDER BY target_date
        """, (TRADEABLE_STATIONS,))
        rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])


def _engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # Historical rows have om_tmax_f only; live rows have both.
    # Cross-fill so all feature columns are non-null.
    df['om_tmax_f']  = df['om_tmax_f'].fillna(df['nws_tmax_f'])
    df['nws_tmax_f'] = df['nws_tmax_f'].fillna(df['om_tmax_f'])  # after OM is filled
    df['nws_tmax_mean_bias']    = df['nws_tmax_mean_bias'].fillna(0.0)
    df['om_tmax_mean_bias']     = df['om_tmax_mean_bias'].fillna(0.0)
    df['nws_tmax_rmse']         = df['nws_tmax_rmse'].fillna(0.0)
    df['om_tmax_rmse']          = df['om_tmax_rmse'].fillna(0.0)
    df['nws_tmax_calibrated_f'] = df['nws_tmax_calibrated_f'].fillna(df['nws_tmax_f'])
    df['forecast_spread_f']     = df['nws_tmax_f'] - df['om_tmax_f']
    df['station_enc'] = df['station_code'].map(STATION_ENC).astype(float)
    df['season_enc']  = df['season'].map(SEASON_ENC).astype(float)
    return df


def train(conn=None) -> dict:
    close = conn is None
    if conn is None:
        conn = _get_conn()

    try:
        df = _load_data(conn)
    finally:
        if close:
            conn.close()

    if df.empty:
        sys.exit("ERROR: no training rows found in weather_gold_city_day_features")

    df = _engineer_features(df)

    # Split by data_source — live rows sorted chronologically for the split
    live_df = df[df['data_source'] == 'live'].sort_values('target_date').reset_index(drop=True)
    hist_df = df[df['data_source'] != 'live'].reset_index(drop=True)

    n_live       = len(live_df)
    n_historical = len(hist_df)

    if n_live < 10:
        print(f"WARNING: only {n_live} live rows — test set will be very small")

    # Test set = most recent 20% of live rows (honest live validation)
    # Training = all historical + first 80% of live rows
    live_split = max(1, int(n_live * 0.8))
    live_train_df = live_df.iloc[:live_split]
    live_test_df  = live_df.iloc[live_split:]

    train_df = pd.concat([hist_df, live_train_df], ignore_index=True)
    test_df  = live_test_df

    X_train = train_df[FEATURE_COLS].values.astype(float)
    y_train = train_df['actual_tmax_f'].values.astype(float)
    X_test  = test_df[FEATURE_COLS].values.astype(float)
    y_test  = test_df['actual_tmax_f'].values.astype(float)

    # Sample weights: live rows 3x, historical 1x
    weights = np.where(train_df['data_source'] == 'live', WEIGHT_LIVE, WEIGHT_HISTORICAL)

    model = XGBRegressor(**XGB_PARAMS)
    model.fit(X_train, y_train, sample_weight=weights)

    train_rmse = math.sqrt(mean_squared_error(y_train, model.predict(X_train)))
    if len(X_test) > 0:
        test_rmse = math.sqrt(mean_squared_error(y_test, model.predict(X_test)))
    else:
        test_rmse = float('nan')
        print("WARNING: test set is empty")

    print(f"n_live={n_live}  n_historical={n_historical}")
    print(f"train_size={len(train_df)}  "
          f"(historical={n_historical}, live_train={len(live_train_df)})")
    print(f"test_size={len(test_df)}  (live rows only)")
    print(f"Train RMSE : {train_rmse:.4f} F")
    print(f"Test  RMSE : {test_rmse:.4f} F  [live validation]")

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(str(MODEL_PATH))
    print(f"Model saved → {MODEL_PATH}")

    return {
        'n_live':      n_live,
        'n_historical': n_historical,
        'train_rmse':  round(train_rmse, 4),
        'test_rmse':   round(test_rmse, 4),
    }


if __name__ == "__main__":
    result = train()
    print(result)
