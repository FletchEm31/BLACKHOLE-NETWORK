#!/usr/bin/env python3
"""
weather_data_collector.py — BHN Strategy 9 (BHN-PREDICTION-ALPHA) Phase 1 collector.

Polls free weather data sources every 6 hours (via bhn-weather-collector.timer)
and writes to the weather-schema tables.

Model hierarchy (post-research-findings update, 2026-05-13):
  PRIMARY      NWS gridpoints API  — Kalshi settles weather contracts on
                                     NWS Daily Climate Report (CLI). Same
                                     office's gridpoints forecast is the
                                     authoritative model input.
  SECONDARY    ECMWF 51-member     — Ensemble model via the ecmwf-opendata
               ensemble             package (ECMWF went fully open Oct 2025,
                                     CC-BY 4.0). Provides probabilistic
                                     uncertainty quantification.
  CONFIRMATION HRRR                — High-Resolution Rapid Refresh (3km,
                                     hourly updates). Short-range
                                     (0-48h) cross-check before contract
                                     settlement.
  REDUNDANCY   Open-Meteo          — Free aggregator; multi-model output
                                     kept as a sanity cross-check against
                                     NWS in case NWS endpoint fails.

  Source                  → Table(s)                     Status
  ──────────────────────────────────────────────────────────────────────
  NWS gridpoints forecast → weather_forecasts            ✅ implemented (primary)
  NWS CLI climate report  → weather_observations         ✅ implemented (settlement)
  ECMWF open-data         → weather_forecasts (51-mem)   ⚠  scaffold (GRIB parsing)
  HRRR                    → weather_forecasts            ⚠  scaffold (GRIB parsing)
  Open-Meteo API          → weather_forecasts            ✅ implemented (redundancy)
  Iowa State ASOS         → weather_observations         ✅ implemented
  NOAA CPC ENSO           → enso_index                   ✅ implemented
  USDA NASS crops         → crop_conditions              ⚠  scaffold (needs key)
  Kalshi weather markets  → prediction_contracts         ✅ implemented
                          → weather_contract_prices      ✅ implemented

Cities — 6 (original 4 + Phoenix and Denver added for Phase 3).
Phase 3 trading scope: Miami, Phoenix, Denver (High + Low on Kalshi).
NWS office mapping per operator:
  NYC      → NWS office OKX, ASOS station KNYC
  Chicago  → NWS office LOT, ASOS station KORD
  Miami    → NWS office MFL, ASOS station KMIA
  Austin   → NWS office EWX, ASOS station KAUS
  Phoenix  → NWS office PSR, ASOS station KPHX
  Denver   → NWS office BOU, ASOS station KDEN

After fetch, computes degree_days from the day's observations + tmax/tmin
forecasts for each station.

All data is paper / informational at Phase 1. No betting, no execution,
no rules.json registration — that comes in Phase 3+.

Cross-platform arb deferred to Phase 3 — same event has resolved differently
on Kalshi vs Polymarket in 2024, so it's NOT zero-risk. Polymarket US access
is invite-only + legally uncertain; Phase 1 is Kalshi-only.

Env config (no API keys needed for the implemented sources at Phase 1):
  PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PASSWORD  (via trading_core._load_env)
  USDA_NASS_API_KEY                              (scaffold source)
  KALSHI_KEY_ID                                  (Phase 3 betting — not yet used)

Phase 3 Kalshi setup (NOT in Phase 1, documenting the contract):
  - Kalshi auth = RSA-PSS, NOT simple API key
  - Generate RSA key pair; store private key at /etc/bhn-trading/kalshi_private.pem (0600)
  - Store key id in /etc/bhn-trading/strat9.env as KALSHI_KEY_ID=<id>
  - SDK: pip install kalshi-python==2.1.4
  - Demo environment: demo-api.kalshi.co (use for all paper trading)
  - Production: trading-api.kalshi.com

Phase 2 economics markets pipeline (NOT in this file — separate collector
when it lands):
  - FRED API (free key at fred.stlouisfed.org) — GDPNow, treasury yields
  - BLS API (free key)                          — CPI, NFP, unemployment
  - Cleveland Fed scraping                      — daily inflation nowcast
  - pyfedwatch package                          — Fed decision probabilities from SOFR futures

Dependencies (one-time on LA when promoting scaffolds to full):
  pip install ecmwf-opendata    # ECMWF 51-member open-data client
  pip install cfgrib eccodes    # GRIB2 parsing (used by both ECMWF and HRRR)
  pip install herbie-data       # HRRR retrieval wrapper

CLI:
  python3 weather_data_collector.py              # full cycle (all sources)
  python3 weather_data_collector.py --source nws
  python3 weather_data_collector.py --source open_meteo
  python3 weather_data_collector.py --source asos
  python3 weather_data_collector.py --source enso
  python3 weather_data_collector.py --source kalshi_markets
  python3 weather_data_collector.py --source degree_days
  python3 weather_data_collector.py --dry-run    # log only, no PG writes
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, date, timedelta, timezone
from typing import Any, Iterable, Optional

import re

import requests

import trading_core as tc


logger = tc.get_logger("strat_9_weather_alpha_collector")


# ─────────────────────────────────────────────────────────────────────────
# 10 Phase-1 target cities (Kalshi-aligned ICAO codes)
# ─────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class City:
    icao:         str       # ASOS station code (e.g. 'KNYC')
    nws_office:   str       # NWS WFO office code (e.g. 'OKX')
    name:         str
    state:        str
    lat:          float
    lon:          float
    asos_network: str       # Iowa State ASOS network code (e.g. 'NY_ASOS')


# Kalshi-aligned 4-city set with explicit NWS office mapping per operator
# (research findings: Kalshi weather contracts only cover these 4 markets,
# and they settle on the matching NWS office's Daily Climate Report).
CITIES: tuple[City, ...] = (
    City("KNYC", "OKX", "New York City",   "NY", 40.7831,  -73.9712, "NY_ASOS"),  # Central Park
    City("KORD", "LOT", "Chicago O'Hare",  "IL", 41.9742,  -87.9073, "IL_ASOS"),
    City("KMIA", "MFL", "Miami",           "FL", 25.7959,  -80.2870, "FL_ASOS"),
    City("KAUS", "EWX", "Austin",          "TX", 30.1944,  -97.6700, "TX_ASOS"),  # Austin-Bergstrom
    City("KPHX", "PSR", "Phoenix",         "AZ", 33.4373, -112.0078, "AZ_ASOS"),  # Phoenix Sky Harbor
    City("KDEN", "BOU", "Denver",          "CO", 39.8561, -104.6737, "CO_ASOS"),  # Denver Intl
    City("KLAX", "LOX", "Los Angeles",    "CA", 33.9425, -118.4081, "CA_ASOS"),  # LAX
    City("KDFW", "FWD", "Dallas/Fort Worth", "TX", 32.8998,  -97.0403, "TX_ASOS"),  # DFW
)

VARIABLES = ("tmax_f", "tmin_f", "precip_in", "snow_in")


def _season_for(d: date) -> str:
    """Northern hemisphere meteorological seasons. Matches NOAA convention."""
    m = d.month
    if m in (12, 1, 2):
        return "winter"
    if m in (3, 4, 5):
        return "spring"
    if m in (6, 7, 8):
        return "summer"
    return "fall"


def _http_get_json(url: str, params: Optional[dict] = None, timeout: int = 30,
                    attempts: int = 3) -> Optional[Any]:
    """GET with retries + 429 backoff. Returns parsed JSON or None."""
    for attempt in range(attempts):
        try:
            resp = requests.get(url, params=params, timeout=timeout,
                                headers={"User-Agent": "BHN-Weather-Collector/1.0 "
                                         "(operator@eventhorizonvpn.com)"})
            if resp.status_code == 429:
                wait = 2 ** attempt + 5
                logger.warning(f"429 rate-limit on {url}; sleeping {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.warning(f"fetch attempt {attempt+1}/{attempts} failed: {url} — {e}")
            time.sleep(2 ** attempt)
    logger.error(f"GET failed after {attempts} attempts: {url}")
    return None


def _http_get_text(url: str, params: Optional[dict] = None,
                    timeout: int = 30, attempts: int = 3) -> Optional[str]:
    """GET with retries returning text body (for CSV / TSV / plain text endpoints)."""
    for attempt in range(attempts):
        try:
            resp = requests.get(url, params=params, timeout=timeout,
                                headers={"User-Agent": "BHN-Weather-Collector/1.0 "
                                         "(operator@eventhorizonvpn.com)"})
            if resp.status_code == 429:
                wait = 2 ** attempt + 5
                logger.warning(f"429 rate-limit on {url}; sleeping {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            logger.warning(f"fetch attempt {attempt+1}/{attempts} failed: {url} — {e}")
            time.sleep(2 ** attempt)
    logger.error(f"GET failed after {attempts} attempts: {url}")
    return None


# ─────────────────────────────────────────────────────────────────────────
# Source 1: Open-Meteo API (GFS + ECMWF, free, no key needed)
# ─────────────────────────────────────────────────────────────────────────

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
# Open-Meteo "models" parameter — comma-separated. We pull both GFS and ECMWF.
OPEN_METEO_MODELS = ("gfs_seamless", "ecmwf_ifs04")
OPEN_METEO_VARS_DAILY = (
    "temperature_2m_max", "temperature_2m_min",
    "precipitation_sum",   "snowfall_sum",
)
# Lead times we tag forecasts with (hours from forecast-issue time, approximate)
LEAD_TIME_DAYS = (0, 1, 2, 3, 5, 7, 10, 14)


def _c_to_f(c: Optional[float]) -> Optional[float]:
    return None if c is None else (c * 9.0 / 5.0 + 32.0)


def _mm_to_in(mm: Optional[float]) -> Optional[float]:
    return None if mm is None else (mm / 25.4)


def _cm_to_in(cm: Optional[float]) -> Optional[float]:
    return None if cm is None else (cm / 2.54)


def fetch_open_meteo(dry_run: bool = False) -> int:
    """For each city, pull GFS + ECMWF daily forecasts up to 16 days out.
    Writes one weather_forecasts row per (city, variable, lead_day, model).
    Returns total rows inserted."""
    rows_inserted = 0
    for city in CITIES:
        params = {
            "latitude":  city.lat,
            "longitude": city.lon,
            "daily":     ",".join(OPEN_METEO_VARS_DAILY),
            "models":    ",".join(OPEN_METEO_MODELS),
            "temperature_unit": "fahrenheit",    # request F directly
            "precipitation_unit": "mm",
            "timezone": "America/New_York",
            "forecast_days": 16,
        }
        data = _http_get_json(OPEN_METEO_URL, params=params)
        if not data or "daily" not in data:
            logger.warning(f"{city.icao}: no Open-Meteo daily data")
            continue

        # Open-Meteo returns aligned arrays per model. With multi-model the
        # response keys get suffixed: e.g. "temperature_2m_max_gfs_seamless".
        daily = data["daily"]
        time_array = daily.get("time") or []
        if not time_array:
            continue

        # Each requested model is suffixed in the response keys
        for model_key in OPEN_METEO_MODELS:
            # variable_name + "_" + model_key — but Open-Meteo strips
            # underscores oddly; try the suffixed key first, then bare.
            for idx, day_str in enumerate(time_array):
                try:
                    target_date = date.fromisoformat(day_str)
                except ValueError:
                    continue
                lead_days = (target_date - datetime.now(timezone.utc).date()).days
                if lead_days < 0:
                    continue
                if lead_days not in LEAD_TIME_DAYS:
                    continue
                lead_hours = lead_days * 24

                def _val(var_base: str) -> Optional[float]:
                    """Pull arr[idx] for var with model-suffix, fall back to bare."""
                    for k in (f"{var_base}_{model_key}", var_base):
                        if k in daily and isinstance(daily[k], list) and idx < len(daily[k]):
                            return daily[k][idx]
                    return None

                tmax_f_raw = _val("temperature_2m_max")
                tmin_f_raw = _val("temperature_2m_min")
                prcp_mm = _val("precipitation_sum")
                snow_cm = _val("snowfall_sum")

                samples = (
                    ("tmax_f",    tmax_f_raw),
                    ("tmin_f",    tmin_f_raw),
                    ("precip_in", _mm_to_in(prcp_mm)),
                    ("snow_in",   _cm_to_in(snow_cm)),
                )
                for var, value in samples:
                    if value is None:
                        continue
                    if not dry_run:
                        _insert_forecast(
                            station_code=city.icao,
                            variable=var,
                            value=float(value),
                            target_date=target_date,
                            lead_time_hours=lead_hours,
                            source_model=f"open-meteo:{model_key}",
                            season=_season_for(target_date),
                            raw_payload=None,  # full payload not stored per-row
                        )
                    rows_inserted += 1
    logger.info(f"open_meteo: {rows_inserted} forecast rows {'(dry-run)' if dry_run else 'inserted'}")
    return rows_inserted


# ─────────────────────────────────────────────────────────────────────────
# Source 2: Iowa State ASOS — daily observations
# ─────────────────────────────────────────────────────────────────────────

ASOS_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/daily.py"


def fetch_asos(days_lookback: int = 3, dry_run: bool = False) -> int:
    """Pull daily ASOS observations for each city, last `days_lookback` days.
    Default 3 days catches yesterday + recent re-runs idempotently (UNIQUE
    constraint dedups). Returns rows inserted."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days_lookback)

    rows_inserted = 0
    for city in CITIES:
        # Strip leading K — ASOS uses 3-letter codes ('NYC', 'LAX', etc.) for
        # most stations; Iowa State accepts the K-prefixed form too.
        station = city.icao[1:] if city.icao.startswith("K") else city.icao
        params = {
            "network": city.asos_network,
            "stations": station,
            "year1": start.year, "month1": start.month, "day1": start.day,
            "year2": end.year,   "month2": end.month,   "day2": end.day,
            "var": "max_temp_f,min_temp_f,precip_in,snow_in",
            "format": "comma",
            "missing": "M",
        }
        csv_text = _http_get_text(ASOS_URL, params=params)
        if not csv_text:
            logger.warning(f"{city.icao}: no ASOS data")
            continue

        # CSV header: station,day,max_temp_f,min_temp_f,precip_in,snow_in
        lines = [ln for ln in csv_text.splitlines() if ln and not ln.startswith("#")]
        if len(lines) < 2:
            continue
        header = lines[0].split(",")
        try:
            col_day  = header.index("day")
            col_tmax = header.index("max_temp_f") if "max_temp_f" in header else None
            col_tmin = header.index("min_temp_f") if "min_temp_f" in header else None
            col_pcp  = header.index("precip_in")  if "precip_in"  in header else None
            col_snow = header.index("snow_in")    if "snow_in"    in header else None
        except ValueError:
            logger.warning(f"{city.icao}: ASOS header unrecognized: {header}")
            continue

        for line in lines[1:]:
            parts = line.split(",")
            if len(parts) < len(header):
                continue
            try:
                obs_date = date.fromisoformat(parts[col_day].strip())
            except (ValueError, IndexError):
                continue

            def _parse(idx: Optional[int]) -> Optional[float]:
                if idx is None:
                    return None
                raw = parts[idx].strip() if idx < len(parts) else "M"
                if raw in ("M", "", "None"):
                    return None
                try:
                    return float(raw)
                except ValueError:
                    return None

            samples = (
                ("tmax_f",    _parse(col_tmax)),
                ("tmin_f",    _parse(col_tmin)),
                ("precip_in", _parse(col_pcp)),
                ("snow_in",   _parse(col_snow)),
            )
            # ASOS reports a single daily summary — bin observation time at
            # 23:59 UTC of the report day for the timestamp.
            obs_ts = datetime.combine(obs_date, datetime.min.time(),
                                       tzinfo=timezone.utc) + timedelta(hours=23, minutes=59)
            for var, val in samples:
                if val is None:
                    continue
                if not dry_run:
                    _insert_observation(
                        station_code=city.icao, variable=var,
                        observed_value=float(val), observed_at=obs_ts,
                        source="asos", raw_payload=None,
                    )
                rows_inserted += 1
    logger.info(f"asos: {rows_inserted} observation rows {'(dry-run)' if dry_run else 'upserted'}")
    return rows_inserted


