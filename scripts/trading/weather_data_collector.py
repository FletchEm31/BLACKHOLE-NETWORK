#!/usr/bin/env python3
"""
weather_data_collector.py — BHN Strategy 9 (BHN-PREDICTION-ALPHA) Phase 1 collector.

Polls free weather data sources every 6 hours (via bhn-weather-collector.timer)
and writes to the weather-schema tables.

BSG dual-write layer (added 2026-06-11):
  Every bronze table write is followed inline by a silver population helper.
  Old tables (weather_forecasts, weather_contract_prices, prediction_contracts)
  are still written to in parallel — no backward-compat break.

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

  Source                  → Table(s)                               Status
  ──────────────────────────────────────────────────────────────────────────
  NWS gridpoints forecast → weather_forecasts (legacy)            ✅ running
                          → weather_bronze_nws_forecast_snapshots  ✅ new
                          → weather_silver_forecast_conformed       ✅ new
  NWS CLI climate report  → weather_observations (legacy)         ✅ running
                          → weather_bronze_nws_actuals             ✅ new
                          → weather_silver_actuals_conformed       ✅ new
  Open-Meteo API          → weather_forecasts (legacy)            ✅ running
                          → weather_bronze_openmeteo_*            ✅ new
                          → weather_silver_forecast_conformed      ✅ new
  Kalshi weather markets  → prediction_contracts (legacy)         ✅ running
                          → weather_contract_prices (legacy)       ✅ running
                          → weather_bronze_kalshi_market_snapshots ✅ new
                          → weather_kalshi_contract_catalog        ✅ new
                          → weather_silver_market_conformed        ✅ new
  Iowa State ASOS         → weather_observations                  ✅ running

Cities — 8 (Kalshi-aligned ICAO codes).
NWS office mapping per operator:
  NYC      → NWS office OKX, ASOS station KNYC
  Chicago  → NWS office LOT, ASOS station KORD
  Miami    → NWS office MFL, ASOS station KMIA
  Austin   → NWS office EWX, ASOS station KAUS
  Phoenix  → NWS office PSR, ASOS station KPHX
  Denver   → NWS office BOU, ASOS station KDEN
  LA       → NWS office LOX, ASOS station KLAX
  DFW      → NWS office FWD, ASOS station KDFW

CLI:
  python3 weather_data_collector.py              # full cycle (all sources)
  python3 weather_data_collector.py --source nws
  python3 weather_data_collector.py --source open_meteo
  python3 weather_data_collector.py --source asos
  python3 weather_data_collector.py --source kalshi_markets
  python3 weather_data_collector.py --source nws_actuals
  python3 weather_data_collector.py --dry-run    # log only, no PG writes
"""
from __future__ import annotations

import argparse
import json
import math
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


# Kalshi-aligned 8-city set with explicit NWS office mapping per operator
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
OPEN_METEO_MODELS = ("gfs_seamless", "ecmwf_ifs04")
OPEN_METEO_VARS_DAILY = (
    "temperature_2m_max", "temperature_2m_min",
    "precipitation_sum",   "snowfall_sum",
)
LEAD_TIME_DAYS = (0, 1, 2, 3, 5, 7, 10, 14)

OPEN_METEO_VARS_HOURLY = (
    "temperature_2m", "dewpoint_2m", "relative_humidity_2m",
    "cloud_cover", "precipitation_probability", "precipitation",
    "rain", "snowfall", "wind_speed_10m", "wind_gusts_10m",
    "surface_pressure", "weather_code",
)

# NWS CLI 3-char location codes for fetching Daily Climate Reports
_NWS_CLI_LOCATIONS: dict[str, str] = {
    "KMIA": "MIA", "KDEN": "DEN", "KPHX": "PHX", "KLAX": "LAX",
    "KDFW": "DFW", "KNYC": "NYC", "KORD": "ORD", "KAUS": "AUS",
}

# Default sigma (°F) per station until 30 days of calibration history exists
_SIGMA_DEFAULTS: dict[str, float] = {
    "KMIA": 2.5, "KDEN": 3.5, "KPHX": 2.0,
    "KLAX": 2.0, "KDFW": 3.0, "KNYC": 3.0,
    "KORD": 3.5, "KAUS": 3.0,
}


def _c_to_f(c: Optional[float]) -> Optional[float]:
    return None if c is None else (c * 9.0 / 5.0 + 32.0)


def _mm_to_in(mm: Optional[float]) -> Optional[float]:
    return None if mm is None else (mm / 25.4)


def _cm_to_in(cm: Optional[float]) -> Optional[float]:
    return None if cm is None else (cm / 2.54)


def _kmh_to_mph(kmh: Optional[float]) -> Optional[float]:
    return None if kmh is None else (kmh * 0.621371)


def _parse_kalshi_ticker(ticker: str) -> dict:
    """Extract bucket fields from a Kalshi weather ticker.
    KXHIGHDEN-26JUN11-B78.5 → {contract_side, bucket_type, bucket_floor, bucket_cap, bucket_label}
    KXHIGHMIA-26JUN15-T90 → above/below depending on T threshold direction.
    Returns empty dict if parsing fails.
    """
    parts = ticker.split("-")
    if len(parts) < 3:
        return {}
    series_part = parts[0].upper()
    price_part = parts[2]

    contract_side = "high" if "HIGH" in series_part else ("low" if "LOW" in series_part else None)
    if not contract_side:
        return {}

    try:
        if price_part.upper().startswith("B"):
            mid = float(price_part[1:])
            floor_v = mid - 0.5
            cap_v = mid + 0.5
            label = f"{int(floor_v)}-{int(cap_v)}"
            return {"contract_side": contract_side, "bucket_type": "between",
                    "bucket_floor": floor_v, "bucket_cap": cap_v, "bucket_label": label}
        elif price_part.upper().startswith("T"):
            val = float(price_part[1:])
            # Kalshi uses T for top-cap (above) and bottom-floor (below) buckets.
            # The context (series_ticker structure) disambiguates, but we can't
            # tell here without more data. Store as-is with bucket_type='threshold'
            # — the catalog upsert in kalshi_client.py will have the correct op.
            return {"contract_side": contract_side, "bucket_type": "threshold",
                    "bucket_floor": val, "bucket_cap": val, "bucket_label": f"T{int(val)}"}
    except (ValueError, IndexError):
        pass
    return {}


# ─────────────────────────────────────────────────────────────────────────
# NWS raw gridpoints value parser
# ─────────────────────────────────────────────────────────────────────────

def _parse_nws_gridpoints_property(values: list[dict],
                                    target_dates: set[date]) -> dict[date, list[float]]:
    """Extract per-date value lists from NWS gridpoints property array.
    validTime format: '2026-06-11T06:00:00+00:00/PT1H' (duration suffix ignored).
    """
    result: dict[date, list[float]] = {}
    for item in values:
        vt = item.get("validTime", "")
        val = item.get("value")
        if val is None or not vt:
            continue
        dt_str = vt.split("/")[0]
        try:
            dt = datetime.fromisoformat(dt_str)
            d = dt.date()
        except ValueError:
            continue
        if d not in target_dates:
            continue
        result.setdefault(d, []).append(float(val))
    return result


