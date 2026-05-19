#!/usr/bin/env python3
"""
trading_core.py — BHN trading framework shared infrastructure.

Used by all 5 strategy_*.py scripts + reconciliation_daemon.py + master_killswitch.py
+ daily_summary.py. Handles:

  * Alpaca connection (paper-only enforced)
  * PostgreSQL connection pool to LA via WG tunnel
  * Logger setup per strategy
  * rules.json loading (LA pushes; NJ consumes)
  * Strategy lifecycle (is_active, should_run, status updates)
  * Circuit breakers (3 tiers: daily, weekly, drawdown)
  * Signal + trade audit logging
  * Position sizing + order placement
  * Reconciliation primitives (Alpaca + NJ cache + LA PG)
  * VIX-aware reconciliation cadence
  * Alert routing (webhook → bhn-alert-router → ntfy + SMS)

Design principle: every public function commits to PG OR retries cleanly OR
halts loudly. No silent failures. No Anthropic API calls in the hot path.

Paper-only enforcement (defense in depth):
  1. ALPACA_BASE_URL must contain "paper-api" unless TRADING_LIVE_MODE=true
  2. trading_strategies.live_mode_approved must be true per-strategy
  3. trading_strategies.status='halted' for 'system' row halts everything

CLI for ad-hoc inspection + operator toggles:
  python3 trading_core.py status                 # print all strategies + state
  python3 trading_core.py reconcile              # one-shot reconcile, no daemon
  python3 trading_core.py health                 # check Alpaca + PG connectivity
  python3 trading_core.py enable STRAT2 [reason] # flip rules.json enabled=true
  python3 trading_core.py disable STRAT3 [reason]
  python3 trading_core.py sms 'ENABLE STRAT2'    # parse HORIZON SMS body
"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP, getcontext
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Optional

import psycopg2
import psycopg2.extras
import requests
from psycopg2.pool import ThreadedConnectionPool
import alpaca_trade_api as tradeapi


# ─────────────────────────────────────────────────────────────────────────
# Constants + enums
# ─────────────────────────────────────────────────────────────────────────

getcontext().prec = 18  # plenty for money math; Decimals quantize to 4dp on PG write

RULES_PATH = Path(os.environ.get("BHN_RULES_PATH", "/etc/bhn/rules.json"))
ALERT_WEBHOOK_PATH = Path("/etc/bhn/alert-webhook-url")
SYSTEM_STRATEGY_ID = "system"


class StrategyId(str, Enum):
    CONGRESS = "strat_1_congress"
    VALUE = "strat_2_value"
    MEAN_REVERSION = "strat_3_mean_reversion"
    MOMENTUM = "strat_4_momentum"
    PRED_MKT = "strat_5_pred_mkt"
    NASDAQ_LONG = "strat_6_nasdaq_long"
    NASDAQ_SHORT = "strat_7_nasdaq_short"
    SECTOR_ROTATION = "strat_8_sector_rotation"
    # 9-12 reserved for prior scaffolds (prediction-alpha, bollinger,
    # january-barometer) that are PARKED (status=inactive) per 2026-05-14
    # restructure. Strat 13 numbered intentionally to leave safe distance.
    RSI_INTRADAY = "strat_13_rsi_intraday"
    SYSTEM = SYSTEM_STRATEGY_ID


class Action(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class TradeStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class ExitReason(str, Enum):
    STOP_LOSS = "stop_loss"
    TARGET = "target"
    TRAILING_STOP = "trailing_stop"
    TIME_EXIT = "time_exit"
    MANUAL = "manual"
    BREAKER_HALT = "breaker_halt"
    SYSTEM_HALT = "system_halt"
    END_OF_DAY = "end_of_day"
    RECONCILE_CLOSE = "reconcile_close"


class BreakerType(str, Enum):
    DAILY_LOSS_5PCT = "daily_loss_5pct"
    WEEKLY_LOSS_10PCT = "weekly_loss_10pct"
    DRAWDOWN_15PCT = "drawdown_15pct"
    MANUAL_KILLSWITCH = "manual_killswitch"


class MismatchType(str, Enum):
    UNKNOWN_POSITION = "unknown_position"   # Alpaca has it; PG + NJ don't
    MISSING_POSITION = "missing_position"   # PG/NJ thinks open; Alpaca doesn't
    SYNC_DRIFT = "sync_drift"                # NJ cache disagrees with LA PG


class MarketPhase(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    OPEN_VOLATILE = "open_volatile"      # 9:30-9:45 ET or 15:45-16:00 ET
    HIGH_VIX = "high_vix"                # VIX > 25


@dataclass
class Mismatch:
    type: MismatchType
    strategy_id: Optional[str]
    ticker: str
    expected_qty: int
    actual_qty: int
    value_usd: Decimal
    details: dict = field(default_factory=dict)

    def to_json_dict(self) -> dict:
        d = asdict(self)
        d["value_usd"] = str(self.value_usd)
        return d


# ─────────────────────────────────────────────────────────────────────────
# Environment loader (validates once on module import)
# ─────────────────────────────────────────────────────────────────────────

_ENV: dict[str, Any] = {}

def _load_env() -> dict[str, Any]:
    global _ENV
    if _ENV:
        return _ENV

    required = [
        "ALPACA_API_KEY", "ALPACA_API_SECRET", "ALPACA_BASE_URL",
        "PG_HOST", "PG_PORT", "PG_DB", "PG_USER", "PG_PASSWORD",
    ]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {missing}")

    base_url = os.environ["ALPACA_BASE_URL"]
    live_mode = os.environ.get("TRADING_LIVE_MODE", "false").lower() == "true"
    if "paper-api" not in base_url and not live_mode:
        raise RuntimeError(
            f"Paper-only enforcement: ALPACA_BASE_URL={base_url!r} doesn't look "
            f"like paper trading and TRADING_LIVE_MODE != true. Refusing to start. "
            f"Set TRADING_LIVE_MODE=true explicitly to override (production deploy)."
        )

    _ENV = {
        "alpaca_key":      os.environ["ALPACA_API_KEY"],
        "alpaca_secret":   os.environ["ALPACA_API_SECRET"],
        "alpaca_url":      base_url,
        "live_mode":       live_mode,
        "pg_host":         os.environ["PG_HOST"],
        "pg_port":         int(os.environ["PG_PORT"]),
        "pg_db":           os.environ["PG_DB"],
        "pg_user":         os.environ["PG_USER"],
        "pg_pwd":          os.environ["PG_PASSWORD"],
        "quiver_key":      os.environ.get("QUIVER_API_KEY"),
        "fmp_key":         os.environ.get("FMP_API_KEY"),
        "polymarket_key":  os.environ.get("POLYMARKET_API_KEY"),
        "kalshi_key":      os.environ.get("KALSHI_API_KEY"),
        "log_level":       os.environ.get("LOG_LEVEL", "INFO"),
        "log_dir":         os.environ.get("LOG_DIR", "/var/log/bhn-trading"),
    }
    return _ENV


# ─────────────────────────────────────────────────────────────────────────
# Logger
# ─────────────────────────────────────────────────────────────────────────

_LOGGERS: dict[str, logging.Logger] = {}

def get_logger(strategy_id: str) -> logging.Logger:
    if strategy_id in _LOGGERS:
        return _LOGGERS[strategy_id]

    env = _load_env()
    log_dir = Path(env["log_dir"])
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"bhn.trading.{strategy_id}")
    logger.setLevel(env["log_level"])
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    fh = logging.handlers.TimedRotatingFileHandler(
        filename=log_dir / f"{strategy_id}.log",
        when="midnight",
        backupCount=14,
        utc=True,
    )
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    _LOGGERS[strategy_id] = logger
    return logger


# ─────────────────────────────────────────────────────────────────────────
# Alpaca client (singleton)
# ─────────────────────────────────────────────────────────────────────────

_ALPACA: Optional[tradeapi.REST] = None

def get_alpaca() -> tradeapi.REST:
    global _ALPACA
    if _ALPACA is None:
        env = _load_env()
        _ALPACA = tradeapi.REST(
            key_id=env["alpaca_key"],
            secret_key=env["alpaca_secret"],
            base_url=env["alpaca_url"],
            api_version="v2",
        )
        # Authentication + paper sanity check
        acct = _ALPACA.get_account()
        if acct.account_blocked or acct.trading_blocked:
            raise RuntimeError(
                f"Alpaca account is blocked: account_blocked={acct.account_blocked}, "
                f"trading_blocked={acct.trading_blocked}"
            )
    return _ALPACA


# ─────────────────────────────────────────────────────────────────────────
# PostgreSQL connection pool
# ─────────────────────────────────────────────────────────────────────────

_PG_POOL: Optional[ThreadedConnectionPool] = None

def _init_pg_pool() -> None:
    global _PG_POOL
    env = _load_env()
    _PG_POOL = ThreadedConnectionPool(
        minconn=1,
        maxconn=5,
        host=env["pg_host"],
        port=env["pg_port"],
        database=env["pg_db"],
        user=env["pg_user"],
        password=env["pg_pwd"],
        connect_timeout=5,
    )


@contextmanager
def get_pg_conn() -> Iterator[psycopg2.extensions.connection]:
    """Context manager — auto-commit on success, rollback on exception."""
    if _PG_POOL is None:
        _init_pg_pool()
    assert _PG_POOL is not None  # for type-checkers
    conn = _PG_POOL.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _PG_POOL.putconn(conn)


@contextmanager
def pg_advisory_lock(key: int) -> Iterator[None]:
    """
    Session-level advisory lock for race-condition prevention between
    strategy execution and reconciliation daemon. Both call this with the
    same key (e.g. hash of strategy_id) so only one runs at a time.
    """
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (key,))
            try:
                yield
            finally:
                cur.execute("SELECT pg_advisory_unlock(%s)", (key,))


# ─────────────────────────────────────────────────────────────────────────
# rules.json loader (file on disk; LA pushes, NJ consumes)
# ─────────────────────────────────────────────────────────────────────────

_RULES: Optional[dict] = None
_RULES_MTIME: float = 0.0

def load_rules() -> dict:
    """
    Load rules.json, caching by mtime. Re-reads file if modified since
    last load. If file missing, returns empty dict + WARNs (strategies
    should fall back to safe defaults or refuse to run).
    """
    global _RULES, _RULES_MTIME
    if not RULES_PATH.exists():
        if _RULES is None:
            logging.getLogger("bhn.trading.core").warning(
                f"rules.json not found at {RULES_PATH}; strategies will see empty rules"
            )
            _RULES = {}
        return _RULES

    current_mtime = RULES_PATH.stat().st_mtime
    if _RULES is None or current_mtime > _RULES_MTIME:
        with RULES_PATH.open() as f:
            _RULES = json.load(f)
        _RULES_MTIME = current_mtime
    return _RULES


def get_strategy_rules(strategy_id: str) -> dict:
    """Returns the per-strategy rules block from rules.json. Empty if absent.
    Strategy blocks live at the top level of rules.json (per rules_schema.py),
    not under a 'strategies' key."""
    rules = load_rules()
    block = rules.get(strategy_id)
    return block if isinstance(block, dict) else {}


# ─────────────────────────────────────────────────────────────────────────
# Per-strategy enable/disable + HORIZON SMS toggle
#
# Each strategy carries its own `enabled` boolean in rules.json. Operator
# toggles via SMS to HORIZON ("ENABLE STRAT2" / "DISABLE STRAT3"). HORIZON
# resolves the command to a `trading_core.py sms ...` invocation on LA,
# which atomically rewrites rules.json. LA then rsyncs to NJ; strategies
# on NJ pick up the new flag via the mtime-based reload in load_rules().
# ─────────────────────────────────────────────────────────────────────────

STRAT_NUMBER: dict[str, int] = {
    "strat_1_congress":        1,
    "strat_2_value":           2,
    "strat_3_mean_reversion":  3,
    "strat_4_momentum":        4,
    "strat_5_pred_mkt":        5,
    "strat_6_nasdaq_long":     6,
    "strat_7_nasdaq_short":    7,
    "strat_8_sector_rotation": 8,
    "strat_13_rsi_intraday":   13,
}

# Maps short SMS names to full strategy ids.
SMS_NAME_MAP: dict[str, str] = {
    "STRAT1":  "strat_1_congress",
    "STRAT2":  "strat_2_value",
    "STRAT3":  "strat_3_mean_reversion",
    "STRAT4":  "strat_4_momentum",
    "STRAT5":  "strat_5_pred_mkt",
    "STRAT6":  "strat_6_nasdaq_long",
    "STRAT7":  "strat_7_nasdaq_short",
    "STRAT8":  "strat_8_sector_rotation",
    "STRAT13": "strat_13_rsi_intraday",
}


def is_strategy_enabled(strategy_id: str) -> bool:
    """Read rules.json[<sid>].enabled. Returns False if the block is missing
    entirely; True is the schema-level default when the block exists but the
    field is absent. Source of truth for runtime enable/disable — operator
    toggles this from HORIZON SMS without touching PG."""
    block = get_strategy_rules(strategy_id)
    if not block:
        return False
    return bool(block.get("enabled", True))


def set_strategy_enabled(strategy_id: str, enabled: bool, reason: str = "") -> None:
    """Atomically toggle rules.json[<sid>].enabled. Writes a sibling .tmp,
    fsyncs implicitly via os.replace, then renames over the target. Bust the
    mtime cache so the next load_rules() picks it up immediately.

    Caller (HORIZON workflow) is responsible for re-validating the file with
    validate_rules.py and re-rsync'ing to NJ. This function does the local
    write only."""
    if strategy_id not in STRAT_NUMBER:
        raise ValueError(f"Unknown strategy_id: {strategy_id}")
    if not RULES_PATH.exists():
        raise RuntimeError(f"rules.json missing at {RULES_PATH}")

    rules = json.loads(RULES_PATH.read_text())
    block = rules.get(strategy_id)
    if not isinstance(block, dict):
        raise RuntimeError(f"rules.json has no '{strategy_id}' block — cannot toggle")

    block["enabled"] = bool(enabled)
    rules[strategy_id] = block
    rules["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    tmp = RULES_PATH.with_suffix(RULES_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(rules, indent=2) + "\n")
    os.replace(tmp, RULES_PATH)

    # Bust the in-process cache so subsequent load_rules() re-reads.
    global _RULES, _RULES_MTIME
    _RULES = None
    _RULES_MTIME = 0.0

    get_logger("system").info(
        f"set_strategy_enabled({strategy_id!r}, {enabled!r}) reason={reason!r}"
    )


def parse_sms_toggle_command(text: str) -> Optional[tuple[bool, str]]:
    """Parse 'ENABLE STRAT2' / 'DISABLE STRAT3' style SMS body.
    Returns (enabled, strategy_id) on match, None otherwise.
    Case-insensitive, tolerates surrounding whitespace, rejects anything
    that isn't exactly two tokens with a recognized action + name."""
    if not text:
        return None
    parts = text.strip().upper().split()
    if len(parts) != 2:
        return None
    action, name = parts
    if action == "ENABLE":
        enabled = True
    elif action == "DISABLE":
        enabled = False
    else:
        return None
    sid = SMS_NAME_MAP.get(name)
    if not sid:
        return None
    return (enabled, sid)


