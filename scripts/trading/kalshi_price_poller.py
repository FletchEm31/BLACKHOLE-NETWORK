#!/usr/bin/env python3
"""
kalshi_price_poller.py — BHN fast Kalshi price collector.

Runs continuously, calling fetch_kalshi_markets() every 5 seconds
and writing price snapshots to weather_contract_prices.

Designed to run under systemd (bhn-kalshi-prices.service).
Logs a summary every 60 seconds rather than on every poll — at 5s
cadence that would be 12 lines/minute of pure noise.

Usage (manual test):
  cd /opt/bhn/trading
  set -a && source /etc/bhn-trading/env && source /etc/bhn-trading/strat9.env && set +a
  python3 kalshi_price_poller.py
"""
from __future__ import annotations

import sys
import time

import trading_core as tc

logger = tc.get_logger("strat_9_kalshi_price_poller")

POLL_INTERVAL_S = 5     # seconds between price fetches
LOG_INTERVAL_S  = 60    # seconds between summary log lines


def main() -> None:
    try:
        from weather_data_collector import fetch_kalshi_markets
    except ImportError as e:
        logger.error(f"Cannot import weather_data_collector: {e} — exiting")
        sys.exit(1)

    logger.info(
        f"kalshi-price-poller: start  poll={POLL_INTERVAL_S}s  "
        f"log_summary={LOG_INTERVAL_S}s"
    )

    polls_window       = 0
    rows_window        = 0
    errors_window      = 0
    last_log_mono      = time.monotonic()

    while True:
        try:
            rows = fetch_kalshi_markets(dry_run=False)
            rows_window += rows
        except Exception as e:
            errors_window += 1
            logger.debug(f"poll error (non-fatal): {e}")

        polls_window += 1

        if time.monotonic() - last_log_mono >= LOG_INTERVAL_S:
            logger.info(
                f"kalshi-price-poller: {polls_window} polls  "
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
