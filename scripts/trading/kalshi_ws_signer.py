#!/usr/bin/env python3
"""
Kalshi WS handshake signer -- LA side ONLY. Holds the private RSA key so it
never has to leave this host (operator decision 2026-07-21: NJ's WS relay
holds only the public KALSHI_KEY_ID; NJ calls this endpoint over the
WireGuard mesh for a fresh signed handshake header set before every
(re)connect).

Binds ONLY to LA's mesh IP (10.8.0.1), not 0.0.0.0 -- unreachable from the
public internet by construction. Additionally checks the client IP is
NJ's mesh IP (10.8.0.5) before signing anything, so nothing else on the
mesh can obtain a signature under our Kalshi identity even if it somehow
reaches this port.

Signs specifically for wss://external-api-ws.kalshi.com/trade-api/ws/v2
(method=GET, path=/trade-api/ws/v2) -- reuses kalshi_client.py's existing
_load_private_key()/_sign_request() rather than re-implementing RSA-PSS
signing. Uses KALSHI_ENV=prod credentials (strat9.env) -- public market
data should come from prod, not demo, per this project's established rule
that demo Kalshi is execution-mechanics-testing only, never a signal/data
source.

This process does NOT place orders, does NOT read/write any trading
table, and does NOT open a WS connection itself -- it only computes one
signature per request.

Usage:
    python3 kalshi_ws_signer.py

Environment:
    Reads /etc/bhn-trading/strat9.env directly for KALSHI_KEY_ID /
    KALSHI_PRIVATE_KEY_PATH / KALSHI_PRIVATE_KEY_PASSWORD.
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BIND_HOST = "10.8.0.1"
BIND_PORT = 8097
ALLOWED_CLIENT_IPS = {"10.8.0.5"}  # NJ's mesh IP only
WS_PATH = "/trade-api/ws/v2"


def _prime_env() -> None:
    # kalshi_client.py imports trading_core, which strictly validates the
    # FULL trading env (Alpaca keys, PG creds, etc.) at import time, not
    # just the Kalshi-specific vars -- strat9.env first (so its prod
    # KALSHI_ENV/KALSHI_KEY_ID win), then the base env file to fill in
    # everything else trading_core requires, via setdefault so strat9.env's
    # values are never overridden. MUST run before importing kalshi_client
    # below, since that import triggers trading_core's strict env check
    # immediately at module load time.
    for path in ("/etc/bhn-trading/strat9.env", "/etc/bhn-trading/env"):
        p = Path(path)
        if not p.is_file():
            sys.exit(f"ERROR: {p} not found")
        for ln in p.read_text().splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_prime_env()

sys.path.insert(0, "/opt/bhn/trading")
import kalshi_client as kc  # reuse _load_private_key / _sign_request, don't re-derive

_key_id = os.environ.get("KALSHI_KEY_ID", "").strip()
_key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")
_key_password = os.environ.get("KALSHI_PRIVATE_KEY_PASSWORD") or None
if not _key_id or not _key_path:
    sys.exit("ERROR: KALSHI_KEY_ID / KALSHI_PRIVATE_KEY_PATH not set in strat9.env")

_private_key = kc._load_private_key(_key_path, _key_password)  # loaded once at startup


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep the systemd journal quiet -- no per-request logging

    def do_GET(self):
        if self.client_address[0] not in ALLOWED_CLIENT_IPS:
            self.send_response(403)
            self.end_headers()
            return
        if self.path != "/kalshi-ws-signature":
            self.send_response(404)
            self.end_headers()
            return

        ts_ms = int(time.time() * 1000)
        signature = kc._sign_request(_private_key, ts_ms, "GET", WS_PATH)
        body = json.dumps({
            "key_id": _key_id,
            "signature": signature,
            "timestamp_ms": ts_ms,
        }).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    server = ThreadingHTTPServer((BIND_HOST, BIND_PORT), Handler)
    print(f"kalshi_ws_signer listening on {BIND_HOST}:{BIND_PORT} (allowlist: {ALLOWED_CLIENT_IPS})")
    server.serve_forever()


if __name__ == "__main__":
    main()