# ─────────────────────────────────────────────────────────────────────────
# Source 3: NOAA CPC ENSO (Niño 3.4 weekly index)
# ─────────────────────────────────────────────────────────────────────────

NOAA_CPC_NINO_URL = "https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for"


def _enso_phase(oni: Optional[float]) -> Optional[str]:
    """Threshold mapping. NOAA convention: ONI ≥ 0.5 = El Niño, ≤ -0.5 = La Niña.
    Strong: |ONI| ≥ 1.5."""
    if oni is None:
        return None
    if oni >= 1.5:
        return "el_nino_strong"
    if oni >= 0.5:
        return "el_nino"
    if oni <= -1.5:
        return "la_nina_strong"
    if oni <= -0.5:
        return "la_nina"
    return "neutral"


def fetch_enso(dry_run: bool = False) -> int:
    """Parse CPC's fixed-width weekly text file. One row per week_ending.
    File format:
        Week         Nino1+2      Nino3        Nino34        Nino4
                  SST   SSTA    SST  SSTA    SST  SSTA    SST  SSTA
        02JAN1990  23.4  -0.4  25.4  -0.3  26.4  -0.2  28.7  -0.4
        ...
    We extract week-ending date + Niño 3.4 SST anomaly (column index 5).
    """
    text = _http_get_text(NOAA_CPC_NINO_URL)
    if not text:
        logger.warning("ENSO: no CPC data")
        return 0

    rows_inserted = 0
    for line in text.splitlines():
        # NOAA file has leading spaces on every data line — strip before any checks.
        line = line.strip()
        if not line or line.lower().startswith("week") or line.startswith("#"):
            continue
        if len(line) < 20:
            continue
        # First token is the date in DDMMMYYYY format, e.g. "02SEP1981"
        date_token = line[:9]
        try:
            week_ending = datetime.strptime(date_token, "%d%b%Y").date()
        except ValueError:
            continue
        # Values are packed without whitespace between SST and SSTA
        # (e.g. "20.6-0.1") — regex extracts all signed floats.
        floats = [float(m) for m in re.findall(r"-?\d+\.\d+", line[9:])]
        # Expect 8 floats (4 regions × {SST, SSTA}). Niño 3.4 SSTA = index 5.
        if len(floats) < 6:
            continue
        nino34_anom = floats[5]
        # ONI is a 3-month rolling mean of Niño 3.4 SSTA — we don't compute it
        # here; we record the weekly anomaly and let the Phase 2 calibration
        # job derive ONI from rolling windows. Use the weekly anomaly as a
        # proxy for the phase signal in the meantime.
        phase = _enso_phase(nino34_anom)

        if not dry_run:
            _insert_enso(
                week_ending=week_ending,
                nino34_sst_anomaly=nino34_anom,
                oni_value=None,  # populated by Phase 2 rolling-mean job
                phase=phase,
            )
        rows_inserted += 1
    logger.info(f"enso: {rows_inserted} weekly rows {'(dry-run)' if dry_run else 'upserted'}")
    return rows_inserted


