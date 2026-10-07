"""Response-curve maths (B5, spec §7.2): adstock, Hill, the model, its analytic Jacobian, fitting, joint
moving-block bootstrap, and prediction. Pure numpy/scipy; persistence and orchestration live in fit_curves.py.

Model (normalised units: x = spend / median spend, y = net revenue / median revenue, b = baseline / its mean):
    a_t = x_t + theta * a_{t-1}            (geometric adstock; a_0 given)
    y_t = beta * Hill(a_t; K, S) + gamma * b_t,      Hill(a; K, S) = a^S / (K^S + a^S)
The curve is labelled "estimated": observational, calibrated later by outcomes (§10), never "true incremental".
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import least_squares

PARAMS = ("beta", "K", "S", "theta", "gamma")


def adstock(x: np.ndarray, theta: float, a0: float) -> tuple[np.ndarray, np.ndarray]:
    """Adstock series and its derivative with respect to theta."""
    a = np.empty_like(x, dtype=float)
    da = np.empty_like(x, dtype=float)
    prev, dprev = a0, 0.0
    for t, v in enumerate(x):
        prev, dprev = v + theta * prev, prev + theta * dprev
        a[t], da[t] = prev, dprev
    return a, da


def hill(a: np.ndarray, K: float, S: float) -> np.ndarray:
    a = np.maximum(a, 0.0)
    num = a ** S
    return num / (K ** S + num)


def predict(p: np.ndarray, x: np.ndarray, b: np.ndarray, a0: float) -> np.ndarray:
    beta, K, S, theta, gamma = p
    a, _ = adstock(x, theta, a0)
    return beta * hill(a, K, S) + gamma * b


def jacobian(p: np.ndarray, x: np.ndarray, b: np.ndarray, a0: float) -> np.ndarray:
    beta, K, S, theta, gamma = p
    a, da = adstock(x, theta, a0)
    a = np.maximum(a, 1e-12)
    f = hill(a, K, S)
    ks, as_ = K ** S, a ** S
    denom = (ks + as_) ** 2
    df_dK = -S * K ** (S - 1) * as_ / denom
    df_dS = f * (1 - f) * np.log(a / K)
    df_da = S * ks * a ** (S - 1) / denom
    return np.column_stack([f, beta * df_dK, beta * df_dS, beta * df_da * da, b])


@dataclass
class Fit:
    params: np.ndarray
    success: bool
    residuals: np.ndarray
    condition_number: float
    r2: float
    message: str = ""


def _bounds(cfg: dict) -> tuple[np.ndarray, np.ndarray]:
    lo = np.array([cfg["bounds"][k][0] for k in PARAMS])
    hi = np.array([cfg["bounds"][k][1] for k in PARAMS])
    return lo, hi


def r_squared(y: np.ndarray, yhat: np.ndarray) -> float:
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")


def fit(series: list[tuple[np.ndarray, np.ndarray, np.ndarray, float]], cfg: dict,
        init: np.ndarray | None = None) -> Fit:
    """Least squares over one or more (x, y, b, a0) series sharing the parameters (several = the pooled curve)."""
    lo, hi = _bounds(cfg)
    if init is None:
        y_all = np.concatenate([s[1] for s in series])
        init = np.array([max(2 * float(np.mean(y_all)), 1e-3), 1.0, 1.5, 0.1, 0.1])
    init = np.clip(init, lo + 1e-9, hi - 1e-9)

    def resid(p):
        return np.concatenate([predict(p, x, b, a0) - y for x, y, b, a0 in series])

    def jac(p):
        return np.vstack([jacobian(p, x, b, a0) for x, _, b, a0 in series])

    try:
        res = least_squares(resid, init, jac=jac, bounds=(lo, hi), x_scale="jac", max_nfev=200)
    except (ValueError, FloatingPointError) as exc:
        return Fit(init, False, np.array([]), float("inf"), float("nan"), str(exc))
    J = res.jac
    try:
        cond = float(np.linalg.cond(J))
    except np.linalg.LinAlgError:
        cond = float("inf")
    y_all = np.concatenate([s[1] for s in series])
    r2 = r_squared(y_all, y_all + res.fun)
    return Fit(res.x, bool(res.success) and np.all(np.isfinite(res.x)), res.fun, cond, r2, res.message)


def block_indices(n: int, block: int, draws: int, seed: int) -> np.ndarray:
    """Joint moving-block bootstrap: draw d resamples residual positions in 7-day blocks; the SAME indices are used
    for every budget unit in draw d, so correlated shocks stay correlated across the portfolio (spec §8.2)."""
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block))
    starts = rng.integers(0, max(n - block + 1, 1), size=(draws, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(draws, -1)[:, :n]
    return idx


def bootstrap(series: tuple[np.ndarray, np.ndarray, np.ndarray, float], base: Fit, idx: np.ndarray,
              cfg: dict) -> np.ndarray:
    """Residual moving-block bootstrap refits for one unit; returns (draws, 5) parameter sets."""
    x, y, b, a0 = series
    yhat = y + base.residuals
    out = np.empty((idx.shape[0], len(PARAMS)))
    for d in range(idx.shape[0]):
        y_star = yhat - base.residuals[idx[d]]
        f = fit([(x, y_star, b, a0)], cfg, init=base.params)
        out[d] = f.params
    return out


@dataclass
class CurveArtifact:
    """What B6 consumes: point params + bootstrap draws for the unit and its channel's pooled curve, scales, the
    shrinkage weight, the terminal adstock state and the forecast baseline level."""
    unit_id: str
    status: str                       # OK | POOLED | MODEL_UNAVAILABLE
    w: float
    median_spend: float
    median_revenue: float
    baseline_mean: float
    baseline_level: float             # normalised baseline carried forward (mean of the last 14 values)
    terminal_adstock: float           # normalised a_T at fit_ts (unit curve)
    params: np.ndarray | None
    draws: np.ndarray | None
    pooled_params: np.ndarray | None = None
    pooled_draws: np.ndarray | None = None
    pooled_terminal_adstock: float = 0.0
    diagnostics: dict = field(default_factory=dict)

    def revenue(self, spend_path: np.ndarray, draw: int | None = None) -> np.ndarray:
        """Expected daily net revenue (INR) for a future spend path, adstock continued from fit_ts."""
        if self.status == "MODEL_UNAVAILABLE":
            raise ValueError(f"{self.unit_id}: MODEL_UNAVAILABLE")
        x = np.asarray(spend_path, dtype=float) / self.median_spend
        b = np.full_like(x, self.baseline_level)
        out = np.zeros_like(x)
        if self.w > 0 and self.params is not None:
            p = self.params if draw is None else self.draws[draw]
            out += self.w * predict(p, x, b, self.terminal_adstock)
        if self.w < 1:
            pp = self.pooled_params if draw is None else self.pooled_draws[draw]
            out += (1 - self.w) * predict(pp, x, b, self.pooled_terminal_adstock)
        return out * self.median_revenue

    def marginal_roas(self, spend: float, draw: int | None = None, eps: float = 0.01) -> float:
        """Model-estimated steady-state marginal ROAS dR/ds at a constant daily spend (labelled 'model-estimated')."""
        h = 60
        lo = self.revenue(np.full(h, spend * (1 - eps)), draw)[-1]
        hi = self.revenue(np.full(h, spend * (1 + eps)), draw)[-1]
        return float((hi - lo) / (2 * eps * spend))