def _parse_nws_cli_text(text: str) -> tuple[Optional[float], Optional[float]]:
    """Extract MAX TEMP and MIN TEMP from NWS CLI product text body.

    The NWS CLI format has two relevant line shapes:
      '  MAXIMUM         88   1:51 PM  96    1994  89  ...'  ← daily observed (has timestamp)
      '  MAXIMUM         61   NORMAL/AVERAGE ...'            ← normal value (no timestamp)
      '  MAXIMUM TEMPERATURE (F)   89 ...'                   ← normal/record (skip, non-digit)
    Some stations (e.g. KDEN) emit a normals line before the observed line, both starting
    with MAXIMUM. Prefer the observed line (identified by a HH:MM AM/PM timestamp).
    Fall back to the first bare match if no timestamped line exists.
    """
    tmax = tmin = None
    tmax_fallback = tmin_fallback = None
    for raw_line in text.splitlines():
        line = raw_line.strip().upper()
        # Observed line: MAXIMUM  88   1:51 PM ...
        if tmax is None:
            if m := re.match(r'^MAXIMUM\s+(\d+)\s+\d{1,2}:\d{2}\s+[AP]M', line):
                try:
                    tmax = float(m.group(1))
                except ValueError:
                    pass
        if tmin is None:
            if m := re.match(r'^MINIMUM\s+(\d+)\s+\d{1,2}:\d{2}\s+[AP]M', line):
                try:
                    tmin = float(m.group(1))
                except ValueError:
                    pass
        # Fallback: any MAXIMUM/MINIMUM line (normals, records, etc.)
        if tmax_fallback is None:
            if m := re.match(r'^MAXIMUM\s+(\d+)', line):
                try:
                    tmax_fallback = float(m.group(1))
                except ValueError:
                    pass
        if tmin_fallback is None:
            if m := re.match(r'^MINIMUM\s+(\d+)', line):
                try:
                    tmin_fallback = float(m.group(1))
                except ValueError:
                    pass
        if tmax is not None and tmin is not None:
            break
    return (tmax or tmax_fallback, tmin or tmin_fallback)