# ─────────────────────────────────────────────────────────────────────────
# Per-strategy Alpaca client
#
# Each strategy has its OWN dedicated Alpaca paper account — blast-radius
# isolation, so a leaked or compromised strat2 key cannot drain strat4's
# balance. Credentials live as env-var NAMES in rules.json[<sid>].broker:
#   alpaca_key_id:   "STRAT2_ALPACA_KEY_ID"   ← env var name, not the key
#   alpaca_secret:   "STRAT2_ALPACA_SECRET"
#   alpaca_base_url: "https://paper-api.alpaca.markets/v2"
# Env vars are sourced from /etc/bhn-trading/strat<N>.env on first use.
# ─────────────────────────────────────────────────────────────────────────

_STRATEGY_ENV_LOADED: set[str] = set()
_STRATEGY_ALPACA: dict[str, tradeapi.REST] = {}


def _load_strategy_env(strategy_id: str) -> None:
    """Source /etc/bhn-trading/strat<N>.env into os.environ on first use.
    Idempotent within a process. Skips lines that are blank/commented or
    don't contain '='. Existing os.environ values win (setdefault)."""
    if strategy_id in _STRATEGY_ENV_LOADED:
        return
    n = STRAT_NUMBER.get(strategy_id)
    if n is None:
        return
    env_file = Path(f"/etc/bhn-trading/strat{n}.env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))
    _STRATEGY_ENV_LOADED.add(strategy_id)


def get_strategy_alpaca(strategy_id: str) -> tradeapi.REST:
    """Return the Alpaca client for this strategy's dedicated paper account.
    Cached per (process, strategy_id). Resolves credentials via the
    rules.json broker subblock + /etc/bhn-trading/strat<N>.env + os.environ."""
    if strategy_id in _STRATEGY_ALPACA:
        return _STRATEGY_ALPACA[strategy_id]

    block = get_strategy_rules(strategy_id)
    sb = (block or {}).get("broker") or {}
    if not sb:
        raise RuntimeError(
            f"{strategy_id}: rules.json has no broker subblock — cannot resolve "
            f"per-strategy Alpaca credentials"
        )
    key_env    = sb.get("alpaca_key_id")
    secret_env = sb.get("alpaca_secret")
    base_url   = sb.get("alpaca_base_url")
    if not (key_env and secret_env and base_url):
        raise RuntimeError(
            f"{strategy_id}.broker incomplete: "
            f"alpaca_key_id={key_env!r}, alpaca_secret={secret_env!r}, "
            f"alpaca_base_url={base_url!r}"
        )

    _load_strategy_env(strategy_id)
    key    = os.environ.get(key_env)
    secret = os.environ.get(secret_env)
    if not (key and secret):
        n = STRAT_NUMBER.get(strategy_id)
        raise RuntimeError(
            f"{strategy_id}: env vars {key_env}/{secret_env} not set — checked "
            f"os.environ + /etc/bhn-trading/strat{n}.env"
        )

    # Paper-only safety net: refuse live URL unless TRADING_LIVE_MODE=true
    live_mode = os.environ.get("TRADING_LIVE_MODE", "false").lower() == "true"
    if "paper-api" not in base_url and not live_mode:
        raise RuntimeError(
            f"{strategy_id}.broker.alpaca_base_url={base_url!r} points at the live "
            f"endpoint and TRADING_LIVE_MODE != true — refusing to construct a live client"
        )

    client = tradeapi.REST(
        key_id=key, secret_key=secret, base_url=base_url, api_version="v2",
    )
    acct = client.get_account()
    if acct.account_blocked or acct.trading_blocked:
        raise RuntimeError(
            f"{strategy_id} Alpaca account blocked: "
            f"account_blocked={acct.account_blocked}, "
            f"trading_blocked={acct.trading_blocked}"
        )
    _STRATEGY_ALPACA[strategy_id] = client
    return client


def iter_strategy_alpaca_clients() -> Iterator[tuple[str, tradeapi.REST]]:
    """Yield (strategy_id, REST client) for every strategy in STRAT_NUMBER
    whose broker block is fully configured. Strategies with a missing broker
    subblock, unset env vars, or a blocked account are silently skipped
    (logged at debug). Callers must NOT assume all STRAT_NUMBER ids appear
    — under partial onboarding only some strategies have per-account creds.
    Default get_alpaca() singleton is NOT yielded; query it separately when
    you need a sweep of the legacy/default account."""
    for sid in STRAT_NUMBER:
        try:
            yield (sid, get_strategy_alpaca(sid))
        except Exception as e:
            get_logger("system").debug(
                f"iter_strategy_alpaca_clients: skipping {sid}: {e}"
            )


# ─────────────────────────────────────────────────────────────────────────
# Strategy lifecycle
# ─────────────────────────────────────────────────────────────────────────

def get_strategy_meta(strategy_id: str) -> dict:
    with get_pg_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM trading_strategies WHERE id = %s",
                (strategy_id,),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError(f"Unknown strategy_id: {strategy_id}")
            return dict(row)


def is_strategy_active(strategy_id: str) -> bool:
    return get_strategy_meta(strategy_id)["status"] == "active"


def is_system_halted() -> bool:
    return get_strategy_meta(SYSTEM_STRATEGY_ID)["status"] == "halted"


def should_run(strategy_id: str, requires_market_open: bool = False) -> tuple[bool, str]:
    """
    Composite gate: system not halted AND strategy enabled in rules.json AND
    PG status=active AND breaker clear AND (market open OR strategy is non-intraday).
    Returns (allowed, reason_if_blocked).

    Note: rules.json `enabled` is the operator-facing toggle (HORIZON SMS
    flips this). PG `status` is the framework-internal lifecycle (paused,
    halted by breakers, etc.). Both must be true to run.
    """
    if is_system_halted():
        return False, "system halted (killswitch)"

    if not is_strategy_enabled(strategy_id):
        return False, "disabled in rules.json"

    meta = get_strategy_meta(strategy_id)
    if meta["status"] != "active":
        return False, f"strategy status={meta['status']}"

    breaker = check_circuit_breakers(strategy_id)
    if breaker is not None:
        return False, f"circuit breaker tripped: {breaker.value}"

    if requires_market_open and not is_market_open():
        return False, "market closed"

    return True, ""


def update_strategy_status(strategy_id: str, status: str, reason: str) -> None:
    """
    Updates trading_strategies.status + records reason. Does NOT log to
    circuit_breaker_log — caller decides whether this is a breaker event
    or a normal lifecycle transition.
    """
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE trading_strategies
                SET status = %s,
                    last_status_change_at = NOW(),
                    last_status_change_reason = %s
                WHERE id = %s
                """,
                (status, reason, strategy_id),
            )


def get_rls_capital_allocation(strategy_id: str) -> Decimal:
    return Decimal(str(get_strategy_meta(strategy_id)["capital_allocation"]))


# ─────────────────────────────────────────────────────────────────────────
# Circuit breakers (3 tiers per BHN spec)
# ─────────────────────────────────────────────────────────────────────────

def check_circuit_breakers(strategy_id: str) -> Optional[BreakerType]:
    """
    Evaluate all three tiers. Returns the FIRST tripped breaker (or None).
    Order matters: most-recent-data tier (daily) checked first; system-wide
    drawdown last.
    Pure read — does NOT trip the breaker. Caller does trip_breaker() if action needed.
    """
    if strategy_id == SYSTEM_STRATEGY_ID:
        return None

    today = date.today()
    allocation = get_rls_capital_allocation(strategy_id)
    if allocation == 0:
        return None

    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            # Tier 1: daily loss > 5% of allocation
            cur.execute(
                """
                SELECT daily_pnl FROM strategy_performance
                WHERE strategy_id = %s AND date = %s
                """,
                (strategy_id, today),
            )
            row = cur.fetchone()
            if row is not None:
                daily_pnl = Decimal(str(row[0]))
                if daily_pnl < -allocation * Decimal("0.05"):
                    return BreakerType.DAILY_LOSS_5PCT

            # Tier 2: weekly loss > 10% of allocation
            week_ago = today - timedelta(days=7)
            cur.execute(
                """
                SELECT COALESCE(SUM(daily_pnl), 0) FROM strategy_performance
                WHERE strategy_id = %s AND date >= %s AND date <= %s
                """,
                (strategy_id, week_ago, today),
            )
            weekly = Decimal(str(cur.fetchone()[0]))
            if weekly < -allocation * Decimal("0.10"):
                return BreakerType.WEEKLY_LOSS_10PCT

            # Tier 3: system-wide drawdown > 15% from peak cumulative P&L
            cur.execute(
                """
                SELECT MAX(high_water_mark), MAX(cumulative_pnl)
                FROM strategy_performance WHERE strategy_id = %s
                """,
                (strategy_id,),
            )
            mark_row = cur.fetchone()
            if mark_row is not None and mark_row[0] is not None:
                hwm = Decimal(str(mark_row[0]))
                cum = Decimal(str(mark_row[1])) if mark_row[1] is not None else Decimal("0")
                if hwm > 0:
                    drawdown = (cum - hwm) / hwm
                    if drawdown < Decimal("-0.15"):
                        return BreakerType.DRAWDOWN_15PCT

    return None


def trip_breaker(
    strategy_id: str,
    breaker_type: BreakerType,
    reason: str,
    value_at_trigger: Optional[Decimal] = None,
    threshold: Optional[Decimal] = None,
) -> int:
    """
    Record the breaker hit + update status. Tier-1 → pause; Tier-2 → halt;
    Tier-3 → halt all (system).
    Returns circuit_breaker_log.id.
    """
    if breaker_type == BreakerType.DAILY_LOSS_5PCT:
        new_status = "paused"
        affects_scope = "strategy"
    elif breaker_type == BreakerType.WEEKLY_LOSS_10PCT:
        new_status = "halted"
        affects_scope = "strategy"
    elif breaker_type == BreakerType.DRAWDOWN_15PCT:
        new_status = "halted"
        affects_scope = "system"
    else:
        new_status = "halted"
        affects_scope = "strategy"

    update_strategy_status(strategy_id, new_status, f"{breaker_type.value}: {reason}")
    if affects_scope == "system":
        # Also halt the 'system' row so should_run() blocks everyone
        update_strategy_status(SYSTEM_STRATEGY_ID, "halted",
                               f"system drawdown trip from {strategy_id}: {reason}")

    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO circuit_breaker_log
                    (event_class, event_type, severity, strategy_id, affects_scope,
                     reason, value_at_trigger, threshold, halt_triggered)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                ("circuit_breaker", breaker_type.value, new_status, strategy_id,
                 affects_scope, reason, value_at_trigger, threshold, True),
            )
            log_id = cur.fetchone()[0]

    _send_alert(severity=new_status, message=f"BHN {breaker_type.value} on {strategy_id}: {reason}")
    return log_id


def reset_breaker(strategy_id: str, resolved_by: str, notes: str = "") -> None:
    """
    Operator-callable: clear an active breaker and resume the strategy.
    Marks unresolved circuit_breaker_log rows for this strategy as resolved.
    """
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE circuit_breaker_log
                SET resolved_at = NOW(), resolved_by = %s, notes = COALESCE(notes,'') || %s
                WHERE strategy_id = %s AND resolved_at IS NULL
                """,
                (resolved_by, f"\nresolved: {notes}", strategy_id),
            )
    update_strategy_status(strategy_id, "active", f"breaker reset by {resolved_by}: {notes}")


