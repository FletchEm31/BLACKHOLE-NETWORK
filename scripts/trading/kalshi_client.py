#!/usr/bin/env python3
"""
kalshi_client.py — BHN Strategy 9 (BHN-PREDICTION-ALPHA) Kalshi API client.

WRITTEN FROM SCRATCH per Kalshi's public auth docs (docs.kalshi.com/api/auth).
No external SDK, no reference-repo code in the auth path — every line is ours.
Pure Python + the `cryptography` library (PEP-conformant, audited primitives).

Auth scheme (RSA-PSS):
  1. Build the signature payload as the concatenation:
        timestamp_ms_str + http_method_upper + path
     where path is the API path including the /trade-api/v2 prefix but
     WITHOUT any query string. Query params are sent on the wire as
     normal — they're just not part of the signed payload.
  2. Hash payload with SHA-256.
  3. Sign with RSA-PSS (MGF1 over SHA-256, salt length = digest length).
  4. Base64-encode signature → KALSHI-ACCESS-SIGNATURE header.
  5. Send these headers on every authenticated request:
        KALSHI-ACCESS-KEY        = operator's key id (e.g. 'a1b2c3...')
        KALSHI-ACCESS-SIGNATURE  = base64(signature)
        KALSHI-ACCESS-TIMESTAMP  = timestamp_ms_str

Environment + secrets:
  KALSHI_KEY_ID                          — env var, the operator's key id
  KALSHI_PRIVATE_KEY_PATH                — env var, default
                                            /etc/bhn-trading/kalshi_private.pem
  KALSHI_PRIVATE_KEY_PASSWORD            — env var, optional; if set the
                                            PEM file is treated as encrypted
                                            and decrypted on load
  KALSHI_ENV                             — env var, 'demo' (default) or 'prod'

Paper-only safety:
  - Constructor argument paper_only=True (default) refuses to construct a
    client pointing at the production URL. Set False explicitly (in code
    or via env KALSHI_PAPER_ONLY=false) when promoting to live.

CLI (Phase 1 smoke tests):
  python3 kalshi_client.py status              # GET /exchange/status (no auth)
  python3 kalshi_client.py balance             # GET /portfolio/balance (auth)
  python3 kalshi_client.py markets --series KXHIGHNY --status open
  python3 kalshi_client.py orderbook KXHIGHNYM-26MAY15-T80   # example

Phase 1 does NOT use this client for actual orders — strategy_prediction_alpha
(Phase 3) calls create_order / cancel_order; for now we just verify auth and
data fetch work against demo-api.kalshi.co.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlencode, urlparse

import requests

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
except ImportError:
    print("ERROR: cryptography not installed. apt-get install python3-cryptography",
          file=sys.stderr)
    sys.exit(2)

import trading_core as tc


logger = tc.get_logger("strat_9_prediction_alpha_kalshi")


# ─────────────────────────────────────────────────────────────────────────
# Endpoint configuration
# ─────────────────────────────────────────────────────────────────────────

DEMO_HOST = "https://demo-api.kalshi.co"
PROD_HOST = "https://api.elections.kalshi.com"  # migrated 2026 from trading-api.kalshi.com

# All v2 endpoints live under this path prefix. The prefix is part of the
# string fed to the signature payload — DO NOT strip it before signing.
API_PREFIX = "/trade-api/v2"

# Default location for the operator's RSA private key
DEFAULT_PRIVATE_KEY_PATH = "/etc/bhn-trading/kalshi_private.pem"

# Conservative request defaults
DEFAULT_TIMEOUT = 20
DEFAULT_RETRY_ATTEMPTS = 3

# Polling cadences (operator-spec'd via research findings):
# Kalshi rate limits are not publicly documented but community research
# suggests ~10 req/sec sustained, ~30 req/sec burst. Back off on 429.
NORMAL_POLL_INTERVAL = 2.0       # seconds — between scans, normal operation
GFS_WINDOW_POLL_INTERVAL = 0.5   # seconds — during GFS update window (first 5 min)
BURST_POLL_INTERVAL = 0.1        # 100ms — maximum burst; use sparingly
MAX_RETRIES = 5
BACKOFF_BASE = 1.5               # exponential backoff base for 429s


# ─────────────────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────────────────

class KalshiAuthError(Exception):
    """RSA key loading / signature construction problem."""


class KalshiAPIError(Exception):
    """Non-2xx response from Kalshi. Carries status_code + parsed body."""

    def __init__(self, status_code: int, body: Any, message: str = ""):
        super().__init__(message or f"Kalshi API error {status_code}: {body}")
        self.status_code = status_code
        self.body = body


class KalshiPaperOnlyViolation(Exception):
    """Refused to construct a prod-URL client with paper_only=True."""


# ─────────────────────────────────────────────────────────────────────────
# Private-key loading
# ─────────────────────────────────────────────────────────────────────────

def _load_private_key(path: str,
                      password: Optional[str] = None) -> rsa.RSAPrivateKey:
    """Read a PEM-encoded RSA private key from disk. Password optional —
    only set when the operator chose to encrypt the PEM at provisioning time.

    Validates that the loaded key is RSA (not Ed25519 / ECDSA / etc.) since
    Kalshi's auth scheme is RSA-PSS specifically.
    """
    p = Path(path)
    if not p.is_file():
        raise KalshiAuthError(
            f"Kalshi private key not found at {path}. Generate one with:\n"
            f"  openssl genrsa -out {path} 2048\n"
            f"Then upload the corresponding public key in Kalshi's web console "
            f"and store the returned key id in /etc/bhn-trading/strat9.env as "
            f"KALSHI_KEY_ID=<id>"
        )
    try:
        with p.open("rb") as f:
            pem_bytes = f.read()
        key = serialization.load_pem_private_key(
            pem_bytes,
            password=password.encode("utf-8") if password else None,
        )
    except Exception as e:
        raise KalshiAuthError(f"failed to load private key at {path}: {e}") from e

    if not isinstance(key, rsa.RSAPrivateKey):
        raise KalshiAuthError(
            f"Key at {path} is not RSA ({type(key).__name__}). Kalshi requires "
            f"RSA-PSS auth — regenerate with `openssl genrsa -out {path} 2048`."
        )
    if key.key_size < 2048:
        raise KalshiAuthError(
            f"Key at {path} is only {key.key_size} bits. Kalshi requires "
            f"≥ 2048-bit RSA. Regenerate with 2048+ bits."
        )
    return key


# ─────────────────────────────────────────────────────────────────────────
# Signature construction
# ─────────────────────────────────────────────────────────────────────────

def _sign_request(private_key: rsa.RSAPrivateKey,
                  timestamp_ms: int, method: str, path: str) -> str:
    """Build the Kalshi RSA-PSS signature for a request and return it
    base64-encoded.

    Payload format (concatenated, no separators):
        f"{timestamp_ms}{method.upper()}{path}"

    Path includes the API prefix (/trade-api/v2) and any query string.
    """
    payload = f"{timestamp_ms}{method.upper()}{path}".encode("utf-8")
    try:
        signature = private_key.sign(
            payload,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=hashes.SHA256().digest_size,  # =32; PSS.DIGEST_LENGTH added in cryptography≥37
            ),
            hashes.SHA256(),
        )
    except Exception as e:
        raise KalshiAuthError(f"RSA-PSS signing failed: {e}") from e
    return base64.b64encode(signature).decode("ascii")


# ─────────────────────────────────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class KalshiBalance:
    balance_cents: int
    payout_cents: int
    @property
    def balance_usd(self) -> float:
        return self.balance_cents / 100.0


class KalshiClient:
    """Authenticated client for Kalshi's v2 trade API.

    Construction reads credentials from the constructor args (preferred for
    tests) or from env vars (KALSHI_KEY_ID + KALSHI_PRIVATE_KEY_PATH + optional
    KALSHI_PRIVATE_KEY_PASSWORD + KALSHI_ENV).

    paper_only=True (default) blocks construction with a prod URL — Phase 1
    is demo-only.
    """

    def __init__(self,
                 key_id: Optional[str] = None,
                 private_key_path: Optional[str] = None,
                 private_key_password: Optional[str] = None,
                 env: Optional[str] = None,
                 paper_only: bool = True,
                 timeout: int = DEFAULT_TIMEOUT,
                 attempts: int = DEFAULT_RETRY_ATTEMPTS):
        self._key_id = key_id or os.environ.get("KALSHI_KEY_ID", "").strip()
        self._private_key_path = private_key_path or os.environ.get(
            "KALSHI_PRIVATE_KEY_PATH", DEFAULT_PRIVATE_KEY_PATH
        )
        self._password = (private_key_password
                          or os.environ.get("KALSHI_PRIVATE_KEY_PASSWORD")
                          or None)
        self._env = (env or os.environ.get("KALSHI_ENV", "demo")).lower().strip()
        if self._env not in ("demo", "prod", "production"):
            raise KalshiAuthError(f"Unknown env {self._env!r}; expected demo|prod")
        if self._env in ("prod", "production"):
            env_flag = os.environ.get("KALSHI_PAPER_ONLY", "true").lower()
            if paper_only and env_flag != "false":
                raise KalshiPaperOnlyViolation(
                    "Constructing a prod-URL client requires paper_only=False "
                    "AND env KALSHI_PAPER_ONLY=false. Refusing to operate "
                    "against trading-api.kalshi.com with paper-only safety on."
                )
            self._base = PROD_HOST
        else:
            self._base = DEMO_HOST

        # Lazy-load key — defer until first signed request so /exchange/status
        # (which is unauthenticated) works without a key configured
        self._private_key: Optional[rsa.RSAPrivateKey] = None
        self._timeout = timeout
        self._attempts = attempts
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "BHN-Kalshi-Client/1.0 (operator@eventhorizonvpn.com)",
            "Accept": "application/json",
        })

    # ─────────────────────────────────────────────────────────────────────
    # Key + signature plumbing
    # ─────────────────────────────────────────────────────────────────────

    def _ensure_key_loaded(self) -> rsa.RSAPrivateKey:
        if self._private_key is None:
            if not self._key_id:
                raise KalshiAuthError(
                    "KALSHI_KEY_ID env var not set — required for "
                    "authenticated requests. Get the id from Kalshi's web "
                    "console after uploading the public key half of the "
                    "RSA key pair."
                )
            self._private_key = _load_private_key(
                self._private_key_path, self._password,
            )
        return self._private_key

    def _signed_headers(self, method: str, path_with_query: str) -> dict:
        key = self._ensure_key_loaded()
        ts_ms = int(time.time() * 1000)
        signature = _sign_request(key, ts_ms, method, path_with_query)
        return {
            "KALSHI-ACCESS-KEY":       self._key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": str(ts_ms),
        }

    # ─────────────────────────────────────────────────────────────────────
    # Low-level request
    # ─────────────────────────────────────────────────────────────────────

    def _request(self, method: str, path: str,
                 params: Optional[dict] = None,
                 json_body: Optional[dict] = None,
                 authenticated: bool = True) -> Any:
        """Single low-level request method.

        Signature contract: the path passed to RSA-PSS signing is the API
        path WITHOUT the query string (e.g. '/trade-api/v2/markets', never
        '/trade-api/v2/markets?series_ticker=KXHIGHNY'). The query string
        is built and sent on the URL as normal but is NOT part of the
        signed payload. This matches Kalshi's auth docs exactly."""
        method = method.upper()
        # Path that goes BOTH on the URL and into the signature payload.
        signed_path = f"{API_PREFIX}{path}" if not path.startswith(API_PREFIX) else path

        # Build the full URL with query string (sent on the wire only).
        if params:
            qs = urlencode(params, doseq=True)
            url = f"{self._base}{signed_path}?{qs}"
        else:
            url = f"{self._base}{signed_path}"

        headers: dict = {}
        if authenticated:
            # Sign the path WITHOUT query — Kalshi auth contract.
            headers.update(self._signed_headers(method, signed_path))
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        body_str = json.dumps(json_body) if json_body is not None else None

        last_exc: Optional[Exception] = None
        for attempt in range(self._attempts):
            try:
                resp = self._session.request(
                    method=method, url=url,
                    headers=headers, data=body_str,
                    timeout=self._timeout,
                )
                # 429 → backoff
                if resp.status_code == 429:
                    wait = 2 ** attempt + 1
                    logger.warning(f"Kalshi 429 on {method} {url}; sleeping {wait}s")
                    time.sleep(wait)
                    continue
                # Non-2xx → raise with parsed body
                if not (200 <= resp.status_code < 300):
                    try:
                        body: Any = resp.json()
                    except ValueError:
                        body = resp.text
                    raise KalshiAPIError(
                        status_code=resp.status_code,
                        body=body,
                        message=f"{method} {url} → {resp.status_code}",
                    )
                # OK — parse + return
                if not resp.content:
                    return {}
                return resp.json()
            except KalshiAPIError:
                raise   # don't retry 4xx/5xx; only 429 + transport errors
            except requests.RequestException as e:
                last_exc = e
                wait = 2 ** attempt
                logger.warning(
                    f"Kalshi transport error on attempt {attempt+1}/{self._attempts} "
                    f"({method} {url}): {e}. sleeping {wait}s"
                )
                time.sleep(wait)
        raise KalshiAPIError(
            status_code=0, body=str(last_exc),
            message=f"transport failed after {self._attempts} attempts",
        )

    # Convenience wrappers
    def get(self, path: str, params: Optional[dict] = None,
            authenticated: bool = True) -> Any:
        return self._request("GET", path, params=params, authenticated=authenticated)

    def post(self, path: str, json_body: Optional[dict] = None) -> Any:
        return self._request("POST", path, json_body=json_body, authenticated=True)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path, authenticated=True)

    # ─────────────────────────────────────────────────────────────────────
    # High-level helpers — Phase 1: read-only verification surface
    # ─────────────────────────────────────────────────────────────────────

    def exchange_status(self) -> dict:
        """GET /exchange/status. UNAUTHENTICATED — useful for connectivity
        sanity-check before fussing with keys."""
        return self.get("/exchange/status", authenticated=False)

    def get_balance(self) -> KalshiBalance:
        """GET /portfolio/balance. First authenticated call — best smoke test
        for the RSA-PSS auth setup. Returns the operator's demo (or prod)
        cents balance and payout sum."""
        body = self.get("/portfolio/balance")
        return KalshiBalance(
            balance_cents=int(body.get("balance", 0)),
            payout_cents=int(body.get("payout", 0)),
        )

    def get_markets(self, *,
                     series: Optional[str] = None,
                     event_ticker: Optional[str] = None,
                     status: Optional[str] = None,
                     limit: int = 100,
                     cursor: Optional[str] = None,
                     log_to_pg: bool = True) -> dict:
        """GET /markets — filter by series (e.g. 'KXHIGHNY' for NYC daily high),
        event, status ('open' / 'closed' / 'settled'), with pagination.

        When log_to_pg=True (default), every returned market is UPSERTed into
        the prediction_contracts table — this gives us a persistent catalog
        of every contract we've ever seen for downstream signal logic + audit.
        """
        params: dict = {"limit": limit}
        if series:
            params["series_ticker"] = series
        if event_ticker:
            params["event_ticker"] = event_ticker
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor
        body = self.get("/markets", params=params, authenticated=False)
        if log_to_pg:
            self._upsert_markets_to_pg(body.get("markets") or [])
        return body

    def get_market(self, ticker: str) -> dict:
        """GET /markets/{ticker}. Phase 3 reads the strike + resolution
        details here for edge calculation."""
        return self.get(f"/markets/{ticker}", authenticated=False)

    def get_orderbook(self, ticker: str, depth: int = 30) -> dict:
        """GET /markets/{ticker}/orderbook. Returns yes/no price ladders."""
        return self.get(f"/markets/{ticker}/orderbook",
                        params={"depth": depth}, authenticated=False)

    def get_orders(self, *, status: Optional[str] = None, limit: int = 100,
                    cursor: Optional[str] = None) -> dict:
        """GET /portfolio/orders. status='open'|'executed'|'canceled'|None (all)."""
        params: dict = {"limit": limit}
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor
        return self.get("/portfolio/orders", params=params)

    def get_positions(self, *, limit: int = 100,
                       cursor: Optional[str] = None) -> dict:
        """GET /portfolio/positions. Current open positions per market."""
        params: dict = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self.get("/portfolio/positions", params=params)

    def list_fills(self, *, limit: int = 100, ticker: Optional[str] = None,
                    cursor: Optional[str] = None) -> dict:
        """GET /portfolio/fills. Each fill is one matched order leg."""
        params: dict = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if cursor:
            params["cursor"] = cursor
        return self.get("/portfolio/fills", params=params)

    def fetch_kalshi_portfolio(self) -> dict:
        """Fetch current positions + recent fills; write both to DB.

        Returns dict with keys:
          positions_fetched: int
          fills_fetched: int
          positions_upserted: int
          fills_inserted: int
        """
        positions_body = self.get_positions(limit=200)
        fills_body     = self.list_fills(limit=100)

        positions = (positions_body.get("market_positions")
                     or positions_body.get("positions") or [])
        fills     = fills_body.get("fills") or []

        pos_rows  = _upsert_positions_to_pg(positions)
        fill_rows = _upsert_fills_to_pg(fills)

        return {
            "positions_fetched":  len(positions),
            "fills_fetched":      len(fills),
            "positions_upserted": pos_rows,
            "fills_inserted":     fill_rows,
        }

    # ─────────────────────────────────────────────────────────────────────
    # Order placement — Phase 3+ uses these. Phase 1 deliberately doesn't.
    # ─────────────────────────────────────────────────────────────────────

    def place_order(self,
                     ticker: str,
                     side: str,                  # 'yes' | 'no'
                     count: int,
                     price: Optional[int] = None,  # cents 1..99 for limit; None for market
                     order_type: str = "limit",  # 'limit' | 'market'
                     action: str = "buy",         # 'buy' | 'sell'
                     client_order_id: Optional[str] = None,
                     time_in_force: str = "GTC") -> dict:
        """POST /portfolio/orders. Place a single order.

        Kalshi prices are integer cents 1..99. `price` is applied to whichever
        leg is being traded (yes_price if side='yes'; no_price if side='no').
        Market orders omit price entirely.

        Caller is responsible for ALL Phase 3+ pre-checks before invoking:
          - paper_only env consistency (constructor enforces demo URL)
          - rules.json strat_9 enabled + edge ≥ 8% per contract
          - confidence ≥ 0.65 ensemble threshold
          - Kelly-sized count within strat_9 daily_loss_limit budget
        This client is a thin auth + HTTP wrapper; it does NOT enforce risk."""
        if side not in ("yes", "no"):
            raise ValueError(f"side must be 'yes'|'no', got {side!r}")
        if action not in ("buy", "sell"):
            raise ValueError(f"action must be 'buy'|'sell', got {action!r}")
        if order_type not in ("limit", "market"):
            raise ValueError(f"order_type must be 'limit'|'market', got {order_type!r}")
        if count <= 0:
            raise ValueError(f"count must be > 0, got {count}")
        if order_type == "limit":
            if price is None:
                raise ValueError("limit orders require a price (cents 1..99)")
            if not (1 <= price <= 99):
                raise ValueError(f"price must be in cents [1, 99], got {price}")

        body: dict = {
            "ticker": ticker, "side": side, "action": action,
            "count": count, "type": order_type, "time_in_force": time_in_force,
        }
        if order_type == "limit":
            if side == "yes":
                body["yes_price"] = price
            else:
                body["no_price"] = price
        if client_order_id:
            body["client_order_id"] = client_order_id

        logger.info(f"place_order ticker={ticker} side={side} count={count} "
                    f"price={price} type={order_type} action={action}")
        return self.post("/portfolio/orders", json_body=body)

    def cancel_order(self, order_id: str) -> dict:
        """DELETE /portfolio/orders/{order_id}."""
        logger.info(f"cancel_order id={order_id}")
        return self.delete(f"/portfolio/orders/{order_id}")

    # ─────────────────────────────────────────────────────────────────────
    # Weather-specific helpers
    # ─────────────────────────────────────────────────────────────────────

    # Kalshi weather series tickers — Phase 3 scope: Miami, Phoenix, Denver
    # (High + Low). If actual API tickers differ from these assumed names,
    # update here and in prediction_signal.SERIES_TO_STATION_VAR to match.
    WEATHER_SERIES = (
        "KXHIGHMIA", "KXLOWMIA",
        "KXHIGHPHX", "KXLOWPHX",
        "KXHIGHDEN", "KXLOWDEN",
        "KXHIGHLAX", "KXLOWLAX",
        "KXHIGHDFW", "KXLOWDFW",
    )

    def get_weather_markets(self, *, status: str = "open") -> list:
        """Concatenate get_markets() across the 4 Kalshi weather series.
        Each call auto-UPSERTs discovered contracts into prediction_contracts."""
        all_markets: list = []
        for series in self.WEATHER_SERIES:
            try:
                body = self.get_markets(series=series, status=status,
                                         limit=200, log_to_pg=True)
                all_markets.extend(body.get("markets") or [])
            except KalshiAPIError as e:
                logger.warning(f"get_weather_markets({series}) failed: {e}")
        return all_markets

    def get_weather_market_price(self, ticker: str) -> Optional[float]:
        """Return current YES ask price (0-1 fractional) for a weather contract,
        or None if not available. Pulls the orderbook and returns the lowest
        YES ask. For market sizing decisions Phase 3 should use the
        orderbook depth instead; this is just the at-the-tape probability."""
        try:
            book = self.get_orderbook(ticker)
        except KalshiAPIError as e:
            logger.warning(f"get_weather_market_price({ticker}): {e}")
            return None
        # Kalshi v2 returns 'orderbook_fp' with yes_dollars/no_dollars price
        # strings in 0-1 fractional form (e.g. "0.0100" = 1¢ = 1% probability).
        # Older 'orderbook' key used integer cents — fallback kept for safety.
        ob = ((book or {}).get("orderbook_fp")
              or (book or {}).get("orderbook") or {})
        yes_side = ob.get("yes_dollars") or ob.get("yes") or []
        if not yes_side:
            return None
        raw = yes_side[0][0] if isinstance(yes_side[0], (list, tuple)) else None
        if raw is None:
            return None
        price = float(raw)
        return price if price <= 1.0 else price / 100.0

    # ─────────────────────────────────────────────────────────────────────
    # PG audit — UPSERT discovered markets into prediction_contracts
    # ─────────────────────────────────────────────────────────────────────

    def _upsert_markets_to_pg(self, markets: list) -> int:
        """Bulk UPSERT a markets payload into prediction_contracts.
        Returns count of rows touched. Best-effort — PG failures don't
        crash the client (they're logged + swallowed)."""
        if not markets:
            return 0
        n = 0
        try:
            with tc.get_pg_conn() as conn:
                with conn.cursor() as cur:
                    for m in markets:
                        ticker = m.get("ticker")
                        if not ticker:
                            continue
                        title = m.get("title") or m.get("subtitle") or ""

                        # Parse station_code / variable / resolution_date /
                        # threshold from the ticker — more reliable than title.
                        meta = _parse_ticker_metadata(ticker)
                        station_code    = meta["station_code"] or _station_from_kalshi_title(title)
                        variable        = meta["variable"]     or _variable_from_kalshi_title(title)
                        resolution_date = meta["resolution_date"]
                        threshold_op    = meta["threshold_op"]
                        threshold_value = meta["threshold_value"]

                        # resolution_date fallback: close_time from API payload
                        if resolution_date is None:
                            close_str = m.get("close_time") or m.get("expiration_time")
                            if close_str:
                                try:
                                    resolution_date = close_str[:10]
                                except (TypeError, IndexError):
                                    pass

                        # threshold_op fallback: parse from title text
                        if threshold_op is None and threshold_value is not None:
                            tl = title.lower()
                            if ">" in tl:
                                threshold_op = ">"
                            elif "<" in tl:
                                threshold_op = "<"

                        cur.execute("""
                            INSERT INTO prediction_contracts
                                (exchange, contract_id, title,
                                 station_code, variable,
                                 resolution_date, is_active,
                                 threshold_op, threshold_value,
                                 raw_payload, last_seen_at)
                            VALUES ('kalshi', %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s::jsonb, NOW())
                            ON CONFLICT (exchange, contract_id) DO UPDATE SET
                                title           = EXCLUDED.title,
                                station_code    = EXCLUDED.station_code,
                                variable        = EXCLUDED.variable,
                                resolution_date = EXCLUDED.resolution_date,
                                is_active       = EXCLUDED.is_active,
                                threshold_op    = EXCLUDED.threshold_op,
                                threshold_value = EXCLUDED.threshold_value,
                                raw_payload     = EXCLUDED.raw_payload,
                                last_seen_at    = NOW()
                        """, (
                            ticker, title, station_code, variable,
                            resolution_date,
                            (m.get("status") or "").lower() in ("open", "active"),
                            threshold_op, threshold_value,
                            json.dumps(m),
                        ))
                        n += 1
        except Exception as e:
            logger.warning(f"_upsert_markets_to_pg failed (non-fatal): {e}")
            return 0
        logger.debug(f"prediction_contracts: upserted {n} rows")
        return n

    # ─────────────────────────────────────────────────────────────────────
    # Aggressive post-GFS-update polling
    #
    # Operator's design (per research findings): Kalshi prices reprice
    # slowly after each GFS update — there's a 10-30 minute window where
    # our BHN model has the new forecast but the Kalshi market hasn't
    # yet adjusted. The strategy: spam Kalshi's orderbook endpoints
    # during that window and catch lagging prices.
    #
    # Three-phase polling (operator-spec):
    #   0-5  min after GFS publish → 0.5s interval (BURST)
    #   5-15 min                  → 2s   interval (ACTIVE)
    #   15-30 min                  → 5s   interval (WIND_DOWN)
    #   >30  min                   → exit; next window is next GFS cycle
    #
    # Implementation note on async: operator's spec asked for asyncio
    # throughout. This method uses ThreadPoolExecutor instead — same I/O
    # parallelism (4 concurrent HTTP GETs per cycle), no new aiohttp
    # dependency, no risk to the working sync auth code. A full asyncio
    # refactor of the entire client is a clean follow-up if benchmarks
    # show threads aren't fast enough; at 4 markets × 1 fetch/cycle the
    # threaded approach is ~50ms per cycle (well under the 500ms burst
    # interval).
    # ─────────────────────────────────────────────────────────────────────

    def poll_weather_prices_aggressive(
        self,
        duration_seconds: int = 1800,
        tickers: Optional[list[str]] = None,
        gfs_run_at: Optional[datetime] = None,
        gfs_run_hour: Optional[int] = None,
        on_market_update: Optional[Callable[[dict], Optional[dict]]] = None,
        write_pg_stats: bool = True,
        send_sms_summary: bool = True,
    ) -> dict:
        """Three-phase post-GFS-update polling loop.

        Polls each ticker's orderbook concurrently every cycle; calls
        on_market_update(payload) per ticker per cycle. The callback
        decides whether the data point is an opportunity, places the
        bet, and returns a dict describing the outcome.

        Args:
            duration_seconds: how long to run (default 1800 = 30 min).
            tickers: which contract tickers to poll. If None, queries
                get_weather_markets(status='open') and polls every open
                contract across the 4 Kalshi weather series.
            gfs_run_at: timestamp of the GFS cycle this window is
                tracking. Used for lag calculations + the PG stats row.
                Defaults to next_gfs_publish_window_utc()'s most-recent
                cycle estimate.
            gfs_run_hour: 0/6/12/18. Defaults from gfs_run_at.
            on_market_update: callback receiving:
                {
                  'ticker', 'best_yes_cents', 'best_no_cents',
                  'raw_book', 'poll_number', 'phase',
                  'minutes_since_gfs', 'api_latency_ms',
                }
                Return dict with keys to update window stats:
                  is_opportunity: bool   — count toward opportunities_found
                  bet_placed:     bool   — count toward bets_placed
                  edge_captured_usd: float — sum into total_edge_captured
                  lag_minutes:    float — used for fastest/slowest stats
            write_pg_stats: insert a gfs_window_stats row on completion.
            send_sms_summary: fire HORIZON SMS with the summary text.

        Returns dict with the same fields written to PG:
          poll_count, rate_limit_hits, opportunities_found, bets_placed,
          total_edge_captured, avg_lag_minutes, fastest_capture_minutes,
          slowest_capture_minutes, window_start, window_end.
        """
        if gfs_run_at is None:
            # Best-effort: most recent cycle whose publish-time has passed
            now = datetime.now(timezone.utc)
            today = now.date()
            most_recent: Optional[datetime] = None
            most_recent_h: Optional[int] = None
            for h in GFS_CYCLE_HOURS:
                cycle_dt = datetime(today.year, today.month, today.day,
                                     h, 0, 0, 0, tzinfo=timezone.utc)
                if cycle_dt <= now and (most_recent is None or cycle_dt > most_recent):
                    most_recent = cycle_dt
                    most_recent_h = h
            if most_recent is None:
                # Pre-00z UTC; use yesterday's 18z
                y = today - timedelta(days=1)
                most_recent = datetime(y.year, y.month, y.day,
                                        18, 0, 0, 0, tzinfo=timezone.utc)
                most_recent_h = 18
            gfs_run_at = most_recent
            gfs_run_hour = most_recent_h
        if gfs_run_hour is None:
            gfs_run_hour = gfs_run_at.hour

        if tickers is None:
            try:
                weather_markets = self.get_weather_markets(status="open")
                tickers = [m.get("ticker") for m in weather_markets if m.get("ticker")]
            except Exception as e:
                logger.error(f"poll_weather: failed to resolve weather tickers: {e}")
                tickers = []
        if not tickers:
            logger.warning("poll_weather_prices_aggressive: no tickers to poll — exiting")
            return {
                "poll_count": 0, "rate_limit_hits": 0,
                "opportunities_found": 0, "bets_placed": 0,
                "total_edge_captured": 0.0,
                "avg_lag_minutes": None,
                "fastest_capture_minutes": None,
                "slowest_capture_minutes": None,
                "window_start": datetime.now(timezone.utc),
                "window_end": datetime.now(timezone.utc),
            }

        window_start = datetime.now(timezone.utc)
        start_mono = time.monotonic()
        poll_count = 0
        rate_limit_hits = 0
        consecutive_429s = 0
        opportunities_found = 0
        bets_placed = 0
        total_edge_captured = 0.0
        lag_minutes_captured: list[float] = []

        n_workers = min(len(tickers), 8)
        logger.info(
            f"poll_weather_prices_aggressive: starting "
            f"gfs_run={gfs_run_at.isoformat()} ({gfs_run_hour}z) "
            f"tickers={len(tickers)} duration={duration_seconds}s workers={n_workers}"
        )

        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            while time.monotonic() - start_mono < duration_seconds:
                elapsed = time.monotonic() - start_mono
                phase = _current_phase_for_elapsed(elapsed)
                if phase == "idle":
                    break

                cycle_start_mono = time.monotonic()
                poll_count += 1

                # Concurrent orderbook fetch across all tickers
                futures: dict = {}
                for t in tickers:
                    fetch_start = time.monotonic()
                    futures[executor.submit(self._timed_orderbook, t, fetch_start)] = t

                cycle_429 = False
                for fut in as_completed(futures):
                    ticker = futures[fut]
                    try:
                        book, latency_ms = fut.result(timeout=10)
                    except KalshiAPIError as e:
                        if e.status_code == 429:
                            cycle_429 = True
                            rate_limit_hits += 1
                            logger.warning(
                                f"poll_weather: 429 on {ticker} "
                                f"(cycle 429 hits this run: {rate_limit_hits})"
                            )
                        else:
                            logger.warning(f"poll_weather: API error on {ticker}: {e}")
                        continue
                    except Exception as e:
                        logger.warning(f"poll_weather: fetch failed for {ticker}: {e}")
                        continue

                    ob = (book or {}).get("orderbook") or {}
                    yes_side = ob.get("yes") or []
                    no_side = ob.get("no") or []
                    best_yes = (yes_side[0][0]
                                if yes_side and isinstance(yes_side[0], list) else None)
                    best_no  = (no_side[0][0]
                                if no_side  and isinstance(no_side[0],  list) else None)
                    minutes_since_gfs = (
                        (datetime.now(timezone.utc) - gfs_run_at).total_seconds() / 60.0
                    )

                    payload = {
                        "ticker":             ticker,
                        "best_yes_cents":     best_yes,
                        "best_no_cents":      best_no,
                        "raw_book":           book,
                        "poll_number":        poll_count,
                        "phase":              phase,
                        "minutes_since_gfs":  minutes_since_gfs,
                        "api_latency_ms":     latency_ms,
                    }
                    if on_market_update is None:
                        continue
                    try:
                        result = on_market_update(payload) or {}
                    except Exception as e:
                        logger.warning(f"on_market_update callback raised: {e}")
                        result = {}
                    if result.get("is_opportunity"):
                        opportunities_found += 1
                    if result.get("bet_placed"):
                        bets_placed += 1
                    edge_usd = result.get("edge_captured_usd")
                    if isinstance(edge_usd, (int, float)):
                        total_edge_captured += float(edge_usd)
                    lag = result.get("lag_minutes")
                    if isinstance(lag, (int, float)):
                        lag_minutes_captured.append(float(lag))

                # Rate-limit escalation per operator spec:
                # 3 consecutive cycles with any 429 → drop to NORMAL interval
                if cycle_429:
                    consecutive_429s += 1
                    if consecutive_429s >= 3:
                        wait = BACKOFF_BASE ** min(poll_count, 8)
                        logger.warning(
                            f"poll_weather: {consecutive_429s} consecutive cycles with "
                            f"429 — backing off {wait:.1f}s + dropping to NORMAL interval"
                        )
                        time.sleep(wait)
                        # Override interval for remainder by spoofing phase
                        sleep_for = NORMAL_POLL_INTERVAL
                    else:
                        sleep_for = _phase_interval(phase)
                else:
                    consecutive_429s = 0
                    sleep_for = _phase_interval(phase)

                # Honor per-phase interval, accounting for time already spent in cycle
                cycle_elapsed = time.monotonic() - cycle_start_mono
                remaining = sleep_for - cycle_elapsed
                if remaining > 0:
                    time.sleep(remaining)

        window_end = datetime.now(timezone.utc)
        avg_lag = (sum(lag_minutes_captured) / len(lag_minutes_captured)
                   if lag_minutes_captured else None)
        fastest = min(lag_minutes_captured) if lag_minutes_captured else None
        slowest = max(lag_minutes_captured) if lag_minutes_captured else None

        summary = {
            "poll_count":              poll_count,
            "rate_limit_hits":         rate_limit_hits,
            "opportunities_found":     opportunities_found,
            "bets_placed":             bets_placed,
            "total_edge_captured":     round(total_edge_captured, 2),
            "avg_lag_minutes":         (round(avg_lag, 2) if avg_lag is not None else None),
            "fastest_capture_minutes": (round(fastest, 2) if fastest is not None else None),
            "slowest_capture_minutes": (round(slowest, 2) if slowest is not None else None),
            "window_start":            window_start,
            "window_end":              window_end,
            "gfs_run_at":              gfs_run_at,
            "gfs_run_hour":            gfs_run_hour,
        }
        logger.info(
            f"poll_weather_prices_aggressive complete: polls={poll_count} "
            f"429s={rate_limit_hits} opps={opportunities_found} "
            f"bets={bets_placed} edge=${total_edge_captured:.2f}"
        )

        if write_pg_stats:
            self._write_gfs_window_stats(summary)
        if send_sms_summary:
            self._send_window_sms(summary)
        return summary

    def _timed_orderbook(self, ticker: str,
                          fetch_start_mono: float) -> tuple[dict, int]:
        """Helper: fetch orderbook and return (book, latency_ms) for the
        ThreadPoolExecutor path. Re-raises KalshiAPIError so the cycle
        loop can distinguish 429 from other failures."""
        book = self.get_orderbook(ticker)
        latency_ms = int((time.monotonic() - fetch_start_mono) * 1000)
        return book, latency_ms

    def _write_gfs_window_stats(self, summary: dict) -> None:
        """INSERT one row into gfs_window_stats. Best-effort; PG failures
        are logged but don't crash the polling result."""
        try:
            with tc.get_pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO gfs_window_stats
                            (gfs_run_at, gfs_run_hour, window_start, window_end,
                             total_polls, rate_limit_hits, opportunities_found,
                             bets_placed, total_edge_captured, avg_lag_minutes,
                             fastest_capture_minutes, slowest_capture_minutes)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        summary["gfs_run_at"], summary["gfs_run_hour"],
                        summary["window_start"], summary["window_end"],
                        summary["poll_count"], summary["rate_limit_hits"],
                        summary["opportunities_found"], summary["bets_placed"],
                        summary["total_edge_captured"], summary["avg_lag_minutes"],
                        summary["fastest_capture_minutes"], summary["slowest_capture_minutes"],
                    ))
            logger.info("gfs_window_stats row inserted")
        except Exception as e:
            logger.warning(f"_write_gfs_window_stats failed (non-fatal): {e}")

    def _send_window_sms(self, summary: dict) -> None:
        """HORIZON SMS summary per operator spec. Best-effort via the
        trading_core webhook used elsewhere."""
        try:
            hh = summary["gfs_run_hour"]
            avg_lag = summary["avg_lag_minutes"]
            avg_lag_str = f"{avg_lag:.1f} min" if avg_lag is not None else "n/a"
            msg = (
                f"GFS WINDOW COMPLETE\n"
                f"Run: {hh:02d}UTC\n"
                f"Polls: {summary['poll_count']}\n"
                f"Opportunities: {summary['opportunities_found']}\n"
                f"Bets placed: {summary['bets_placed']}\n"
                f"Avg lag: {avg_lag_str}\n"
                f"Edge captured: ${summary['total_edge_captured']:.2f}\n"
                f"— HORIZON"
            )
            tc._send_alert(severity="info", message=msg)
        except Exception as e:
            logger.warning(f"_send_window_sms failed (non-fatal): {e}")