def fetch_open_meteo(dry_run: bool = False) -> int:
    """For each city, pull GFS + ECMWF daily forecasts up to 16 days out.
    Dual-write: legacy weather_forecasts + new bronze/silver tables.
    Returns total rows inserted."""
    rows_inserted = 0
    now_utc = datetime.now(timezone.utc)
    # Round down to nearest 6h GFS cycle boundary for bronze forecast_run_time
    hr_offset = now_utc.hour % 6
    run_time_6h = now_utc.replace(minute=0, second=0, microsecond=0) - timedelta(hours=hr_offset)

    for city in CITIES:
        params = {
            "latitude":  city.lat,
            "longitude": city.lon,
            "daily":     ",".join(OPEN_METEO_VARS_DAILY),
            "models":    ",".join(OPEN_METEO_MODELS),
            "temperature_unit": "fahrenheit",
            "precipitation_unit": "mm",
            "timezone": "America/New_York",
            "forecast_days": 16,
        }
        data = _http_get_json(OPEN_METEO_URL, params=params)
        if not data or "daily" not in data:
            logger.warning(f"{city.icao}: no Open-Meteo daily data")
            continue

        daily = data["daily"]
        time_array = daily.get("time") or []
        if not time_array:
            continue

        for model_key in OPEN_METEO_MODELS:
            # Accumulate tmax/tmin for this model+city to write one bronze row per date
            for idx, day_str in enumerate(time_array):
                try:
                    target_date = date.fromisoformat(day_str)
                except ValueError:
                    continue
                lead_days = (target_date - now_utc.date()).days
                if lead_days < 0:
                    continue
                if lead_days not in LEAD_TIME_DAYS:
                    continue
                lead_hours = lead_days * 24

                def _val(var_base: str, _idx: int = idx, _mk: str = model_key) -> Optional[float]:
                    for k in (f"{var_base}_{_mk}", var_base):
                        if k in daily and isinstance(daily[k], list) and _idx < len(daily[k]):
                            return daily[k][_idx]
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
                            raw_payload=None,
                        )
                    rows_inserted += 1

                # Bronze + silver write (one row per model/city/date)
                if not dry_run and tmax_f_raw is not None:
                    source_name = f"open_meteo_{model_key}"
                    try:
                        _insert_bronze_openmeteo_snapshot(
                            city=city.name, station_code=city.icao,
                            lat=city.lat, lon=city.lon,
                            model=model_key, forecast_run_time=run_time_6h,
                            target_date=target_date, hour=None,
                            temperature_2m=tmax_f_raw,
                            source_payload_json=None,
                        )
                        _populate_silver_openmeteo_forecast(
                            city=city.name, station_code=city.icao,
                            source_name=source_name,
                            forecast_run_time=run_time_6h,
                            target_date=target_date,
                            lead_hours=lead_hours,
                            tmax_f=tmax_f_raw,
                            tmin_f=tmin_f_raw,
                        )
                    except Exception as e:
                        logger.warning(f"{city.icao}: bronze/silver open_meteo write failed: {e}")

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
        line = line.strip()
        if not line or line.lower().startswith("week") or line.startswith("#"):
            continue
        if len(line) < 20:
            continue
        date_token = line[:9]
        try:
            week_ending = datetime.strptime(date_token, "%d%b%Y").date()
        except ValueError:
            continue
        floats = [float(m) for m in re.findall(r"-?\d+\.\d+", line[9:])]
        if len(floats) < 6:
            continue
        nino34_anom = floats[5]
        phase = _enso_phase(nino34_anom)

        if not dry_run:
            _insert_enso(
                week_ending=week_ending,
                nino34_sst_anomaly=nino34_anom,
                oni_value=None,
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
    SCAFFOLD: query shape known but not yet wired to PG insert."""
    import os
    api_key = os.environ.get("USDA_NASS_API_KEY")
    if not api_key:
        logger.info("usda_crops: USDA_NASS_API_KEY not set — skipping (Phase 1 scaffold)")
        return 0
    logger.info("usda_crops: SCAFFOLD — endpoint reachable but PG insert not yet wired.")
    return 0


# ─────────────────────────────────────────────────────────────────────────
# Source 5: NWS gridpoints API — 7-day high/low forecasts
# ─────────────────────────────────────────────────────────────────────────

NWS_API_BASE = "https://api.weather.gov"

# Module-level cache: icao → (forecast_url, office, gridX, gridY).
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
    Dual-write: legacy weather_forecasts + new bronze/silver tables.
    Also fetches raw gridpoints endpoint for extended fields (dewpoint, RH, wind, etc.).
    Returns rows inserted to weather_forecasts."""
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

        # Accumulate per-date tmax/tmin + weather text
        date_data: dict[date, dict] = {}

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

            # Accumulate for bronze write
            if target_date not in date_data:
                date_data[target_date] = {"lead_hours": lead_days * 24}
            if is_daytime:
                date_data[target_date]["tmax_f"] = temp_f
                # Capture weather text from daytime period
                date_data[target_date]["weather_text"] = (
                    period.get("detailedForecast") or period.get("shortForecast") or ""
                )
            else:
                date_data[target_date]["tmin_f"] = temp_f

        if not date_data or dry_run:
            if not date_data:
                continue
            else:
                logger.info(f"nws: {city.icao} dry-run; {len(date_data)} dates accumulated")
                continue

        # Fetch raw gridpoints for extended fields (optional; failure doesn't block bronze write)
        raw_gridpoints: Optional[dict] = None
        try:
            raw_url = f"{NWS_API_BASE}/gridpoints/{office}/{grid_x},{grid_y}"
            raw_gridpoints = _http_get_json(raw_url, timeout=20)
        except Exception as e:
            logger.debug(f"{city.icao}: raw gridpoints fetch skipped: {e}")

        # Parse extended fields if available
        if raw_gridpoints:
            gp_props = raw_gridpoints.get("properties") or {}
            target_set = set(date_data.keys())

            def _gp_max(prop: str, convert=None) -> dict[date, Optional[float]]:
                vals = _parse_nws_gridpoints_property(
                    gp_props.get(prop, {}).get("values", []), target_set
                )
                out: dict[date, Optional[float]] = {}
                for d, lst in vals.items():
                    v = max(lst) if lst else None
                    out[d] = convert(v) if (v is not None and convert) else v
                return out

            def _gp_avg(prop: str, convert=None) -> dict[date, Optional[float]]:
                vals = _parse_nws_gridpoints_property(
                    gp_props.get(prop, {}).get("values", []), target_set
                )
                out: dict[date, Optional[float]] = {}
                for d, lst in vals.items():
                    v = sum(lst) / len(lst) if lst else None
                    out[d] = convert(v) if (v is not None and convert) else v
                return out

            tmax_gp = _gp_max("temperature", _c_to_f)
            tmin_gp: dict[date, Optional[float]] = {}
            temp_all = _parse_nws_gridpoints_property(
                gp_props.get("temperature", {}).get("values", []), target_set
            )
            for d, lst in temp_all.items():
                tmin_gp[d] = _c_to_f(min(lst)) if lst else None

            dewpoint_gp = _gp_avg("dewpoint", _c_to_f)
            rh_gp = _gp_avg("relativeHumidity")
            wind_speed_gp = _gp_avg("windSpeed", _kmh_to_mph)
            wind_gust_gp = _gp_max("windGust", _kmh_to_mph)
            cloud_cover_gp = _gp_avg("skyCover")
            pop_gp = _gp_max("probabilityOfPrecipitation")

            for d, dd in date_data.items():
                dd.setdefault("tmax_f", tmax_gp.get(d))
                dd.setdefault("tmin_f", tmin_gp.get(d))
                dd["dewpoint_f"] = dewpoint_gp.get(d)
                dd["rh_pct"] = rh_gp.get(d)
                dd["wind_speed_mph"] = wind_speed_gp.get(d)
                dd["wind_gust_mph"] = wind_gust_gp.get(d)
                dd["cloud_cover_pct"] = cloud_cover_gp.get(d)
                dd["pop_pct"] = pop_gp.get(d)

        run_time_nws = datetime.now(timezone.utc)

        # Write bronze row + silver per date
        for target_date, dd in date_data.items():
            lead_hours = dd.get("lead_hours", 0)
            try:
                _insert_bronze_nws_forecast(
                    city=city.name,
                    station_code=city.icao,
                    nws_office=office,
                    forecast_run_time=run_time_nws,
                    target_date=target_date,
                    lead_hours=lead_hours,
                    tmax_f=dd.get("tmax_f"),
                    tmin_f=dd.get("tmin_f"),
                    dewpoint_f=dd.get("dewpoint_f"),
                    rh_pct=dd.get("rh_pct"),
                    wind_speed_mph=dd.get("wind_speed_mph"),
                    wind_gust_mph=dd.get("wind_gust_mph"),
                    cloud_cover_pct=dd.get("cloud_cover_pct"),
                    pop_pct=dd.get("pop_pct"),
                    weather_text=dd.get("weather_text"),
                    source_payload_json=None,
                )
                _populate_silver_nws_forecast(
                    city=city.name,
                    station_code=city.icao,
                    forecast_run_time=run_time_nws,
                    target_date=target_date,
                    lead_hours=lead_hours,
                    tmax_f=dd.get("tmax_f"),
                    tmin_f=dd.get("tmin_f"),
                    dewpoint_f=dd.get("dewpoint_f"),
                    rh_pct=dd.get("rh_pct"),
                    wind_speed_mph=dd.get("wind_speed_mph"),
                    wind_gust_mph=dd.get("wind_gust_mph"),
                    cloud_cover_pct=dd.get("cloud_cover_pct"),
                    pop_pct=dd.get("pop_pct"),
                )
            except Exception as e:
                logger.warning(f"{city.icao}/{target_date}: bronze/silver NWS write failed: {e}")

    logger.info(f"nws: {rows_inserted} forecast rows {'(dry-run)' if dry_run else 'inserted'}")
    return rows_inserted


# ─────────────────────────────────────────────────────────────────────────
# Source 6: NOAA NOMADS GFS ensemble — scaffold
# ─────────────────────────────────────────────────────────────────────────

def fetch_nomads_gfs_ensemble(dry_run: bool = False) -> int:
    """SCAFFOLD: deferred. Open-Meteo seamless approximates ensemble mean."""
    logger.info("nomads_gfs_ensemble: SCAFFOLD — GRIB2 parsing not yet implemented.")
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

# ICAO → human-readable city name (matches weather_bronze_nws_actuals.city)
_STATION_CITY_NAME: dict[str, str] = {c.icao: c.name for c in CITIES}

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
    """Fetch open Kalshi weather markets.
    Dual-write: legacy tables + new bronze/catalog/silver tables.
    Returns rows inserted to weather_contract_prices."""
    try:
        from kalshi_client import KalshiClient
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
    snapshot_time = datetime.now(timezone.utc)

    def _cents_to_frac(v: Any) -> Optional[float]:
        """Convert Kalshi integer-cent price (1–99) or fractional (0–1) to fractional."""
        if v is None:
            return None
        f = float(v)
        return f / 100.0 if f > 1.0 else f

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

        yes_cents = market.get("yes_ask") or market.get("yes_bid")
        no_cents  = market.get("no_ask")  or market.get("no_bid")
        yes_price: Optional[float] = float(yes_cents) / 100.0 if yes_cents else None
        no_price:  Optional[float] = float(no_cents)  / 100.0 if no_cents  else None

        yes_bid_val: Optional[float] = _cents_to_frac(market.get("yes_bid"))
        yes_ask_val: Optional[float] = _cents_to_frac(market.get("yes_ask"))
        no_bid_val:  Optional[float] = _cents_to_frac(market.get("no_bid"))
        no_ask_val:  Optional[float] = _cents_to_frac(market.get("no_ask"))

        if yes_price is None and no_price is None:
            try:
                book = client.get_orderbook(ticker)
                ob = ((book or {}).get("orderbook_fp")
                      or (book or {}).get("orderbook") or {})
                yes_side = ob.get("yes_dollars") or ob.get("yes") or []
                no_side  = ob.get("no_dollars")  or ob.get("no")  or []
                ob_yes_ask: Optional[float] = None
                ob_no_ask:  Optional[float] = None
                if yes_side and isinstance(yes_side[0], (list, tuple)):
                    raw = float(yes_side[0][0])
                    ob_yes_ask = raw if raw <= 1.0 else raw / 100.0
                    yes_price = ob_yes_ask
                if no_side and isinstance(no_side[0], (list, tuple)):
                    raw = float(no_side[0][0])
                    ob_no_ask = raw if raw <= 1.0 else raw / 100.0
                    no_price = ob_no_ask
                # Populate bid/ask from orderbook when market snapshot lacks them
                if yes_ask_val is None and ob_yes_ask is not None:
                    yes_ask_val = ob_yes_ask
                if no_ask_val is None and ob_no_ask is not None:
                    no_ask_val = ob_no_ask
                # yes_bid ≈ 1 - best_no_ask (complementary side)
                if yes_bid_val is None and ob_no_ask is not None:
                    yes_bid_val = round(1.0 - ob_no_ask, 4)
                if no_bid_val is None and ob_yes_ask is not None:
                    no_bid_val = round(1.0 - ob_yes_ask, 4)
            except Exception as e:
                logger.debug(f"kalshi_markets: orderbook fetch failed for {ticker}: {e}")

        if yes_price is not None:
            implied_prob = yes_price
        elif no_price is not None:
            implied_prob = 1.0 - no_price
        else:
            logger.debug(f"kalshi_markets: no price data for {ticker} — skipping")
            continue
        yes_mid: Optional[float] = None
        if yes_bid_val is not None and yes_ask_val is not None:
            yes_mid = (yes_bid_val + yes_ask_val) / 2.0
        elif yes_bid_val is not None:
            yes_mid = yes_bid_val
        elif yes_ask_val is not None:
            yes_mid = yes_ask_val
        elif implied_prob is not None:
            yes_mid = implied_prob  # fallback when bid/ask unavailable (e.g. orderbook path)

        _v24 = market.get("volume_24h")
        volume_raw = _v24 if _v24 is not None else market.get("volume")
        open_int_raw = market.get("open_interest")
        volume_24h = float(volume_raw) if volume_raw is not None else None
        open_interest = float(open_int_raw) if open_int_raw is not None else None
        last_price_val: Optional[float] = _cents_to_frac(market.get("last_price"))
        market_status = market.get("status", "open")
        event_ticker = market.get("event_ticker")
        series_ticker = (ticker.split("-")[0] if "-" in ticker else None)

        station_code = _station_from_kalshi_ticker(ticker)
        bucket_info = _parse_kalshi_ticker(ticker)

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
                region=station_code,
                variable=_variable_from_kalshi_ticker(ticker),
                raw_payload=market,
            )

            # Bronze snapshot
            try:
                _insert_bronze_kalshi_snapshot(
                    market_ticker=ticker,
                    event_ticker=event_ticker,
                    series_ticker=series_ticker,
                    station_code=station_code,
                    bucket_info=bucket_info,
                    target_date=resolution_date,
                    yes_bid=yes_bid_val,
                    yes_ask=yes_ask_val,
                    no_bid=no_bid_val,
                    no_ask=no_ask_val,
                    yes_mid=yes_mid,
                    last_price=last_price_val,
                    volume=volume_24h,
                    open_interest=open_interest,
                    market_status=market_status,
                    source_payload_json=market,
                    retrieved_at=snapshot_time,
                )
            except Exception as e:
                logger.warning(f"kalshi_markets: bronze snapshot failed for {ticker}: {e}")

            # Catalog upsert
            try:
                _upsert_kalshi_catalog(
                    market_ticker=ticker,
                    event_ticker=event_ticker,
                    series_ticker=series_ticker,
                    station_code=station_code,
                    bucket_info=bucket_info,
                    target_date=resolution_date,
                    market_status=market_status,
                    source_payload_json=market,
                )
            except Exception as e:
                logger.warning(f"kalshi_markets: catalog upsert failed for {ticker}: {e}")

            # Silver market
            if station_code and resolution_date:
                try:
                    _populate_silver_market(
                        market_ticker=ticker,
                        series_ticker=series_ticker,
                        station_code=station_code,
                        bucket_info=bucket_info,
                        target_date=resolution_date,
                        snapshot_time=snapshot_time,
                        yes_mid=yes_mid,
                        yes_bid=yes_bid_val,
                        yes_ask=yes_ask_val,
                        volume=volume_24h,
                        open_interest=open_interest,
                        market_status=market_status,
                    )
                except Exception as e:
                    logger.warning(f"kalshi_markets: silver market failed for {ticker}: {e}")

        rows_inserted += 1

    logger.info(
        f"kalshi_markets: {rows_inserted} price snapshots "
        f"{'(dry-run)' if dry_run else 'inserted'}"
    )
    return rows_inserted


