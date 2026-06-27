#!/usr/bin/env python3
"""
weather_edge_calculator.py — BHN Strategy 9 Gold Layer edge calculator.

Reads from silver tables → computes bucket probabilities → writes gold edge sheet.
Scope (Phase 1): Miami (KMIA) + Denver (KDEN), daily HIGH temp only.
Run every 5 minutes (manual trigger for now; systemd timer added Phase 2).

CLI:
  python3 weather_edge_calculator.py
  python3 weather_edge_calculator.py --dry-run
  python3 weather_edge_calculator.py --city KMIA
  python3 weather_edge_calculator.py --city KDEN
  python3 weather_edge_calculator.py --days-ahead 2

Normal distribution CDF via math.erf (no scipy needed):
  P(X ≤ x) = 0.5 * (1 + erf((x - mu) / (sigma * sqrt(2))))

Calibrator version: v0_passthrough (raw model probs, no isotonic calibration yet).
Calibration kicks in Phase 2 once 30+ error pairs exist per station.

Sigma defaults (used until 30 days of silver_forecast_error history):
  KMIA=2.5°F, KDEN=3.5°F, KPHX=2.0°F, KLAX=2.0°F,
  KDFW=3.0°F, KNYC=3.0°F, KORD=3.5°F, KAUS=3.0°F

Kelly sizing: half-Kelly with 25% bankroll cap per bet.
Bankroll: KALSHI_BANKROLL env var (default 500).

Edge thresholds: BET_YES if edge ≥ 0.05, BET_NO if edge ≤ -0.05, else SKIP.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


def _prime_env() -> None:
    """Load /etc/bhn-trading/env and strat9.env before trading_core initialises."""
    for path in ("/etc/bhn-trading/env", "/etc/bhn-trading/strat9.env"):
        p = Path(path)
        if not p.is_file():
            continue
        for ln in p.read_text().splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            k = k.strip()
            if k not in os.environ:
                os.environ[k] = v.strip().strip('"').strip("'")


_prime_env()

import trading_core as tc  # noqa: E402
from fee_calculator import maker_fee  # A3: Kalshi July-2025 formula (replaces flat FEE_BUFFER)

logger = logging.getLogger("strat9_edge_calculator")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)

CALIBRATOR_VERSION = "v0_passthrough"

ACTIVE_STATIONS = {"KMIA", "KDEN"}     # Phase 1 scope — expand in Phase 2
ACTIVE_SIDE = "high"                    # daily HIGH temp only in Phase 1

SIGMA_DEFAULTS: dict[str, float] = {
    "KMIA": 2.5, "KDEN": 3.5, "KPHX": 2.0,
    "KLAX": 2.0, "KDFW": 3.0, "KNYC": 3.0,
    "KORD": 3.5, "KAUS": 3.0,
}

EDGE_THRESHOLD_BET = 0.05   # minimum edge to recommend a YES or NO bet
MIP_MIN = 0.05              # skip contracts where market YES price < 5¢ (fringe tail)
MIP_MAX = 0.95              # skip contracts where market YES price > 95¢ (fringe tail)
MIN_BIAS_ROWS = 7           # need at least this many error pairs to use bias
MIN_SIGMA_ROWS = 30         # need at least this many to use computed sigma
KELLY_CAP = 0.25            # cap Kelly fraction at 25% of bankroll

CITY_NAME: dict[str, str] = {
    "KMIA": "Miami", "KDEN": "Denver", "KPHX": "Phoenix",
    "KLAX": "Los Angeles", "KDFW": "Dallas/Fort Worth",
    "KNYC": "New York City", "KORD": "Chicago", "KAUS": "Austin",
}

# Onshore wind direction ranges (degrees FROM, meteorological convention: 0=N, 90=E).
# Sea breeze flag only meaningful for coastal cities — inland cities return None.
# KMIA: Atlantic + Biscayne Bay inflow from E/SE/S
# KLAX: Pacific inflow from SW/W/NW
# KNYC: Atlantic inflow from E/SE
COASTAL_ONSHORE: dict[str, tuple[int, int]] = {
    "KMIA": (45,  225),
    "KLAX": (180, 315),
    "KNYC": (45,  180),
}


# ─────────────────────────────────────────────────────────────────────────
# Normal distribution helpers (no scipy)
# ─────────────────────────────────────────────────────────────────────────

def _norm_cdf(x: float, mu: float, sigma: float) -> float:
    """CDF of normal distribution at x with mean mu and std sigma."""
    if sigma <= 0:
        return 1.0 if x >= mu else 0.0
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def _bucket_prob(bucket_type: str,
                 bucket_floor: Optional[float],
                 bucket_cap: Optional[float],
                 mu: float,
                 sigma: float) -> float:
    """Compute P(actual falls in bucket) given normal(mu, sigma).
    bucket_type: 'between', 'above', 'below', 'threshold'
    """
    if bucket_type == "between" and bucket_floor is not None and bucket_cap is not None:
        return max(0.0, _norm_cdf(bucket_cap, mu, sigma) - _norm_cdf(bucket_floor, mu, sigma))
    elif bucket_type in ("above", "threshold") and bucket_floor is not None:
        return max(0.0, 1.0 - _norm_cdf(bucket_floor, mu, sigma))
    elif bucket_type == "below" and bucket_cap is not None:
        return max(0.0, _norm_cdf(bucket_cap, mu, sigma))
    return 1.0 / 10.0


def _half_kelly(edge: float, market_prob: float) -> float:
    """Half-Kelly stake fraction for a YES bet.
    Formula: edge / (1 - market_prob) * 0.5, capped at KELLY_CAP.
    Returns 0 if edge <= 0 or market_prob is degenerate.
    """
    if market_prob <= 0.0 or market_prob >= 1.0 or edge <= 0.0:
        return 0.0
    return min(edge / (1.0 - market_prob) * 0.5, KELLY_CAP)


# ─────────────────────────────────────────────────────────────────────────
# DB reads
# ─────────────────────────────────────────────────────────────────────────

def _get_active_contracts(conn, station_code: str,
                           target_dates: list[date]) -> list[dict]:
    """Read active contracts from weather_kalshi_contract_catalog."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT market_ticker, event_ticker, series_ticker,
                   contract_side, bucket_type, bucket_floor, bucket_cap, bucket_label,
                   target_date, market_status
            FROM weather_kalshi_contract_catalog
            WHERE station_code = %s
              AND contract_side = %s
              AND target_date = ANY(%s)
              AND is_active = TRUE
            ORDER BY target_date, bucket_floor NULLS LAST
        """, (station_code, ACTIVE_SIDE, target_dates))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _get_latest_nws_forecast(conn, station_code: str,
                              target_date: date) -> Optional[dict]:
    """Get most recent NWS forecast for station/date from silver."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT tmax_f, tmin_f, forecast_run_time, lead_hours,
                   dewpoint_f, rh_pct, cloud_cover_pct, pop_pct
            FROM weather_silver_forecast_conformed
            WHERE station_code = %s AND source_name = 'nws'
              AND target_date = %s AND is_latest_run = TRUE
              AND tmax_f IS NOT NULL
            LIMIT 1
        """, (station_code, target_date))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))


def _get_latest_gfs_forecast(conn, station_code: str,
                              target_date: date) -> Optional[dict]:
    """Get most recent GFS/Open-Meteo forecast for station/date from silver."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT tmax_f, tmin_f, forecast_run_time
            FROM weather_silver_forecast_conformed
            WHERE station_code = %s AND source_name = 'open_meteo_gfs_seamless'
              AND target_date = %s AND is_latest_run = TRUE
              AND tmax_f IS NOT NULL
            LIMIT 1
        """, (station_code, target_date))
        row = cur.fetchone()
        if not row:
            # Fallback: any open_meteo source
            cur.execute("""
                SELECT tmax_f, tmin_f, forecast_run_time
                FROM weather_silver_forecast_conformed
                WHERE station_code = %s AND source_name LIKE 'open_meteo%%'
                  AND target_date = %s AND is_latest_run = TRUE
                  AND tmax_f IS NOT NULL
                ORDER BY forecast_run_time DESC
                LIMIT 1
            """, (station_code, target_date))
            row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))


def _get_ensemble_spread(conn, station_code: str,
                          target_date: date) -> Optional[float]:
    """Get today's ensemble spread (stddev of member daily highs) from bronze.
    Returns None if no ensemble data available for this station/date.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ensemble_spread_tmax
            FROM weather_bronze_openmeteo_forecast_snapshots
            WHERE station_code = %s AND model = 'open_meteo_ensemble'
              AND target_date = %s AND hour = -1
              AND ensemble_spread_tmax IS NOT NULL
            ORDER BY retrieved_at DESC
            LIMIT 1
        """, (station_code, target_date))
        row = cur.fetchone()
        return float(row[0]) if row else None


def _get_ensemble_member_highs(conn, station_code: str,
                                target_date: date) -> Optional[list]:
    """Get individual GFS ensemble member daily highs from bronze.
    Returns list of per-member tmax °F values, or None if unavailable.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT member_highs_json
            FROM weather_bronze_openmeteo_forecast_snapshots
            WHERE station_code = %s AND model = 'open_meteo_ensemble'
              AND target_date = %s AND hour = -1
              AND member_highs_json IS NOT NULL
            ORDER BY retrieved_at DESC
            LIMIT 1
        """, (station_code, target_date))
        row = cur.fetchone()
        if not row or not row[0]:
            return None
        data = row[0]
        if isinstance(data, str):
            data = json.loads(data)
        return [float(v) for v in data if v is not None]


def _ensemble_bucket_prob(member_highs: list,
                           bucket_type: str,
                           bucket_floor: Optional[float],
                           bucket_cap: Optional[float]) -> float:
    """Compute P(daily high in bucket) by counting ensemble members."""
    if not member_highs:
        return 0.0
    total = len(member_highs)
    if bucket_type == "between" and bucket_floor is not None and bucket_cap is not None:
        count = sum(1 for h in member_highs if bucket_floor <= h < bucket_cap)
    elif bucket_type in ("above", "threshold") and bucket_floor is not None:
        count = sum(1 for h in member_highs if h >= bucket_floor)
    elif bucket_type == "below" and bucket_cap is not None:
        count = sum(1 for h in member_highs if h < bucket_cap)
    else:
        return 1.0 / max(total, 10)
    return round(count / total, 6)


def _get_nbm_percentiles(conn, station_code: str,
                          target_date: date) -> Optional[dict]:
    """Get latest NWS NBM temperature percentiles for station/date."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p10_tmax_f, p25_tmax_f, p50_tmax_f, p75_tmax_f, p90_tmax_f
            FROM weather_bronze_nbm_snapshots
            WHERE station_code = %s AND target_date = %s
              AND p50_tmax_f IS NOT NULL
            ORDER BY retrieved_at DESC
            LIMIT 1
        """, (station_code, target_date))
        row = cur.fetchone()
        if not row:
            return None
        p10, p25, p50, p75, p90 = [float(v) if v is not None else None for v in row]
        if p50 is None:
            return None
        return {"p10": p10, "p25": p25, "p50": p50, "p75": p75, "p90": p90}


def _bucket_prob_from_percentiles(bucket_type: str,
                                   bucket_floor: Optional[float],
                                   bucket_cap: Optional[float],
                                   pcts: dict) -> float:
    """Compute P(daily high in bucket) using piecewise linear CDF from percentiles.

    Builds a 5-point CDF from the available percentiles and interpolates.
    No distributional assumption — purely data-driven.
    """
    pts = []
    for key, prob in (("p10", 0.10), ("p25", 0.25), ("p50", 0.50),
                      ("p75", 0.75), ("p90", 0.90)):
        if pcts.get(key) is not None:
            pts.append((float(pcts[key]), prob))
    if len(pts) < 2:
        return 0.1  # not enough data

    pts.sort()

    def _cdf(x: float) -> float:
        if x <= pts[0][0]:
            # Extrapolate below P10
            if len(pts) >= 2 and pts[1][0] > pts[0][0]:
                slope = (pts[1][1] - pts[0][1]) / (pts[1][0] - pts[0][0])
                return max(0.0, pts[0][1] + slope * (x - pts[0][0]))
            return 0.0
        if x >= pts[-1][0]:
            # Extrapolate above P90
            if len(pts) >= 2 and pts[-1][0] > pts[-2][0]:
                slope = (pts[-1][1] - pts[-2][1]) / (pts[-1][0] - pts[-2][0])
                return min(1.0, pts[-1][1] + slope * (x - pts[-1][0]))
            return 1.0
        for i in range(len(pts) - 1):
            x0, p0 = pts[i]
            x1, p1 = pts[i + 1]
            if x0 <= x <= x1 and x1 > x0:
                return p0 + (p1 - p0) * (x - x0) / (x1 - x0)
        return 0.5

    if bucket_type == "between" and bucket_floor is not None and bucket_cap is not None:
        return max(0.0, _cdf(bucket_cap) - _cdf(bucket_floor))
    elif bucket_type in ("above", "threshold") and bucket_floor is not None:
        return max(0.0, 1.0 - _cdf(bucket_floor))
    elif bucket_type == "below" and bucket_cap is not None:
        return max(0.0, _cdf(bucket_cap))
    return 0.1


def _get_forecast_stats(conn, station_code: str) -> dict:
    """Get historical bias and MAE from silver_forecast_error, last 30 days."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) AS row_count,
                AVG(forecast_error_f) AS mean_bias,
                AVG(ABS(forecast_error_f)) AS mae
            FROM weather_silver_forecast_error
            WHERE station_code = %s
              AND feature_name = 'tmax_f'
              AND source_name = 'nws'
              AND created_at >= NOW() - INTERVAL '30 days'
        """, (station_code,))
        row = cur.fetchone()
        if not row or row[0] == 0:
            return {"row_count": 0, "mean_bias": None, "mae": None}
        return {"row_count": row[0], "mean_bias": row[1], "mae": row[2]}


def _get_latest_market_snapshot(conn, market_ticker: str) -> Optional[dict]:
    """Get latest market price from silver_market_conformed."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT yes_mid, implied_prob, volume, market_status, snapshot_time
            FROM weather_silver_market_conformed
            WHERE market_ticker = %s AND is_latest_snapshot = TRUE
            LIMIT 1
        """, (market_ticker,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))


def _get_orderbook_asks(conn, market_ticker: str) -> Optional[dict]:
    """Read yes_ask and no_ask directly from the bronze orderbook snapshot.

    A1 Part 2: always read no_ask from the real orderbook; never derive as (1 - yes_ask).
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT yes_ask, no_ask
            FROM weather_bronze_kalshi_market_snapshots
            WHERE market_ticker = %s
              AND yes_ask IS NOT NULL
              AND no_ask  IS NOT NULL
            ORDER BY retrieved_at DESC
            LIMIT 1
        """, (market_ticker,))
        row = cur.fetchone()
        if not row:
            return None
        return {"yes_ask": float(row[0]), "no_ask": float(row[1])}


def _get_bankroll() -> float:
    try:
        return float(os.environ.get("KALSHI_BANKROLL", "500"))
    except (ValueError, TypeError):
        return 500.0


# ─────────────────────────────────────────────────────────────────────────
# Gold writes
# ─────────────────────────────────────────────────────────────────────────

def _insert_calibrated_prob(conn, *, city: str, station_code: str,
                              target_date: date, market_ticker: str,
                              bucket_floor: Optional[float], bucket_cap: Optional[float],
                              bucket_label: Optional[str],
                              raw_model_prob: float, calibrated_prob: float,
                              market_implied_prob: float,
                              edge: float, edge_rank: int,
                              trade_flag: str, confidence: str,
                              model_delta_flag: str) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO weather_gold_calibrated_probabilities
                (city, station_code, target_date, contract_side, market_ticker,
                 bucket_floor, bucket_cap, bucket_label,
                 raw_model_prob, calibrated_prob, market_implied_prob,
                 edge, edge_rank, trade_flag, confidence, model_delta_flag,
                 calibrator_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (city, station_code, target_date, ACTIVE_SIDE, market_ticker,
              bucket_floor, bucket_cap, bucket_label,
              raw_model_prob, calibrated_prob, market_implied_prob,
              edge, edge_rank, trade_flag, confidence, model_delta_flag,
              CALIBRATOR_VERSION))


def _compute_hourly_features(conn, station_code: str, target_date) -> Optional[dict]:
    """Derive 5 physical features from NWS hourly data for one station/date.

    Returns None if no hourly rows exist yet for that date.
    sea_breeze_flag is None for inland cities (not in COASTAL_ONSHORE).
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT hour, tmax_f, pop_pct, cloud_cover_pct, wind_speed_mph, wind_direction_deg
            FROM weather_bronze_nws_forecast_snapshots
            WHERE station_code = %s
              AND target_date  = %s
              AND source_name  = 'nws_hourly'
              AND hour IS NOT NULL
            ORDER BY hour
        """, (station_code, target_date))
        rows = cur.fetchall()

    if not rows:
        return None

    # peak_hour: hour with highest hourly temp during daytime (hours 6-20)
    day_rows = [(h, t, p, c, w, d) for h, t, p, c, w, d in rows
                if t is not None and 6 <= h <= 20]
    if not day_rows:
        return None
    peak_hour = max(day_rows, key=lambda x: x[1])[0]

    # afternoon_storm_flag: any hour 12-17 with pop_pct > 20%
    afternoon_storm_flag = any(
        p is not None and float(p) > 20.0
        for h, t, p, c, w, d in rows if 12 <= h <= 17
    )

    # pre_peak_storm_flag: storm in hours [12, peak_hour) — suppresses heating before max
    pre_peak_storm_flag = any(
        p is not None and float(p) > 20.0
        for h, t, p, c, w, d in rows if 12 <= h < peak_hour
    )

    # cloud_timing_delta: (hour of max cloud cover) - peak_hour
    # Negative = clouds peaked before max heat = stronger suppression
    cloud_rows = [(h, float(c)) for h, t, p, c, w, d in rows
                  if c is not None and 10 <= h <= 20]
    cloud_timing_delta: Optional[float] = None
    if cloud_rows:
        max_cloud_hour = max(cloud_rows, key=lambda x: x[1])[0]
        cloud_timing_delta = float(max_cloud_hour - peak_hour)

    # sea_breeze_flag: coastal cities only — wind_speed_mph > 5 AND onshore during 12-17
    sea_breeze_flag: Optional[bool] = None
    if station_code in COASTAL_ONSHORE:
        lo, hi = COASTAL_ONSHORE[station_code]
        sea_breeze_flag = any(
            w is not None and d is not None
            and float(w) > 5.0
            and lo <= float(d) <= hi
            for h, t, p, c, w, d in rows if 12 <= h <= 17
        )

    return {
        "peak_hour":            peak_hour,
        "afternoon_storm_flag": afternoon_storm_flag,
        "pre_peak_storm_flag":  pre_peak_storm_flag,
        "cloud_timing_delta":   cloud_timing_delta,
        "sea_breeze_flag":      sea_breeze_flag,
    }


def _upsert_edge_sheet(conn, *, city: str, station_code: str,
                        target_date: date, contract_ticker: str,
                        bucket_floor: Optional[float], bucket_cap: Optional[float],
                        bucket_label: Optional[str],
                        raw_forecast_f: Optional[float],
                        gfs_forecast_f: Optional[float],
                        model_delta_f: Optional[float],
                        model_confidence: str,
                        calibrated_prob: float, raw_model_prob: float,
                        market_implied_prob: Optional[float],
                        market_yes_mid: Optional[float],
                        market_volume: Optional[float],
                        market_liquidity: str,
                        edge: float, edge_pct: float, edge_rank: int,
                        recommended_action: str,
                        stake_fraction: float, stake_usd: float,
                        skip_reason: Optional[str],
                        ensemble_spread: Optional[float] = None,
                        nws_high_prob_pct: Optional[float] = None,
                        gfs_high_prob_pct: Optional[float] = None,
                        peak_hour: Optional[int] = None,
                        afternoon_storm_flag: Optional[bool] = None,
                        pre_peak_storm_flag: Optional[bool] = None,
                        cloud_timing_delta: Optional[float] = None,
                        sea_breeze_flag: Optional[bool] = None) -> None:
    sheet_date = datetime.now(timezone.utc).date()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO weather_gold_daily_edge_sheet
                (city, station_code, target_date, contract_side, contract_ticker,
                 bucket_floor, bucket_cap, bucket_label, sheet_date,
                 raw_forecast_f, gfs_forecast_f, model_delta_f, model_confidence,
                 calibrated_prob, raw_model_prob,
                 market_implied_prob, market_yes_mid, market_volume, market_liquidity,
                 edge, edge_pct, edge_rank,
                 recommended_action, stake_fraction, stake_usd, skip_reason,
                 ensemble_spread, nws_high_prob_pct, gfs_high_prob_pct,
                 peak_hour, afternoon_storm_flag, pre_peak_storm_flag,
                 cloud_timing_delta, sea_breeze_flag,
                 last_updated, calibrator_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, NOW(), %s)
            ON CONFLICT (contract_ticker, sheet_date) DO UPDATE SET
                bucket_floor          = EXCLUDED.bucket_floor,
                bucket_cap            = EXCLUDED.bucket_cap,
                bucket_label          = EXCLUDED.bucket_label,
                raw_forecast_f        = EXCLUDED.raw_forecast_f,
                gfs_forecast_f        = EXCLUDED.gfs_forecast_f,
                model_delta_f         = EXCLUDED.model_delta_f,
                model_confidence      = EXCLUDED.model_confidence,
                calibrated_prob       = EXCLUDED.calibrated_prob,
                raw_model_prob        = EXCLUDED.raw_model_prob,
                market_implied_prob   = EXCLUDED.market_implied_prob,
                market_yes_mid        = EXCLUDED.market_yes_mid,
                market_volume         = EXCLUDED.market_volume,
                market_liquidity      = EXCLUDED.market_liquidity,
                edge                  = EXCLUDED.edge,
                edge_pct              = EXCLUDED.edge_pct,
                edge_rank             = EXCLUDED.edge_rank,
                recommended_action    = EXCLUDED.recommended_action,
                stake_fraction        = EXCLUDED.stake_fraction,
                stake_usd             = EXCLUDED.stake_usd,
                skip_reason           = EXCLUDED.skip_reason,
                ensemble_spread       = EXCLUDED.ensemble_spread,
                nws_high_prob_pct     = COALESCE(EXCLUDED.nws_high_prob_pct, weather_gold_daily_edge_sheet.nws_high_prob_pct),
                gfs_high_prob_pct     = COALESCE(EXCLUDED.gfs_high_prob_pct, weather_gold_daily_edge_sheet.gfs_high_prob_pct),
                peak_hour             = COALESCE(EXCLUDED.peak_hour, weather_gold_daily_edge_sheet.peak_hour),
                afternoon_storm_flag  = COALESCE(EXCLUDED.afternoon_storm_flag, weather_gold_daily_edge_sheet.afternoon_storm_flag),
                pre_peak_storm_flag   = COALESCE(EXCLUDED.pre_peak_storm_flag, weather_gold_daily_edge_sheet.pre_peak_storm_flag),
                cloud_timing_delta    = COALESCE(EXCLUDED.cloud_timing_delta, weather_gold_daily_edge_sheet.cloud_timing_delta),
                sea_breeze_flag       = COALESCE(EXCLUDED.sea_breeze_flag, weather_gold_daily_edge_sheet.sea_breeze_flag),
                last_updated          = NOW()
        """, (city, station_code, target_date, ACTIVE_SIDE, contract_ticker,
              bucket_floor, bucket_cap, bucket_label, sheet_date,
              raw_forecast_f, gfs_forecast_f, model_delta_f, model_confidence,
              calibrated_prob, raw_model_prob,
              market_implied_prob, market_yes_mid, market_volume, market_liquidity,
              edge, edge_pct, edge_rank,
              recommended_action, stake_fraction, stake_usd, skip_reason,
              ensemble_spread, nws_high_prob_pct, gfs_high_prob_pct,
              peak_hour, afternoon_storm_flag, pre_peak_storm_flag,
              cloud_timing_delta, sea_breeze_flag,
              CALIBRATOR_VERSION))


