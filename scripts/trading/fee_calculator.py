"""
Kalshi fee calculator — post July 2025 formula.

Maker: ceil(0.0175 * p * (1-p) * n * 100) / 100
Taker: ceil(0.07  * p * (1-p) * n * 100) / 100

p = contract price (0.0–1.0)
n = number of contracts
"""
import math


def maker_fee(p: float, n: int = 1) -> float:
    p = max(0.0, min(1.0, float(p)))
    return math.ceil(0.0175 * p * (1 - p) * n * 100) / 100


def taker_fee(p: float, n: int = 1) -> float:
    p = max(0.0, min(1.0, float(p)))
    return math.ceil(0.07 * p * (1 - p) * n * 100) / 100