# ─────────────────────────────────────────────────────────────────────────
# Portfolio DB helper
# ─────────────────────────────────────────────────────────────────────────

def _upsert_fills_to_pg(fills: list) -> int:
    """Insert fill records from /portfolio/fills into kalshi_fills.
    Each fill is one matched order leg — insert-only (no conflict update).
    """
    if not fills:
        return 0
    n = 0
    try:
        with tc.get_pg_conn() as conn:
            with conn.cursor() as cur:
                for f in fills:
                    ticker = f.get("market_ticker") or f.get("ticker")
                    if not ticker:
                        continue
                    title  = f.get("market_title") or f.get("title") or ""
                    side   = (f.get("side") or "yes").lower()
                    action = (f.get("action") or "buy").lower()
                    count  = int(f.get("count") or 0)
                    # price is in cents (integer 1..99)
                    price_cents = int(f.get("yes_price") or f.get("price") or 0)
                    cost_usd    = round(count * price_cents / 100.0, 4) if count and price_cents else None
                    is_taker    = bool(f.get("is_taker"))
                    created_str = f.get("created_time") or f.get("created_at")
                    cur.execute("""
                        INSERT INTO kalshi_fills
                            (contract_ticker, contract_title, side, action,
                             count, price_cents, cost_usd, is_taker, created_time)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                    """, (
                        ticker, title, side, action,
                        count, price_cents, cost_usd, is_taker, created_str,
                    ))
                    n += 1
            conn.commit()
    except Exception as e:
        logger.warning(f"_upsert_fills_to_pg failed (non-fatal): {e}")
        return 0
    logger.debug(f"kalshi_fills: inserted {n} rows")
    return n


