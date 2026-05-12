#!/usr/bin/env python3
"""
strategy_prediction_market.py — BHN Strategy 5: Prediction Market Arbitrage.

Two parallel signal sources, both in this file:

A. MACRO-EVENT ARBITRAGE (live, real positions via Alpaca):
   Polymarket/Kalshi odds move >10% in 1h on macro events (Fed, energy, tech regs,
   healthcare, defense, climate, banking, China trade) → buy correlated sector ETF.
   Position size $2k per signal. Exit: 48h hold / 5% TP / 3% SL. Max 3 positions.

B. WEATHER ARBITRAGE (read-only until Kalshi+Polymarket auth keys arrive):
   BHN weather forecasts vs Kalshi/Polymarket weather-contract implied probabilities.
   Edge = bhn_predicted_prob - market_implied_prob. Tiered position sizing:
     - 10-25% edge → $2k
     - 25-35% edge → $3k
     - >35% edge   → $5k (SMS confirmation required)
   Max 3 simultaneous weather positions. Stop loss 50% of position value.
   Hold to contract resolution.

Cadence: every 10 min during market hours, hourly otherwise (cadence_seconds=600).
Capital allocation: $15,000.

Read-only mode (current v1 default since exchange auth keys aren't provisioned):
- live_execution_enabled=False in rules.json
- Macro signals still trade real ETFs on Alpaca paper account
- Weather signals log to signals_log with acted_on=false + metadata.simulated=true
- weather_model_accuracy rows still written so the self-improvement loop has
  a data trail even without real positions

Self-improvement loop (Phase C, separate session):
- Weekly HORIZON workflow reads weather_model_accuracy
- Computes per-region/per-variable Brier scores
- Proposes edge_threshold tweaks via bhn-rules-mutator workflow
- Operator confirms via SMS → rules.json updated → next run uses new thresholds

Data sources:
- Polymarket Gamma API (https://gamma-api.polymarket.com) — public, no auth
- Kalshi public market data (https://trading-api.kalshi.com/trade-api/v2/markets) — public read
- BHN weather_snapshots PG table (already populated by eh-weather-poll)
- Alpaca (sector ETF execution)
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal
from typing import Any, Optional

import requests
import psycopg2.extras

import trading_core as tc


STRATEGY_ID = tc.StrategyId.PRED_MKT.value
logger = tc.get_logger(STRATEGY_ID)

POLYMARKET_BASE = "https://gamma-api.polymarket.com"
KALSHI_BASE = "https://trading-api.kalshi.com/trade-api/v2"


# ─── Hardcoded defaults (overridden by rules.json strat_5_pred_mkt block) ──
DEFAULTS = {
    "macro_events": {
        "odds_move_pct_min": 10.0,          # 10 percentage-point move
        "odds_move_window_min": 60,          # in 60 minutes
        "sector_mapping": {
            "fed_rates":            "XLF",
            "energy_policy":        "XLE",
            "tech_regulation":      "XLK",
            "healthcare_policy":    "XLV",
            "defense":              "ITA",
            "climate":              "ICLN",
            "banking_regulation":   "KBE",
            "china_trade":          "FXI",
        },
        # Keyword → category mapping for matching Polymarket/Kalshi macro contracts
        "keyword_categories": {
            "fed_rates":          ["fed rate", "interest rate", "fomc", "powell"],
            "energy_policy":      ["oil", "opec", "energy policy", "gas price", "spr release"],
            "tech_regulation":    ["antitrust", "tech regulation", "section 230", "ai regulation"],
            "healthcare_policy":  ["healthcare", "medicare", "medicaid", "obamacare", "drug pricing"],
            "defense":            ["ukraine", "defense spending", "nato", "pentagon"],
            "climate":            ["climate", "carbon tax", "ev tax credit", "epa regulation"],
            "banking_regulation": ["bank regulation", "basel", "sec rule", "dodd-frank"],
            "china_trade":        ["china tariff", "taiwan", "chip export", "huawei"],
        },
        "position_limits": {
            "size_per_signal":  2000,
            "max_positions":    3,
            "hold_hours":       48,
            "take_profit_pct":  0.05,
            "stop_loss_pct":    0.03,
        },
    },
    "weather": {
        "tiers": {
            "tier_1_min_edge": 0.10, "tier_1_size": 2000,
            "tier_2_min_edge": 0.25, "tier_2_size": 3000,
            "tier_3_min_edge": 0.35, "tier_3_size": 5000,
        },
        "position_limits": {
            "max_positions":    3,
            "stop_loss_pct":    0.50,        # 50% of position value
            # No hold limit — natural resolution at contract expiry
        },
        # For v1: BHN only has OWM data for operator-home (Laguna Niguel area).
        # Future expansion: add NOAA + multi-location OWM for wider coverage.
        "regions_supported": ["operator-home"],
        "variables_supported": ["precipitation"],
    },
    # Master switch — flip to true via rules.json once Kalshi+Polymarket
    # auth keys are provisioned and operator wants weather-contract execution
    "live_execution_enabled": False,
}


# ─────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────────────────────

def http_get(url: str, params: Optional[dict] = None, timeout: int = 10) -> Optional[Any]:
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 429:
                logger.warning(f"Rate-limit on {url}; backing off")
                time.sleep(2 ** attempt + 3)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.debug(f"GET {url} attempt {attempt+1}/3 failed: {e}")
            time.sleep(2 ** attempt)
    return None


# ─────────────────────────────────────────────────────────────────────────
# Polymarket polling (macro events + weather contracts)
# ─────────────────────────────────────────────────────────────────────────

def fetch_polymarket_markets(active_only: bool = True, limit: int = 200) -> list[dict]:
    params = {"limit": limit, "active": "true" if active_only else "false",
              "closed": "false"}
    data = http_get(f"{POLYMARKET_BASE}/markets", params)
    if not isinstance(data, list):
        return []
    return data


def fetch_kalshi_markets(limit: int = 200) -> list[dict]:
    # Kalshi public endpoint; auth-free for market list + prices
    data = http_get(f"{KALSHI_BASE}/markets", {"limit": limit, "status": "open"})
    if not isinstance(data, dict):
        return []
    return data.get("markets", [])


# ─────────────────────────────────────────────────────────────────────────
# Macro-event signal generation
# ─────────────────────────────────────────────────────────────────────────

def categorize_macro_market(title: str, keyword_categories: dict) -> Optional[str]:
    """Match contract title against keyword categories; return category or None."""
    tl = title.lower()
    for category, keywords in keyword_categories.items():
        if any(kw in tl for kw in keywords):
            return category
    return None


def get_prior_odds(contract_id: str, exchange: str, minutes_ago: int) -> Optional[Decimal]:
    """
    Query weather_contract_prices for the prior-window snapshot. Note: macro
    contracts also written to this table (it's the time-series store for all
    prediction-market price snapshots, despite the table name).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT implied_probability FROM weather_contract_prices
                WHERE contract_id = %s AND exchange = %s
                  AND captured_at <= %s
                ORDER BY captured_at DESC LIMIT 1
                """,
                (contract_id, exchange, cutoff),
            )
            row = cur.fetchone()
            return Decimal(str(row[0])) if row else None


def snapshot_contract_price(exchange: str, contract_id: str, title: str,
                             yes_price: float, implied_prob: float,
                             resolution_date: Optional[date] = None,
                             region: Optional[str] = None, variable: Optional[str] = None,
                             raw: Optional[dict] = None) -> None:
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO weather_contract_prices
                    (exchange, contract_id, contract_title, implied_probability,
                     yes_price, no_price, resolution_date, region, variable, raw_payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (exchange, contract_id, title, implied_prob, yes_price,
                 (1.0 - yes_price) if yes_price else None,
                 resolution_date, region, variable,
                 json.dumps(raw) if raw else None),
            )


def process_macro_signals(rules: dict, allocation: Decimal) -> None:
    """
    Poll Polymarket + Kalshi for macro-event contracts. Detect >10% odds move
    in last hour. Buy correlated sector ETF.
    """
    cfg = rules["macro_events"]
    move_min = Decimal(str(cfg["odds_move_pct_min"])) / 100  # convert to fraction
    window_min = int(cfg["odds_move_window_min"])
    sector_map = cfg["sector_mapping"]
    keyword_cats = cfg["keyword_categories"]
    limits = cfg["position_limits"]
    size = Decimal(str(limits["size_per_signal"]))
    max_positions = int(limits["max_positions"])

    open_trades = [t for t in tc.get_open_trades(STRATEGY_ID)
                   if (t.get("metadata") or {}).get("source") == "macro"]
    open_count = len(open_trades)

    poly = fetch_polymarket_markets()
    kalshi = fetch_kalshi_markets()
    logger.info(f"Macro poll: polymarket={len(poly)} kalshi={len(kalshi)}")

    candidates: list[tuple[str, Decimal, str, dict]] = []  # (category, move_size, etf, signal_data)

    # Polymarket
    for m in poly:
        title = m.get("question") or m.get("title") or ""
        category = categorize_macro_market(title, keyword_cats)
        if not category:
            continue
        yes_price = m.get("lastTradePrice") or m.get("outcomePrices", [None])[0]
        if yes_price is None:
            continue
        try:
            implied = float(yes_price)
        except (TypeError, ValueError):
            continue
        if implied <= 0 or implied >= 1:
            continue

        contract_id = str(m.get("id") or m.get("conditionId") or "")
        if not contract_id:
            continue

        # Snapshot for time-series
        snapshot_contract_price("polymarket", contract_id, title, implied, implied,
                                region=None, variable=None, raw=m)

        prior = get_prior_odds(contract_id, "polymarket", window_min)
        if prior is None:
            continue  # need history to detect move
        move = Decimal(str(implied)) - prior
        if abs(move) < move_min:
            continue

        etf = sector_map.get(category)
        if not etf:
            continue

        candidates.append((category, move, etf, {
            "exchange": "polymarket", "contract_id": contract_id,
            "title": title, "prior_prob": float(prior), "current_prob": implied,
            "move": float(move),
        }))

    # Kalshi (similar shape; uses "yes_bid" or "last_price" in cents 0-100)
    for m in kalshi:
        title = m.get("title") or ""
        category = categorize_macro_market(title, keyword_cats)
        if not category:
            continue
        # Kalshi prices in cents
        last = m.get("last_price") or m.get("yes_bid")
        if last is None:
            continue
        try:
            implied = float(last) / 100.0
        except (TypeError, ValueError):
            continue
        if implied <= 0 or implied >= 1:
            continue

        contract_id = str(m.get("ticker") or m.get("market_ticker") or "")
        if not contract_id:
            continue

        snapshot_contract_price("kalshi", contract_id, title, implied, implied,
                                region=None, variable=None, raw=m)

        prior = get_prior_odds(contract_id, "kalshi", window_min)
        if prior is None:
            continue
        move = Decimal(str(implied)) - prior
        if abs(move) < move_min:
            continue

        etf = sector_map.get(category)
        if not etf:
            continue

        candidates.append((category, move, etf, {
            "exchange": "kalshi", "contract_id": contract_id,
            "title": title, "prior_prob": float(prior), "current_prob": implied,
            "move": float(move),
        }))

    if not candidates:
        return

    # Sort by absolute move magnitude (biggest moves first)
    candidates.sort(key=lambda x: abs(x[1]), reverse=True)
    logger.info(f"Macro: {len(candidates)} contracts moved >{move_min*100:.0f}pp in {window_min}min")

    # Place trades (up to position limit)
    alpaca = tc.get_alpaca()
    held_etfs = {t["ticker"] for t in open_trades}

    for category, move, etf, sig_data in candidates:
        if open_count >= max_positions:
            break
        if etf in held_etfs:
            continue  # already holding this sector

        try:
            bar = alpaca.get_latest_trade(etf)
            price = Decimal(str(bar.price))
        except Exception as e:
            logger.warning(f"No price for {etf}: {e}")
            continue

        qty = int(size / price)
        if qty < 1:
            logger.info(f"Skip {etf}: position too small at ${price}")
            continue

        signal_id = tc.log_signal(
            STRATEGY_ID, etf, tc.Action.BUY,
            reason=f"macro event {category} ({sig_data['exchange']}): "
                   f"odds {sig_data['prior_prob']:.2%} → {sig_data['current_prob']:.2%}",
            value=float(move),
            acted_on=True,
            raw_payload={**sig_data, "category": category, "etf": etf,
                         "source": "macro"},
        )

        tp_price = price * (Decimal("1") + Decimal(str(limits["take_profit_pct"])))
        sl_price = price * (Decimal("1") - Decimal(str(limits["stop_loss_pct"])))

        try:
            order = tc.place_order(
                strategy_id=STRATEGY_ID,
                ticker=etf,
                side=tc.Action.BUY,
                qty=qty,
                order_type="market",
                signal_id=signal_id,
                stop_loss=sl_price,
                target=tp_price,
                metadata={
                    "source": "macro", "category": category,
                    "exchange": sig_data["exchange"],
                    "contract_id": sig_data["contract_id"],
                    "contract_title": sig_data["title"],
                    "prior_prob": sig_data["prior_prob"],
                    "current_prob": sig_data["current_prob"],
                    "odds_move": float(move),
                    "hold_hours": int(limits["hold_hours"]),
                },
            )
            logger.info(f"BUY {etf} {qty}@${price} (macro {category}, "
                        f"move={float(move):.2%}, order={order['alpaca_order_id']})")
            open_count += 1
            held_etfs.add(etf)
        except RuntimeError as e:
            logger.warning(f"BUY refused for {etf}: {e}")
            with tc.get_pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE signals_log SET acted_on=false WHERE id=%s",
                                (signal_id,))


# ─────────────────────────────────────────────────────────────────────────
# Weather contract — title parser
# ─────────────────────────────────────────────────────────────────────────

# Region keywords mapped to weather_snapshots.location_label (for now: operator-home only)
REGION_KEYWORDS = {
    "operator-home": ["laguna niguel", "orange county", "los angeles", "lax", "socal"],
}

VARIABLE_KEYWORDS = {
    "precipitation": ["rain", "precipitation", "snow", "inches", "snowfall"],
    "hurricane_track": ["hurricane", "tropical storm", "category"],
    "hurricane_intensity": ["category", "cat 3", "cat 4", "cat 5", "intensity"],
    "el_nino_la_nina": ["el nino", "la nina", "enso"],
}


def parse_weather_contract(title: str) -> Optional[dict]:
    """
    Parse a Kalshi/Polymarket weather-contract title into structured fields.
    Returns dict with region, variable, threshold (if numeric in title),
    and resolution_date (if extractable). None if not a weather contract or
    region not supported.
    """
    tl = title.lower()

    # Variable matching
    variable = None
    for v, kws in VARIABLE_KEYWORDS.items():
        if any(kw in tl for kw in kws):
            variable = v
            break
    if not variable:
        return None

    # Region matching (must be supported region for v1)
    region = None
    for r, kws in REGION_KEYWORDS.items():
        if any(kw in tl for kw in kws):
            region = r
            break
    if not region:
        return None

    # Threshold extraction (e.g., ">1 inch", ">0.5 inches")
    threshold = None
    m = re.search(r">\s*([\d.]+)\s*(inch|inches|°|°f|mm|mph)", tl)
    if m:
        try:
            threshold = float(m.group(1))
        except ValueError:
            pass

    # Resolution date extraction (e.g., "by May 15", "on May 15")
    resolution_date = None
    m = re.search(r"(?:by|on|before|through)\s+(\w+ \d+)(?:,?\s+(\d{4}))?", tl)
    if m:
        try:
            month_day = m.group(1)
            year = m.group(2) or str(date.today().year)
            resolution_date = datetime.strptime(f"{month_day} {year}", "%B %d %Y").date()
        except ValueError:
            pass

    return {
        "region": region,
        "variable": variable,
        "threshold": threshold,
        "resolution_date": resolution_date,
    }


# ─────────────────────────────────────────────────────────────────────────
# BHN forecast generation
# ─────────────────────────────────────────────────────────────────────────

def get_latest_weather_snapshot(region: str) -> Optional[dict]:
    """For v1: only 'operator-home' supported via weather_snapshots."""
    if region != "operator-home":
        return None
    with tc.get_pg_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM weather_snapshots
                WHERE location_label = 'operator-home'
                ORDER BY fetched_at DESC LIMIT 1
                """
            )
            row = cur.fetchone()
            return dict(row) if row else None