# ─────────────────────────────────────────────────────────────────────────
# Source 4: USDA NASS crop conditions — scaffold
# ─────────────────────────────────────────────────────────────────────────

USDA_NASS_API_URL = "https://quickstats.nass.usda.gov/api/api_GET/"


def fetch_usda_crops(dry_run: bool = False) -> int:
    """Pull weekly crop progress + conditions from USDA NASS Quick Stats API.
    Requires USDA_NASS_API_KEY env var (free signup at quickstats.nass.usda.gov).

    SCAFFOLD: query shape known but not yet wired to PG insert. Next session
    fills in:
      params = {
          "key": api_key,
          "source_desc": "SURVEY",
          "sector_desc": "CROPS",
          "group_desc": "FIELD CROPS",
          "commodity_desc": "CORN",   # or SOYBEANS, WHEAT, etc.
          "statisticcat_desc": "CONDITION",
          "year": str(date.today().year),
          "format": "JSON",
      }
    Response yields 5-bucket condition % (VERY POOR / POOR / FAIR / GOOD /
    EXCELLENT) per state per week. Insert one row per category.
    """
    import os
    api_key = os.environ.get("USDA_NASS_API_KEY")
    if not api_key:
        logger.info("usda_crops: USDA_NASS_API_KEY not set — skipping (Phase 1 scaffold)")
        return 0
    logger.info("usda_crops: SCAFFOLD — endpoint reachable but PG insert not yet wired. "
                "Operator: paste actual NASS API query parameters next session.")
    return 0