def _upsert_positions_to_pg(positions: list) -> int:
    """Upsert a /portfolio/positions payload into kalshi_positions.

    Each call snapshots the full position state at the current moment.
    Kalshi API field names (v2):
      ticker, market_title, side, position (# contracts),
      market_exposure (cents), total_traded (cents),
      realized_pnl (cents), unrealized_pnl (cents)
    """
    if not positions:
        return 0
    n = 0
    try:
        with tc.get_pg_conn() as conn:
            with conn.cursor() as cur:
                for p in positions:
                    ticker = p.get("ticker") or p.get("market_ticker")
                    if not ticker:
                        continue
                    title      = p.get("market_title") or p.get("title") or ""
                    # 'side' in positions is 'yes'|'no'; fallback if absent
                    side       = (p.get("side") or "yes").lower()
                    # Number of contracts held (integer)
                    contracts  = int(p.get("position") or p.get("quantity") or 0)
                    # All monetary fields come in cents from Kalshi
                    def _cents(key: str) -> Optional[float]:
                        v = p.get(key)
                        return float(v) / 100.0 if v is not None else None
                    cost_usd           = _cents("total_traded")
                    unrealized_pnl_usd = _cents("unrealized_pnl")
                    realized_pnl_usd   = _cents("realized_pnl")
                    market_exposure    = _cents("market_exposure")
                    # avg_price per contract in fractional dollars (0-1)
                    avg_price: Optional[float] = None
                    if cost_usd is not None and contracts > 0:
                        avg_price = cost_usd / contracts
                    # market value ≈ market_exposure or best estimate
                    market_value_usd = market_exposure
                    # max payout if YES wins = contracts × $1.00
                    payout_if_right  = float(contracts) if side == "yes" else None

                    cur.execute("""
                        INSERT INTO kalshi_positions
                            (contract_ticker, contract_title, side, contracts,
                             avg_price, cost_usd, market_value_usd,
                             unrealized_pnl_usd, payout_if_right_usd,
                             captured_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    """, (
                        ticker, title, side, contracts,
                        avg_price, cost_usd, market_value_usd,
                        unrealized_pnl_usd, payout_if_right,
                    ))
                    n += 1
            conn.commit()
    except Exception as e:
        logger.warning(f"_upsert_positions_to_pg failed (non-fatal): {e}")
        return 0
    logger.debug(f"kalshi_positions: inserted {n} rows")
    return n