# ─────────────────────────────────────────────────────────────────────────
# Signal lifecycle
# ─────────────────────────────────────────────────────────────────────────

def log_signal(
    strategy_id: str,
    ticker: str,
    action: Action,
    reason: str = "",
    value: Optional[float] = None,
    acted_on: bool = False,
    raw_payload: Optional[dict] = None,
) -> int:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO signals_log
                    (strategy_id, ticker, action, acted_on, reason, value, raw_payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                RETURNING id
                """,
                (strategy_id, ticker, action.value, acted_on, reason, value,
                 json.dumps(raw_payload) if raw_payload else None),
            )
            return cur.fetchone()[0]


def link_signal_to_trade(signal_id: int, trade_id: int) -> None:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE signals_log SET trade_id = %s WHERE id = %s",
                (trade_id, signal_id),
            )


# ─────────────────────────────────────────────────────────────────────────
# Trade lifecycle
# ─────────────────────────────────────────────────────────────────────────

def open_trade(
    strategy_id: str,
    ticker: str,
    side: Action,
    qty: int,
    entry_price: Decimal,
    signal_id: Optional[int] = None,
    stop_loss: Optional[Decimal] = None,
    target: Optional[Decimal] = None,
    trailing_stop_pct: Optional[Decimal] = None,
    alpaca_order_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> int:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO paper_trades
                    (strategy_id, ticker, side, qty, entry_price, entry_time,
                     status, signal_id, stop_loss, target, trailing_stop_pct,
                     alpaca_order_id_entry, metadata)
                VALUES (%s, %s, %s, %s, %s, NOW(), 'open', %s, %s, %s, %s, %s, %s::jsonb)
                RETURNING id
                """,
                (strategy_id, ticker, side.value, qty, entry_price, signal_id,
                 stop_loss, target, trailing_stop_pct, alpaca_order_id,
                 json.dumps(metadata) if metadata else None),
            )
            trade_id = cur.fetchone()[0]
    if signal_id:
        link_signal_to_trade(signal_id, trade_id)
    return trade_id


