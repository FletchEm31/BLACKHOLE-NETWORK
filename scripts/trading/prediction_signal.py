#!/usr/bin/env python3
"""
prediction_signal.py — BHN Strategy 9 (BHN-PREDICTION-ALPHA) signal + bet logic.

The `on_market_update` callback for kalshi_client.poll_weather_prices_aggressive.
Per cycle, per ticker:

  1. Parse Kalshi ticker → (station, variable, target_date, threshold, op)
  2. Pull BHN model forecast + (when available) bias correction
  3. Compute model probability that the contract resolves YES
  4. Compute edge = model_prob - market_implied_prob
  5. Gate on operator thresholds:
       edge_pct ≥ EDGE_THRESHOLD (8%)
       confidence ≥ CONFIDENCE_THRESHOLD (0.65)
       rules.json strat_9 enabled=True (refuses to bet otherwise)
       paper-only Kalshi env (refuses live URL while paper_only=True)
  6. Size with half-Kelly, capped at MAX_POSITION_FRACTION × allocation
  7. Place limit order on Kalshi via the authenticated client
  8. INSERT one row into weather_bets capturing the full decision context
  9. Return stats dict for the polling loop to aggregate into gfs_window_stats

Operator thresholds (locked in for Strat 9):
  edge_threshold           = 0.08   (8% — required model_prob - market_prob)
  confidence_threshold     = 0.65   (ensemble reliability score floor)
  kelly_fraction           = 0.5    (half-Kelly, not 15% as in the reference repo)
  max_position_fraction    = 0.20   (cap any single bet at 20% of strategy allocation)
  capital_allocation       = $6,000 (from operator's Strat 9 spec; broken down as
                                      $2k Kalshi + $1k Polymarket + $2k commodity
                                      ETFs + $1k arb reserve — Phase 1 only the
                                      Kalshi $2k matters)

Phase gating:
  Phase 1 (now)            — signal eval runs, weather_bets rows are LOGGED but
                              place_order is BLOCKED unless rules.json
                              strat_9_prediction_alpha.enabled=true (currently
                              false per Strat 9's enabled=false default).
  Phase 3                  — operator flips enabled=true via HORIZON SMS;
                              bets actually flow to demo-api.kalshi.co.

No external SDK. No betting-side reference-repo code. Math + parsing all
written from scratch using stdlib (math.erf for normal CDF).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Optional

import trading_core as tc
import kalshi_client as kc


logger = tc.get_logger("strat_9_prediction_alpha_signal")


# ─────────────────────────────────────────────────────────────────────────
# Operator-locked thresholds
# ─────────────────────────────────────────────────────────────────────────

STRATEGY_ID                = "strat_9_prediction_alpha"  # not yet in StrategyId enum
EDGE_THRESHOLD             = 0.08
CONFIDENCE_THRESHOLD       = 0.65
KELLY_FRACTION             = 0.5         # half-Kelly per operator
MAX_POSITION_FRACTION      = 0.20        # cap any one bet at 20% of allocation
DEFAULT_KALSHI_ALLOCATION_USD = Decimal("2000")  # Strat 9 Kalshi sleeve

# Default ensemble σ fallback when we don't have model_calibration RMSE yet.
# Picked conservatively wide so Phase 1 returns LOW confidence and never
# fires bets accidentally before calibration data accumulates.
DEFAULT_FALLBACK_SIGMA_F = 6.0   # ~6°F stdev for temperature variables
DEFAULT_FALLBACK_SIGMA_PRECIP = 0.5   # 0.5 inch for precipitation


# Kalshi weather series → (BHN station, variable)
# Phase 3 scope: Miami, Phoenix, Denver — High (tmax_f) and Low (tmin_f).
SERIES_TO_STATION_VAR: dict[str, tuple[str, str]] = {
    "KXHIGHMIA": ("KMIA", "tmax_f"),
    "KXLOWMIA":  ("KMIA", "tmin_f"),
    "KXHIGHPHX": ("KPHX", "tmax_f"),
    "KXLOWPHX":  ("KPHX", "tmin_f"),
    "KXHIGHDEN": ("KDEN", "tmax_f"),
    "KXLOWDEN":  ("KDEN", "tmin_f"),
    "KXHIGHLAX": ("KLAX", "tmax_f"),
    "KXLOWLAX":  ("KLAX", "tmin_f"),
    "KXHIGHDFW": ("KDFW", "tmax_f"),
    "KXLOWDFW":  ("KDFW", "tmin_f"),
}


# ─────────────────────────────────────────────────────────────────────────
# Kalshi ticker parser
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class ContractMetadata:
    ticker:       str
    series:       str
    station:      str
    variable:     str
    target_date:  date
    threshold:    float           # the contract's strike value
    threshold_op: str             # '>' | '>=' | '<' | '<=' | 'between'
    threshold_high: Optional[float] = None   # used only for 'between'


# Patterns observed in Kalshi weather tickers:
#   KXHIGHNYM-26MAY15-T80       → "tmax > 80°F on 2026-05-15"
#   KXHIGHCHIM-26JUN03-T85      → similar
#   KXHIGHMIAM-26JUL10-B85T90   → "85 ≤ tmax < 90" (range market)
#
# The series part has an extra 'M' suffix (market identifier letter) in the
# fully-qualified contract ticker but not in the series prefix. Strip it.
_RE_DATE_PART = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})$")
_MONTH_MAP = {
    "JAN": 1,  "FEB": 2,  "MAR": 3,  "APR": 4,
    "MAY": 5,  "JUN": 6,  "JUL": 7,  "AUG": 8,
    "SEP": 9,  "OCT": 10, "NOV": 11, "DEC": 12,
}


def parse_kalshi_weather_ticker(ticker: str) -> Optional[ContractMetadata]:
    """Parse 'KXHIGHNYM-26MAY15-T80' style into ContractMetadata. Returns
    None if the ticker doesn't match a known weather series.

    Two strike forms supported:
      -T<N>            → strict greater-than threshold N (or per Kalshi's
                          docs: 'YES resolves true if the observed value
                          exceeds N')
      -B<low>T<high>   → between-range: low ≤ value < high
    """
    parts = ticker.upper().split("-")
    if len(parts) < 3:
        return None
    series_part = parts[0]
    date_part   = parts[1]
    strike_part = "-".join(parts[2:])

    # Series_part includes a trailing 'M' for the market identifier in some
    # contract tickers (e.g. KXHIGHNYM). Strip exactly one trailing M when it
    # leaves a known series.
    series = series_part
    if series_part.endswith("M") and series_part[:-1] in SERIES_TO_STATION_VAR:
        series = series_part[:-1]
    if series not in SERIES_TO_STATION_VAR:
        return None
    station, variable = SERIES_TO_STATION_VAR[series]

    m_date = _RE_DATE_PART.match(date_part)
    if not m_date:
        return None
    yy, mon, dd = m_date.group(1), m_date.group(2), m_date.group(3)
    month = _MONTH_MAP.get(mon)
    if month is None:
        return None
    try:
        target_date = date(2000 + int(yy), month, int(dd))
    except ValueError:
        return None

    # Strike: 'T80', 'B85T90'
    if strike_part.startswith("B"):
        m = re.match(r"^B(\d+(?:\.\d+)?)T(\d+(?:\.\d+)?)$", strike_part)
        if not m:
            return None
        return ContractMetadata(
            ticker=ticker, series=series, station=station, variable=variable,
            target_date=target_date,
            threshold=float(m.group(1)),
            threshold_op="between",
            threshold_high=float(m.group(2)),
        )
    if strike_part.startswith("T"):
        m = re.match(r"^T(\d+(?:\.\d+)?)$", strike_part)
        if not m:
            return None
        return ContractMetadata(
            ticker=ticker, series=series, station=station, variable=variable,
            target_date=target_date,
            threshold=float(m.group(1)),
            threshold_op=">",
        )
    return None


# ─────────────────────────────────────────────────────────────────────────
# Model probability + confidence (pulls from weather-schema tables)
# ─────────────────────────────────────────────────────────────────────────

def _normal_cdf(z: float) -> float:
    """Φ(z) — standard normal CDF via math.erf. No scipy."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _season_for(d: date) -> str:
    m = d.month
    if m in (12, 1, 2):
        return "winter"
    if m in (3, 4, 5):
        return "spring"
    if m in (6, 7, 8):
        return "summer"
    return "fall"


@dataclass
class ModelEstimate:
    predicted_value:    Optional[float]    # bias-corrected mean
    sigma:              Optional[float]    # uncertainty (°F for temp, in for precip)
    confidence:         Optional[float]    # 0-1 reliability_score (None = no calibration yet)
    sources_used:       list               # source_model names that contributed
    sample_size_calib:  int                # rows of calibration data backing this estimate


def estimate_model_probability(meta: ContractMetadata,
                                today: Optional[date] = None) -> tuple[Optional[float], ModelEstimate]:
    """Return (probability_YES_resolves, model_estimate).

    Phase 1 path (no calibration data yet):
      - Pull most recent forecast(s) for (station, variable, target_date)
      - Use the average across sources (NWS / ECMWF / Open-Meteo) as the
        bias-corrected mean (mean_bias = 0 in Phase 1)
      - σ = ensemble_std if available, else DEFAULT_FALLBACK_SIGMA_* (wide,
        so confidence is low and bets don't fire pre-calibration)
      - confidence = None (operator's gate `≥ 0.65` rejects automatically)

    Phase 2+ path (calibration available):
      - Same forecasts, but JOIN model_calibration on
        (station, variable, season, lead_time_hours, source_model)
      - mean_bias applied additively per source, then averaged
      - σ from RMSE in model_calibration
      - confidence = reliability_score (or 1 - normalized RMSE)
    """
    today = today or datetime.now(timezone.utc).date()
    lead_days = (meta.target_date - today).days
    if lead_days < 0:
        # Contract is already past; can't model
        return None, ModelEstimate(None, None, None, [], 0)

    season = _season_for(meta.target_date)
    rows = _query_forecasts(meta.station, meta.variable, meta.target_date)
    if not rows:
        return None, ModelEstimate(None, None, None, [], 0)

    sources_used: list[str] = []
    corrected_values: list[float] = []
    spread_values: list[float] = []
    confidences: list[float] = []
    calib_n_total = 0

    for row in rows:
        source_model = row["source_model"]
        predicted = row["predicted_value"]
        if predicted is None:
            continue
        # Look up calibration if present
        calib = _query_calibration(meta.station, meta.variable, season,
                                    row.get("lead_time_hours") or (lead_days * 24),
                                    source_model)
        if calib:
            corrected = float(predicted) + float(calib["mean_bias"] or 0.0)
            sigma = float(calib["rmse"]) if calib["rmse"] else None
            confidence = float(calib["reliability_score"]) if calib["reliability_score"] is not None else None
            calib_n_total += int(calib.get("sample_size") or 0)
        else:
            corrected = float(predicted)
            sigma = None
            confidence = None
        corrected_values.append(corrected)
        if row.get("ensemble_std") is not None:
            spread_values.append(float(row["ensemble_std"]))
        elif sigma is not None:
            spread_values.append(sigma)
        if confidence is not None:
            confidences.append(confidence)
        sources_used.append(source_model)

    if not corrected_values:
        return None, ModelEstimate(None, None, None, [], 0)

    if meta.variable == "tmax_f":
        mean = max(corrected_values)
    elif meta.variable == "tmin_f":
        mean = min(corrected_values)
    else:
        mean = sum(corrected_values) / len(corrected_values)
    if spread_values:
        sigma_final = sum(spread_values) / len(spread_values)
    elif meta.variable in ("precip_in", "snow_in"):
        sigma_final = DEFAULT_FALLBACK_SIGMA_PRECIP
    else:
        sigma_final = DEFAULT_FALLBACK_SIGMA_F
    # Average ensemble-spread confidence with model-calibration confidence;
    # None until calibration data lands in Phase 2.
    confidence_final = (sum(confidences) / len(confidences)) if confidences else None

    # P(value > threshold)  (for ">" markets; "between" handled below)
    if meta.threshold_op == ">":
        z = (meta.threshold - mean) / sigma_final if sigma_final > 0 else 0.0
        prob_yes = 1.0 - _normal_cdf(z)
    elif meta.threshold_op == "between" and meta.threshold_high is not None:
        z_lo = (meta.threshold - mean) / sigma_final if sigma_final > 0 else 0.0
        z_hi = (meta.threshold_high - mean) / sigma_final if sigma_final > 0 else 0.0
        prob_yes = _normal_cdf(z_hi) - _normal_cdf(z_lo)
    else:
        prob_yes = None

    return prob_yes, ModelEstimate(
        predicted_value=mean, sigma=sigma_final,
        confidence=confidence_final,
        sources_used=sources_used, sample_size_calib=calib_n_total,
    )


def _query_forecasts(station: str, variable: str, target_date: date) -> list:
    """Pull the most recent forecast(s) for the target. Returns list of dicts
    keyed by source_model — one row per (station, variable, target_date,
    source_model), the freshest predicted_at within the last 24h."""
    rows: list = []
    try:
        with tc.get_pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (source_model)
                        source_model,
                        predicted_value, predicted_probability,
                        ensemble_mean, ensemble_std, bias_correction,
                        corrected_value, lead_time_hours
                    FROM weather_forecasts
                    WHERE station_code = %s
                      AND variable     = %s
                      AND target_date  = %s
                      AND predicted_at > NOW() - INTERVAL '24 hours'
                    ORDER BY source_model, predicted_at DESC
                """, (station, variable, target_date))
                cols = [c[0] for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:
        logger.warning(f"_query_forecasts failed (non-fatal): {e}")
    return rows


def _query_calibration(station: str, variable: str, season: str,
                        lead_time_hours: int, source_model: str) -> Optional[dict]:
    """One row from model_calibration, or None if no calibration yet."""
    try:
        with tc.get_pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT sample_size, mean_bias, rmse, mae,
                           crps, reliability_score
                    FROM model_calibration
                    WHERE station_code   = %s
                      AND variable       = %s
                      AND season         = %s
                      AND lead_time_hours = %s
                      AND source_model   = %s
                """, (station, variable, season, lead_time_hours, source_model))
                row = cur.fetchone()
                if not row:
                    return None
                cols = [c[0] for c in cur.description]
                return dict(zip(cols, row))
    except Exception as e:
        logger.warning(f"_query_calibration failed (non-fatal): {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────
# Half-Kelly sizing
# ─────────────────────────────────────────────────────────────────────────

def kelly_fraction_to_bet(model_prob: float, market_price: float) -> float:
    """Standard Kelly for a binary contract priced at market_price ∈ (0,1):
        f* = (p - market_price) / (1 - market_price)    [for YES bet]
    where p is our estimated win probability.

    Caller multiplies by KELLY_FRACTION (0.5 for half-Kelly) and caps at
    MAX_POSITION_FRACTION. Returns the raw Kelly fraction (pre-half, pre-cap)
    so callers can audit. Returns 0.0 when market_price ≥ 1 (no upside) or
    edge ≤ 0.
    """
    if market_price >= 1.0 or market_price <= 0.0:
        return 0.0
    edge = model_prob - market_price
    if edge <= 0:
        return 0.0
    return edge / (1.0 - market_price)


def stake_dollars_from_kelly(model_prob: float, market_price: float,
                              allocation: Decimal) -> Decimal:
    """Half-Kelly stake in dollars, capped at MAX_POSITION_FRACTION × allocation."""
    raw_kelly = kelly_fraction_to_bet(model_prob, market_price)
    half_kelly = raw_kelly * KELLY_FRACTION
    capped = min(half_kelly, MAX_POSITION_FRACTION)
    stake = allocation * Decimal(str(capped))
    return stake.quantize(Decimal("0.01"), rounding=ROUND_DOWN)


# ─────────────────────────────────────────────────────────────────────────
# Decision
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class BetDecision:
    is_opportunity:           bool
    edge:                     Optional[float]
    side:                     Optional[str]      # 'yes' | 'no'
    model_probability:        Optional[float]
    market_implied_probability: Optional[float]
    confidence:               Optional[float]
    stake_dollars:            Decimal
    contracts_to_buy:         int
    limit_price_cents:        Optional[int]
    rejection_reason:         Optional[str]
    metadata:                 dict


def _allocation_remaining_today() -> Decimal:
    """Phase 1: full allocation (no bets placed yet so nothing's drawn).
    Phase 3: subtract today's open weather_bets stake from the allocation."""
    base = DEFAULT_KALSHI_ALLOCATION_USD
    try:
        with tc.get_pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COALESCE(SUM(stake_usd), 0)
                    FROM weather_bets
                    WHERE status = 'open'
                      AND placed_at::date = CURRENT_DATE
                """)
                row = cur.fetchone()
                drawn = Decimal(str(row[0])) if row else Decimal("0")
                return max(Decimal("0"), base - drawn)
    except Exception as e:
        logger.warning(f"_allocation_remaining_today failed (non-fatal): {e}")
        return base


def _is_strat_enabled() -> bool:
    """Read rules.json strat_9 enabled flag via trading_core's loader.
    Returns False if the block isn't in rules.json (default-off)."""
    try:
        rules = tc.load_rules() or {}
        block = rules.get(STRATEGY_ID)
        if not isinstance(block, dict):
            return False
        return bool(block.get("enabled", False))
    except Exception as e:
        logger.warning(f"_is_strat_enabled failed (non-fatal): {e}")
        return False


def evaluate_decision(meta: ContractMetadata,
                        market_implied_prob: float) -> BetDecision:
    """Pure decision logic — no order placement. Caller wraps with
    Kalshi place_order in Phase 3+."""
    prob_yes, est = estimate_model_probability(meta)
    if prob_yes is None:
        return BetDecision(
            is_opportunity=False, edge=None, side=None,
            model_probability=None,
            market_implied_probability=market_implied_prob,
            confidence=est.confidence,
            stake_dollars=Decimal("0"), contracts_to_buy=0,
            limit_price_cents=None,
            rejection_reason="no model forecast available",
            metadata={"estimate": est.__dict__},
        )

    # Decide YES vs NO side
    edge_yes = prob_yes - market_implied_prob
    no_implied = 1.0 - market_implied_prob
    prob_no = 1.0 - prob_yes
    edge_no = prob_no - no_implied
    if edge_yes >= edge_no:
        side, model_p, market_p, edge = "yes", prob_yes, market_implied_prob, edge_yes
    else:
        side, model_p, market_p, edge = "no", prob_no, no_implied, edge_no

    rejection = None
    if edge < EDGE_THRESHOLD:
        rejection = f"edge {edge:.4f} < threshold {EDGE_THRESHOLD}"
    elif est.confidence is None:
        rejection = ("no calibration available yet — confidence undefined; "
                     "Phase 1 deliberately refuses to bet pre-calibration")
    elif est.confidence < CONFIDENCE_THRESHOLD:
        rejection = (f"confidence {est.confidence:.3f} < threshold "
                     f"{CONFIDENCE_THRESHOLD}")

    allocation = _allocation_remaining_today()
    stake = stake_dollars_from_kelly(model_p, market_p, allocation)
    limit_price_cents = int(round(market_p * 100))
    contracts = int(stake / (Decimal(limit_price_cents) / Decimal("100"))
                    ) if limit_price_cents > 0 else 0

    is_opp = rejection is None and contracts >= 1 and stake > 0
    return BetDecision(
        is_opportunity=is_opp, edge=edge, side=side,
        model_probability=model_p,
        market_implied_probability=market_p,
        confidence=est.confidence,
        stake_dollars=stake, contracts_to_buy=contracts,
        limit_price_cents=limit_price_cents,
        rejection_reason=rejection,
        metadata={
            "estimate": {
                "predicted_value":   est.predicted_value,
                "sigma":             est.sigma,
                "sources_used":      est.sources_used,
                "sample_size_calib": est.sample_size_calib,
            },
            "allocation_remaining_usd": str(allocation),
            "raw_kelly_fraction": kelly_fraction_to_bet(model_p, market_p),
        },
    )


# ─────────────────────────────────────────────────────────────────────────
# PG audit — INSERT into weather_bets with full capture context
# ─────────────────────────────────────────────────────────────────────────

def _insert_weather_bet_row(
    contract_db_id: int,
    decision: BetDecision,
    meta: ContractMetadata,
    payload: dict,
    exchange_order_id: Optional[str],
) -> Optional[int]:
    """INSERT one row into weather_bets capturing the decision context.
    Phase 1: contract_db_id comes from the prediction_contracts row
    auto-UPSERTed by kalshi_client when this market was first seen."""
    try:
        with tc.get_pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO weather_bets
                        (contract_id, exchange, side, stake_usd,
                         entry_price, model_probability, edge_pct,
                         kelly_fraction, confidence_score, status,
                         gfs_window_poll_number, gfs_window_phase,
                         kalshi_api_latency_ms,
                         exchange_order_id, raw_payload)
                    VALUES (%s, 'kalshi', %s, %s, %s, %s, %s, %s, %s,
                            'open', %s, %s, %s, %s, %s::jsonb)
                    RETURNING id
                """, (
                    contract_db_id, decision.side,
                    str(decision.stake_dollars),
                    decision.market_implied_probability,
                    decision.model_probability,
                    decision.edge,
                    decision.metadata.get("raw_kelly_fraction"),
                    decision.confidence,
                    payload.get("poll_number"),
                    payload.get("phase"),
                    payload.get("api_latency_ms"),
                    exchange_order_id,
                    __import__("json").dumps({
                        "ticker": meta.ticker,
                        "station": meta.station,
                        "variable": meta.variable,
                        "target_date": meta.target_date.isoformat(),
                        "threshold": meta.threshold,
                        "threshold_op": meta.threshold_op,
                        "decision": {
                            "is_opportunity": decision.is_opportunity,
                            "rejection_reason": decision.rejection_reason,
                            "contracts_to_buy": decision.contracts_to_buy,
                            "limit_price_cents": decision.limit_price_cents,
                            "estimate": decision.metadata.get("estimate"),
                        },
                    }),
                ))
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as e:
        logger.warning(f"_insert_weather_bet_row failed (non-fatal): {e}")
        return None


def _lookup_contract_db_id(ticker: str) -> Optional[int]:
    """Find prediction_contracts.id for this Kalshi ticker. The kalshi_client
    auto-UPSERTs this row on every get_weather_markets() call so it should
    exist by the time we evaluate."""
    try:
        with tc.get_pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id FROM prediction_contracts
                    WHERE exchange = 'kalshi' AND contract_id = %s
                """, (ticker,))
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as e:
        logger.warning(f"_lookup_contract_db_id failed (non-fatal): {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────
# Main callback — passed to kalshi_client.poll_weather_prices_aggressive
# ─────────────────────────────────────────────────────────────────────────

class PredictionSignal:
    """Stateful callback wrapper. Constructed once per polling window with
    a configured KalshiClient; the .on_market_update method is the contract
    consumed by kalshi_client.poll_weather_prices_aggressive."""

    def __init__(self, kalshi: kc.KalshiClient,
                 dry_run: Optional[bool] = None):
        self.kalshi = kalshi
        # dry_run defaults to "not enabled in rules.json" — Phase 1 safety.
        self.dry_run = (not _is_strat_enabled()) if dry_run is None else dry_run
        self._processed_tickers_this_window: set = set()
        if self.dry_run:
            logger.info("PredictionSignal: dry_run mode — decisions logged, "
                        "no orders placed")
        else:
            logger.info("PredictionSignal: live mode — bets WILL be placed "
                        "on the configured Kalshi env")

    def on_market_update(self, payload: dict) -> dict:
        """Called by the polling loop per ticker per cycle. Returns the
        stats dict the loop aggregates into gfs_window_stats."""
        ticker = payload["ticker"]
        best_yes_cents = payload.get("best_yes_cents")
        if best_yes_cents is None:
            return {}
        meta = parse_kalshi_weather_ticker(ticker)
        if not meta:
            return {}

        market_implied = float(best_yes_cents) / 100.0
        decision = evaluate_decision(meta, market_implied)

        if not decision.is_opportunity:
            return {
                "is_opportunity": False,
                "edge": decision.edge,
                "rejection_reason": decision.rejection_reason,
            }

        # Avoid re-betting the same contract in a single window
        if ticker in self._processed_tickers_this_window:
            return {"is_opportunity": True, "edge": decision.edge,
                    "rejection_reason": "already bet this window"}

        exchange_order_id: Optional[str] = None
        bet_placed = False
        if self.dry_run:
            logger.info(
                f"DRY RUN bet: {ticker} side={decision.side} "
                f"contracts={decision.contracts_to_buy} "
                f"@ {decision.limit_price_cents}¢ "
                f"stake=${decision.stake_dollars} edge={decision.edge:.4f} "
                f"conf={decision.confidence}"
            )
        else:
            try:
                order = self.kalshi.place_order(
                    ticker=ticker, side=decision.side,
                    count=decision.contracts_to_buy,
                    price=decision.limit_price_cents,
                    order_type="limit", action="buy",
                )
                exchange_order_id = order.get("order", {}).get("order_id") \
                                     or order.get("order_id")
                bet_placed = True
                logger.info(
                    f"BET placed: {ticker} side={decision.side} "
                    f"contracts={decision.contracts_to_buy} "
                    f"@ {decision.limit_price_cents}¢ "
                    f"order_id={exchange_order_id}"
                )
            except Exception as e:
                logger.error(f"place_order failed for {ticker}: {e}")
                return {"is_opportunity": True, "edge": decision.edge,
                        "bet_placed": False,
                        "rejection_reason": f"place_order error: {e}"}

        # Audit row — always insert, even on dry_run (so paper-mode operator
        # can see what we WOULD have bet had we been live)
        contract_db_id = _lookup_contract_db_id(ticker)
        if contract_db_id is not None:
            _insert_weather_bet_row(
                contract_db_id=contract_db_id, decision=decision,
                meta=meta, payload=payload,
                exchange_order_id=exchange_order_id,
            )

        self._processed_tickers_this_window.add(ticker)

        # Roll-up data for the polling loop's gfs_window_stats
        return {
            "is_opportunity":     True,
            "edge":               decision.edge,
            "bet_placed":         bet_placed,
            "edge_captured_usd":  float(decision.stake_dollars) * (decision.edge or 0.0),
            "lag_minutes":        payload.get("minutes_since_gfs"),
        }


# ─────────────────────────────────────────────────────────────────────────
# CLI smoke tests
# ─────────────────────────────────────────────────────────────────────────

def _cli_parse(args) -> int:
    meta = parse_kalshi_weather_ticker(args.ticker)
    if meta is None:
        print(f"could not parse ticker: {args.ticker}")
        return 1
    print(f"series:       {meta.series}")
    print(f"station:      {meta.station}")
    print(f"variable:     {meta.variable}")
    print(f"target_date:  {meta.target_date}")
    print(f"threshold:    {meta.threshold} ({meta.threshold_op})")
    if meta.threshold_high is not None:
        print(f"threshold_hi: {meta.threshold_high}")
    return 0


def _cli_estimate(args) -> int:
    meta = parse_kalshi_weather_ticker(args.ticker)
    if meta is None:
        print(f"could not parse ticker: {args.ticker}")
        return 1
    prob, est = estimate_model_probability(meta)
    print(f"P(yes) = {prob}")
    print(f"predicted_value = {est.predicted_value}")
    print(f"sigma           = {est.sigma}")
    print(f"confidence      = {est.confidence}")
    print(f"sources_used    = {est.sources_used}")
    return 0


def _cli_decide(args) -> int:
    meta = parse_kalshi_weather_ticker(args.ticker)
    if meta is None:
        print(f"could not parse ticker: {args.ticker}")
        return 1
    decision = evaluate_decision(meta, args.market_price)
    print(f"is_opportunity:           {decision.is_opportunity}")
    print(f"edge:                     {decision.edge}")
    print(f"side:                     {decision.side}")
    print(f"model_probability:        {decision.model_probability}")
    print(f"market_implied:           {decision.market_implied_probability}")
    print(f"confidence:               {decision.confidence}")
    print(f"stake_dollars:            ${decision.stake_dollars}")
    print(f"contracts_to_buy:         {decision.contracts_to_buy}")
    print(f"limit_price_cents:        {decision.limit_price_cents}")
    if decision.rejection_reason:
        print(f"rejection_reason:         {decision.rejection_reason}")
    return 0


def main() -> int:
    import argparse, sys
    parser = argparse.ArgumentParser(description="BHN Strat 9 prediction signal")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("parse", help="Parse a Kalshi weather ticker")
    pp.add_argument("ticker")

    pe = sub.add_parser("estimate",
                          help="Estimate model probability for a contract "
                          "(needs forecast rows in PG)")
    pe.add_argument("ticker")

    pd = sub.add_parser("decide",
                          help="Full decision: parse + estimate + Kelly sizing")
    pd.add_argument("ticker")
    pd.add_argument("--market-price", type=float, required=True,
                    help="Current market implied probability (0-1)")

    args = parser.parse_args()
    if args.cmd == "parse":    return _cli_parse(args)
    if args.cmd == "estimate": return _cli_estimate(args)
    if args.cmd == "decide":   return _cli_decide(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
