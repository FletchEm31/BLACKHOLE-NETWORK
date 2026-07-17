"""Kalshi fee calculator — mirrors scripts/trading/fee_calculator.py exactly
(post July 2025 formula). Duplicated rather than imported: this service
deploys independently of scripts/trading/ and the formula is 5 lines,
trivial to keep faithfully in sync by inspection.

Maker: ceil(0.0175 * p * (1-p) * n * 100) / 100
Taker: ceil(0.07  * p * (1-p) * n * 100) / 100

p = contract price (0.0-1.0), n = number of contracts.

The live trading architecture places maker-only resting limit orders
(per operator confirmation 2026-07-17), so every calculator in this
dashboard defaults to maker_fee — taker is exposed only as a minor,
explicitly-labeled preview toggle, never the default.
"""
import math


def maker_fee(p: float, n: int = 1) -> float:
    p = max(0.0, min(1.0, float(p)))
    return math.ceil(0.0175 * p * (1 - p) * n * 100) / 100


def taker_fee(p: float, n: int = 1) -> float:
    p = max(0.0, min(1.0, float(p)))
    return math.ceil(0.07 * p * (1 - p) * n * 100) / 100