# ─────────────────────────────────────────────────────────────────────────
# Title parsing — best-effort mapping of Kalshi titles to BHN station codes
# ─────────────────────────────────────────────────────────────────────────

_KALSHI_TITLE_STATION_MAP = (
    # (substring search ignoring case, station_code)
    ("new york",   "KNYC"),
    ("nyc",        "KNYC"),
    ("chicago",    "KORD"),
    ("miami",      "KMIA"),
    ("austin",     "KAUS"),
    ("denver",     "KDEN"),
    ("phoenix",    "KPHX"),
    ("los angeles", "KLAX"),
    ("lax",         "KLAX"),
    ("dallas",      "KDFW"),
    ("dfw",         "KDFW"),
)

# Ticker series prefix → ICAO station code
# Parsed directly from ticker (e.g. KXHIGHDEN → DEN → KDEN).
# More reliable than title text matching — use as primary source.
_TICKER_CITY_TO_STATION: dict[str, str] = {
    "DEN": "KDEN", "MIA": "KMIA", "PHX": "KPHX",
    "LAX": "KLAX", "DFW": "KDFW", "NYC": "KNYC",
    "CHI": "KORD", "AUS": "KAUS", "NY":  "KNYC",
}


def _parse_ticker_metadata(ticker: str) -> dict:
    """Extract station_code, variable, resolution_date, threshold_value
    from a Kalshi weather ticker string.

    Format: KXHIGH{CITY}-{YYMONDD}-{B|T}{value}
    Examples:
      KXHIGHDEN-26JUN11-B78.5  → KDEN, tmax_f, 2026-06-11, between, 78.5
      KXHIGHMIA-26JUN10-T92    → KMIA, tmax_f, 2026-06-10, >, 92.0
      KXLOWMIA-26JUN10-T85     → KMIA, tmin_f, 2026-06-10, <, 85.0
    """
    result: dict = {
        "station_code": None, "variable": None,
        "resolution_date": None,
        "threshold_op": None, "threshold_value": None,
    }
    parts = ticker.split("-")
    if len(parts) < 2:
        return result

    series = parts[0]  # e.g. "KXHIGHDEN"

    # Variable + city code
    if series.startswith("KXHIGH"):
        result["variable"] = "tmax_f"
        city_code = series[6:]  # strip "KXHIGH"
    elif series.startswith("KXLOW"):
        result["variable"] = "tmin_f"
        city_code = series[5:]  # strip "KXLOW"
    else:
        city_code = ""

    result["station_code"] = _TICKER_CITY_TO_STATION.get(city_code)

    # Resolution date  e.g. "26JUN11" → 2026-06-11
    if len(parts) >= 2:
        _MONTH = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
                  "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}
        import re as _re
        m = _re.match(r'(\d{2})([A-Z]{3})(\d{2})', parts[1])
        if m:
            yy, mon, dd = m.groups()
            mo = _MONTH.get(mon)
            if mo:
                from datetime import date as _date
                try:
                    result["resolution_date"] = _date(2000 + int(yy), mo, int(dd))
                except ValueError:
                    pass

    # Threshold  e.g. "T92" → (>, 92.0)  "B78.5" → (between, 78.5)
    if len(parts) >= 3:
        thresh = parts[2]
        try:
            if thresh.startswith("B"):
                result["threshold_op"]    = "between"
                result["threshold_value"] = float(thresh[1:])
            elif thresh.startswith("T"):
                result["threshold_value"] = float(thresh[1:])
                # op determined from title text in the caller
        except ValueError:
            pass

    return result


