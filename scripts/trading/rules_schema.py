#!/usr/bin/env python3
"""
rules_schema.py — JSON Schema for the BHN trading framework's rules.json.

This file is the single source of truth for what shape rules.json must take.
Consumed by:
  - validate_rules.py (CLI: validates a rules.json before it's rsync'd to NJ)
  - trading_core.load_rules() (runtime: rejects malformed rules on load)
  - bhn-rules-mutator workflow (HORIZON proposes changes against this schema)
  - operator-facing config-templates/rules.example.json (generated from EXAMPLE_RULES)

Schema philosophy:
  - Draft 2020-12 (most recent stable, well-supported by jsonschema)
  - `additionalProperties: false` everywhere — typos in keys SHOULD fail loud,
    not silently get ignored. If the framework adds a new field, the schema
    must add it too.
  - `required` is intentionally minimal — most fields fall back to hardcoded
    DEFAULTS in each strategy file. rules.json overrides; it doesn't have to
    be exhaustive.
  - Numeric bounds match operator-stated limits (e.g. stop_loss_pct ∈ [0, 1])
    rather than business intent (e.g. "stop loss should be < 0.10"). Business
    intent lives in validate_rules.py as warnings, not schema errors.

The top-level structure mirrors trading_strategies row ids:
  {
    "version":          "1.0",
    "updated_at":       "2026-05-12T22:00:00Z",
    "system":           {...},
    "strat_1_congress": {...},
    "strat_2_value":    {...},
    "strat_3_scalp":    {...},
    "strat_4_momentum": {...},
    "strat_5_pred_mkt": {...}
  }

Only `version` + `system` are required at the top level. Per-strategy
blocks are optional — when absent, the strategy uses its hardcoded
DEFAULTS dict entirely.
"""
from __future__ import annotations

import copy
from typing import Any


# ─────────────────────────────────────────────────────────────────────────
# Reusable subschemas
# ─────────────────────────────────────────────────────────────────────────

_PCT_FRAC = {"type": "number", "minimum": 0, "maximum": 1,
             "description": "Fractional percentage (0.05 = 5%)"}

_PCT_POS = {"type": "number", "exclusiveMinimum": 0, "maximum": 1,
            "description": "Positive fractional percentage (0 < x ≤ 1)"}

_POS_INT = {"type": "integer", "exclusiveMinimum": 0}
_NON_NEG_INT = {"type": "integer", "minimum": 0}
_POS_NUM = {"type": "number", "exclusiveMinimum": 0}
_NON_NEG_NUM = {"type": "number", "minimum": 0}

_TICKER = {"type": "string", "pattern": r"^[A-Z]{1,5}(\.[A-Z])?$",
           "description": "Uppercase ticker symbol, optional .X class suffix"}

_PROBABILITY = {"type": "number", "minimum": 0, "maximum": 1}

_STRATEGY_COMMON = {
    "enabled": {
        "type": "boolean",
        "description": "Master switch — strategy runs only when true",
    },
    "live_mode_approved": {
        "type": "boolean",
        "description": ("Per-strategy override. Even when set, paper-only "
                        "is still enforced if ALPACA_BASE_URL doesn't look "
                        "like the live endpoint."),
    },
    "cadence_seconds": {
        **_POS_INT,
        "description": "Override the trading_strategies.cadence_seconds for this strategy",
    },
}


# ─────────────────────────────────────────────────────────────────────────
# System-level schema (circuit breakers, cross-cutting flags)
# ─────────────────────────────────────────────────────────────────────────

SYSTEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "halted": {
            "type": "boolean",
            "description": ("Master kill flag. When true, every strategy run "
                            "exits before placing orders. Set via "
                            "master_killswitch.py — rules.json edits to this "
                            "field are accepted but the operator should use "
                            "the killswitch CLI for the full halt sequence."),
        },
        "circuit_breakers": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "daily_loss_limit_pct":   {**_PCT_FRAC, "description": "Default 0.05 (5%) — pause new entries"},
                "weekly_loss_limit_pct":  {**_PCT_FRAC, "description": "Default 0.10 (10%) — halt all"},
                "drawdown_limit_pct":     {**_PCT_FRAC, "description": "Default 0.15 (15%) — halt all"},
                "daily_turnover_multiple": {
                    **_POS_NUM,
                    "description": ("Max daily turnover dollars per strategy "
                                    "as a multiple of capital_allocation. "
                                    "Default 6x for scalp, 2x for everything else."),
                },
            },
        },
        "reconciliation": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "interval_seconds": {**_POS_INT, "description": "Default 300 (5min)"},
                "price_tolerance_dollars": {
                    **_NON_NEG_NUM,
                    "description": "Avg entry price comparison tolerance (default 0.005)",
                },
                "halt_on_mismatch": {
                    "type": "boolean",
                    "description": ("Default true. When false, mismatch is logged "
                                    "but does NOT halt — operator debug only."),
                },
            },
        },
        "twilio": {
            "type": "object",
            "additionalProperties": False,
            "description": "Twilio integration knobs — credentials still in env vars",
            "properties": {
                "operator_number_allowlist": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": ("Whitelist of phone numbers that may receive "
                                    "framework SMS (mirrors TWILIO_ALLOWED_FROM)."),
                },
                "rate_limit_per_hour": {
                    **_POS_INT,
                    "description": "Max SMS per hour (defense vs runaway loop). Default 20.",
                },
            },
        },
    },
}


# ─────────────────────────────────────────────────────────────────────────
# Strategy 1: Congress trade-following
# ─────────────────────────────────────────────────────────────────────────

STRAT_1_CONGRESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        **_STRATEGY_COMMON,
        "poll_interval_seconds": {**_POS_INT, "description": "Default 900 (15min)"},
        "min_transaction_usd": {
            **_POS_NUM,
            "description": ("Floor on disclosed transaction size — smaller "
                            "trades are noise. Default $10,000."),
        },
        "max_days_after_disclosure": {
            **_POS_INT,
            "description": "Skip disclosures older than N days. Default 2.",
        },
        "max_position_pct": {**_PCT_POS, "description": "Of strategy allocation per signal. Default 0.10"},
        "stop_loss_pct":    {**_PCT_POS, "description": "Default 0.15"},
        "hold_days":        {**_POS_INT, "description": "Default 30"},
        "seniority_hints": {
            "type": "object",
            "additionalProperties": {"type": "number"},
            "description": ("Override hardcoded seniority weight multipliers. "
                            "Keys = legislator name (lowercase, hyphen-separated), "
                            "values = weight multiplier (e.g. 'nancy-pelosi': 1.5)."),
        },
    },
}


# ─────────────────────────────────────────────────────────────────────────
# Strategy 2: Buffett deep-value
# ─────────────────────────────────────────────────────────────────────────

STRAT_2_VALUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        **_STRATEGY_COMMON,
        "screener_filters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "pe_max":             {**_POS_NUM, "description": "P/E ratio ceiling. Default 15"},
                "pb_max":             {**_POS_NUM, "description": "P/B ratio ceiling. Default 1.5"},
                "de_max":             {**_POS_NUM, "description": "Debt/equity ceiling. Default 0.5"},
                "roe_min":            {**_NON_NEG_NUM, "description": "ROE floor (as percentage, e.g. 15 = 15%). Default 15"},
                "decline_52w_min_pct": {**_PCT_FRAC, "description": "Min 52w decline from high. Default 0.10"},
            },
        },
        "exit": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "hold_days":      {**_POS_INT, "description": "Default 90"},
                "stop_loss_pct":  {**_PCT_POS, "description": "Default 0.20"},
                "pe_target_max":  {**_POS_NUM, "description": "Exit when P/E exceeds this. Default 25"},
            },
        },
        "position": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "max_position_pct": {**_PCT_POS, "description": "Default 0.10"},
                "max_positions":    {**_POS_INT, "description": "Default 6"},
            },
        },
        "earnings_blackout_days": {
            **_NON_NEG_INT,
            "description": "Skip names with earnings within N days. Default 7.",
        },
    },
}


# ─────────────────────────────────────────────────────────────────────────
# Strategy 3: Mean-reversion Bollinger scalp
# ─────────────────────────────────────────────────────────────────────────