# ─────────────────────────────────────────────────────────────────────────
# Source 9: NWS CLI Daily Climate Reports — settlement actuals
# ─────────────────────────────────────────────────────────────────────────

NWS_PRODUCTS_URL = "https://api.weather.gov/products"


def fetch_nws_actuals(dry_run: bool = False) -> int:
    """Fetch NWS Daily Climate Reports (CLI) for each city.
    These are the same reports Kalshi uses for weather contract settlement.
    Dual-write: weather_bronze_nws_actuals + weather_silver_actuals_conformed.
    Returns new rows inserted (already-existing rows are skipped via ON CONFLICT DO NOTHING)."""
    rows_inserted = 0
    rows_already_exist = 0
    today = datetime.now(timezone.utc).date()

    for city in CITIES:
        cli_code = _NWS_CLI_LOCATIONS.get(city.icao)
        if not cli_code:
            continue

        try:
            # Get list of recent CLI products for this location
            products_data = _http_get_json(
                NWS_PRODUCTS_URL,
                params={"type": "CLI", "location": cli_code},
            )
            if not products_data:
                logger.warning(f"{city.icao}: no CLI products returned")
                continue

            product_list = products_data.get("@graph") or []
            if not product_list:
                logger.debug(f"{city.icao}: CLI product list empty")
                continue

            # Most recent product is first
            latest_product = product_list[0]
            product_id = latest_product.get("id") or latest_product.get("@id", "").split("/")[-1]
            issuance_time_str = latest_product.get("issuanceTime")
            if not product_id:
                logger.warning(f"{city.icao}: CLI product has no id")
                continue

            # Parse issuance time — CLI is issued the NEXT day for the previous day's data
            if issuance_time_str:
                try:
                    issuance_dt = datetime.fromisoformat(issuance_time_str.replace("Z", "+00:00"))
                    # CLI report covers the day before issuance
                    target_date = (issuance_dt.date() - timedelta(days=1))
                except (ValueError, TypeError):
                    target_date = today - timedelta(days=1)
            else:
                target_date = today - timedelta(days=1)

            # Fetch full product text
            product_data = _http_get_json(f"{NWS_PRODUCTS_URL}/{product_id}")
            if not product_data:
                logger.warning(f"{city.icao}: CLI product fetch failed for id={product_id}")
                continue

            product_text = product_data.get("productText", "")
            if not product_text:
                logger.debug(f"{city.icao}: CLI product text empty")
                continue

            tmax_f, tmin_f = _parse_nws_cli_text(product_text)
            if tmax_f is None and tmin_f is None:
                logger.debug(f"{city.icao}: CLI text parse yielded no temps for {target_date}")
                continue

            report_issued_at: Optional[datetime] = None
            if issuance_time_str:
                try:
                    report_issued_at = datetime.fromisoformat(
                        issuance_time_str.replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    pass

            if not dry_run:
                try:
                    is_new = _insert_bronze_actual(
                        city=city.name,
                        station_code=city.icao,
                        cli_location=cli_code,
                        target_date=target_date,
                        final_tmax_f=tmax_f,
                        final_tmin_f=tmin_f,
                        report_issued_at=report_issued_at,
                        source_payload_json={"product_text": product_text[:2000],
                                              "issuance_time": issuance_time_str},
                    )
                    if not is_new:
                        rows_already_exist += 1
                        logger.debug(
                            f"{city.icao}: CLI actual {target_date} already stored — skipping"
                        )
                        continue
                    # Silver is idempotent (DO UPDATE) — write unconditionally on new bronze
                    _populate_silver_actuals(
                        city=city.name,
                        station_code=city.icao,
                        target_date=target_date,
                        final_tmax_f=tmax_f,
                        final_tmin_f=tmin_f,
                        report_issued_at=report_issued_at,
                    )
                except Exception as e:
                    logger.warning(f"{city.icao}: CLI bronze/silver write failed: {e}")
                    continue

            logger.info(
                f"{city.icao}: CLI actual {target_date} — "
                f"tmax={tmax_f}°F tmin={tmin_f}°F"
            )
            rows_inserted += 1

        except Exception as e:
            logger.warning(f"{city.icao}: fetch_nws_actuals error: {e}")
            continue

    if rows_already_exist:
        logger.debug(f"nws_actuals: {rows_already_exist} cities already have today's CLI (waiting for tomorrow's publish)")
    logger.info(f"nws_actuals: {rows_inserted} new actuals {'(dry-run)' if dry_run else 'inserted'}")
    return rows_inserted


# ─────────────────────────────────────────────────────────────────────────
# PG insert helpers — legacy tables (unchanged)
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
# PG insert helpers — Bronze tables
# ─────────────────────────────────────────────────────────────────────────

def _insert_bronze_nws_forecast(*, city: str, station_code: str, nws_office: str,
                                  forecast_run_time: datetime, target_date: date,
                                  lead_hours: Optional[int],
                                  tmax_f: Optional[float], tmin_f: Optional[float],
                                  dewpoint_f: Optional[float], rh_pct: Optional[float],
                                  wind_speed_mph: Optional[float],
                                  wind_gust_mph: Optional[float],
                                  cloud_cover_pct: Optional[float],
                                  pop_pct: Optional[float],
                                  weather_text: Optional[str],
                                  source_payload_json: Optional[dict]) -> None:
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO weather_bronze_nws_forecast_snapshots
                    (city, station_code, nws_office, forecast_run_time, target_date,
                     lead_hours, tmax_f, tmin_f, dewpoint_f, rh_pct,
                     wind_speed_mph, wind_gust_mph, cloud_cover_pct, pop_pct,
                     weather_text, source_name, source_payload_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'nws_gridpoints', %s::jsonb)
                ON CONFLICT (station_code, forecast_run_time, target_date)
                WHERE source_name = 'nws_gridpoints' DO NOTHING
            """, (city, station_code, nws_office, forecast_run_time, target_date,
                  lead_hours, tmax_f, tmin_f, dewpoint_f, rh_pct,
                  wind_speed_mph, wind_gust_mph, cloud_cover_pct, pop_pct,
                  weather_text,
                  json.dumps(source_payload_json) if source_payload_json else None))


def _insert_bronze_openmeteo_snapshot(*, city: str, station_code: str,
                                        lat: Optional[float], lon: Optional[float],
                                        model: str, forecast_run_time: datetime,
                                        target_date: date, hour: Optional[int],
                                        temperature_2m: Optional[float],
                                        tmax_f: Optional[float] = None,
                                        tmin_f: Optional[float] = None,
                                        ensemble_spread_tmax: Optional[float] = None,
                                        ensemble_spread_tmin: Optional[float] = None,
                                        member_highs_json: Optional[list] = None,
                                        source_payload_json: Optional[dict] = None) -> None:
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO weather_bronze_openmeteo_forecast_snapshots
                    (city, station_code, lat, lon, model,
                     forecast_run_time, target_date, hour, temperature_2m,
                     tmax_f, tmin_f, ensemble_spread_tmax, ensemble_spread_tmin,
                     member_highs_json, source_payload_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                ON CONFLICT (station_code, model, forecast_run_time, target_date, hour) DO NOTHING
            """, (city, station_code, lat, lon, model,
                  forecast_run_time, target_date, hour, temperature_2m,
                  tmax_f, tmin_f, ensemble_spread_tmax, ensemble_spread_tmin,
                  json.dumps(member_highs_json) if member_highs_json is not None else None,
                  json.dumps(source_payload_json) if source_payload_json else None))