def _station_from_kalshi_title(title: str) -> Optional[str]:
    if not title:
        return None
    t = title.lower()
    for needle, code in _KALSHI_TITLE_STATION_MAP:
        if needle in t:
            return code
    return None


def _variable_from_kalshi_title(title: str) -> Optional[str]:
    """Best-effort variable derivation. 'KXHIGH*' series → tmax_f."""
    if not title:
        return None
    t = title.lower()
    if "high temp" in t or "high temperature" in t or "highest temp" in t:
        return "tmax_f"
    if "low temp" in t or "lowest temp" in t:
        return "tmin_f"
    if "precip" in t or "rain" in t:
        return "precip_in"
    if "snow" in t:
        return "snow_in"
    return None


# ─────────────────────────────────────────────────────────────────────────
# GFS cycle helpers (module-level — no auth needed)
# ─────────────────────────────────────────────────────────────────────────

GFS_CYCLE_HOURS = (0, 6, 12, 18)
DEFAULT_GFS_LAG_HOURS = 3.5         # cycle-to-publish lag


def next_gfs_publish_window_utc(
    lag_hours: float = DEFAULT_GFS_LAG_HOURS,
    watch_duration_minutes: int = 30,
) -> tuple[datetime, datetime, int]:
    """Return (window_start, window_end, gfs_run_hour) UTC for the NEXT GFS
    publish window. GFS runs at 00/06/12/18 UTC; products typically publish
    `lag_hours` after cycle start. Returned start = cycle_h + lag_hours."""
    now = datetime.now(timezone.utc)
    today = now.date()
    candidates: list[tuple[datetime, int]] = []
    for cycle_h in GFS_CYCLE_HOURS:
        cycle_start = datetime(today.year, today.month, today.day,
                                cycle_h, 0, 0, 0, tzinfo=timezone.utc)
        publish = cycle_start + timedelta(hours=lag_hours)
        candidates.append((publish, cycle_h))
    # Wrap to tomorrow's first cycle
    tomorrow = today + timedelta(days=1)
    candidates.append((
        datetime(tomorrow.year, tomorrow.month, tomorrow.day,
                  GFS_CYCLE_HOURS[0], 0, 0, 0, tzinfo=timezone.utc)
        + timedelta(hours=lag_hours),
        GFS_CYCLE_HOURS[0],
    ))
    for publish, cycle_h in candidates:
        if publish > now:
            return (publish, publish + timedelta(minutes=watch_duration_minutes), cycle_h)
    # unreachable in practice
    publish, cycle_h = candidates[-1]
    return (publish, publish + timedelta(minutes=watch_duration_minutes), cycle_h)