def close_trade(trade_id: int, exit_price: Decimal, exit_reason: ExitReason,
                alpaca_order_id_exit: Optional[str] = None) -> dict:
    """
    UPDATEs paper_trades — sets status=closed, computes pnl_dollar + pnl_pct.
    Returns {pnl_dollar, pnl_pct, duration_seconds, ticker, side}.
    """
    with get_pg_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM paper_trades WHERE id = %s FOR UPDATE", (trade_id,))
            row = cur.fetchone()
            if row is None:
                raise RuntimeError(f"Unknown trade_id: {trade_id}")
            if row["status"] == "closed":
                raise RuntimeError(f"Trade {trade_id} already closed")

            entry_price = Decimal(str(row["entry_price"]))
            qty = int(row["qty"])
            sign = Decimal("1") if row["side"] == "buy" else Decimal("-1")
            pnl_dollar = (exit_price - entry_price) * qty * sign
            pnl_pct = (pnl_dollar / (entry_price * qty)) * Decimal("100")

            cur.execute(
                """
                UPDATE paper_trades
                SET status = 'closed',
                    exit_price = %s,
                    exit_time = NOW(),
                    exit_reason = %s,
                    pnl_dollar = %s,
                    pnl_pct = %s,
                    alpaca_order_id_exit = %s
                WHERE id = %s
                RETURNING entry_time
                """,
                (exit_price, exit_reason.value, pnl_dollar, pnl_pct,
                 alpaca_order_id_exit, trade_id),
            )
            entry_time = cur.fetchone()["entry_time"]

    duration = (datetime.now(timezone.utc) - entry_time).total_seconds()
    return {
        "trade_id": trade_id,
        "pnl_dollar": pnl_dollar,
        "pnl_pct": pnl_pct,
        "duration_seconds": int(duration),
        "ticker": row["ticker"],
        "side": row["side"],
    }


