#!/usr/bin/env python3
"""
validate_rules.py — Pre-rsync gate for rules.json.

Four layers of validation, in order:

1. **JSON Schema** (via rules_schema.SCHEMA) — structural correctness.
   Catches typos, wrong types, out-of-physical-bound values. Failure here
   is a hard error; later layers are skipped because they assume
   schema-valid input.

2. **Cross-field invariants** — things JSON Schema can't express within a
   single object. e.g. short_period < long_period, tier_1_edge < tier_2_edge.
   Failure here is a hard error — these are impossible configs that would
   break the strategy at runtime.

2.5. **Operator safe bounds** — tighter than schema's physical bounds.
   Hard-rejects any value outside the operator's stated risk tolerance
   for the new execution_limits + system.broker fields, plus cross-strategy
   invariants (sum of per-strat daily_loss_limit ≤ portfolio cap; paper_mode
   vs per-strategy alpaca_base_url consistency; enabled strategies must
   have a populated broker block). Failure here is a hard error.

3. **Business-intent warnings** — "this is technically legal but probably
   not what you wanted." e.g. stop_loss_pct = 0.50 (too wide), bollinger
   period = 5 (too tight), daily_loss > weekly_loss (inverted limits).
   Warnings don't fail the validator by default; --strict promotes them
   to errors so CI can enforce them.

Designed to run as the LA-side gate before rsync to NJ:
    python3 validate_rules.py /etc/bhn/rules.json && \
        rsync -avz /etc/bhn/rules.json nj:/etc/bhn/rules.json && \
        ssh nj 'systemctl reload bhn-strategy-runner@\*'

Exit codes:
    0   clean (or warnings without --strict)
    1   errors present (or warnings with --strict)
    2   bad invocation / file not found

CLI:
    python3 validate_rules.py rules.json
    python3 validate_rules.py rules.json --strict
    python3 validate_rules.py rules.json --schema-only
    python3 validate_rules.py rules.json --format json
    python3 validate_rules.py --generate-example > rules.example.json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import jsonschema
    from jsonschema import Draft202012Validator
except ImportError:
    print("ERROR: jsonschema not installed. Install with: pip install jsonschema",
          file=sys.stderr)
    sys.exit(2)

import rules_schema


# ─────────────────────────────────────────────────────────────────────────
# Result container
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    errors:   list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def is_clean(self) -> bool:
        return not self.errors and not self.warnings

    def has_errors(self) -> bool:
        return bool(self.errors)

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def merge(self, other: "ValidationResult") -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)


# ─────────────────────────────────────────────────────────────────────────
# Layer 1: JSON Schema
# ─────────────────────────────────────────────────────────────────────────

def validate_schema(rules: dict) -> ValidationResult:
    result = ValidationResult()
    schema = rules_schema.get_schema()
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(rules), key=lambda e: list(e.absolute_path))
    for e in errors:
        path = "/".join(str(p) for p in e.absolute_path) or "(root)"
        result.error(f"schema: {path}: {e.message}")
    return result


# ─────────────────────────────────────────────────────────────────────────
# Layer 2: Cross-field invariants
# ─────────────────────────────────────────────────────────────────────────

def _g(d: dict, *path, default=None):
    """Safe nested dict get. _g(d, 'a', 'b') == d.get('a', {}).get('b')."""
    cur = d
    for k in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def validate_cross_field(rules: dict) -> ValidationResult:
    result = ValidationResult()

    # System: loss-limit hierarchy must escalate
    daily = _g(rules, "system", "circuit_breakers", "daily_loss_limit_pct")
    weekly = _g(rules, "system", "circuit_breakers", "weekly_loss_limit_pct")
    drawdown = _g(rules, "system", "circuit_breakers", "drawdown_limit_pct")
    if daily is not None and weekly is not None and weekly <= daily:
        result.error(
            f"system.circuit_breakers: weekly_loss_limit_pct ({weekly}) must be "
            f"greater than daily_loss_limit_pct ({daily}) — weekly is the wider gate"
        )
    if weekly is not None and drawdown is not None and drawdown <= weekly:
        result.error(
            f"system.circuit_breakers: drawdown_limit_pct ({drawdown}) must be "
            f"greater than weekly_loss_limit_pct ({weekly}) — drawdown is the widest gate"
        )

    # Strategy 3 (scalp): max_hold_minutes vs trading session
    s3_hold = _g(rules, "strat_3_scalp", "position_limits", "max_hold_minutes")
    if s3_hold is not None and s3_hold > 390:
        result.error(
            f"strat_3_scalp.position_limits.max_hold_minutes ({s3_hold}) > 390 — "
            f"a single RTH session is ~390min; longer makes EOD flatten redundant"
        )

    # Strategy 4 (momentum): short < long, lookback covers long+buffer
    s4 = rules.get("strat_4_momentum", {}) or {}
    s4_short = _g(s4, "crossover", "short_period")
    s4_long = _g(s4, "crossover", "long_period")
    s4_lookback = _g(s4, "crossover", "lookback_days")
    if s4_short is not None and s4_long is not None and s4_short >= s4_long:
        result.error(
            f"strat_4_momentum.crossover: short_period ({s4_short}) must be "
            f"strictly less than long_period ({s4_long})"
        )
    if s4_long is not None and s4_lookback is not None and s4_lookback < s4_long + 20:
        result.error(
            f"strat_4_momentum.crossover.lookback_days ({s4_lookback}) must be "
            f">= long_period+20 ({s4_long + 20}) so the SMA has clean data"
        )

    # Strategy 5: tier edges and sizes must escalate
    tiers = _g(rules, "strat_5_pred_mkt", "weather", "tiers") or {}
    t1e = tiers.get("tier_1_min_edge")
    t2e = tiers.get("tier_2_min_edge")
    t3e = tiers.get("tier_3_min_edge")
    if t1e is not None and t2e is not None and t2e <= t1e:
        result.error(
            f"strat_5_pred_mkt.weather.tiers: tier_2_min_edge ({t2e}) must be "
            f"greater than tier_1_min_edge ({t1e})"
        )
    if t2e is not None and t3e is not None and t3e <= t2e:
        result.error(
            f"strat_5_pred_mkt.weather.tiers: tier_3_min_edge ({t3e}) must be "
            f"greater than tier_2_min_edge ({t2e})"
        )
    t1s = tiers.get("tier_1_size")
    t2s = tiers.get("tier_2_size")
    t3s = tiers.get("tier_3_size")
    if t1s is not None and t2s is not None and t2s < t1s:
        result.error(
            f"strat_5_pred_mkt.weather.tiers: tier_2_size ({t2s}) must be "
            f">= tier_1_size ({t1s}) — bigger edge should never get smaller size"
        )
    if t2s is not None and t3s is not None and t3s < t2s:
        result.error(
            f"strat_5_pred_mkt.weather.tiers: tier_3_size ({t3s}) must be "
            f">= tier_2_size ({t2s})"
        )

    # Strategy 5: regions/variables must be currently supported
    SUPPORTED_REGIONS = {"operator-home"}
    SUPPORTED_VARS = {"precipitation"}
    regions = _g(rules, "strat_5_pred_mkt", "weather", "regions_supported") or []
    for r in regions:
        if r not in SUPPORTED_REGIONS:
            result.error(
                f"strat_5_pred_mkt.weather.regions_supported: '{r}' not yet "
                f"supported by BHN forecast data (v1 supports: "
                f"{sorted(SUPPORTED_REGIONS)})"
            )
    variables = _g(rules, "strat_5_pred_mkt", "weather", "variables_supported") or []
    for v in variables:
        if v not in SUPPORTED_VARS:
            result.error(
                f"strat_5_pred_mkt.weather.variables_supported: '{v}' not yet "
                f"supported by BHN forecast logic (v1 supports: "
                f"{sorted(SUPPORTED_VARS)})"
            )

    # Strategy 5: macro keyword_categories must align with sector_mapping keys
    sector_map = _g(rules, "strat_5_pred_mkt", "macro_events", "sector_mapping") or {}
    kw_map = _g(rules, "strat_5_pred_mkt", "macro_events", "keyword_categories") or {}
    sector_keys = set(sector_map.keys())
    kw_keys = set(kw_map.keys())
    only_in_kw = kw_keys - sector_keys
    only_in_sector = sector_keys - kw_keys
    if only_in_kw:
        result.error(
            f"strat_5_pred_mkt.macro_events: keyword_categories has categories "
            f"with no sector_mapping entry: {sorted(only_in_kw)}"
        )
    if only_in_sector:
        result.error(
            f"strat_5_pred_mkt.macro_events: sector_mapping has categories "
            f"with no keyword_categories entry: {sorted(only_in_sector)} "
            f"(would never fire — orphaned)"
        )

    return result


# ─────────────────────────────────────────────────────────────────────────
# Layer 2.5: Operator safe-bound hard rejects
#
# These are tighter than the schema's physical bounds — they represent
# the operator's stated risk tolerance and reject fat-finger config
# inflation at deploy time. Failure here is a hard error.
# ─────────────────────────────────────────────────────────────────────────

_PAPER_URL = "https://paper-api.alpaca.markets/v2"
_LIVE_URL  = "https://api.alpaca.markets/v2"


def _error_in_range(result: ValidationResult, value, low, high, name: str, expected: str):
    """Emit a HARD error if value falls outside [low, high]."""
    if value is None:
        return
    if value < low or value > high:
        result.error(f"operator-bounds: {name}={value} outside accept range {expected}")


def validate_operator_bounds(rules: dict) -> ValidationResult:
    """Hard-reject any value outside the operator-specified safe range."""
    result = ValidationResult()

    # ----- system.broker portfolio caps -----
    broker = _g(rules, "system", "broker") or {}
    _error_in_range(result, broker.get("total_allocation"),
                    10_000, 200_000,
                    "system.broker.total_allocation", "[$10,000, $200,000]")
    _error_in_range(result, broker.get("max_portfolio_daily_loss"),
                    100, 10_000,
                    "system.broker.max_portfolio_daily_loss", "[$100, $10,000]")
    _error_in_range(result, broker.get("max_portfolio_drawdown"),
                    0.01, 0.25,
                    "system.broker.max_portfolio_drawdown", "[0.01, 0.25] (1-25%)")
    _error_in_range(result, broker.get("max_overnight_positions"),
                    0, 20,
                    "system.broker.max_overnight_positions", "[0, 20]")
    _error_in_range(result, broker.get("force_close_if_down_pct"),
                    0.01, 0.20,
                    "system.broker.force_close_if_down_pct", "[0.01, 0.20] (1-20%)")
    _error_in_range(result, broker.get("killswitch_drawdown_pct"),
                    0.01, 0.25,
                    "system.broker.killswitch_drawdown_pct", "[0.01, 0.25] (1-25%)")

    # ----- per-strategy execution_limits bounds -----
    for sid in rules_schema.STRATEGY_SCHEMAS:
        block = rules.get(sid) or {}
        if not isinstance(block, dict):
            continue
        el = block.get("execution_limits") or {}
        if not el:
            continue
        prefix = f"{sid}.execution_limits"
        _error_in_range(result, el.get("max_position_size"),
                        100, 10_000,
                        f"{prefix}.max_position_size", "[$100, $10,000]")
        _error_in_range(result, el.get("daily_loss_limit"),
                        100, 5_000,
                        f"{prefix}.daily_loss_limit", "[$100, $5,000]")
        _error_in_range(result, el.get("max_trades_per_day"),
                        1, 100,
                        f"{prefix}.max_trades_per_day", "[1, 100]")
        _error_in_range(result, el.get("stop_loss_pct"),
                        0.005, 0.30,
                        f"{prefix}.stop_loss_pct", "[0.005, 0.30] (0.5-30%)")
        _error_in_range(result, el.get("profit_target_pct"),
                        0.005, 0.50,
                        f"{prefix}.profit_target_pct", "[0.005, 0.50] (0.5-50%)")
        _error_in_range(result, el.get("max_hold_days"),
                        1, 365,
                        f"{prefix}.max_hold_days", "[1, 365]")
        _error_in_range(result, el.get("close_before_earnings_days"),
                        1, 30,
                        f"{prefix}.close_before_earnings_days", "[1, 30]")

    # ----- cross-field: per-strategy daily-loss sum ≤ portfolio cap -----
    portfolio_cap = broker.get("max_portfolio_daily_loss")
    if portfolio_cap is not None:
        strat_total = 0.0
        for sid in rules_schema.STRATEGY_SCHEMAS:
            block = rules.get(sid) or {}
            if not isinstance(block, dict):
                continue
            # Only count enabled strategies — disabled ones can't lose money.
            if block.get("enabled") is not True:
                continue
            v = (block.get("execution_limits") or {}).get("daily_loss_limit")
            if isinstance(v, (int, float)):
                strat_total += v
        if strat_total > portfolio_cap:
            result.error(
                f"system.broker.max_portfolio_daily_loss=${portfolio_cap} but "
                f"sum of enabled strategies' daily_loss_limit=${strat_total:g} — "
                f"strategies can collectively breach the portfolio cap"
            )

    # ----- cross-field: paper_mode ↔ per-strategy broker URL consistency -----
    paper_mode = broker.get("paper_mode")
    if paper_mode is not None:
        for sid in rules_schema.STRATEGY_SCHEMAS:
            block = rules.get(sid) or {}
            if not isinstance(block, dict):
                continue
            sb = block.get("broker") or {}
            base_url = sb.get("alpaca_base_url")
            if base_url is None:
                continue
            if paper_mode is True and base_url == _LIVE_URL:
                result.error(
                    f"system.broker.paper_mode=true but {sid}.broker.alpaca_base_url "
                    f"= live endpoint — contradictory. Paper mode requires every "
                    f"strategy on the paper endpoint."
                )
            if paper_mode is False and base_url == _PAPER_URL:
                # Only flag enabled strategies — disabled paper URLs are harmless.
                if block.get("enabled") is True:
                    result.error(
                        f"system.broker.paper_mode=false but {sid}.broker.alpaca_base_url "
                        f"= paper endpoint AND {sid}.enabled=true — strategy is enabled "
                        f"in live mode but still pointed at paper account."
                    )

    # ----- cross-field: enabled strategies must have a populated broker block -----
    for sid in rules_schema.STRATEGY_SCHEMAS:
        block = rules.get(sid) or {}
        if not isinstance(block, dict):
            continue
        if block.get("enabled") is not True:
            continue
        sb = block.get("broker") or {}
        missing = [k for k in ("alpaca_key_id", "alpaca_secret", "alpaca_base_url")
                   if not sb.get(k)]
        if missing:
            result.error(
                f"{sid}.enabled=true but broker subblock is missing/empty: {missing}. "
                f"Enabled strategies must declare their dedicated Alpaca account."
            )

    # ----- cross-field: paper_mode=true should mean no live_mode_approved=true -----
    if paper_mode is True:
        approved = []
        for sid in rules_schema.STRATEGY_SCHEMAS:
            block = rules.get(sid) or {}
            if isinstance(block, dict) and block.get("live_mode_approved") is True:
                approved.append(sid)
        if approved:
            result.error(
                f"system.broker.paper_mode=true but {len(approved)} strategy(ies) "
                f"have live_mode_approved=true: {approved}. Either set paper_mode=false "
                f"to actually go live, or revert the per-strategy approvals to false."
            )

    return result


# ─────────────────────────────────────────────────────────────────────────
# Layer 3: Business-intent warnings
# ─────────────────────────────────────────────────────────────────────────

def _warn_in_range(result: ValidationResult, value, low, high, name: str, expected: str):
    """Emit a warning if value falls outside [low, high]."""
    if value is None:
        return
    if value < low or value > high:
        result.warn(f"{name}={value} outside expected range {expected}")


def validate_business(rules: dict) -> ValidationResult:
    result = ValidationResult()

    # System
    daily = _g(rules, "system", "circuit_breakers", "daily_loss_limit_pct")
    _warn_in_range(result, daily, 0.01, 0.10,
                   "system.circuit_breakers.daily_loss_limit_pct",
                   "[0.01, 0.10] (1-10%)")
    weekly = _g(rules, "system", "circuit_breakers", "weekly_loss_limit_pct")
    _warn_in_range(result, weekly, 0.03, 0.20,
                   "system.circuit_breakers.weekly_loss_limit_pct",
                   "[0.03, 0.20] (3-20%)")
    drawdown = _g(rules, "system", "circuit_breakers", "drawdown_limit_pct")
    _warn_in_range(result, drawdown, 0.05, 0.30,
                   "system.circuit_breakers.drawdown_limit_pct",
                   "[0.05, 0.30] (5-30%)")

    recon_interval = _g(rules, "system", "reconciliation", "interval_seconds")
    if recon_interval is not None and recon_interval < 60:
        result.warn(
            f"system.reconciliation.interval_seconds={recon_interval} < 60 — "
            f"will hit PG advisory locks every cycle, may impact strategy throughput"
        )
    if recon_interval is not None and recon_interval > 900:
        result.warn(
            f"system.reconciliation.interval_seconds={recon_interval} > 900 — "
            f"divergence detection latency exceeds 15min, increases blast radius"
        )

    halt_on_mismatch = _g(rules, "system", "reconciliation", "halt_on_mismatch")
    if halt_on_mismatch is False:
        result.warn(
            "system.reconciliation.halt_on_mismatch=false — debug-only mode; "
            "divergence will be logged but framework will NOT halt"
        )

    # Per-strategy live-mode audit
    for sid in rules_schema.STRATEGY_SCHEMAS:
        block = rules.get(sid)
        if isinstance(block, dict) and block.get("live_mode_approved") is True:
            result.warn(
                f"{sid}.live_mode_approved=true — strategy is approved for LIVE trading. "
                f"Confirm this is intentional (paper-only is the safe default)."
            )

    # Strategy 1: Congress
    s1 = rules.get("strat_1_congress", {}) or {}
    _warn_in_range(result, s1.get("min_transaction_usd"), 5_000, 1_000_000,
                   "strat_1_congress.min_transaction_usd",
                   "[$5k, $1M] (smaller = noise, bigger = no signal)")
    _warn_in_range(result, s1.get("max_days_after_disclosure"), 1, 7,
                   "strat_1_congress.max_days_after_disclosure",
                   "[1, 7] days (stale signal beyond a week)")
    _warn_in_range(result, s1.get("stop_loss_pct"), 0.05, 0.25,
                   "strat_1_congress.stop_loss_pct", "[5%, 25%]")
    _warn_in_range(result, s1.get("hold_days"), 7, 90,
                   "strat_1_congress.hold_days", "[7, 90] days")

    # Strategy 2: Value
    s2 = rules.get("strat_2_value", {}) or {}
    sf = s2.get("screener_filters", {}) or {}
    _warn_in_range(result, sf.get("pe_max"), 5, 25,
                   "strat_2_value.screener_filters.pe_max",
                   "[5, 25] (>25 isn't 'deep value')")
    _warn_in_range(result, sf.get("pb_max"), 0.5, 3.0,
                   "strat_2_value.screener_filters.pb_max", "[0.5, 3.0]")
    _warn_in_range(result, sf.get("de_max"), 0.0, 1.0,
                   "strat_2_value.screener_filters.de_max",
                   "[0, 1] (>1 = not conservative leverage)")
    _warn_in_range(result, sf.get("roe_min"), 5, 30,
                   "strat_2_value.screener_filters.roe_min", "[5%, 30%]")
    ex = s2.get("exit", {}) or {}
    _warn_in_range(result, ex.get("hold_days"), 30, 365,
                   "strat_2_value.exit.hold_days", "[30, 365] days")
    _warn_in_range(result, ex.get("stop_loss_pct"), 0.10, 0.30,
                   "strat_2_value.exit.stop_loss_pct", "[10%, 30%]")

    # Strategy 3: Scalp
    s3 = rules.get("strat_3_scalp", {}) or {}
    bb = s3.get("bollinger", {}) or {}
    _warn_in_range(result, bb.get("period"), 10, 50,
                   "strat_3_scalp.bollinger.period", "[10, 50] bars")
    _warn_in_range(result, bb.get("stddev"), 1.5, 3.0,
                   "strat_3_scalp.bollinger.stddev", "[1.5, 3.0] σ")
    pl3 = s3.get("position_limits", {}) or {}
    sl3 = pl3.get("stop_loss_pct")
    if sl3 is not None and sl3 > 0.05:
        result.warn(
            f"strat_3_scalp.position_limits.stop_loss_pct={sl3} > 5% — wider "
            f"than typical scalp stops; tail risk per trade may exceed reversion edge"
        )
    _warn_in_range(result, pl3.get("max_hold_minutes"), 30, 390,
                   "strat_3_scalp.position_limits.max_hold_minutes",
                   "[30, 390] minutes")

    # Strategy 4: Momentum
    s4 = rules.get("strat_4_momentum", {}) or {}
    pl4 = s4.get("position_limits", {}) or {}
    sl4 = pl4.get("stop_loss_pct")
    ts4 = pl4.get("trailing_stop_pct")
    if sl4 is not None and ts4 is not None and ts4 > sl4 * 1.5:
        result.warn(
            f"strat_4_momentum: trailing_stop_pct ({ts4}) > 1.5× stop_loss_pct ({sl4}) — "
            f"trailing stop wider than hard stop may never trigger before SL"
        )
    _warn_in_range(result, sl4, 0.03, 0.20,
                   "strat_4_momentum.position_limits.stop_loss_pct", "[3%, 20%]")

    # Strategy 5: pred-market
    s5 = rules.get("strat_5_pred_mkt", {}) or {}
    if s5.get("live_execution_enabled") is True:
        # Cross-check that exchange auth env vars are documented; we can't
        # check env vars here, but flag for operator
        result.warn(
            "strat_5_pred_mkt.live_execution_enabled=true — Kalshi+Polymarket "
            "auth keys must be in /etc/bhn/trading.env on NJ. v1 strategy code "
            "still has a Phase B placeholder for actual weather-position execution; "
            "confirm placeholder is replaced before enabling."
        )
    macro_pl = _g(s5, "macro_events", "position_limits") or {}
    macro_size = macro_pl.get("size_per_signal")
    macro_max = macro_pl.get("max_positions")
    if macro_size is not None and macro_max is not None:
        macro_max_exposure = macro_size * macro_max
        # strat_5_pred_mkt allocation is $15k per memory
        if macro_max_exposure > 15_000:
            result.warn(
                f"strat_5_pred_mkt.macro_events: size×max = ${macro_max_exposure} "
                f"exceeds documented strategy allocation of $15,000"
            )

    return result


# ─────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────

def validate(rules: dict, schema_only: bool = False) -> ValidationResult:
    """Returns combined ValidationResult. Schema errors short-circuit
    further checks (they assume schema-valid input)."""
    result = ValidationResult()

    schema_result = validate_schema(rules)
    result.merge(schema_result)
    if schema_result.has_errors() or schema_only:
        return result

    result.merge(validate_cross_field(rules))
    result.merge(validate_operator_bounds(rules))
    result.merge(validate_business(rules))
    return result


# ─────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────

def format_text(result: ValidationResult, path: str) -> str:
    lines = [f"Validating: {path}"]
    if result.is_clean():
        lines.append("  ✓ Clean — no errors or warnings.")
        return "\n".join(lines)
    if result.errors:
        lines.append(f"  ✗ {len(result.errors)} error(s):")
        for e in result.errors:
            lines.append(f"    ERR  {e}")
    if result.warnings:
        lines.append(f"  ⚠ {len(result.warnings)} warning(s):")
        for w in result.warnings:
            lines.append(f"    WARN {w}")
    return "\n".join(lines)


def format_json(result: ValidationResult, path: str) -> str:
    return json.dumps({
        "path": path,
        "clean": result.is_clean(),
        "error_count": len(result.errors),
        "warning_count": len(result.warnings),
        "errors": result.errors,
        "warnings": result.warnings,
    }, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="BHN trading framework rules.json validator"
    )
    parser.add_argument("rules_path", nargs="?",
                        help="Path to rules.json (omit with --generate-example)")
    parser.add_argument("--strict", action="store_true",
                        help="Promote warnings to errors")
    parser.add_argument("--schema-only", action="store_true",
                        help="Skip cross-field + business checks; schema only")
    parser.add_argument("--format", choices=("text", "json"), default="text",
                        help="Output format")
    parser.add_argument("--generate-example", action="store_true",
                        help="Print the canonical example rules.json to stdout")
    parser.add_argument("--summary", action="store_true",
                        help="Print the schema coverage summary")
    args = parser.parse_args()

    if args.summary:
        print(rules_schema.schema_summary())
        return 0

    if args.generate_example:
        print(json.dumps(rules_schema.get_example_rules(), indent=2))
        return 0

    if not args.rules_path:
        parser.print_help()
        return 2

    path = Path(args.rules_path)
    if not path.is_file():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 2

    try:
        rules = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: {path}: invalid JSON: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"ERROR: cannot read {path}: {e}", file=sys.stderr)
        return 2

    if not isinstance(rules, dict):
        print(f"ERROR: {path}: top-level must be a JSON object, got {type(rules).__name__}",
              file=sys.stderr)
        return 1

    result = validate(rules, schema_only=args.schema_only)

    if args.format == "json":
        print(format_json(result, str(path)))
    else:
        print(format_text(result, str(path)))

    if result.errors:
        return 1
    if args.strict and result.warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
