"""Detector maths (spec §6): transforms, out-of-sample STL forecast, robust z, PELT change points, ImpactCBA.

All functions are pure (numpy in, numpy out) so each is unit-tested on hand-made series.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.special import expit, logit
from statsmodels.tsa.seasonal import STL

PERIOD = 7
STL_OUTER_ITER = 5  # robustness re-weighting passes (statsmodels default 15; the weights settle well before 5)
# Stable components: statsmodels' default smoothers are flexible enough that the last week's "seasonal" values echo
# that week's noise and the forecast repeats it (measured: an outlier day 7 days earlier reappears as the expectation).
# A constant-per-weekday seasonal smoothed across all cycles and a 3-week trend keep a real weekly pattern and trend
# while ignoring single noisy days.
STL_SEASONAL = 15        # seasonal smoother length (odd); >= the number of cycles in the fit window
STL_SEASONAL_DEG = 0     # locally constant seasonal per weekday
STL_TREND = 21           # trend smoother length (odd, days)


# ---- transforms (metric_catalog.yaml) -------------------------------------------------------------------------
def to_transformed(num: np.ndarray, den: np.ndarray | None, transform: str, pseudo: float = 0.0,
                   pseudo_den: float = 0.0) -> np.ndarray:
    """Daily transformed values; NaN where undefined (e.g. no impressions). Level metrics pass den=None."""
    num = np.asarray(num, dtype=float)
    if transform == "log1p":
        return np.log1p(np.maximum(num, 0.0))
    den = np.asarray(den, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        if transform == "logit":
            return np.where(den > 0, logit((num + 0.5) / (den + 1.0)), np.nan)
        if transform == "log":
            ratio = (num + pseudo) / (den + pseudo_den)
            return np.where((den + pseudo_den > 0) & (ratio > 0), np.log(ratio), np.nan)
        if transform == "none":
            return np.where(den > 0, num / den, np.nan)
    raise ValueError(f"unknown transform {transform}")


def expected_numerator(yhat: np.ndarray, den: np.ndarray | None, transform: str, pseudo: float = 0.0,
                       pseudo_den: float = 0.0) -> np.ndarray:
    """Invert a forecast back to numerator units given the day's actual denominator (single-factor substitution)."""
    if transform == "log1p":
        return np.expm1(yhat)
    den = np.asarray(den, dtype=float)
    if transform == "logit":
        return expit(yhat) * (den + 1.0) - 0.5
    if transform == "log":
        return np.exp(yhat) * (den + pseudo_den) - pseudo
    if transform == "none":
        return yhat * den
    raise ValueError(f"unknown transform {transform}")


def interpolate(y: np.ndarray) -> np.ndarray:
    """Fill NaN gaps linearly (edges: nearest value) so STL can fit; all-NaN raises."""
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(y)
    if not ok.any():
        raise ValueError("series has no finite values")
    idx = np.arange(len(y))
    return np.interp(idx, idx[ok], y[ok])


# ---- out-of-sample STL forecast ---------------------------------------------------------------------------------
@dataclass(frozen=True)
class Forecast:
    yhat: np.ndarray        # transformed-scale expectation for each evaluated day
    resid_insample: np.ndarray


def stl_forecast(y_fit: np.ndarray, horizon: int, trend_points: int = 14) -> Forecast:
    """Fit STL (period 7, robust) on the fit window only, then expected_t = T_hat_t + S_hat_t where T_hat is an OLS line
    through the last `trend_points` fitted trend values, extrapolated, and S_hat is the same weekday's seasonal value
    from the last complete week of the fit window (spec §6). The evaluated days are never part of the fit."""
    y = interpolate(y_fit)
    n = len(y)
    res = STL(y, period=PERIOD, seasonal=STL_SEASONAL, seasonal_deg=STL_SEASONAL_DEG, trend=STL_TREND,
              robust=True).fit(outer_iter=STL_OUTER_ITER)
    trend, seasonal = np.asarray(res.trend), np.asarray(res.seasonal)
    k = min(trend_points, n)
    x = np.arange(n - k, n)
    slope, intercept = np.polyfit(x, trend[-k:], 1)
    t = np.arange(n, n + horizon)
    t_hat = intercept + slope * t
    s_hat = seasonal[n - PERIOD + (t - n) % PERIOD]
    return Forecast(yhat=t_hat + s_hat, resid_insample=np.asarray(res.resid))


def backtest_residuals(y: np.ndarray, end: int, fit_days: int, origins: int = 6, horizon: int = PERIOD,
                       trend_points: int = 14) -> np.ndarray | None:
    """Out-of-sample errors of the same forecast rule: for each of `origins` weekly origins before `end`, fit on the
    `fit_days` before the origin and forecast the next `horizon` days. These, not STL's in-sample residuals, measure
    how wrong the expected value really is (in-sample residuals of a flexible STL understate it several-fold and
    inflate every z). Returns None when the history is too short for at least 3 origins."""
    errs = []
    for k in range(1, origins + 1):
        origin = end - horizon * k
        if origin - fit_days < 0:
            break
        seg = y[origin - fit_days:origin]
        target = y[origin:origin + horizon]
        if not np.isfinite(seg).any():
            continue
        fc = stl_forecast(seg, horizon, trend_points)
        errs.append(target - fc.yhat)
    if len(errs) < 3:
        return None
    out = np.concatenate(errs)
    return out[np.isfinite(out)]


@dataclass(frozen=True)
class RobustZ:
    z: np.ndarray            # NaN where the day is undefined
    status: str              # OK | INSUFFICIENT_VARIANCE