# ─────────────────────────────────────────────────────────────────────────
# Main calculation loop
# ─────────────────────────────────────────────────────────────────────────

def run_edge_calc(stations: Optional[list[str]] = None,
                  days_ahead: int = 1,
                  dry_run: bool = False) -> int:
    """Run the edge calculation for all active contracts.
    Returns number of edge sheet rows written."""
    target_stations = stations or list(ACTIVE_STATIONS)
    bankroll = _get_bankroll()
    today = datetime.now(timezone.utc).date()
    target_dates = [today + timedelta(days=i) for i in range(days_ahead + 1)]

    rows_written = 0
    sheet_date = today

    with tc.get_pg_conn() as conn:
        for station_code in target_stations:
            city = CITY_NAME.get(station_code, station_code)

            # Historical forecast stats for this station
            stats = _get_forecast_stats(conn, station_code)
            n_error_rows = stats["row_count"]
            mean_bias = float(stats["mean_bias"]) if stats["mean_bias"] is not None else 0.0
            mae = float(stats["mae"]) if stats["mae"] is not None else None

            # Sigma: use computed MAE if enough data, else default
            if mae is not None and n_error_rows >= MIN_SIGMA_ROWS:
                sigma = mae
            else:
                sigma = SIGMA_DEFAULTS.get(station_code, 3.0)

            # Use bias only if enough data
            bias = mean_bias if n_error_rows >= MIN_BIAS_ROWS else 0.0

            for target_date in target_dates:
                # Get NWS + GFS forecasts
                nws = _get_latest_nws_forecast(conn, station_code, target_date)
                gfs = _get_latest_gfs_forecast(conn, station_code, target_date)

                if nws is None:
                    logger.debug(f"{station_code} {target_date}: no NWS forecast — skipping")
                    continue

                nws_tmax = float(nws["tmax_f"])
                gfs_tmax = float(gfs["tmax_f"]) if gfs and gfs.get("tmax_f") else None

                # Bias-adjusted point forecast
                mu = nws_tmax + bias

                # Model confidence: ensemble spread is primary signal; NWS-GFS delta is fallback
                ensemble_spread = _get_ensemble_spread(conn, station_code, target_date)
                member_highs = _get_ensemble_member_highs(conn, station_code, target_date)
                nbm_pcts = _get_nbm_percentiles(conn, station_code, target_date)
                if gfs_tmax is not None:
                    model_delta = abs(nws_tmax - gfs_tmax)
                    model_delta_flag = "AGREE" if model_delta < 2.0 else "DIVERGE"
                else:
                    model_delta = None
                    model_delta_flag = "NO_GFS"

                if ensemble_spread is not None:
                    # Ensemble spread overrides NWS-GFS delta for confidence
                    if ensemble_spread <= 2.0:
                        confidence = "HIGH"
                    elif ensemble_spread <= 4.0:
                        confidence = "MEDIUM"
                    else:
                        confidence = "LOW"
                elif gfs_tmax is not None:
                    if model_delta < 2.0:       # type: ignore[operator]
                        confidence = "HIGH"
                    elif model_delta < 4.0:     # type: ignore[operator]
                        confidence = "MEDIUM"
                    else:
                        confidence = "LOW"
                else:
                    confidence = "MEDIUM"

                # Get all active contracts for this station/date
                contracts = _get_active_contracts(conn, station_code, [target_date])
                if not contracts:
                    logger.debug(f"{station_code} {target_date}: no active contracts in catalog")
                    continue

                # Hourly-derived features (once per station/date, shared across all contracts)
                hourly_features = _compute_hourly_features(conn, station_code, target_date) or {}

                # Compute blended probability for each contract.
                # Two independent source families:
                #   NWS: NBM percentile piecewise-CDF (preferred, non-parametric) or
                #        Gaussian approximation from point forecast (fallback).
                #   GFS: direct ensemble member counting from Open-Meteo.
                # Equal weights between the two available families; swap for Brier-score
                # weights once sufficient settlement history exists.
                _nws_src = "nbm" if nbm_pcts else "gauss"
                _gfs_src = f"{len(member_highs)}mbrs" if member_highs else "none"
                logger.debug(
                    f"{station_code} {target_date}: prob sources → nws={_nws_src} gfs={_gfs_src}"
                )

                contract_probs: list[tuple[dict, float, float, Optional[float], Optional[float]]] = []
                for c in contracts:
                    bucket_type = c["bucket_type"] or "between"
                    bucket_floor = float(c["bucket_floor"]) if c["bucket_floor"] is not None else None
                    bucket_cap = float(c["bucket_cap"]) if c["bucket_cap"] is not None else None

                    nws_gauss_prob = _bucket_prob(bucket_type, bucket_floor, bucket_cap, mu, sigma)
                    nbm_prob = (
                        _bucket_prob_from_percentiles(bucket_type, bucket_floor, bucket_cap, nbm_pcts)
                        if nbm_pcts else None
                    )
                    gfs_prob = (
                        _ensemble_bucket_prob(member_highs, bucket_type, bucket_floor, bucket_cap)
                        if member_highs else None
                    )
                    # NBM supersedes Gaussian when available — same NWS source, better representation
                    nws_prob = nbm_prob if nbm_prob is not None else nws_gauss_prob
                    source_probs = [p for p in [nws_prob, gfs_prob] if p is not None]
                    raw_prob = sum(source_probs) / len(source_probs)

                    contract_probs.append((c, raw_prob, nws_gauss_prob, nbm_prob, gfs_prob))

                # Rank contracts by edge magnitude (need market prices first)
                edges: list[tuple[dict, float, float, float, float, Optional[float], Optional[float]]] = []
                for c, raw_prob, nws_gauss_prob, nbm_prob, gfs_prob in contract_probs:
                    market = _get_latest_market_snapshot(conn, c["market_ticker"])
                    if market is None:
                        continue
                    mip = float(market["yes_mid"]) if market.get("yes_mid") else None
                    if mip is None:
                        continue
                    edge_yes = raw_prob - mip  # YES-perspective; used for trading decision
                    edges.append((c, raw_prob, mip, edge_yes, nws_gauss_prob, nbm_prob, gfs_prob))

                # Sort by abs(edge) descending for ranking
                edges.sort(key=lambda x: abs(x[3]), reverse=True)

                for rank, (c, raw_prob, mip, edge_yes,
                           nws_gauss_prob, nbm_prob, gfs_prob) in enumerate(edges, start=1):
                    market = _get_latest_market_snapshot(conn, c["market_ticker"])
                    if market is None:
                        continue

                    calibrated_prob = raw_prob  # passthrough until calibration exists

                    # A1 Part 2 + A3: fetch real ask prices from orderbook
                    # no_ask is NEVER derived as (1 - yes_ask) — that gives the wrong price
                    asks    = _get_orderbook_asks(conn, c["market_ticker"])
                    yes_ask = float(asks["yes_ask"]) if asks else mip
                    no_ask  = float(asks["no_ask"])  if asks else None

                    # A1 Part 1 + A3: fee-adjusted edge per side using real ask prices
                    yes_fee      = maker_fee(yes_ask)
                    adj_edge_yes = calibrated_prob       - yes_ask - yes_fee
                    if no_ask is not None:
                        no_fee      = maker_fee(no_ask)
                        adj_edge_no = (1 - calibrated_prob) - no_ask - no_fee
                    else:
                        adj_edge_no = -1.0  # no real no_ask available — cannot evaluate NO side

                    # Trading decision using fee-adjusted, side-specific edges
                    if mip < MIP_MIN or mip > MIP_MAX:
                        action = "SKIP"
                        kelly = 0.0
                        skip_reason = f"mip={mip:.3f} outside [{MIP_MIN},{MIP_MAX}] fringe"
                    elif adj_edge_yes >= EDGE_THRESHOLD_BET:
                        action = "BET_YES"
                        kelly = _half_kelly(adj_edge_yes, yes_ask)
                        skip_reason = None
                    elif adj_edge_no >= EDGE_THRESHOLD_BET:
                        action = "BET_NO"
                        kelly = _half_kelly(adj_edge_no, no_ask)
                        skip_reason = None
                    else:
                        action = "SKIP"
                        kelly = 0.0
                        skip_reason = (
                            f"adj_yes={adj_edge_yes:.3f} adj_no={adj_edge_no:.3f} "
                            f"both below threshold {EDGE_THRESHOLD_BET}"
                        )

                    # Stored edge: always positive = BHN advantage in the recommended direction
                    stored_edge = adj_edge_no if action == "BET_NO" else adj_edge_yes

                    stake_usd = kelly * bankroll

                    bucket_floor = float(c["bucket_floor"]) if c["bucket_floor"] is not None else None
                    bucket_cap = float(c["bucket_cap"]) if c["bucket_cap"] is not None else None
                    vol = float(market.get("volume", 0) or 0)
                    liquidity = "liquid" if vol > 1000 else ("thin" if vol > 100 else "illiquid")

                    # Source probabilities pre-computed in blend loop above
                    nws_high_prob_pct = nbm_prob if nbm_prob is not None else nws_gauss_prob
                    gfs_high_prob_pct = gfs_prob

                    _nws_tag  = f"nbm={nbm_prob:.3f}" if nbm_prob is not None else f"gauss={nws_gauss_prob:.3f}"
                    _gfs_tag  = f" gfs={gfs_prob:.3f}" if gfs_prob is not None else ""
                    _ask_tag  = f"yes_ask={yes_ask:.2f}"
                    if no_ask is not None:
                        _ask_tag += f" no_ask={no_ask:.2f}"
                    log_msg = (
                        f"{station_code} {target_date} {c['bucket_label'] or c['market_ticker']}: "
                        f"blend={calibrated_prob:.3f} [{_nws_tag}{_gfs_tag}] "
                        f"mid={mip:.3f} [{_ask_tag}] edge={stored_edge:+.3f} ({action})"
                        + (f" kelly={kelly:.3f} stake=${stake_usd:.2f}" if action != "SKIP" else "")
                    )
                    logger.info(log_msg)

                    if not dry_run:
                        try:
                            _insert_calibrated_prob(
                                conn,
                                city=city,
                                station_code=station_code,
                                target_date=target_date,
                                market_ticker=c["market_ticker"],
                                bucket_floor=bucket_floor,
                                bucket_cap=bucket_cap,
                                bucket_label=c.get("bucket_label"),
                                raw_model_prob=raw_prob,
                                calibrated_prob=calibrated_prob,
                                market_implied_prob=mip,
                                edge=stored_edge,
                                edge_rank=rank,
                                trade_flag=action,
                                confidence=confidence,
                                model_delta_flag=model_delta_flag,
                            )
                            _upsert_edge_sheet(
                                conn,
                                city=city,
                                station_code=station_code,
                                target_date=target_date,
                                contract_ticker=c["market_ticker"],
                                bucket_floor=bucket_floor,
                                bucket_cap=bucket_cap,
                                bucket_label=c.get("bucket_label"),
                                raw_forecast_f=nws_tmax,
                                gfs_forecast_f=gfs_tmax,
                                model_delta_f=model_delta,
                                model_confidence=confidence,
                                calibrated_prob=calibrated_prob,
                                raw_model_prob=raw_prob,
                                market_implied_prob=mip,
                                market_yes_mid=float(market.get("yes_mid", mip)),
                                market_volume=vol if vol > 0 else None,
                                market_liquidity=liquidity,
                                edge=stored_edge,
                                edge_pct=stored_edge * 100.0,
                                edge_rank=rank,
                                recommended_action=action,
                                stake_fraction=kelly,
                                stake_usd=stake_usd,
                                skip_reason=skip_reason,
                                ensemble_spread=ensemble_spread,
                                nws_high_prob_pct=nws_high_prob_pct,
                                gfs_high_prob_pct=gfs_high_prob_pct,
                                peak_hour=hourly_features.get("peak_hour"),
                                afternoon_storm_flag=hourly_features.get("afternoon_storm_flag"),
                                pre_peak_storm_flag=hourly_features.get("pre_peak_storm_flag"),
                                cloud_timing_delta=hourly_features.get("cloud_timing_delta"),
                                sea_breeze_flag=hourly_features.get("sea_breeze_flag"),
                            )
                            rows_written += 1
                        except Exception as e:
                            logger.error(
                                f"{station_code} {target_date} {c['market_ticker']}: "
                                f"gold write failed: {e}"
                            )

    logger.info(
        f"=== edge calc complete: {rows_written} rows written "
        f"(dry_run={dry_run}, bankroll=${bankroll:.0f}) ==="
    )
    return rows_written


def main() -> int:
    dry_run_env = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")
    parser = argparse.ArgumentParser(
        description="BHN Strat 9 — gold layer edge calculator"
    )
    parser.add_argument("--dry-run", action="store_true", default=dry_run_env,
                        help="Compute and log but do not write to DB")
    parser.add_argument("--city", choices=list(ACTIVE_STATIONS),
                        help="Restrict to one station (default: all active)")
    parser.add_argument("--days-ahead", type=int, default=1,
                        help="How many days ahead to compute (default 1)")
    args = parser.parse_args()

    stations = [args.city] if args.city else None
    run_edge_calc(
        stations=stations,
        days_ahead=args.days_ahead,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