def get_open_trades(strategy_id: Optional[str] = None) -> list[dict]:
    sql = "SELECT * FROM paper_trades WHERE status = 'open'"
    params: tuple = ()
    if strategy_id:
        sql += " AND strategy_id = %s"
        params = (strategy_id,)
    sql += " ORDER BY entry_time DESC"
    with get_pg_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────────────
# Order placement
# ─────────────────────────────────────────────────────────────────────────

def place_order(
    strategy_id: str,
    ticker: str,
    side: Action,
    qty: int,
    order_type: str = "market",
    limit_price: Optional[Decimal] = None,
    time_in_force: str = "day",
    signal_id: Optional[int] = None,
    stop_loss: Optional[Decimal] = None,
    target: Optional[Decimal] = None,
    metadata: Optional[dict] = None,
) -> dict:
    """
    Composite: should_run gate → Alpaca submit → log paper_trade.
    Returns {alpaca_order_id, trade_id, status, fill_price?}.
    Refuses if strategy not active or breaker tripped.
    """
    allowed, reason = should_run(strategy_id)
    if not allowed:
        raise RuntimeError(f"place_order refused for {strategy_id}: {reason}")

    # Per-strategy live-mode gate
    meta = get_strategy_meta(strategy_id)
    if _load_env()["live_mode"] and not meta["live_mode_approved"]:
        raise RuntimeError(
            f"live_mode enabled at module level but strategy {strategy_id} has "
            f"live_mode_approved=false. Refusing order."
        )

    alpaca = get_strategy_alpaca(strategy_id)
    order_kwargs = {
        "symbol": ticker,
        "qty": qty,
        "side": side.value,
        "type": order_type,
        "time_in_force": time_in_force,
    }
    if order_type == "limit" and limit_price is not None:
        order_kwargs["limit_price"] = str(limit_price)

    order = alpaca.submit_order(**order_kwargs)
    fill_price = Decimal(str(order.filled_avg_price or order.limit_price or 0))
    if fill_price == 0:
        # Order pending — for market orders this'd be unusual. For limit orders
        # we record the limit price as entry_price target; trade_id update later.
        fill_price = Decimal(str(limit_price)) if limit_price else Decimal("0")

    trade_id = open_trade(
        strategy_id=strategy_id,
        ticker=ticker,
        side=side,
        qty=qty,
        entry_price=fill_price,
        signal_id=signal_id,
        stop_loss=stop_loss,
        target=target,
        alpaca_order_id=order.id,
        metadata=metadata,
    )

    return {
        "alpaca_order_id": order.id,
        "trade_id": trade_id,
        "status": order.status,
        "fill_price": fill_price,
    }