# ─────────────────────────────────────────────────────────────────────────
# Source 5: NWS gridpoints API — 7-day high/low forecasts
# ─────────────────────────────────────────────────────────────────────────

NWS_API_BASE = "https://api.weather.gov"

# Module-level cache: icao → (forecast_url, office, gridX, gridY).
# Gridpoints never change for a given lat/lon — safe to cache for process lifetime.
NWS_GRIDPOINTS: dict[str, tuple[str, str, int, int]] = {}


def _discover_nws_gridpoint(city: "City") -> Optional[tuple[str, str, int, int]]:
    """GET /points/{lat},{lon} and cache the result.
    Returns (forecast_url, office, gridX, gridY) or None on failure."""
    if city.icao in NWS_GRIDPOINTS:
        return NWS_GRIDPOINTS[city.icao]
    url = f"{NWS_API_BASE}/points/{city.lat},{city.lon}"
    data = _http_get_json(url)
    if not data:
        logger.warning(f"{city.icao}: NWS /points returned no data")
        return None
    props = (data.get("properties") or {})
    office = props.get("gridId")
    grid_x = props.get("gridX")
    grid_y = props.get("gridY")
    forecast_url = props.get("forecast")
    if not (office and grid_x is not None and grid_y is not None and forecast_url):
        logger.warning(f"{city.icao}: NWS /points response missing fields: {list(props)}")
        return None
    result: tuple[str, str, int, int] = (forecast_url, office, int(grid_x), int(grid_y))
    NWS_GRIDPOINTS[city.icao] = result
    logger.debug(f"{city.icao}: NWS gridpoint → {office} {grid_x},{grid_y}")
    return result