def _insert_bronze_kalshi_snapshot(*, market_ticker: str,
                                     event_ticker: Optional[str],
                                     series_ticker: Optional[str],
                                     station_code: Optional[str],
                                     bucket_info: dict,
                                     target_date: Optional[date],
                                     yes_bid: Optional[float],
                                     yes_ask: Optional[float],
                                     no_bid: Optional[float],
                                     no_ask: Optional[float],
                                     yes_mid: Optional[float],
                                     last_price: Optional[float] = None,
                                     volume: Optional[float] = None,
                                     open_interest: Optional[float] = None,
                                     market_status: Optional[str] = None,
                                     source_payload_json: Optional[dict] = None,
                                     retrieved_at: Optional[datetime] = None) -> None:
    city = _STATION_CITY_NAME.get(station_code or "", "")
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO weather_bronze_kalshi_market_snapshots
                    (market_ticker, event_ticker, series_ticker,
                     city, station_code, contract_side, bucket_type,
                     bucket_floor, bucket_cap, bucket_label, target_date,
                     yes_bid, yes_ask, no_bid, no_ask, yes_mid, last_price,
                     volume, open_interest, market_status,
                     source_payload_json, retrieved_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            """, (market_ticker, event_ticker, series_ticker,
                  city, station_code,
                  bucket_info.get("contract_side"),
                  bucket_info.get("bucket_type"),
                  bucket_info.get("bucket_floor"),
                  bucket_info.get("bucket_cap"),
                  bucket_info.get("bucket_label"),
                  target_date,
                  yes_bid, yes_ask, no_bid, no_ask, yes_mid, last_price,
                  volume, open_interest, market_status,
                  json.dumps(source_payload_json) if source_payload_json else None,
                  retrieved_at or datetime.now(timezone.utc)))


def _upsert_kalshi_catalog(*, market_ticker: str, event_ticker: Optional[str],
                             series_ticker: Optional[str], station_code: Optional[str],
                             bucket_info: dict, target_date: Optional[date],
                             market_status: Optional[str],
                             source_payload_json: Optional[dict]) -> None:
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO weather_kalshi_contract_catalog
                    (market_ticker, event_ticker, series_ticker,
                     station_code, contract_side, bucket_type,
                     bucket_floor, bucket_cap, bucket_label,
                     target_date, market_status, is_active,
                     last_seen_at, source_payload_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, NOW(), %s::jsonb)
                ON CONFLICT (market_ticker) DO UPDATE SET
                    market_status  = EXCLUDED.market_status,
                    is_active      = TRUE,
                    last_seen_at   = NOW(),
                    source_payload_json = EXCLUDED.source_payload_json
            """, (market_ticker, event_ticker, series_ticker,
                  station_code,
                  bucket_info.get("contract_side"),
                  bucket_info.get("bucket_type"),
                  bucket_info.get("bucket_floor"),
                  bucket_info.get("bucket_cap"),
                  bucket_info.get("bucket_label"),
                  target_date, market_status,
                  json.dumps(source_payload_json) if source_payload_json else None))


def _insert_bronze_actual(*, city: str, station_code: str,
                            cli_location: Optional[str], target_date: date,
                            final_tmax_f: Optional[float], final_tmin_f: Optional[float],
                            report_issued_at: Optional[datetime],
                            source_payload_json: Optional[dict]) -> bool:
    """Returns True if a new row was inserted (False = conflict / already exists)."""
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO weather_bronze_nws_actuals
                    (city, station_code, cli_location, target_date,
                     final_tmax_f, final_tmin_f, report_issued_at,
                     source_payload_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (station_code, target_date) DO NOTHING
            """, (city, station_code, cli_location, target_date,
                  final_tmax_f, final_tmin_f, report_issued_at,
                  json.dumps(source_payload_json) if source_payload_json else None))
            return cur.rowcount > 0


# ─────────────────────────────────────────────────────────────────────────
# Silver population helpers (called inline after each bronze write)
# UPDATE-then-INSERT within the same transaction for is_latest_* flags.
# ─────────────────────────────────────────────────────────────────────────

def _populate_silver_nws_forecast(*, city: str, station_code: str,
                                    forecast_run_time: datetime, target_date: date,
                                    lead_hours: Optional[int],
                                    tmax_f: Optional[float], tmin_f: Optional[float],
                                    dewpoint_f: Optional[float], rh_pct: Optional[float],
                                    wind_speed_mph: Optional[float],
                                    wind_gust_mph: Optional[float],
                                    cloud_cover_pct: Optional[float],
                                    pop_pct: Optional[float]) -> None:
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE weather_silver_forecast_conformed
                SET is_latest_run = FALSE
                WHERE station_code = %s AND source_name = 'nws'
                  AND target_date = %s AND is_latest_run = TRUE
            """, (station_code, target_date))
            cur.execute("""
                INSERT INTO weather_silver_forecast_conformed
                    (city, station_code, source_name, forecast_run_time, target_date,
                     lead_hours, tmax_f, tmin_f, dewpoint_f, rh_pct,
                     wind_speed_mph, wind_gust_mph, cloud_cover_pct, pop_pct,
                     is_latest_run)
                VALUES (%s, %s, 'nws', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                ON CONFLICT (station_code, source_name, forecast_run_time, target_date)
                DO UPDATE SET
                    tmax_f          = EXCLUDED.tmax_f,
                    tmin_f          = EXCLUDED.tmin_f,
                    dewpoint_f      = EXCLUDED.dewpoint_f,
                    rh_pct          = EXCLUDED.rh_pct,
                    wind_speed_mph  = EXCLUDED.wind_speed_mph,
                    wind_gust_mph   = EXCLUDED.wind_gust_mph,
                    cloud_cover_pct = EXCLUDED.cloud_cover_pct,
                    pop_pct         = EXCLUDED.pop_pct,
                    is_latest_run   = TRUE
            """, (city, station_code, forecast_run_time, target_date,
                  lead_hours, tmax_f, tmin_f, dewpoint_f, rh_pct,
                  wind_speed_mph, wind_gust_mph, cloud_cover_pct, pop_pct))