STRAT_3_SCALP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        **_STRATEGY_COMMON,
        "bollinger": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "period":        {**_POS_INT, "description": "SMA period in bars. Default 20"},
                "stddev":        {**_POS_NUM, "description": "Band width in σ. Default 2.0"},
                "timeframe":     {"type": "string", "enum": ["1Min", "5Min", "15Min", "30Min", "1Hour"],
                                   "description": "Alpaca bar size. Default '5Min'"},
                "lookback_bars": {**_POS_INT, "description": "How many bars to pull (≥ period*2). Default 40"},
            },
        },
        "entry_filters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "min_price":           {**_POS_NUM, "description": "Default $10"},
                "min_avg_volume":      {**_POS_NUM, "description": "Daily avg volume floor. Default 1,000,000"},
                "volume_confirm_mult": {**_POS_NUM, "description": "Entry-bar vol vs trailing avg. Default 1.2"},
                "min_z_score":         {**_POS_NUM, "description": "How oversold required (σ). Default 2.0"},
            },
        },
        "position_limits": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "size_per_signal":  {**_POS_NUM, "description": "Default $2,000"},
                "max_positions":    {**_POS_INT, "description": "Default 5"},
                "max_hold_minutes": {**_POS_INT, "description": "Default 240 (4h)"},
                "stop_loss_pct":    {**_PCT_POS, "description": "Default 0.02 (2%)"},
                "eod_flatten_minutes_before_close": {**_POS_INT, "description": "Default 15"},
            },
        },
    },
}


# ─────────────────────────────────────────────────────────────────────────
# Strategy 4: SMA crossover momentum
# ─────────────────────────────────────────────────────────────────────────

STRAT_4_MOMENTUM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        **_STRATEGY_COMMON,
        "crossover": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "short_period":         {**_POS_INT, "description": "Default 50"},
                "long_period":          {**_POS_INT, "description": "Default 200"},
                "volume_confirm_mult":  {**_POS_NUM, "description": "Default 1.5"},
                "lookback_days":        {**_POS_INT, "description": "How many daily bars to pull (≥ long_period + 30). Default 230"},
            },
            "allOf": [{
                "if":   {"properties": {"short_period": {"type": "integer"},
                                         "long_period":  {"type": "integer"}}},
                "then": {"properties": {}},  # placeholder; cross-field rule lives in validator
            }],
        },
        "entry_filters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "min_price":      {**_POS_NUM, "description": "Default $20"},
                "min_avg_volume": {**_POS_NUM, "description": "Default 500,000"},
            },
        },
        "position_limits": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "size_per_signal":      {**_POS_NUM, "description": "Default $2,000"},
                "max_positions":        {**_POS_INT, "description": "Default 10"},
                "stop_loss_pct":        {**_PCT_POS, "description": "Default 0.10"},
                "trailing_stop_pct":    {**_PCT_POS, "description": "Default 0.08 (8% off highest close since entry)"},
            },
        },
        "universe_override": {
            "type": "array",
            "items": _TICKER,
            "uniqueItems": True,
            "description": "When present, replaces the hardcoded 45-name universe in strategy_momentum.py",
        },
    },
}


# ─────────────────────────────────────────────────────────────────────────
# Strategy 5: Prediction-market arbitrage
# ─────────────────────────────────────────────────────────────────────────

STRAT_5_PRED_MKT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        **_STRATEGY_COMMON,
        "live_execution_enabled": {
            "type": "boolean",
            "description": ("Master switch for the WEATHER side. When false (default), "
                            "weather signals log to signals_log with acted_on=false; "
                            "no real positions taken. Flip to true only after "
                            "Kalshi+Polymarket auth keys are provisioned. The MACRO "
                            "side always trades real Alpaca sector ETFs regardless."),
        },
        "macro_events": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "odds_move_pct_min":    {**_POS_NUM, "description": "Default 10 (percentage points in window)"},
                "odds_move_window_min": {**_POS_INT, "description": "Default 60 (minutes)"},
                "sector_mapping": {
                    "type": "object",
                    "patternProperties": {"^[a-z_]+$": _TICKER},
                    "additionalProperties": False,
                    "description": "Category → sector ETF ticker. e.g. {\"fed_rates\": \"XLF\"}",
                },
                "keyword_categories": {
                    "type": "object",
                    "patternProperties": {
                        "^[a-z_]+$": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 2},
                            "minItems": 1,
                        },
                    },
                    "additionalProperties": False,
                    "description": "Category → list of title keywords for matcher",
                },
                "position_limits": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "size_per_signal":  {**_POS_NUM},
                        "max_positions":    {**_POS_INT},
                        "hold_hours":       {**_POS_INT},
                        "take_profit_pct":  {**_PCT_POS},
                        "stop_loss_pct":    {**_PCT_POS},
                    },
                },
            },
        },
        "weather": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tiers": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "tier_1_min_edge": {**_PCT_FRAC, "description": "Default 0.10 (10pp edge)"},
                        "tier_1_size":     {**_POS_NUM, "description": "Default $2,000"},
                        "tier_2_min_edge": {**_PCT_FRAC, "description": "Default 0.25"},
                        "tier_2_size":     {**_POS_NUM, "description": "Default $3,000"},
                        "tier_3_min_edge": {**_PCT_FRAC, "description": "Default 0.35"},
                        "tier_3_size":     {**_POS_NUM, "description": "Default $5,000 (operator-confirmation required)"},
                    },
                },
                "position_limits": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "max_positions":  {**_POS_INT, "description": "Default 3"},
                        "stop_loss_pct":  {**_PCT_POS, "description": "Default 0.50 (50% of position value)"},
                    },
                },
                "regions_supported": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "uniqueItems": True,
                    "description": "v1: only 'operator-home' (Laguna Niguel OWM data)",
                },
                "variables_supported": {
                    "type": "array",
                    "items": {"type": "string",
                              "enum": ["precipitation", "hurricane_track",
                                       "hurricane_intensity", "el_nino_la_nina"]},
                    "uniqueItems": True,
                    "description": "v1: only 'precipitation'",
                },
            },
        },
    },
}