def predict_precipitation_probability(snapshot: dict, threshold_inches: Optional[float],
                                      target_date: Optional[date]) -> Optional[Decimal]:
    """
    Naive v1 BHN forecast: read OWM's precipitation_chance (0-1 scale).
    For threshold-based contracts (e.g. ">1 inch"), apply a heuristic
    discount when threshold > 0.5 inches (large rainfall less likely than
    "any rain at all").
    """
    prob = snapshot.get("precipitation_chance")
    if prob is None:
        return None
    prob = Decimal(str(prob))

    if threshold_inches and threshold_inches > 0.1:
        # Rough heuristic: probability of >X inches roughly halves per additional 0.5"
        threshold_factor = Decimal("1.0") / (Decimal("2") ** Decimal(str(threshold_inches / 0.5)))
        prob = prob * threshold_factor
        prob = max(min(prob, Decimal("1.0")), Decimal("0.0"))
    return prob


def generate_bhn_forecast(parsed: dict, target_date: Optional[date]) -> Optional[Decimal]:
    """Dispatch on variable. Returns BHN-side predicted probability or None."""
    variable = parsed["variable"]
    if variable != "precipitation":
        # Hurricane / ENSO not implemented in v1 — needs additional data sources
        logger.debug(f"v1: variable {variable} not yet supported for BHN forecast")
        return None
    snapshot = get_latest_weather_snapshot(parsed["region"])
    if not snapshot:
        return None
    return predict_precipitation_probability(snapshot, parsed.get("threshold"), target_date)