# ─────────────────────────────────────────────────────────────────────────
# Position sizing
# ─────────────────────────────────────────────────────────────────────────

def equal_weight_size(
    allocation: Decimal,
    max_positions: int,
    current_open_count: int,
    price: Decimal,
) -> int:
    """Returns whole shares to buy. Returns 0 if at position limit."""
    if current_open_count >= max_positions or price <= 0:
        return 0
    per_position_dollar = allocation / Decimal(max_positions)
    shares = per_position_dollar / price
    return int(shares.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# ─────────────────────────────────────────────────────────────────────────
# Market hours + VIX
# ─────────────────────────────────────────────────────────────────────────

def is_market_open() -> bool:
    return get_alpaca().get_clock().is_open


def seconds_until_market_close() -> int:
    clock = get_alpaca().get_clock()
    if not clock.is_open:
        return 0
    delta = clock.next_close - clock.timestamp
    return int(delta.total_seconds())


_VIX_CACHE: tuple[float, float] = (0.0, 0.0)  # (value, fetched_at_unix)

def get_vix_value() -> float:
    """Cached 5min. Tries Alpaca then falls back to FMP."""
    global _VIX_CACHE
    now = time.time()
    if _VIX_CACHE[0] and (now - _VIX_CACHE[1]) < 300:
        return _VIX_CACHE[0]

    vix = 0.0
    try:
        alpaca = get_alpaca()
        bars = alpaca.get_latest_bar("VIXY")  # VIXY ETF proxy
        if bars and bars.c:
            vix = float(bars.c) * 2.0  # rough VIXY→VIX scaler; replace with FMP for accuracy
    except Exception:
        pass

    if vix == 0.0:
        env = _load_env()
        if env.get("fmp_key"):
            try:
                resp = requests.get(
                    f"https://financialmodelingprep.com/api/v3/quote/%5EVIX",
                    params={"apikey": env["fmp_key"]},
                    timeout=5,
                )
                data = resp.json()
                if data and isinstance(data, list):
                    vix = float(data[0].get("price", 0))
            except Exception:
                pass

    _VIX_CACHE = (vix, now)
    return vix


def get_market_phase() -> MarketPhase:
    if not is_market_open():
        return MarketPhase.CLOSED

    clock = get_alpaca().get_clock()
    now = clock.timestamp
    open_t = clock.next_open if not clock.is_open else clock.timestamp.replace(hour=13, minute=30)  # 9:30 ET = 13:30 UTC summer
    close_t = clock.next_close

    minutes_since_open = (now - open_t).total_seconds() / 60
    minutes_until_close = (close_t - now).total_seconds() / 60

    if 0 <= minutes_since_open <= 15:
        return MarketPhase.OPEN_VOLATILE
    if 0 <= minutes_until_close <= 15:
        return MarketPhase.OPEN_VOLATILE
    if get_vix_value() > 25:
        return MarketPhase.HIGH_VIX
    return MarketPhase.OPEN


def compute_next_interval(mismatches_present: bool, phase: MarketPhase) -> int:
    """Returns seconds to sleep before next reconciliation cycle."""
    if mismatches_present:
        return 10
    if phase in (MarketPhase.OPEN_VOLATILE, MarketPhase.HIGH_VIX):
        return 30
    if phase == MarketPhase.OPEN:
        return 60
    return 300


# ─────────────────────────────────────────────────────────────────────────
# Reconciliation primitives (daemon consumes these)
# ─────────────────────────────────────────────────────────────────────────

def reconcile_state() -> list[Mismatch]:
    """
    Multi-account deterministic comparison. For each strategy with a
    configured per-account broker, compare that strategy's Alpaca positions
    against its open paper_trades. Also sweep the legacy/default account
    (the one tied to ALPACA_API_KEY in /etc/bhn-trading/env) — under
    multi-account routing nothing should be held there, so any position
    found is an UNKNOWN_POSITION orphan (this is exactly how the JPST-on-
    PRIMARY incident would surface).

    Returns list of Mismatch objects (empty if clean).
    """
    mismatches: list[Mismatch] = []

    pg_open = get_open_trades()
    # Partition PG state by (strategy_id, ticker)
    pg_by_strat: dict[str, dict[str, list[dict]]] = {}
    for t in pg_open:
        pg_by_strat.setdefault(t["strategy_id"], {}).setdefault(t["ticker"], []).append(t)

    # ── Per-strategy reconciliation ─────────────────────────────────
    seen_strategies: set[str] = set()
    for strategy_id, alpaca in iter_strategy_alpaca_clients():
        seen_strategies.add(strategy_id)
        try:
            alpaca_positions = alpaca.list_positions()
        except Exception as e:
            get_logger("system").warning(
                f"reconcile_state: list_positions failed for {strategy_id}: {e}"
            )
            continue

        alpaca_by_ticker: dict[str, int] = {p.symbol: int(p.qty) for p in alpaca_positions}
        pg_for_strat = pg_by_strat.get(strategy_id, {})

        # Alpaca-side: what's in this account
        for ticker, alpaca_qty in alpaca_by_ticker.items():
            pg_trades = pg_for_strat.get(ticker, [])
            pg_qty = sum(int(t["qty"]) * (1 if t["side"] == "buy" else -1)
                         for t in pg_trades)
            if pg_qty == alpaca_qty:
                continue
            try:
                latest = alpaca.get_latest_trade(ticker)
                price = Decimal(str(latest.price))
            except Exception:
                price = Decimal("0")
            if pg_qty == 0:
                mismatches.append(Mismatch(
                    type=MismatchType.UNKNOWN_POSITION,
                    strategy_id=strategy_id,
                    ticker=ticker,
                    expected_qty=0,
                    actual_qty=alpaca_qty,
                    value_usd=price * abs(alpaca_qty),
                    details={"account": strategy_id, "price": str(price),
                             "alpaca_qty": alpaca_qty},
                ))
            else:
                diff = alpaca_qty - pg_qty
                mtype = (MismatchType.UNKNOWN_POSITION if diff > 0
                         else MismatchType.MISSING_POSITION)
                mismatches.append(Mismatch(
                    type=mtype,
                    strategy_id=strategy_id,
                    ticker=ticker,
                    expected_qty=pg_qty,
                    actual_qty=alpaca_qty,
                    value_usd=price * abs(diff),
                    details={"account": strategy_id, "price": str(price),
                             "diff": diff,
                             "pg_trade_ids": [t["id"] for t in pg_trades]},
                ))

        # PG-side: trades in PG for this strategy but absent from its Alpaca account
        for ticker, pg_trades in pg_for_strat.items():
            if ticker in alpaca_by_ticker:
                continue
            pg_qty = sum(int(t["qty"]) * (1 if t["side"] == "buy" else -1)
                         for t in pg_trades)
            try:
                latest = alpaca.get_latest_trade(ticker)
                price = Decimal(str(latest.price))
            except Exception:
                price = Decimal("0")
            mismatches.append(Mismatch(
                type=MismatchType.MISSING_POSITION,
                strategy_id=strategy_id,
                ticker=ticker,
                expected_qty=pg_qty,
                actual_qty=0,
                value_usd=price * abs(pg_qty),
                details={"account": strategy_id, "price": str(price),
                         "pg_trade_ids": [t["id"] for t in pg_trades]},
            ))

    # ── Default-account sweep ───────────────────────────────────────
    # Anything in the legacy ALPACA_API_KEY account is an orphan under
    # multi-account routing. This is the load-bearing detection path
    # for "place_order routed to PRIMARY instead of the strategy account"
    # bugs — keep it even if all per-strategy accounts come up clean.
    try:
        default_alpaca = get_alpaca()
        default_positions = default_alpaca.list_positions()
    except Exception as e:
        get_logger("system").warning(
            f"reconcile_state: default account sweep failed: {e}"
        )
        default_positions = []

    for p in default_positions:
        ticker = p.symbol
        alpaca_qty = int(p.qty)
        try:
            latest = default_alpaca.get_latest_trade(ticker)
            price = Decimal(str(latest.price))
        except Exception:
            price = Decimal("0")
        mismatches.append(Mismatch(
            type=MismatchType.UNKNOWN_POSITION,
            strategy_id=None,
            ticker=ticker,
            expected_qty=0,
            actual_qty=alpaca_qty,
            value_usd=price * abs(alpaca_qty),
            details={"account": "default", "price": str(price),
                     "alpaca_qty": alpaca_qty,
                     "note": "position on legacy/default ALPACA_API_KEY "
                             "account — no strategy should hold here under "
                             "multi-account routing"},
        ))

    # ── PG rows for strategies we never inspected ───────────────────
    # Surface paper_trades whose strategy_id has no broker config (so we
    # didn't enumerate its Alpaca account above). Otherwise these would be
    # silently invisible to reconciliation.
    for sid, by_ticker in pg_by_strat.items():
        if sid in seen_strategies:
            continue
        for ticker, pg_trades in by_ticker.items():
            pg_qty = sum(int(t["qty"]) * (1 if t["side"] == "buy" else -1)
                         for t in pg_trades)
            mismatches.append(Mismatch(
                type=MismatchType.MISSING_POSITION,
                strategy_id=sid,
                ticker=ticker,
                expected_qty=pg_qty,
                actual_qty=0,
                value_usd=Decimal("0"),
                details={"account": "unconfigured",
                         "note": f"strategy_id {sid!r} has no per-account "
                                 f"broker config; Alpaca side not checked",
                         "pg_trade_ids": [t["id"] for t in pg_trades]},
            ))

    return mismatches


def handle_mismatch(mismatch: Mismatch) -> None:
    """Per BHN spec: ANY mismatch → halt all + SMS + log."""
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO circuit_breaker_log
                    (event_class, event_type, severity, strategy_id, affects_scope,
                     reason, value_at_trigger, details, halt_triggered)
                VALUES ('reconciliation', %s, 'halted', %s, 'system', %s, %s, %s::jsonb, true)
                """,
                (mismatch.type.value,
                 mismatch.strategy_id,
                 f"{mismatch.type.value} on {mismatch.ticker}: "
                 f"expected={mismatch.expected_qty} actual={mismatch.actual_qty}",
                 mismatch.value_usd,
                 json.dumps(mismatch.to_json_dict())),
            )

    halt_all_trading(f"reconciliation mismatch on {mismatch.ticker} ({mismatch.type.value})")
    _send_alert(
        severity="halted",
        message=(f"BHN HALT: {mismatch.type.value} on {mismatch.ticker}. "
                 f"Reply 'what happened' to HORIZON for analysis."),
    )


def halt_all_trading(reason: str) -> None:
    """Set all strategies including 'system' to halted."""
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE trading_strategies
                SET status = 'halted',
                    last_status_change_at = NOW(),
                    last_status_change_reason = %s
                WHERE status != 'halted'
                """,
                (reason,),
            )