# ─────────────────────────────────────────────────────────────────────────
# Top-level schema
# ─────────────────────────────────────────────────────────────────────────

SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title":   "BHN Trading Framework rules.json",
    "type":    "object",
    "additionalProperties": False,
    "required": ["version", "system"],
    "properties": {
        "version":    {"type": "string", "pattern": r"^\d+\.\d+(\.\d+)?$"},
        "updated_at": {"type": "string", "format": "date-time"},
        "operator_note": {
            "type": "string",
            "maxLength": 500,
            "description": "Free-text note describing why this version exists",
        },
        "system":            SYSTEM_SCHEMA,
        "strat_1_congress":  STRAT_1_CONGRESS_SCHEMA,
        "strat_2_value":     STRAT_2_VALUE_SCHEMA,
        "strat_3_scalp":     STRAT_3_SCALP_SCHEMA,
        "strat_4_momentum":  STRAT_4_MOMENTUM_SCHEMA,
        "strat_5_pred_mkt":  STRAT_5_PRED_MKT_SCHEMA,
    },
}


# Map strategy id → its schema (for partial validation / mutator workflow)
STRATEGY_SCHEMAS: dict[str, dict[str, Any]] = {
    "strat_1_congress":  STRAT_1_CONGRESS_SCHEMA,
    "strat_2_value":     STRAT_2_VALUE_SCHEMA,
    "strat_3_scalp":     STRAT_3_SCALP_SCHEMA,
    "strat_4_momentum":  STRAT_4_MOMENTUM_SCHEMA,
    "strat_5_pred_mkt":  STRAT_5_PRED_MKT_SCHEMA,
}


# ─────────────────────────────────────────────────────────────────────────
# Example rules.json — populated with the conservative defaults documented
# in each strategy file. Used to generate config-templates/rules.example.json.
# ─────────────────────────────────────────────────────────────────────────