def record_bhn_forecast(parsed: dict, predicted_prob: Decimal, source: str = "bhn-owm-v1",
                        raw: Optional[dict] = None) -> int:
    with tc.get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO weather_forecasts
                    (target_date, region, variable, predicted_probability,
                     source_model, raw_payload)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                RETURNING id
                """,
                (parsed.get("resolution_date") or date.today(),
                 parsed["region"], parsed["variable"], predicted_prob,
                 source, json.dumps(raw) if raw else None),
            )
            return cur.fetchone()[0]


# ─────────────────────────────────────────────────────────────────────────
# Weather edge + tiered sizing
# ─────────────────────────────────────────────────────────────────────────

def get_position_tier(edge: Decimal, tiers: dict) -> Optional[tuple[str, Decimal, bool]]:
    """
    Returns (tier_name, size_dollars, requires_confirmation) or None if edge
    below all thresholds. Higher tiers = bigger size + (for tier 3) operator
    SMS confirmation.
    """
    abs_edge = abs(edge)
    if abs_edge >= Decimal(str(tiers["tier_3_min_edge"])):
        return ("tier_3", Decimal(str(tiers["tier_3_size"])), True)
    if abs_edge >= Decimal(str(tiers["tier_2_min_edge"])):
        return ("tier_2", Decimal(str(tiers["tier_2_size"])), False)
    if abs_edge >= Decimal(str(tiers["tier_1_min_edge"])):
        return ("tier_1", Decimal(str(tiers["tier_1_size"])), False)
    return None


# ─────────────────────────────────────────────────────────────────────────
# Weather signal processing
# ─────────────────────────────────────────────────────────────────────────

def process_weather_signals(rules: dict, live_execution: bool) -> None:
    cfg = rules["weather"]
    tiers = cfg["tiers"]
    limits = cfg["position_limits"]
    max_positions = int(limits["max_positions"])

    open_trades = [t for t in tc.get_open_trades(STRATEGY_ID)
                   if (t.get("metadata") or {}).get("source") == "weather"]
    open_count = len(open_trades)

    poly = fetch_polymarket_markets()
    kalshi = fetch_kalshi_markets()

    weather_contracts: list[tuple[str, dict, dict]] = []  # (exchange, parsed, market_data)

    for m in poly:
        title = m.get("question") or m.get("title") or ""
        parsed = parse_weather_contract(title)
        if not parsed:
            continue
        yes_price = m.get("lastTradePrice") or (m.get("outcomePrices", [None])[0])
        if yes_price is None:
            continue
        try:
            implied = float(yes_price)
        except (TypeError, ValueError):
            continue
        contract_id = str(m.get("id") or m.get("conditionId") or "")
        if not contract_id:
            continue
        weather_contracts.append(("polymarket", parsed, {
            "contract_id": contract_id, "title": title,
            "implied_probability": implied, "yes_price": yes_price,
            "raw": m,
        }))

    for m in kalshi:
        title = m.get("title") or ""
        parsed = parse_weather_contract(title)
        if not parsed:
            continue
        last = m.get("last_price") or m.get("yes_bid")
        if last is None:
            continue
        try:
            implied = float(last) / 100.0
        except (TypeError, ValueError):
            continue
        contract_id = str(m.get("ticker") or "")
        if not contract_id:
            continue
        weather_contracts.append(("kalshi", parsed, {
            "contract_id": contract_id, "title": title,
            "implied_probability": implied, "yes_price": implied,
            "raw": m,
        }))

    logger.info(f"Weather: {len(weather_contracts)} parseable contracts on supported regions")

    for exchange, parsed, mkt in weather_contracts:
        # Snapshot to time-series
        snapshot_contract_price(
            exchange, mkt["contract_id"], mkt["title"],
            mkt["yes_price"], mkt["implied_probability"],
            resolution_date=parsed.get("resolution_date"),
            region=parsed["region"], variable=parsed["variable"],
            raw=mkt["raw"],
        )

        # BHN forecast
        bhn_prob = generate_bhn_forecast(parsed, parsed.get("resolution_date"))
        if bhn_prob is None:
            continue
        # Record forecast
        record_bhn_forecast(parsed, bhn_prob, raw={"contract_id": mkt["contract_id"]})

        market_prob = Decimal(str(mkt["implied_probability"]))
        edge = bhn_prob - market_prob

        tier = get_position_tier(edge, tiers)
        if not tier:
            continue  # below tier_1 threshold; no signal
        tier_name, size_dollars, requires_confirmation = tier

        # Always record accuracy row (for self-improvement loop even if no position taken)
        position_side = "yes" if edge > 0 else "no"

        # Tier 3 requires operator confirmation — for v1 just flag in metadata
        # and skip auto-execution. Phase B adds the SMS confirmation workflow.
        should_execute = (
            live_execution
            and open_count < max_positions
            and not requires_confirmation
        )

        signal_id = tc.log_signal(
            STRATEGY_ID, mkt["contract_id"], tc.Action.BUY,
            reason=f"weather edge {tier_name}: bhn={float(bhn_prob):.2%} "
                   f"market={float(market_prob):.2%} edge={float(edge):.2%}",
            value=float(edge),
            acted_on=should_execute,
            raw_payload={
                "source": "weather",
                "exchange": exchange,
                "contract_id": mkt["contract_id"],
                "contract_title": mkt["title"],
                "region": parsed["region"],
                "variable": parsed["variable"],
                "resolution_date": (parsed.get("resolution_date") or date.today()).isoformat(),
                "bhn_predicted_probability": float(bhn_prob),
                "market_implied_probability": float(market_prob),
                "edge": float(edge),
                "tier": tier_name,
                "tier_size_dollars": float(size_dollars),
                "side": position_side,
                "requires_confirmation": requires_confirmation,
                "live_execution": live_execution,
                "would_execute": should_execute,
            },
        )

        # weather_model_accuracy row — track even in read-only mode
        with tc.get_pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO weather_model_accuracy
                        (contract_id, contract_title, region, variable,
                         bhn_predicted_probability, market_implied_probability,
                         edge, bhn_position_taken, bhn_position_value,
                         bhn_position_side)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (mkt["contract_id"], mkt["title"],
                     parsed["region"], parsed["variable"],
                     bhn_prob, market_prob, edge, should_execute,
                     size_dollars if should_execute else None,
                     position_side),
                )

        if not live_execution:
            logger.info(
                f"SIGNAL (read-only): {tier_name} edge={float(edge):.2%} "
                f"on '{mkt['title'][:60]}' — would buy ${size_dollars} {position_side}"
            )
            continue

        if requires_confirmation:
            logger.warning(
                f"TIER 3 SIGNAL (needs operator confirmation): edge={float(edge):.2%} "
                f"on '{mkt['title'][:60]}'. v1 skips auto-execution; Phase B adds SMS gate."
            )
            continue

        if open_count >= max_positions:
            logger.info(f"At weather position limit ({max_positions}); signal logged but not executed")
            continue

        # Phase B: actual execution path against Kalshi/Polymarket API
        # Requires exchange auth keys. v1 does not execute weather positions.
        logger.info(
            f"Phase B placeholder: would execute weather position when auth keys provisioned "
            f"({tier_name}, edge={float(edge):.2%})"
        )
        open_count += 1


# ─────────────────────────────────────────────────────────────────────────
# Exit logic for macro positions
# ─────────────────────────────────────────────────────────────────────────

def process_exits(rules: dict) -> None:
    """Exits handled here are macro-event ETF positions on Alpaca.
    Weather positions exit at contract resolution — handled by a separate
    settlement workflow that polls contract_id state on Kalshi/Polymarket
    and calls close_trade. v1 has no weather positions to close (read-only)."""
    open_trades = tc.get_open_trades(STRATEGY_ID)
    if not open_trades:
        return

    macro_trades = [t for t in open_trades
                    if (t.get("metadata") or {}).get("source") == "macro"]
    if not macro_trades:
        return

    cfg = rules["macro_events"]["position_limits"]
    hold_hours = int(cfg["hold_hours"])
    tp_pct = Decimal(str(cfg["take_profit_pct"]))
    sl_pct = Decimal(str(cfg["stop_loss_pct"]))
    now = datetime.now(timezone.utc)

    alpaca = tc.get_alpaca()
    for t in macro_trades:
        ticker = t["ticker"]
        entry_time = t["entry_time"]
        if entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)
        entry_price = Decimal(str(t["entry_price"]))

        age = now - entry_time
        if age >= timedelta(hours=hold_hours):
            _exit_at_market(t, ticker, tc.ExitReason.TIME_EXIT,
                            f"48h hold expired (age={age})")
            continue

        try:
            bar = alpaca.get_latest_trade(ticker)
            price = Decimal(str(bar.price))
        except Exception as e:
            logger.warning(f"Exit check {ticker}: price unavailable ({e})")
            continue

        tp_level = entry_price * (Decimal("1") + tp_pct)
        sl_level = entry_price * (Decimal("1") - sl_pct)
        if price >= tp_level:
            _exit_at_market(t, ticker, tc.ExitReason.TARGET,
                            f"price ${price} ≥ TP ${tp_level:.2f}")
            continue
        if price <= sl_level:
            _exit_at_market(t, ticker, tc.ExitReason.STOP_LOSS,
                            f"price ${price} ≤ SL ${sl_level:.2f}")
            continue


def _exit_at_market(trade: dict, ticker: str, reason: tc.ExitReason, reason_str: str) -> None:
    qty = int(trade["qty"])
    try:
        alpaca = tc.get_alpaca()
        order = alpaca.submit_order(symbol=ticker, qty=qty, side="sell",
                                    type="market", time_in_force="day")
        fill = Decimal(str(order.filled_avg_price or alpaca.get_latest_trade(ticker).price))
        result = tc.close_trade(
            trade_id=trade["id"],
            exit_price=fill,
            exit_reason=reason,
            alpaca_order_id_exit=order.id,
        )
        logger.info(f"EXIT {ticker} {qty}@${fill} ({reason.value}: {reason_str}) "
                    f"P&L=${result['pnl_dollar']} ({result['pnl_pct']:.2f}%)")
    except Exception as e:
        logger.error(f"Failed to close {ticker} (trade_id={trade['id']}): {e}")


# ─────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────

def main() -> int:
    # Only run during market hours for macro-event ETF execution path.
    # Weather signal capture happens regardless (BHN forecasts + market price
    # snapshots are valuable as a time-series even when markets are closed).
    allowed, reason = tc.should_run(STRATEGY_ID, requires_market_open=False)
    if not allowed:
        logger.info(f"Skipping run: {reason}")
        return 0

    rules = tc.get_strategy_rules(STRATEGY_ID) or {}
    # Deep-merge defaults
    rules = {**DEFAULTS, **rules}
    for k in ("macro_events", "weather"):
        rules[k] = {**DEFAULTS[k], **rules.get(k, {})}
        for sub in ("position_limits",):
            if sub in DEFAULTS[k]:
                rules[k][sub] = {**DEFAULTS[k][sub], **rules[k].get(sub, {})}

    live_execution = bool(rules.get("live_execution_enabled", False))
    allocation = tc.get_rls_capital_allocation(STRATEGY_ID)
    if allocation <= 0:
        logger.warning(f"Allocation is ${allocation} — refusing to run")
        return 1

    logger.info(
        f"=== {STRATEGY_ID} cycle start (allocation=${allocation}, "
        f"live_execution={live_execution}) ==="
    )

    with tc.pg_advisory_lock(abs(hash(STRATEGY_ID)) % (2**31)):
        try:
            with tc.get_pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE trading_strategies SET last_run_at = NOW() WHERE id = %s",
                        (STRATEGY_ID,),
                    )

            # Process macro exits first (free capacity)
            if tc.is_market_open():
                process_exits(rules)
                process_macro_signals(rules, allocation)
            else:
                logger.info("Market closed — skipping macro exits + entries")

            # Weather signals always processed (off-hours capture is useful
            # for the time-series + accuracy ledger)
            process_weather_signals(rules, live_execution)

        except Exception:
            logger.exception(f"{STRATEGY_ID} cycle failed")
            tc.update_strategy_status(STRATEGY_ID, "error",
                                      "exception in main loop (see log)")
            return 1

    logger.info(f"=== {STRATEGY_ID} cycle end ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