def robust_z(resid_eval: np.ndarray, resid_insample: np.ndarray) -> RobustZ:
    """z = (r - median) / (1.4826 MAD) of the in-sample residuals. MAD = 0: r == median -> 0, otherwise the day is
    marked INSUFFICIENT_VARIANCE (z NaN) so STAT cannot fire; never a division by zero."""
    med = float(np.median(resid_insample))
    mad = float(np.median(np.abs(resid_insample - med)))
    r = np.asarray(resid_eval, dtype=float)
    if mad == 0.0:
        z = np.where(np.isclose(r, med), 0.0, np.nan)
        return RobustZ(z=z, status="INSUFFICIENT_VARIANCE")
    return RobustZ(z=(r - med) / (1.4826 * mad), status="OK")


def stat_fires(z: np.ndarray, z1: float, z2: float, consecutive: int) -> bool:
    """|z| >= z1 on one day, or |z| >= z2 on `consecutive` days in a row with the same sign."""
    z = np.asarray(z, dtype=float)
    if np.any(np.abs(np.nan_to_num(z)) >= z1):
        return True
    run, sign = 0, 0
    for v in z:
        s = 0 if not np.isfinite(v) or abs(v) < z2 else (1 if v > 0 else -1)
        run = run + 1 if s != 0 and s == sign else (1 if s != 0 else 0)
        sign = s
        if run >= consecutive:
            return True
    return False


# ---- change points ----------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Shift:
    fired: bool
    index: int | None        # index of the first day after the change, in the given series
    score: float             # mean after - mean before, in robust-scale units


def l2_changepoints(x: np.ndarray, pen: float, min_size: int = 3) -> list[int]:
    """Exact penalised L2 segmentation (optimal partitioning, the objective PELT prunes): minimise
    sum over segments of the within-segment sum of squares + pen per change point. Returns breakpoints like ruptures
    (segment ends, last = len(x)). O(n^2) with prefix sums: trivial for the 28-day detector window."""
    n = len(x)
    s1 = np.concatenate([[0.0], np.cumsum(x)])
    s2 = np.concatenate([[0.0], np.cumsum(x * x)])

    def cost(a: int, b: int) -> float:  # segment x[a:b]
        m = b - a
        return float(s2[b] - s2[a] - (s1[b] - s1[a]) ** 2 / m)

    best = np.full(n + 1, np.inf)
    best[0] = -pen
    prev = np.zeros(n + 1, dtype=int)
    for b in range(min_size, n + 1):
        for a in range(0, b - min_size + 1):
            if a != 0 and a < min_size:
                continue
            if not np.isfinite(best[a]):
                continue
            c = best[a] + pen + cost(a, b)
            if c < best[b]:
                best[b], prev[b] = c, a
    bkps, b = [], n
    while b > 0:
        bkps.append(b)
        b = prev[b]
    return sorted(bkps)


def pelt_shift(y: np.ndarray, scale: float, post_len: int, beta: float, min_size: int = 3) -> Shift:
    """Penalised L2 change points on the series standardised by the robust error scale; fires if a change point
    starts inside the last `post_len` days."""
    y = interpolate(y)
    if scale <= 0 or len(y) < 2 * min_size:
        return Shift(False, None, 0.0)
    std = (y - np.median(y)) / scale
    bkps = l2_changepoints(std, beta, min_size)
    inside = [b for b in bkps[:-1] if b >= len(y) - post_len]
    if not inside:
        return Shift(False, None, 0.0)
    b = inside[0]
    prev = [p for p in bkps[:-1] if p < b]
    start = prev[-1] if prev else 0
    return Shift(True, b, float(np.mean(std[b:]) - np.mean(std[start:b])))


# ---- ImpactCBA (spec §22.6) -------------------------------------------------------------------------------------
def impact_cba(metric: str, d: dict[str, np.ndarray], exp_metric: np.ndarray, act_metric: np.ndarray,
               cm: float, ref: dict[str, float]) -> np.ndarray:
    """Per-day CBA difference (expected - actual) from replacing one metric's actual value by its expectation,
    holding every other funnel quantity at that day's actual value (pre-window reference where a day's value is
    undefined). Positive = expected CBA exceeded actual (a shortfall)."""
    imps, clicks, orders, spend = d["impressions"], d["clicks"], d["attributed_orders"], d["spend"]
    rev = d["attributed_net_revenue"]
    with np.errstate(divide="ignore", invalid="ignore"):
        ctr = np.where(imps > 0, clicks / imps, ref["ctr"])
        cvr = np.where(clicks > 0, orders / clicks, ref["cvr"])
        aov = np.where(orders > 0, rev / orders, ref["aov"])
    if metric == "CTR":
        return imps * (exp_metric - act_metric) * cvr * aov * cm
    if metric == "CVR":
        return clicks * (exp_metric - act_metric) * aov * cm
    if metric == "AOV":
        return orders * (exp_metric - act_metric) * cm
    if metric == "CPM":
        with np.errstate(divide="ignore", invalid="ignore"):
            inv = np.where((exp_metric > 0) & (act_metric > 0), 1 / exp_metric - 1 / act_metric, 0.0)
        return 1000 * spend * inv * ctr * cvr * aov * cm
    if metric == "CPA":
        with np.errstate(divide="ignore", invalid="ignore"):
            exp_orders = np.where(exp_metric > 0, spend / exp_metric, 0.0)
        return (exp_orders - orders) * aov * cm
    if metric == "ROAS":
        return spend * (exp_metric - act_metric) * cm
    if metric == "POAS":
        return spend * (exp_metric - act_metric)
    if metric == "SESSION_CLICK":
        return clicks * (exp_metric - act_metric) * ref["session_cvr"] * aov * cm
    if metric == "SPEND":
        return exp_metric - act_metric
    if metric == "SKU_UNITS":
        return (exp_metric - act_metric) * ref["unit_contribution"]
    raise ValueError(f"no ImpactCBA formula for {metric}")