EXAMPLE_RULES: dict[str, Any] = {
    "version":    "1.0",
    "updated_at": "2026-05-12T00:00:00Z",
    "operator_note": "Initial conservative defaults. All strategies enabled but live_mode_approved=false until per-strategy operator sign-off.",
    "system": {
        "halted": False,
        "circuit_breakers": {
            "daily_loss_limit_pct":   0.05,
            "weekly_loss_limit_pct":  0.10,
            "drawdown_limit_pct":     0.15,
            "daily_turnover_multiple": 2.0,
        },
        "reconciliation": {
            "interval_seconds":        300,
            "price_tolerance_dollars": 0.005,
            "halt_on_mismatch":        True,
        },
        "twilio": {
            "rate_limit_per_hour": 20,
        },
    },
    "strat_1_congress": {
        "enabled": True,
        "live_mode_approved": False,
        "poll_interval_seconds":    900,
        "min_transaction_usd":      10_000,
        "max_days_after_disclosure": 2,
        "max_position_pct":          0.10,
        "stop_loss_pct":             0.15,
        "hold_days":                 30,
    },
    "strat_2_value": {
        "enabled": True,
        "live_mode_approved": False,
        "screener_filters": {
            "pe_max":              15,
            "pb_max":              1.5,
            "de_max":              0.5,
            "roe_min":             15,
            "decline_52w_min_pct": 0.10,
        },
        "exit": {
            "hold_days":     90,
            "stop_loss_pct": 0.20,
            "pe_target_max": 25,
        },
        "position": {
            "max_position_pct": 0.10,
            "max_positions":    6,
        },
        "earnings_blackout_days": 7,
    },
    "strat_3_scalp": {
        "enabled": True,
        "live_mode_approved": False,
        "bollinger": {
            "period":        20,
            "stddev":        2.0,
            "timeframe":     "5Min",
            "lookback_bars": 40,
        },
        "entry_filters": {
            "min_price":           10.0,
            "min_avg_volume":      1_000_000,
            "volume_confirm_mult": 1.2,
            "min_z_score":         2.0,
        },
        "position_limits": {
            "size_per_signal":  2000,
            "max_positions":    5,
            "max_hold_minutes": 240,
            "stop_loss_pct":    0.02,
            "eod_flatten_minutes_before_close": 15,
        },
    },
    "strat_4_momentum": {
        "enabled": True,
        "live_mode_approved": False,
        "crossover": {
            "short_period":         50,
            "long_period":          200,
            "volume_confirm_mult":  1.5,
            "lookback_days":        230,
        },
        "entry_filters": {
            "min_price":      20.0,
            "min_avg_volume": 500_000,
        },
        "position_limits": {
            "size_per_signal":   2000,
            "max_positions":     10,
            "stop_loss_pct":     0.10,
            "trailing_stop_pct": 0.08,
        },
    },
    "strat_5_pred_mkt": {
        "enabled": True,
        "live_mode_approved": False,
        "live_execution_enabled": False,
        "macro_events": {
            "odds_move_pct_min":    10.0,
            "odds_move_window_min": 60,
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
            "keyword_categories": {
                "fed_rates":          ["fed rate", "interest rate", "fomc", "powell"],
                "energy_policy":      ["oil", "opec", "energy policy", "gas price"],
                "tech_regulation":    ["antitrust", "tech regulation", "section 230"],
                "healthcare_policy":  ["healthcare", "medicare", "medicaid", "drug pricing"],
                "defense":            ["ukraine", "defense spending", "nato"],
                "climate":            ["climate", "carbon tax", "ev tax credit"],
                "banking_regulation": ["bank regulation", "basel", "sec rule"],
                "china_trade":        ["china tariff", "taiwan", "chip export"],
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
                "max_positions": 3,
                "stop_loss_pct": 0.50,
            },
            "regions_supported":   ["operator-home"],
            "variables_supported": ["precipitation"],
        },
    },
}


# ─────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────

def get_schema() -> dict[str, Any]:
    """Return a deep copy of the full top-level schema. Deep copy so callers
    can mutate freely without polluting the module-level constant."""
    return copy.deepcopy(SCHEMA)


def get_strategy_schema(strategy_id: str) -> dict[str, Any]:
    """Return a deep copy of the schema for a single strategy block.
    Raises KeyError on unknown strategy_id."""
    if strategy_id not in STRATEGY_SCHEMAS:
        raise KeyError(f"Unknown strategy id: {strategy_id}. "
                       f"Known: {sorted(STRATEGY_SCHEMAS.keys())}")
    return copy.deepcopy(STRATEGY_SCHEMAS[strategy_id])


def get_example_rules() -> dict[str, Any]:
    """Return a deep copy of the example rules.json. Used by:
       - validate_rules.py --generate-example
       - config-templates/rules.example.json regeneration
       - mutator workflow as a known-good baseline"""
    return copy.deepcopy(EXAMPLE_RULES)


def schema_summary() -> str:
    """Human-readable summary of what the schema covers. Used in CLI help
    output of validate_rules.py."""
    lines = ["BHN Trading Framework rules.json — schema coverage:"]
    lines.append(f"  Top-level required: {SCHEMA['required']}")
    lines.append(f"  System block:  {list(SYSTEM_SCHEMA['properties'].keys())}")
    for sid, sch in STRATEGY_SCHEMAS.items():
        keys = list(sch["properties"].keys())
        lines.append(f"  {sid:18s} {keys}")
    return "\n".join(lines)


if __name__ == "__main__":
    # When run directly: dump the schema as JSON. Useful for piping into
    # external validators (e.g. ajv on CI, or an IDE's JSON-schema plugin).
    import json
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "example":
        print(json.dumps(get_example_rules(), indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "summary":
        print(schema_summary())
    else:
        print(json.dumps(get_schema(), indent=2))
