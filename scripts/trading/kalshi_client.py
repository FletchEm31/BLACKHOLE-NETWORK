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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
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
PROD_HOST = "https://trading-api.kalshi.com"

# All v2 endpoints live under this path prefix. The prefix is part of the
# string fed to the signature payload — DO NOT strip it before signing.
API_PREFIX = "/trade-api/v2"

# Default location for the operator's RSA private key
DEFAULT_PRIVATE_KEY_PATH = "/etc/bhn-trading/kalshi_private.pem"

# Conservative request defaults
DEFAULT_TIMEOUT = 20
DEFAULT_RETRY_ATTEMPTS = 3


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
                salt_length=padding.PSS.DIGEST_LENGTH,
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

    # Kalshi weather series tickers per operator (4 Kalshi-supported cities).
    # Note: the AUS market uses 'KXHIGHAUX' (not KXHIGHAUS) per operator's spec.
    WEATHER_SERIES = ("KXHIGHNY", "KXHIGHCHI", "KXHIGHMIA", "KXHIGHAUX")

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
        # Kalshi orderbook shape: {"orderbook": {"yes": [[price, count], ...],
        #                                          "no":  [[price, count], ...]}}
        # YES asks are typically sorted ascending; best ask = first entry.
        ob = (book or {}).get("orderbook") or {}
        yes_side = ob.get("yes") or []
        if not yes_side:
            return None
        best_yes_cents = yes_side[0][0] if isinstance(yes_side[0], list) else None
        if best_yes_cents is None:
            return None
        return float(best_yes_cents) / 100.0

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
                        close_str = m.get("close_time") or m.get("expiration_time")
                        # Kalshi returns ISO 8601 strings; we only need the date
                        resolution_date = None
                        if close_str:
                            try:
                                resolution_date = close_str[:10]  # YYYY-MM-DD
                            except (TypeError, IndexError):
                                resolution_date = None
                        # Derive station_code from the title for the 4 weather
                        # series; leave NULL for other markets.
                        station_code = _station_from_kalshi_title(title)
                        variable = _variable_from_kalshi_title(title)
                        cur.execute("""
                            INSERT INTO prediction_contracts
                                (exchange, contract_id, title,
                                 station_code, variable,
                                 resolution_date, is_active, raw_payload, last_seen_at)
                            VALUES ('kalshi', %s, %s, %s, %s, %s,
                                    %s, %s::jsonb, NOW())
                            ON CONFLICT (exchange, contract_id) DO UPDATE SET
                                title           = EXCLUDED.title,
                                station_code    = EXCLUDED.station_code,
                                variable        = EXCLUDED.variable,
                                resolution_date = EXCLUDED.resolution_date,
                                is_active       = EXCLUDED.is_active,
                                raw_payload     = EXCLUDED.raw_payload,
                                last_seen_at    = NOW()
                        """, (
                            ticker, title, station_code, variable,
                            resolution_date,
                            (m.get("status") or "").lower() == "open",
                            json.dumps(m),
                        ))
                        n += 1
        except Exception as e:
            logger.warning(f"_upsert_markets_to_pg failed (non-fatal): {e}")
            return 0
        logger.debug(f"prediction_contracts: upserted {n} rows")
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
)


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

    args = parser.parse_args()
    if args.cmd == "status":     return _cli_status()
    if args.cmd == "balance":    return _cli_balance()
    if args.cmd == "positions":  return _cli_positions()
    if args.cmd == "markets":    return _cli_markets(args)
    if args.cmd == "weather":    return _cli_weather(args)
    if args.cmd == "orderbook":  return _cli_orderbook(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