def record_heartbeat(interval_used: int, mismatches_found: int, cycle_duration_ms: int) -> None:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO reconciliation_heartbeat
                    (interval_used, mismatches_found, cycle_duration_ms)
                VALUES (%s, %s, %s)
                """,
                (interval_used, mismatches_found, cycle_duration_ms),
            )


# ─────────────────────────────────────────────────────────────────────────
# Alert webhook (to bhn-alert-router n8n workflow)
# ─────────────────────────────────────────────────────────────────────────

def _send_alert(severity: str, message: str) -> None:
    """POST to bhn-alert-router webhook. Best-effort; failures don't crash strategies."""
    if not ALERT_WEBHOOK_PATH.exists():
        get_logger("system").warning(
            f"Alert webhook URL not found at {ALERT_WEBHOOK_PATH}; alert dropped: {message}"
        )
        return
    url = ALERT_WEBHOOK_PATH.read_text().strip()
    try:
        requests.post(
            url,
            json={
                "alerts": [{
                    "status": "firing",
                    "labels": {
                        "severity": severity,
                        "dedup_key": f"bhn-trading-{severity}",
                        "alertname": "BHN Trading Alert",
                    },
                    "annotations": {
                        "summary": message,
                        "description": message,
                    },
                }]
            },
            timeout=5,
        )
    except Exception as e:
        get_logger("system").warning(f"Alert webhook post failed: {e}")