def _populate_silver_openmeteo_forecast(*, city: str, station_code: str,
                                          source_name: str,
                                          forecast_run_time: datetime,
                                          target_date: date,
                                          lead_hours: Optional[int],
                                          tmax_f: Optional[float],
                                          tmin_f: Optional[float]) -> None:
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE weather_silver_forecast_conformed
                SET is_latest_run = FALSE
                WHERE station_code = %s AND source_name = %s
                  AND target_date = %s AND is_latest_run = TRUE
            """, (station_code, source_name, target_date))
            cur.execute("""
                INSERT INTO weather_silver_forecast_conformed
                    (city, station_code, source_name, forecast_run_time, target_date,
                     lead_hours, tmax_f, tmin_f, is_latest_run)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                ON CONFLICT (station_code, source_name, forecast_run_time, target_date)
                DO UPDATE SET
                    tmax_f        = EXCLUDED.tmax_f,
                    tmin_f        = EXCLUDED.tmin_f,
                    is_latest_run = TRUE
            """, (city, station_code, source_name, forecast_run_time, target_date,
                  lead_hours, tmax_f, tmin_f))


def _populate_silver_market(*, market_ticker: str, series_ticker: Optional[str],
                              station_code: str, bucket_info: dict,
                              target_date: date, snapshot_time: datetime,
                              yes_mid: Optional[float], yes_bid: Optional[float],
                              yes_ask: Optional[float], volume: Optional[float],
                              open_interest: Optional[float],
                              market_status: Optional[str]) -> None:
    contract_side = bucket_info.get("contract_side", "")
    if not contract_side:
        return

    # City lookup
    city_map = {c.icao: c.name for c in CITIES}
    city = city_map.get(station_code, station_code)

    liquidity = "illiquid"
    if volume and volume > 1000:
        liquidity = "liquid"
    elif volume and volume > 100:
        liquidity = "thin"

    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE weather_silver_market_conformed
                SET is_latest_snapshot = FALSE
                WHERE market_ticker = %s AND is_latest_snapshot = TRUE
            """, (market_ticker,))
            cur.execute("""
                INSERT INTO weather_silver_market_conformed
                    (market_ticker, series_ticker, city, station_code,
                     contract_side, bucket_floor, bucket_cap, bucket_type, bucket_label,
                     target_date, snapshot_time, yes_mid, yes_bid, yes_ask, implied_prob,
                     volume, open_interest, market_status, market_liquidity_flag,
                     is_latest_snapshot)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                ON CONFLICT (market_ticker, snapshot_time) DO UPDATE SET
                    yes_mid              = EXCLUDED.yes_mid,
                    implied_prob         = EXCLUDED.implied_prob,
                    market_liquidity_flag = EXCLUDED.market_liquidity_flag,
                    is_latest_snapshot   = TRUE
            """, (market_ticker, series_ticker, city, station_code,
                  contract_side,
                  bucket_info.get("bucket_floor"), bucket_info.get("bucket_cap"),
                  bucket_info.get("bucket_type"), bucket_info.get("bucket_label"),
                  target_date, snapshot_time,
                  yes_mid, yes_bid, yes_ask, yes_mid,
                  volume, open_interest, market_status, liquidity))


def _populate_silver_actuals(*, city: str, station_code: str,
                               target_date: date,
                               final_tmax_f: Optional[float],
                               final_tmin_f: Optional[float],
                               report_issued_at: Optional[datetime]) -> None:
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            # Upsert actuals
            cur.execute("""
                INSERT INTO weather_silver_actuals_conformed
                    (city, station_code, target_date, final_tmax_f, final_tmin_f,
                     actual_source, report_issued_at, is_final)
                VALUES (%s, %s, %s, %s, %s, 'nws_cli', %s, TRUE)
                ON CONFLICT (station_code, target_date, actual_source) DO UPDATE SET
                    final_tmax_f     = EXCLUDED.final_tmax_f,
                    final_tmin_f     = EXCLUDED.final_tmin_f,
                    report_issued_at = EXCLUDED.report_issued_at,
                    is_final         = TRUE
            """, (city, station_code, target_date, final_tmax_f, final_tmin_f,
                  report_issued_at))

            # Write forecast error pairs for each source with matching forecasts
            if final_tmax_f is not None:
                cur.execute("""
                    INSERT INTO weather_silver_forecast_error
                        (city, station_code, target_date, feature_name, source_name,
                         forecast_run_time, lead_hours, forecast_value, actual_value,
                         forecast_error_f, error_sign)
                    SELECT
                        city, station_code, target_date, 'tmax_f', source_name,
                        forecast_run_time, lead_hours, tmax_f, %s,
                        %s - tmax_f,
                        CASE WHEN %s - tmax_f > 0.1 THEN 'cold'
                             WHEN %s - tmax_f < -0.1 THEN 'hot'
                             ELSE 'exact' END
                    FROM weather_silver_forecast_conformed
                    WHERE station_code = %s AND target_date = %s AND tmax_f IS NOT NULL
                    ON CONFLICT (station_code, target_date, feature_name, source_name, forecast_run_time)
                    DO NOTHING
                """, (final_tmax_f, final_tmax_f, final_tmax_f, final_tmax_f,
                      station_code, target_date))

            if final_tmin_f is not None:
                cur.execute("""
                    INSERT INTO weather_silver_forecast_error
                        (city, station_code, target_date, feature_name, source_name,
                         forecast_run_time, lead_hours, forecast_value, actual_value,
                         forecast_error_f, error_sign)
                    SELECT
                        city, station_code, target_date, 'tmin_f', source_name,
                        forecast_run_time, lead_hours, tmin_f, %s,
                        %s - tmin_f,
                        CASE WHEN %s - tmin_f > 0.1 THEN 'cold'
                             WHEN %s - tmin_f < -0.1 THEN 'hot'
                             ELSE 'exact' END
                    FROM weather_silver_forecast_conformed
                    WHERE station_code = %s AND target_date = %s AND tmin_f IS NOT NULL
                    ON CONFLICT (station_code, target_date, feature_name, source_name, forecast_run_time)
                    DO NOTHING
                """, (final_tmin_f, final_tmin_f, final_tmin_f, final_tmin_f,
                      station_code, target_date))


