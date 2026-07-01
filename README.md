# Blackhole Network (BHN)

A privacy-focused personal infrastructure platform built on WireGuard with defense-in-depth security and algorithmic trading. Single-operator — no customers, no public service offering.

---

## Domains

### FinancialBHN `[20%]`

Algorithmic paper trading via Alpaca across multiple strategies, paired with a financial intelligence layer covering market regime, macro indicators, ETF prices, and prediction markets. Currently in early validation — one strategy active as an operational test, others sidelined pending confirmation.

→ [docs/financialbhn-overview.md](docs/financialbhn-overview.md)

---

### WeatherBHN `[80%]`

A systematic, model-driven strategy trading daily high temperature contracts on Kalshi, the U.S.-regulated prediction market exchange. The full CP1→CP2→CP3→CP4 pipeline is live and running in DRY_RUN mode — XGBoost inference beats the calibrated NWS baseline by 0.29°F RMSE.

→ [docs/weatherbhn-overview.md](docs/weatherbhn-overview.md)

---

### SecurityBHN `[100%]`

Defense-in-depth security telemetry across the full node mesh, covering intrusion detection, threat intelligence, and network anomaly signals, surfaced through a Grafana operations dashboard.

→ [docs/securitybhn-overview.md](docs/securitybhn-overview.md)

---

### PokemonBHN `[50%]`

WOTC-era graded Pokémon card market intelligence pipeline — scarcity data from CGC/PSA population reports paired with eBay sold comps, normalized against a canonical grade catalog and keyed off a curated 637-card watchlist.

→ [docs/pokemonbhn-overview.md](docs/pokemonbhn-overview.md)

---

## License

Source-available — all rights reserved. Public for portfolio and reference purposes only. No license is granted to use, copy, modify, or distribute any part of this codebase without explicit written permission from the operator.
