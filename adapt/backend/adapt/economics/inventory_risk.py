"""Inventory risk predicate (spec §0.5, §5, §8.1, §22.9): one stage switch, used by portfolio_economics, the optimizer's
inventory gate, the S3 safety candidate, the evidence inventory module and the safety monitor.

Stage 1, PROJECTED_SHORTFALL: shortfall_k = max(projected_k - (available_k - safety_stock_k), 0); at risk iff > 0.
Stage 2, STOCKOUT_PROBABILITY (NB2): demand over the horizon D ~ NegBin(mean m, dispersion r), Var = m + m^2 / r,
  scipy nbinom(n = r, p = r / (r + m)); m = max(sum of the champion demand model's P50 over H + effective delta units,
  0); available_count = floor(max(on_hand - reserved + inbound_confidence x inbound within H, 0));
  P(stockout) = 1 - nbinom.cdf(available_count, r, r / (r + m)); at risk iff P > p_unsafe (0.3).
  r per category by method of moments on the trailing 56 days of observed daily demand counts:
  r = mean^2 / max(var - mean, 1e-6), capped at 1e6 (~ Poisson). Applied as given to the H-day mean (a daily r on an
  H-day sum overstates the variance of iid days: conservative, i.e. more SKUs flagged; stated in the contract).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml
from scipy.stats import nbinom

CONFIG = Path(__file__).resolve().parents[1] / "config" / "inventory_risk.yaml"
PROJECTED_SHORTFALL, STOCKOUT_PROBABILITY = "PROJECTED_SHORTFALL", "STOCKOUT_PROBABILITY"


@lru_cache
def risk_config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def method_of_moments_r(counts: np.ndarray, cap: float = 1e6) -> float:
    """NB2 dispersion from daily counts: mean^2 / max(var - mean, 1e-6), capped (Poisson-like data -> cap)."""
    c = np.asarray(counts, dtype=float)
    if c.size < 2 or c.mean() <= 0:
        return cap
    m, v = float(c.mean()), float(c.var(ddof=1))
    return float(min(m * m / max(v - m, 1e-6), cap))


def stockout_probability(mean_demand: np.ndarray, available: np.ndarray, r: np.ndarray) -> np.ndarray:
    """P(D > available_count) per SKU; m <= 0 -> 0 (nothing can be demanded); vectorised."""
    m = np.maximum(np.asarray(mean_demand, dtype=float), 0.0)
    count = np.floor(np.maximum(np.asarray(available, dtype=float), 0.0))
    r = np.asarray(r, dtype=float)
    out = np.zeros_like(m)
    pos = m > 0
    if pos.any():
        out[pos] = 1.0 - nbinom.cdf(count[pos], r[pos], r[pos] / (r[pos] + m[pos]))
    return np.clip(out, 0.0, 1.0)


def shortfall(projected: np.ndarray, available: np.ndarray, safety_stock: np.ndarray) -> np.ndarray:
    return np.maximum(np.asarray(projected) - (np.asarray(available) - np.asarray(safety_stock)), 0.0)


def status(kind: str, value: float, projected: float, available: float, safety_stock: float,
           p_unsafe: float) -> str:
    if kind == PROJECTED_SHORTFALL:
        return "OK" if projected <= available - safety_stock else ("AT_RISK" if projected <= available else "SHORT")
    return "OK" if value <= p_unsafe else ("SHORT" if projected > available else "AT_RISK")