def fetch_nws(dry_run: bool = False) -> int:
    """Pull 7-day high/low forecasts from NWS gridpoints API for each city.
    Two-step: /points/{lat},{lon} discovery (cached) → /gridpoints/{office}/{x},{y}/forecast.
    NWS is the authoritative source for Kalshi weather contract settlement.
    Writes to weather_forecasts as source_model='nws_gridpoints'.
    Returns rows inserted."""
    today = datetime.now(timezone.utc).date()
    rows_inserted = 0

    for city in CITIES:
        try:
            gridpoint = _discover_nws_gridpoint(city)
        except Exception as e:
            logger.warning(f"{city.icao}: NWS gridpoint discovery error — {e}")
            continue
        if gridpoint is None:
            continue

        forecast_url, office, grid_x, grid_y = gridpoint
        data = _http_get_json(forecast_url)
        if not data:
            logger.warning(f"{city.icao}: NWS forecast fetch failed ({forecast_url})")
            continue

        periods = ((data.get("properties") or {}).get("periods") or [])
        if not periods:
            logger.warning(f"{city.icao}: NWS forecast: empty periods")
            continue

        for period in periods:
            is_daytime: bool = bool(period.get("isDaytime", True))
            temp = period.get("temperature")
            temp_unit: str = period.get("temperatureUnit", "F")
            start_str: str = period.get("startTime", "")

            if temp is None or not start_str:
                continue

            temp_f = float(temp) if temp_unit == "F" else _c_to_f(float(temp))
            if temp_f is None:
                continue

            try:
                target_date = date.fromisoformat(start_str[:10])
            except (ValueError, IndexError):
                logger.warning(f"{city.icao}: NWS bad startTime: {start_str!r}")
                continue

            lead_days = (target_date - today).days
            if lead_days < 0 or lead_days > 7:
                continue

            variable = "tmax_f" if is_daytime else "tmin_f"
            if not dry_run:
                _insert_forecast(
                    station_code=city.icao,
                    variable=variable,
                    value=temp_f,
                    target_date=target_date,
                    lead_time_hours=lead_days * 24,
                    source_model="nws_gridpoints",
                    season=_season_for(target_date),
                    raw_payload=None,
                )
            rows_inserted += 1

    logger.info(f"nws: {rows_inserted} forecast rows {'(dry-run)' if dry_run else 'inserted'}")
    return rows_inserted