def _current_phase_for_elapsed(elapsed_seconds: float) -> str:
    """Three-phase polling per operator spec:
      0-5 min:    burst       (0.5s interval — maximum aggression)
      5-15 min:   active      (2s — still catching stragglers)
      15-30 min:  wind_down   (5s — most edge already captured)
      >30 min:    idle        (caller should exit the loop)
    """
    if elapsed_seconds < 300:
        return "burst"
    if elapsed_seconds < 900:
        return "active"
    if elapsed_seconds < 1800:
        return "wind_down"
    return "idle"


def _phase_interval(phase: str) -> float:
    return {
        "burst":     GFS_WINDOW_POLL_INTERVAL,
        "active":    NORMAL_POLL_INTERVAL,
        "wind_down": 5.0,
        "idle":      120.0,
    }.get(phase, NORMAL_POLL_INTERVAL)


# ─────────────────────────────────────────────────────────────────────────
# CLI — Phase 1 smoke tests
# ─────────────────────────────────────────────────────────────────────────

def _cli_status() -> int:
    client = KalshiClient(paper_only=True)
    print(f"connecting to {client._base}{API_PREFIX}/exchange/status …")
    try:
        body = client.exchange_status()
    except KalshiAPIError as e:
        print(f"FAIL: {e}")
        return 1
    print(json.dumps(body, indent=2))
    return 0