# ─────────────────────────────────────────────────────────────────────────
# Main + CLI
# ─────────────────────────────────────────────────────────────────────────

def fetch_kalshi_portfolio(dry_run: bool = False) -> int:
    """Fetch open Kalshi positions + recent fills; write to DB.
    Returns total rows written (positions + fills)."""
    try:
        from kalshi_client import KalshiClient
    except ImportError:
        logger.warning("kalshi_portfolio: kalshi_client not importable — skipping")
        return 0
    try:
        client = KalshiClient()
    except Exception as e:
        logger.warning(f"kalshi_portfolio: KalshiClient init failed: {e}")
        return 0
    try:
        result = client.fetch_kalshi_portfolio()
    except Exception as e:
        logger.warning(f"kalshi_portfolio: fetch failed: {e}")
        return 0
    total = result.get("positions_upserted", 0) + result.get("fills_inserted", 0)
    logger.info(
        f"kalshi_portfolio: "
        f"{result.get('positions_fetched',0)} positions "
        f"({result.get('positions_upserted',0)} rows), "
        f"{result.get('fills_fetched',0)} fills "
        f"({result.get('fills_inserted',0)} rows)"
        + (" (dry-run)" if dry_run else "")
    )
    return total


def fetch_open_meteo_ensemble(dry_run: bool = False) -> int:
    """Fetch Open-Meteo ensemble forecast (31 GFS members) for each city.

    Computes per city per target_date:
      ensemble_mean_tmax  = mean of all-member daily highs
      ensemble_spread_tmax = stddev of all-member daily highs (uncertainty signal)
      ensemble_mean_tmin  = mean of all-member daily lows
      ensemble_spread_tmin = stddev of all-member daily lows

    Writes to weather_bronze_openmeteo_forecast_snapshots with:
      model = 'open_meteo_ensemble', hour = -1 (sentinel for daily aggregate)
      tmax_f = mean_tmax, tmin_f = mean_tmin
      ensemble_spread_tmax, ensemble_spread_tmin

    Spread thresholds used downstream in edge calculator:
      > 4°F → model_confidence = LOW  (skip trade)
      2-4°F → model_confidence = MEDIUM
      ≤ 2°F → model_confidence = HIGH  (bet with confidence)
    """
    import statistics as _stats
    from collections import defaultdict as _dd

    ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
    TIMEZONE_MAP = {
        "KNYC": "America/New_York",  "KORD": "America/Chicago",
        "KMIA": "America/New_York",  "KAUS": "America/Chicago",
        "KPHX": "America/Phoenix",   "KDEN": "America/Denver",
        "KLAX": "America/Los_Angeles", "KDFW": "America/Chicago",
    }
    n = 0
    run_time = datetime.now(timezone.utc)

    for city in CITIES:
        tz = TIMEZONE_MAP.get(city.icao, "UTC")
        params = {
            "latitude": city.lat, "longitude": city.lon, "timezone": tz,
            "models": "gfs_seamless",
            "hourly": "temperature_2m",
            "temperature_unit": "fahrenheit",
            "forecast_days": 7,
        }
        data = _http_get_json(ENSEMBLE_URL, params=params, timeout=60)
        if not data:
            logger.warning(f"open_meteo_ensemble {city.icao}: fetch failed")
            continue

        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []
        member_keys = [k for k in hourly if k.startswith("temperature_2m_member")]
        if not member_keys:
            logger.warning(f"open_meteo_ensemble {city.icao}: no member columns in response")
            continue

        # Group hourly values by (date, member)
        by_date_member: dict = _dd(lambda: _dd(list))
        for i, t_str in enumerate(times):
            d_str = t_str[:10]
            for mk in member_keys:
                vals = hourly.get(mk) or []
                if i < len(vals) and vals[i] is not None:
                    by_date_member[d_str][mk].append(float(vals[i]))

        for d_str, member_data in sorted(by_date_member.items()):
            try:
                target_date = date.fromisoformat(d_str)
            except ValueError:
                continue
            member_highs = [max(temps) for temps in member_data.values() if temps]
            member_lows  = [min(temps) for temps in member_data.values() if temps]
            if len(member_highs) < 2:
                continue

            mean_tmax   = _stats.mean(member_highs)
            spread_tmax = _stats.stdev(member_highs)
            mean_tmin   = _stats.mean(member_lows)
            spread_tmin = _stats.stdev(member_lows)

            if not dry_run:
                try:
                    _insert_bronze_openmeteo_snapshot(
                        city=city.name, station_code=city.icao,
                        lat=city.lat, lon=city.lon,
                        model="open_meteo_ensemble",
                        forecast_run_time=run_time,
                        target_date=target_date,
                        hour=-1,  # sentinel: daily aggregate row
                        temperature_2m=None,
                        tmax_f=round(mean_tmax, 2),
                        tmin_f=round(mean_tmin, 2),
                        ensemble_spread_tmax=round(spread_tmax, 3),
                        ensemble_spread_tmin=round(spread_tmin, 3),
                        member_highs_json=[round(h, 2) for h in member_highs],
                    )
                    n += 1
                except Exception as e:
                    logger.warning(f"open_meteo_ensemble {city.icao}/{d_str}: write failed: {e}")
            else:
                logger.debug(f"open_meteo_ensemble dry-run {city.icao}/{d_str}: "
                             f"tmax={mean_tmax:.1f}±{spread_tmax:.1f} "
                             f"tmin={mean_tmin:.1f}±{spread_tmin:.1f}")
                n += 1

    logger.info(f"open_meteo_ensemble: {n} rows {'(dry-run)' if dry_run else 'inserted'}")
    return n


# ─────────────────────────────────────────────────────────────────────────
# Source: NWS NBM probabilistic temperature percentiles
# ─────────────────────────────────────────────────────────────────────────