# ─────────────────────────────────────────────────────────────────────────
# Source 6: NOAA NOMADS GFS ensemble — scaffold
# ─────────────────────────────────────────────────────────────────────────

def fetch_nomads_gfs_ensemble(dry_run: bool = False) -> int:
    """NOAA NOMADS GFS 50-member ensemble (GEFS). GRIB2 file format — needs
    pygrib or eccodes for parsing. Each cycle is ~50 GRIB files per forecast
    hour, lat/lon grids — expensive to fetch + decode.

    SCAFFOLD: deferred to Phase 1.5. Open-Meteo gives ensemble mean+spread
    via the gfs_seamless model output for now — covers the highest-signal
    use case without the GRIB plumbing.
    """
    logger.info("nomads_gfs_ensemble: SCAFFOLD — GRIB2 parsing not yet implemented. "
                "Open-Meteo seamless model approximates the ensemble mean.")
    return 0


# ─────────────────────────────────────────────────────────────────────────
# Local computation: degree_days from observations
# ─────────────────────────────────────────────────────────────────────────

def compute_degree_days_from_observations(dry_run: bool = False) -> int:
    """For every (station, date) with both tmin_f and tmax_f observations,
    compute HDD + CDD and upsert into degree_days (is_forecast=false)."""
    rows_inserted = 0
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    station_code,
                    observed_at::date AS d,
                    MAX(CASE WHEN variable='tmax_f' THEN observed_value END) AS tmax,
                    MIN(CASE WHEN variable='tmin_f' THEN observed_value END) AS tmin
                FROM weather_observations
                WHERE source = 'asos'
                  AND variable IN ('tmax_f','tmin_f')
                  AND observed_at >= NOW() - INTERVAL '14 days'
                GROUP BY station_code, observed_at::date
                HAVING MAX(CASE WHEN variable='tmax_f' THEN observed_value END) IS NOT NULL
                   AND MIN(CASE WHEN variable='tmin_f' THEN observed_value END) IS NOT NULL
            """)
            rows = cur.fetchall()
    for station, d, tmax, tmin in rows:
        mean_t = (float(tmax) + float(tmin)) / 2.0
        hdd = max(0.0, 65.0 - mean_t)
        cdd = max(0.0, mean_t - 65.0)
        if not dry_run:
            _upsert_degree_days(
                station_code=station, target_date=d,
                tmin_f=float(tmin), tmax_f=float(tmax),
                mean_temp_f=mean_t, hdd=hdd, cdd=cdd,
                is_forecast=False,
            )
        rows_inserted += 1
    logger.info(f"degree_days: {rows_inserted} (station, date) rows "
                f"{'(dry-run)' if dry_run else 'upserted'}")
    return rows_inserted


# ─────────────────────────────────────────────────────────────────────────
# Source 8: Kalshi market prices
# ─────────────────────────────────────────────────────────────────────────

# Ticker prefix → ICAO station code
_KALSHI_TICKER_STATION: dict[str, str] = {
    "KXHIGHMIA": "KMIA", "KXLOWMIA":  "KMIA",
    "KXHIGHDEN": "KDEN", "KXLOWDEN":  "KDEN",
    "KXHIGHPHX": "KPHX", "KXLOWPHX":  "KPHX",
    "KXHIGHNY":  "KNYC", "KXLOWNY":   "KNYC",
    "KXHIGHCHI": "KORD", "KXLOWCHI":  "KORD",
    "KXHIGHLAX": "KLAX", "KXLOWLAX":  "KLAX",
    "KXHIGHDFW": "KDFW", "KXLOWDFW":  "KDFW",
}


def _station_from_kalshi_ticker(ticker: str) -> Optional[str]:
    for prefix, code in _KALSHI_TICKER_STATION.items():
        if ticker.startswith(prefix):
            return code
    return None


def _variable_from_kalshi_ticker(ticker: str) -> Optional[str]:
    if ticker.startswith("KXHIGH"):
        return "tmax_f"
    if ticker.startswith("KXLOW"):
        return "tmin_f"
    return None


def fetch_kalshi_markets(dry_run: bool = False) -> int:
    """Fetch open Kalshi weather markets and snapshot prices to
    weather_contract_prices. The KalshiClient.get_weather_markets() call
    also auto-UPSERTs contracts into prediction_contracts.
    Returns rows inserted to weather_contract_prices."""
    try:
        from kalshi_client import KalshiClient  # co-located on LA
    except ImportError:
        logger.warning("kalshi_markets: kalshi_client not importable — skipping")
        return 0

    try:
        client = KalshiClient()
    except Exception as e:
        logger.warning(f"kalshi_markets: KalshiClient init failed: {e}")
        return 0

    try:
        markets = client.get_weather_markets(status="open")
    except Exception as e:
        logger.warning(f"kalshi_markets: get_weather_markets failed: {e}")
        return 0

    if not markets:
        logger.info("kalshi_markets: no open weather markets returned")
        return 0

    rows_inserted = 0
    for market in markets:
        ticker = market.get("ticker")
        if not ticker:
            continue

        title = market.get("title") or market.get("subtitle") or ""
        close_str = market.get("close_time") or market.get("expiration_time")
        resolution_date: Optional[date] = None
        if close_str:
            try:
                resolution_date = date.fromisoformat(close_str[:10])
            except (ValueError, TypeError):
                pass

        # Try price fields from the market object (integer cents), then
        # fall back to an orderbook fetch if both are absent.
        yes_cents = market.get("yes_ask") or market.get("yes_bid")
        no_cents  = market.get("no_ask")  or market.get("no_bid")
        yes_price: Optional[float] = float(yes_cents) / 100.0 if yes_cents else None
        no_price:  Optional[float] = float(no_cents)  / 100.0 if no_cents  else None

        if yes_price is None and no_price is None:
            try:
                book = client.get_orderbook(ticker)
                # Kalshi v2 returns 'orderbook_fp' with fractional-dollar price
                # strings (e.g. "0.0100" = $0.01 = 1% probability, already 0-1).
                # Older 'orderbook' key had integer cents — keep fallback for safety.
                ob = ((book or {}).get("orderbook_fp")
                      or (book or {}).get("orderbook") or {})
                yes_side = ob.get("yes_dollars") or ob.get("yes") or []
                no_side  = ob.get("no_dollars")  or ob.get("no")  or []
                if yes_side and isinstance(yes_side[0], (list, tuple)):
                    raw = float(yes_side[0][0])
                    # If > 1 it's legacy integer-cent format; divide by 100.
                    yes_price = raw if raw <= 1.0 else raw / 100.0
                if no_side and isinstance(no_side[0], (list, tuple)):
                    raw = float(no_side[0][0])
                    no_price = raw if raw <= 1.0 else raw / 100.0
            except Exception as e:
                logger.debug(f"kalshi_markets: orderbook fetch failed for {ticker}: {e}")

        if yes_price is not None:
            implied_prob = yes_price
        elif no_price is not None:
            implied_prob = 1.0 - no_price
        else:
            logger.debug(f"kalshi_markets: no price data for {ticker} — skipping")
            continue

        volume_raw = market.get("volume_24h") or market.get("volume")
        open_int_raw = market.get("open_interest")
        volume_24h = float(volume_raw) if volume_raw is not None else None
        open_interest = float(open_int_raw) if open_int_raw is not None else None

        if not dry_run:
            _insert_contract_price(
                exchange="kalshi",
                contract_id=ticker,
                contract_title=title,
                implied_probability=implied_prob,
                yes_price=yes_price,
                no_price=no_price,
                volume_24h=volume_24h,
                open_interest=open_interest,
                resolution_date=resolution_date,
                region=_station_from_kalshi_ticker(ticker),
                variable=_variable_from_kalshi_ticker(ticker),
                raw_payload=market,
            )
        rows_inserted += 1

    logger.info(
        f"kalshi_markets: {rows_inserted} price snapshots "
        f"{'(dry-run)' if dry_run else 'inserted'}"
    )
    return rows_inserted


# ─────────────────────────────────────────────────────────────────────────
# PG insert helpers
# ─────────────────────────────────────────────────────────────────────────

def _insert_forecast(*, station_code: str, variable: str, value: float,
                      target_date: date, lead_time_hours: int,
                      source_model: str, season: str,
                      raw_payload: Optional[dict]) -> None:
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO weather_forecasts
                    (region, variable, predicted_value, source_model,
                     station_code, lead_time_hours, season, target_date,
                     raw_payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """, (station_code, variable, value, source_model,
                  station_code, lead_time_hours, season, target_date,
                  json.dumps(raw_payload) if raw_payload else None))


