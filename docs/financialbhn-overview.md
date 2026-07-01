# FinancialBHN — Trading & Financial Intelligence

**Status:** Early validation | **Progress:** 20%

## What It Is

Algorithmic paper trading via Alpaca across three accounts, paired with a financial intelligence layer that ingests macro, ETF, sentiment, earnings, and options data into PostgreSQL and surfaces it through six Grafana dashboards.

**Current state:** Only Strat 13 (`BHN-RSI-INTRADAY`) is live as an operational test to validate execution and protocol. All other strategies are sidelined pending that validation completing cleanly.

---

## Trading Stack

Runs on the NJ trading node. Paper trading via Alpaca.

**Strategy roster (configured, not all active):**

| Account | Strategy | Allocation | Status |
|---|---|---|---|
| BHN-STRAT-PRIMARY | BHN-NASDAQ-LONG (Strat 6) | $40,000 | Sidelined |
| BHN-STRAT-PRIMARY | BHN-NASDAQ-SHORT (Strat 7) | $40,000 | Sidelined — pending Strat 6 |
| BHN-STRAT-PRIMARY | BHN-SECTOR-ROTATION (Strat 8) | $20,000 | Sidelined |
| BHN-STRAT-FUNDAMENTAL | BHN-MEAN-REVERSION (Strat 3) | $20,000 | Sidelined |
| BHN-STRAT-SIGNALS | BHN-MOMENTUM (Strat 4) | $12,500 | Sidelined |
| BHN-STRAT-SIGNALS | BHN-RSI-INTRADAY (Strat 13) | $12,500 | **ACTIVE** — operational test |

**Core scripts (`scripts/trading/`):**
- `trading_core.py` — Alpaca + PostgreSQL integration
- `strategy_*.py` — individual strategy implementations
- `master_killswitch.py` — emergency halt + flatten all positions
- `reconciliation_daemon.py` — position reconciliation

---

## Financial Intelligence Layer

Six Grafana dashboards (VPN access only):
- **BHN Market Intelligence** — regime, ETFs, macro, sentiment, earnings, analyst ratings
- **BHN Trade Execution & Operations** — signals, P&L, paper trades, reconciliation
- **BHN Derivatives & Options Markets** — IV, Greeks, open interest, options chain
- **BHN Prediction & Alternative Markets** — Kalshi/Polymarket, weather markets
- **BHN Commodities & Tangible Asset Markets** — energy, agriculture, precious metals
- **BHN Infrastructure & Security Operations** — node health, security events, pulse

**Data collectors (`scripts/collectors/`):**
- `macro_collector.py` — FRED macro series (daily)
- `market_collector.py` — Alpaca ETF price data (daily)
- `sentiment_collector.py` — Fear/greed, AAII sentiment (daily)

**Data sources:** FRED (macro), Alpaca (market data), USDA (agriculture), EIA (energy), 32 ETF tickers across sector, fixed income, commodity, and international categories.

---

## Roadmap

1. Complete Strat 13 operational validation
2. Re-enable Strat 3 (Mean Reversion) and Strat 4 (Momentum) on paper accounts
3. Re-enable Strat 6 (NASDAQ Long) after Strat 3/4 complete 30-day clean run
4. Strat 7 (NASDAQ Short) last — requires Strat 6 validation
5. Wire Grafana alerting (currently deployed but not configured)