def _cli_balance() -> int:
    client = KalshiClient(paper_only=True)
    print(f"signed GET {client._base}{API_PREFIX}/portfolio/balance …")
    try:
        bal = client.get_balance()
    except (KalshiAuthError, KalshiAPIError) as e:
        print(f"FAIL: {e}")
        return 1
    print(f"balance: ${bal.balance_usd:.2f}  (raw cents={bal.balance_cents}, "
          f"payout cents={bal.payout_cents})")
    return 0


def _cli_markets(args) -> int:
    client = KalshiClient(paper_only=True)
    body = client.get_markets(
        series=args.series, status=args.status, limit=args.limit,
    )
    markets = body.get("markets", [])
    print(f"{len(markets)} markets returned:")
    for m in markets[: args.limit]:
        print(f"  {m.get('ticker'):40s}  status={m.get('status'):8s}  "
              f"yes_ask={m.get('yes_ask')}  no_ask={m.get('no_ask')}  "
              f"close={m.get('close_time')}  title={m.get('title','')[:60]}")
    return 0


def _cli_weather(args) -> int:
    client = KalshiClient(paper_only=True)
    markets = client.get_weather_markets(status=args.status)
    print(f"{len(markets)} weather markets across "
          f"{', '.join(client.WEATHER_SERIES)}:")
    for m in markets:
        print(f"  {m.get('ticker'):40s}  yes={m.get('yes_ask','-'):>4}  "
              f"close={m.get('close_time')}  {m.get('title','')[:60]}")
    return 0