def _insert_observation(*, station_code: str, variable: str,
                         observed_value: float, observed_at: datetime,
                         source: str, raw_payload: Optional[dict]) -> None:
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO weather_observations
                    (station_code, variable, observed_value, observed_at,
                     source, raw_payload)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (station_code, observed_at, variable, source)
                DO NOTHING
            """, (station_code, variable, observed_value, observed_at, source,
                  json.dumps(raw_payload) if raw_payload else None))


def _insert_enso(*, week_ending: date, nino34_sst_anomaly: float,
                  oni_value: Optional[float], phase: Optional[str]) -> None:
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO enso_index
                    (week_ending, nino34_sst_anomaly, oni_value, phase, source)
                VALUES (%s, %s, %s, %s, 'noaa_cpc')
                ON CONFLICT (week_ending) DO UPDATE
                  SET nino34_sst_anomaly = EXCLUDED.nino34_sst_anomaly,
                      phase              = EXCLUDED.phase,
                      fetched_at         = NOW()
            """, (week_ending, nino34_sst_anomaly, oni_value, phase))


def _insert_contract_price(*, exchange: str, contract_id: str,
                            contract_title: str, implied_probability: float,
                            yes_price: Optional[float], no_price: Optional[float],
                            volume_24h: Optional[float], open_interest: Optional[float],
                            resolution_date: Optional[date], region: Optional[str],
                            variable: Optional[str], raw_payload: Optional[dict]) -> None:
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO weather_contract_prices
                    (exchange, contract_id, contract_title, implied_probability,
                     yes_price, no_price, volume_24h, open_interest,
                     resolution_date, region, variable, raw_payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """, (exchange, contract_id, contract_title, implied_probability,
                  yes_price, no_price, volume_24h, open_interest,
                  resolution_date, region, variable,
                  json.dumps(raw_payload) if raw_payload else None))


def _upsert_degree_days(*, station_code: str, target_date: date,
                         tmin_f: float, tmax_f: float, mean_temp_f: float,
                         hdd: float, cdd: float, is_forecast: bool) -> None:
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO degree_days
                    (station_code, target_date, tmin_f, tmax_f, mean_temp_f,
                     hdd, cdd, is_forecast)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (station_code, target_date, is_forecast) DO UPDATE
                  SET tmin_f      = EXCLUDED.tmin_f,
                      tmax_f      = EXCLUDED.tmax_f,
                      mean_temp_f = EXCLUDED.mean_temp_f,
                      hdd         = EXCLUDED.hdd,
                      cdd         = EXCLUDED.cdd,
                      computed_at = NOW()
            """, (station_code, target_date, tmin_f, tmax_f, mean_temp_f,
                  hdd, cdd, is_forecast))


# ─────────────────────────────────────────────────────────────────────────
# Main + CLI
# ─────────────────────────────────────────────────────────────────────────

SOURCES = {
    "open_meteo":     fetch_open_meteo,
    "asos":           fetch_asos,
    "enso":           fetch_enso,
    "usda_crops":     fetch_usda_crops,
    "nws":            fetch_nws,
    "kalshi_markets": fetch_kalshi_markets,
    "nomads":         fetch_nomads_gfs_ensemble,
    "degree_days":    compute_degree_days_from_observations,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="BHN Strat 9 weather data collector")
    parser.add_argument("--source", choices=list(SOURCES.keys()) + ["all"],
                        default="all", help="Single source to run; default all")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log only, no PG writes")
    args = parser.parse_args()

    logger.info(f"=== weather-collector cycle start "
                f"(source={args.source}, dry_run={args.dry_run}) ===")

    total = 0
    targets: Iterable[str] = (
        SOURCES.keys() if args.source == "all" else (args.source,)
    )
    for src in targets:
        fn = SOURCES[src]
        try:
            n = fn(dry_run=args.dry_run)
            total += n
        except Exception:
            logger.exception(f"source '{src}' failed")
    logger.info(f"=== weather-collector cycle end (total={total} rows) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
