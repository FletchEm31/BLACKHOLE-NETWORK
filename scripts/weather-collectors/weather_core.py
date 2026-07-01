"""
BHN WeatherBHN — minimal shared core for weather collector nodes.

Deliberately does NOT depend on trading_core.py: no Alpaca client, no
rules.json, no broker credentials. Weather collector nodes (Helsinki,
Hillsboro) should never hold trading secrets — they only need a PG
connection and a logger. LA's trading_core.py stays the source of truth
for anything trading-related.

Provides the two functions weather_data_collector.py actually uses:
  get_logger(name) -> logging.Logger
  get_pg_conn()     -> contextmanager yielding a psycopg2 connection
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

import psycopg2
import psycopg2.extensions
from psycopg2.pool import ThreadedConnectionPool


_ENV: dict[str, Any] = {}


def _load_env() -> dict[str, Any]:
    global _ENV
    if _ENV:
        return _ENV

    required = ["PG_HOST", "PG_PORT", "PG_DB", "PG_USER", "PG_PASSWORD"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {missing}")

    _ENV = {
        "pg_host":  os.environ["PG_HOST"],
        "pg_port":  int(os.environ["PG_PORT"]),
        "pg_db":    os.environ["PG_DB"],
        "pg_user":  os.environ["PG_USER"],
        "pg_pwd":   os.environ["PG_PASSWORD"],
        "log_level": os.environ.get("LOG_LEVEL", "INFO"),
        "log_dir":   os.environ.get("LOG_DIR", "/var/log/bhn-trading"),
    }
    return _ENV


# ─────────────────────────────────────────────────────────────────────────
# Logger
# ─────────────────────────────────────────────────────────────────────────

_LOGGERS: dict[str, logging.Logger] = {}


def get_logger(name: str) -> logging.Logger:
    if name in _LOGGERS:
        return _LOGGERS[name]

    env = _load_env()
    log_dir = Path(env["log_dir"])
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"bhn.weather.{name}")
    logger.setLevel(env["log_level"])
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    fh = logging.handlers.TimedRotatingFileHandler(
        filename=log_dir / f"{name}.log",
        when="midnight",
        backupCount=14,
        utc=True,
    )
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    _LOGGERS[name] = logger
    return logger


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