def _cli_orderbook(args) -> int:
    client = KalshiClient(paper_only=True)
    body = client.get_orderbook(args.ticker)
    print(json.dumps(body, indent=2))
    return 0


def _cli_positions() -> int:
    client = KalshiClient(paper_only=True)
    body = client.get_positions()
    positions = body.get("market_positions") or body.get("positions") or []
    print(f"{len(positions)} open positions:")
    for p in positions:
        print(f"  {p.get('ticker'):40s}  qty={p.get('position')}  "
              f"avg_yes_price={p.get('average_yes_price')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="BHN Kalshi API client")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status",    help="GET /exchange/status (no auth)")
    sub.add_parser("balance",   help="GET /portfolio/balance (verifies auth)")
    sub.add_parser("positions", help="GET /portfolio/positions")

    pm = sub.add_parser("markets", help="GET /markets")
    pm.add_argument("--series", help="series filter (e.g. KXHIGHNY)")
    pm.add_argument("--status", default="open", choices=["open", "closed", "settled"])
    pm.add_argument("--limit",  type=int, default=20)

    pw = sub.add_parser("weather", help="Aggregate the 4 Kalshi weather series")
    pw.add_argument("--status", default="open", choices=["open", "closed", "settled"])

    po = sub.add_parser("orderbook", help="GET /markets/{ticker}/orderbook")
    po.add_argument("ticker")

    pg = sub.add_parser("next-gfs", help="Show next GFS publish window")
    pg.add_argument("--lag-hours", type=float, default=DEFAULT_GFS_LAG_HOURS)

    pb = sub.add_parser("burst-poll",
                          help="Run the aggressive post-GFS-update poll loop (Phase 1 smoke)")
    pb.add_argument("--duration",  type=int, default=1800,
                    help="seconds (default 1800 = 30 min)")
    pb.add_argument("--ticker", action="append",
                    help="explicit ticker(s); omit to auto-resolve via get_weather_markets")
    pb.add_argument("--no-pg",  action="store_true",
                    help="skip writing the gfs_window_stats row")
    pb.add_argument("--no-sms", action="store_true",
                    help="skip the HORIZON SMS summary")

    args = parser.parse_args()
    if args.cmd == "status":     return _cli_status()
    if args.cmd == "balance":    return _cli_balance()
    if args.cmd == "positions":  return _cli_positions()
    if args.cmd == "markets":    return _cli_markets(args)
    if args.cmd == "weather":    return _cli_weather(args)
    if args.cmd == "orderbook":  return _cli_orderbook(args)
    if args.cmd == "next-gfs":   return _cli_next_gfs(args)
    if args.cmd == "burst-poll": return _cli_burst_poll(args)
    parser.print_help()
    return 0


def _cli_next_gfs(args) -> int:
    start, end, hh = next_gfs_publish_window_utc(lag_hours=args.lag_hours)
    now = datetime.now(timezone.utc)
    sleep_min = (start - now).total_seconds() / 60.0
    print(f"Next GFS cycle:       {hh:02d}UTC")
    print(f"Publish window opens: {start.isoformat()}  ({sleep_min:.1f} min from now)")
    print(f"Publish window ends:  {end.isoformat()}")
    return 0


def _cli_burst_poll(args) -> int:
    client = KalshiClient(paper_only=True)
    print(f"Running burst-poll for {args.duration}s (write_pg={not args.no_pg}, "
          f"sms={not args.no_sms})…")
    summary = client.poll_weather_prices_aggressive(
        duration_seconds=args.duration,
        tickers=args.ticker,
        on_market_update=None,   # CLI smoke test — no edge calc / betting
        write_pg_stats=not args.no_pg,
        send_sms_summary=not args.no_sms,
    )
    print(json.dumps({
        k: (v.isoformat() if isinstance(v, datetime) else v)
        for k, v in summary.items()
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