def _insert_bronze_nbm_snapshot(*, station_code: str, city: str, nws_office: str,
                                  forecast_run_time: datetime, target_date: date,
                                  p10_tmax_f: Optional[float], p25_tmax_f: Optional[float],
                                  p50_tmax_f: Optional[float], p75_tmax_f: Optional[float],
                                  p90_tmax_f: Optional[float],
                                  member_count: Optional[int] = None) -> None:
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO weather_bronze_nbm_snapshots
                    (station_code, city, nws_office, forecast_run_time, target_date,
                     p10_tmax_f, p25_tmax_f, p50_tmax_f, p75_tmax_f, p90_tmax_f,
                     member_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (station_code, forecast_run_time, target_date) DO NOTHING
            """, (station_code, city, nws_office, forecast_run_time, target_date,
                  p10_tmax_f, p25_tmax_f, p50_tmax_f, p75_tmax_f, p90_tmax_f,
                  member_count))


def fetch_nbm(dry_run: bool = False) -> int:
    """Fetch NWS NBM (National Blend of Models) probabilistic temperature percentiles.

    Queries the NWS raw gridpoints endpoint for probabilisticQuantileForecast.temperature.
    Takes daily max of hourly P10/P25/P50/P75/P90 values for each target date.
    Converts from degC to °F. Stores in weather_bronze_nbm_snapshots.

    Falls back gracefully (logs debug, returns 0) if the NWS office does not
    provide probabilistic quantile data in the gridpoints response — not all
    WFOs include this field.
    """
    n = 0
    run_time = datetime.now(timezone.utc)
    today = run_time.date()
    target_set = {today + timedelta(days=i) for i in range(3)}

    # Percentile keys to look for in NWS response (various formats)
    PCT_MAP = {"10": "10", "25": "25", "50": "50", "75": "75", "90": "90",
               "0.1": "10", "0.25": "25", "0.5": "50", "0.75": "75", "0.9": "90"}

    for city in CITIES:
        gridpoint = _get_nws_gridpoint(city)
        if not gridpoint:
            continue
        _, office, grid_x, grid_y = gridpoint

        raw_url = f"{NWS_API_BASE}/gridpoints/{office}/{grid_x},{grid_y}"
        raw_data = _http_get_json(raw_url, timeout=30)
        if not raw_data:
            logger.warning(f"nbm {city.icao}: raw gridpoints fetch failed")
            continue

        props = raw_data.get("properties") or {}
        pqf = props.get("probabilisticQuantileForecast") or {}
        temp_data = pqf.get("temperature") or {}
        values = temp_data.get("values") or []

        if not values:
            logger.debug(f"nbm {city.icao}: no probabilisticQuantileForecast.temperature — skipping")
            continue

        # Group hourly P{N} values by date; take daily max of each percentile
        by_date: dict = {}
        for item in values:
            vt = item.get("validTime", "")
            val = item.get("value")
            if not val or not vt:
                continue
            dt_str = vt.split("/")[0]
            try:
                d = datetime.fromisoformat(dt_str).date()
            except ValueError:
                continue
            if d not in target_set:
                continue
            entry = by_date.setdefault(d, {})
            for raw_key, temp_c in val.items():
                if temp_c is None:
                    continue
                pct_key = PCT_MAP.get(str(raw_key).strip("%"))
                if pct_key is None:
                    continue
                temp_f = float(temp_c) * 9.0 / 5.0 + 32.0
                entry.setdefault(pct_key, []).append(temp_f)

        for d, pct_data in by_date.items():
            if not pct_data.get("50"):
                continue  # need at least P50
            p10 = round(max(pct_data["10"]), 2) if pct_data.get("10") else None
            p25 = round(max(pct_data["25"]), 2) if pct_data.get("25") else None
            p50 = round(max(pct_data["50"]), 2)
            p75 = round(max(pct_data["75"]), 2) if pct_data.get("75") else None
            p90 = round(max(pct_data["90"]), 2) if pct_data.get("90") else None
            n_members = len(pct_data.get("50", []))

            if dry_run:
                logger.info(
                    f"nbm dry-run {city.icao}/{d}: "
                    f"P10={p10} P25={p25} P50={p50} P75={p75} P90={p90}"
                )
                n += 1
                continue

            try:
                _insert_bronze_nbm_snapshot(
                    station_code=city.icao, city=city.name, nws_office=office,
                    forecast_run_time=run_time, target_date=d,
                    p10_tmax_f=p10, p25_tmax_f=p25, p50_tmax_f=p50,
                    p75_tmax_f=p75, p90_tmax_f=p90, member_count=n_members,
                )
                n += 1
            except Exception as e:
                logger.warning(f"nbm {city.icao}/{d}: insert failed: {e}")

    logger.info(f"nbm: {n} rows {'(dry-run)' if dry_run else 'inserted'}")
    return n


def fetch_nws_hourly(dry_run: bool = False) -> int:
    """Fetch NWS hourly gridpoints forecast for each city.

    Extracts per hour for today + tomorrow:
      temperature_f, dewpoint_f, wind_speed_mph, cloud_cover_pct, pop_pct

    Writes to weather_bronze_nws_forecast_snapshots with source_name='nws_hourly'.
    tmax_f stores the hourly temperature (not a daily max).

    Afternoon peak logic: max(temperature_f, hours 12-18 local) is used by the
    edge calculator as nws_hourly_peak. If it differs from daily NWS high, the
    edge sheet uses the hourly peak and flags quality_flag='hourly_override'.
    """
    today = datetime.now(timezone.utc).date()
    target_dates = {today, today + timedelta(days=1)}
    run_time = datetime.now(timezone.utc)
    n = 0

    for city in CITIES:
        gridpoint = _discover_nws_gridpoint(city)
        if gridpoint is None:
            continue
        _, office, grid_x, grid_y = gridpoint

        hourly_url = f"{NWS_API_BASE}/gridpoints/{office}/{grid_x},{grid_y}/forecast/hourly"
        data = _http_get_json(hourly_url, timeout=30)
        if not data:
            logger.warning(f"nws_hourly {city.icao}: fetch failed")
            continue

        periods = ((data.get("properties") or {}).get("periods") or [])
        if not periods:
            logger.warning(f"nws_hourly {city.icao}: empty periods")
            continue

        rows_city = 0
        for period in periods:
            start_str = period.get("startTime") or ""
            if not start_str:
                continue
            try:
                dt_local = datetime.fromisoformat(start_str)
                target_date = dt_local.date()
                hour_local = dt_local.hour
            except (ValueError, AttributeError):
                continue

            if target_date not in target_dates:
                continue

            temp = period.get("temperature")
            temp_unit = period.get("temperatureUnit", "F")
            if temp is None:
                continue
            temp_f = float(temp) if temp_unit == "F" else _c_to_f(float(temp))
            if temp_f is None:
                continue

            # Extract extended fields where available
            dewpoint_raw = (period.get("dewpoint") or {}).get("value")
            dewpoint_f = _c_to_f(dewpoint_raw) if dewpoint_raw is not None else None

            wind_raw = period.get("windSpeed") or ""
            wind_f: Optional[float] = None
            if wind_raw:
                try:
                    wind_f = float(str(wind_raw).split()[0])
                except (ValueError, IndexError):
                    pass

            cloud_raw = (period.get("skyCover") or period.get("relativeHumidity") or {})
            cloud_f: Optional[float] = None
            if isinstance(cloud_raw, dict):
                v = cloud_raw.get("value")
                cloud_f = float(v) if v is not None else None

            pop_raw = (period.get("probabilityOfPrecipitation") or {}).get("value")
            pop_f = float(pop_raw) if pop_raw is not None else None

            if dry_run:
                n += 1
                continue

            try:
                with tc.get_pg_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO weather_bronze_nws_forecast_snapshots
                                (city, station_code, nws_office, forecast_run_time,
                                 target_date, lead_hours, hour,
                                 tmax_f, dewpoint_f, wind_speed_mph,
                                 cloud_cover_pct, pop_pct,
                                 source_name, source_payload_json)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'nws_hourly', NULL)
                            ON CONFLICT (station_code, source_name, forecast_run_time, target_date, hour)
                            WHERE source_name = 'nws_hourly' DO NOTHING
                        """, (
                            city.name, city.icao, office, run_time,
                            target_date,
                            (target_date - today).days * 24 + hour_local,
                            hour_local,
                            temp_f, dewpoint_f, wind_f,
                            cloud_f, pop_f,
                        ))
                n += 1
                rows_city += 1
            except Exception as e:
                logger.warning(f"nws_hourly {city.icao}/{target_date}/{hour_local}: write failed: {e}")

        if rows_city or dry_run:
            logger.debug(f"nws_hourly {city.icao}: {rows_city} hourly rows inserted")

    logger.info(f"nws_hourly: {n} rows {'(dry-run)' if dry_run else 'inserted'}")
    return n


# WeatherBHN active sources — Kalshi weather trading stack only.
# fetch_enso / compute_degree_days_from_observations are Phase 2/5 and excluded.
SOURCES = {
    "open_meteo":           fetch_open_meteo,
    "open_meteo_ensemble":  fetch_open_meteo_ensemble,
    "asos":                 fetch_asos,
    "usda_crops":           fetch_usda_crops,
    "nws":                  fetch_nws,
    "nws_hourly":           fetch_nws_hourly,
    "nws_actuals":          fetch_nws_actuals,
    "nbm":                  fetch_nbm,
    "kalshi_markets":       fetch_kalshi_markets,
    "kalshi_portfolio":     fetch_kalshi_portfolio,
    "nomads":               fetch_nomads_gfs_ensemble,
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