# ─────────────────────────────────────────────────────────────────────────
# CLI for ad-hoc inspection
# ─────────────────────────────────────────────────────────────────────────

def _cli_status() -> int:
    with get_pg_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, status, capital_allocation, live_mode_approved, "
                "last_status_change_at, last_status_change_reason "
                "FROM trading_strategies ORDER BY id"
            )
            for r in cur.fetchall():
                print(f"  {r['id']:24s}  status={r['status']:8s}  "
                      f"alloc=${float(r['capital_allocation']):>10.2f}  "
                      f"live={r['live_mode_approved']}  "
                      f"last_change={r['last_status_change_at']}")
    return 0

def _cli_reconcile() -> int:
    mismatches = reconcile_state()
    if not mismatches:
        print("✓ No mismatches detected")
        return 0
    print(f"✗ {len(mismatches)} mismatch(es):")
    for m in mismatches:
        print(f"  - {m.type.value} on {m.ticker}: "
              f"expected={m.expected_qty} actual={m.actual_qty} "
              f"value=${m.value_usd}")
    return 1

def _cli_health() -> int:
    rc = 0
    # Default / legacy account
    try:
        acct = get_alpaca().get_account()
        print(f"✓ Alpaca[default]: status={acct.status}, "
              f"equity=${acct.equity}, cash=${acct.cash}")
    except Exception as e:
        print(f"✗ Alpaca[default] error: {e}")
        rc = 1

    # Per-strategy accounts — log each independently; an unconfigured
    # strategy is a soft failure (might just not be onboarded yet)
    for sid in STRAT_NUMBER:
        try:
            acct = get_strategy_alpaca(sid).get_account()
            print(f"✓ Alpaca[{sid}]: status={acct.status}, "
                  f"equity=${acct.equity}, cash=${acct.cash}")
        except Exception as e:
            print(f"  Alpaca[{sid}]: not available ({e})")

    try:
        with get_pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        print(f"✓ PostgreSQL: connected to {_load_env()['pg_host']}")
    except Exception as e:
        print(f"✗ PostgreSQL error: {e}")
        rc = 1
    if RULES_PATH.exists():
        rules = load_rules()
        strat_ids = [k for k in rules if k.startswith("strat_")]
        n_enabled = sum(1 for k in strat_ids if rules[k].get("enabled") is True)
        print(f"✓ rules.json: loaded, {len(strat_ids)} strategy blocks "
              f"({n_enabled} enabled), version={rules.get('version')}")
    else:
        print(f"✗ rules.json: missing at {RULES_PATH}")
    return rc


def _cli_set_enabled(enabled: bool, args: list[str]) -> int:
    """Handle `enable STRAT2 [reason]` / `disable STRAT2 [reason]`."""
    if not args:
        verb = "enable" if enabled else "disable"
        print(f"Usage: trading_core.py {verb} {{STRAT1..STRAT5|strat_<n>_<name>}} [reason]")
        return 2
    name = args[0]
    sid = SMS_NAME_MAP.get(name.upper())
    if not sid and name in STRAT_NUMBER:
        sid = name
    if not sid:
        print(f"Unknown strategy: {name!r}. Use STRAT1..STRAT5 or the full strat_<n>_<...> id.")
        return 2
    reason = " ".join(args[1:]) if len(args) > 1 else "via trading_core CLI"
    try:
        set_strategy_enabled(sid, enabled, reason)
    except Exception as e:
        print(f"Failed: {e}")
        return 1
    print(f"OK: {sid}.enabled={enabled} ({reason})")
    return 0


def _cli_sms(args: list[str]) -> int:
    """Handle a raw HORIZON SMS body: `sms 'ENABLE STRAT2'`."""
    if not args:
        print("Usage: trading_core.py sms '<ENABLE|DISABLE> STRAT<N>'")
        return 2
    text = " ".join(args)
    parsed = parse_sms_toggle_command(text)
    if not parsed:
        print(f"Not a recognized toggle command: {text!r}")
        return 2
    enabled, sid = parsed
    try:
        set_strategy_enabled(sid, enabled, f"HORIZON SMS: {text!r}")
    except Exception as e:
        print(f"Failed: {e}")
        return 1
    print(f"OK: {sid}.enabled={enabled} (via SMS: {text!r})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="BHN trading_core CLI")
    parser.add_argument(
        "command",
        choices=["status", "reconcile", "health", "enable", "disable", "sms"],
    )
    parser.add_argument("args", nargs="*", help="Arguments for enable/disable/sms")
    args = parser.parse_args()
    if args.command == "status":
        return _cli_status()
    if args.command == "reconcile":
        return _cli_reconcile()
    if args.command == "health":
        return _cli_health()
    if args.command == "enable":
        return _cli_set_enabled(True, args.args)
    if args.command == "disable":
        return _cli_set_enabled(False, args.args)
    if args.command == "sms":
        return _cli_sms(args.args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
