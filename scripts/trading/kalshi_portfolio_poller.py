#!/usr/bin/env python3
"""
kalshi_portfolio_poller.py — BHN Kalshi positions + fills collector.

Polls /portfolio/positions and /portfolio/fills every 30 seconds
and writes snapshots to the kalshi_positions table so Grafana can
display open positions and unrealized P&L in real time.

Designed to run under systemd (bhn-kalshi-portfolio.service).

Usage (manual test):
  cd /opt/bhn/trading
  set -a && source /etc/bhn-trading/env && source /etc/bhn-trading/strat9.env && set +a
  python3 kalshi_portfolio_poller.py
"""
from __future__ import annotations

import sys
import time

import trading_core as tc

logger = tc.get_logger("strat_9_kalshi_portfolio_poller")

POLL_INTERVAL_S = 30    # seconds between portfolio fetches
LOG_INTERVAL_S  = 300   # log summary every 5 minutes


def main() -> None:
    try:
        from kalshi_client import KalshiClient
    except ImportError as e:
        logger.error(f"Cannot import KalshiClient: {e} — exiting")
        sys.exit(1)

    try:
        client = KalshiClient()
    except Exception as e:
        logger.error(f"KalshiClient init failed: {e} — exiting")
        sys.exit(1)

    logger.info(
        f"kalshi-portfolio-poller: start  "
        f"poll={POLL_INTERVAL_S}s  log_summary={LOG_INTERVAL_S}s"
    )

    polls_window  = 0
    rows_window   = 0
    errors_window = 0
    last_log_mono = time.monotonic()

    while True:
        try:
            result = client.fetch_kalshi_portfolio()
            rows_window += result.get("rows_upserted", 0)
            if result.get("positions_fetched", 0) == 0:
                logger.debug("portfolio: no open positions")
        except Exception as e:
            errors_window += 1
            logger.warning(f"portfolio poll error (non-fatal): {e}")

        polls_window += 1

        if time.monotonic() - last_log_mono >= LOG_INTERVAL_S:
            logger.info(
                f"kalshi-portfolio-poller: {polls_window} polls  "
                f"+{rows_window} rows  "
                f"{errors_window} errors  "
                f"(last {LOG_INTERVAL_S}s)"
            )
            polls_window  = 0
            rows_window   = 0
            errors_window = 0
            last_log_mono = time.monotonic()

        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
