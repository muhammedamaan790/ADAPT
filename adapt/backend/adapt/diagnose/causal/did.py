"""Difference-in-differences (Stage 3, spec §7.1 level 3 "DiD for inventory/price", §22.9 "DiD"). Pure numpy.

The same selected controls as the synthetic control (top K by Pearson correlation over the fit window, ties by entity
id) with EQUAL weights; on the log series y (treated) and c_t = mean of the selected control logs:
  delta = (mean_post y - mean_pre y) - (mean_post c - mean_pre c),   effect% = e^delta - 1
pre = the 28 pre days [start - fit - holdout, start), post = [start, start + n_post).
Counterfactual (for the accounting translation): yhat_t = c_t + mean_pre(y - c).
Gates added to the common ones (in estimate.py):
- pre-fit on the 7-day holdout: sMAPE of e^{c + mean_fit(y - c)} against e^y (the synthetic control's 15% rule);
- pre-trend, worded "no evidence of a material violation", never "parallel trends proven": the OLS slope of
  d_t = y_t - c_t over the 28 pre days with a 90% t interval whose error is max(Newey-West HAC lag 6, classical OLS);
  the gate passes iff the interval includes 0 AND |slope| x 7 < 2% (log points per week ~ relative change).
Effect interval: moving-block bootstrap of the pre-period residuals of d (7-day blocks): delta_k = delta +
mean(e_post_k) - mean(e_pre_k), so both windows' noise enters; effect CI = e^{quantiles} - 1.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.stats import t as student_t

from adapt.diagnose.causal.synthetic_control import block_paths, select_controls, smape

PRE_TREND_MAX_WEEKLY = 0.02


@dataclass
class DiDResult:
    start: int
    controls: list[str]
    weights: list[float]
    lam: float
    holdout_smape: float
    effect_pct: float
    ci_lo: float
    ci_hi: float
    observed: float
    counterfactual: float
    counterfactual_level_sum: float
    pre_trend: dict = field(default_factory=dict)
    paths: dict = field(default_factory=dict)


def _slope_hac(d: np.ndarray, lags: int) -> tuple[float, float]:
    """OLS slope of d on t and its standard error: max(Newey-West (Bartlett), classical OLS)."""
    n = len(d)
    t = np.arange(n, dtype=float)
    t -= t.mean()
    sxx = float((t * t).sum())
    b = float((t * (d - d.mean())).sum() / sxx)
    u = d - d.mean() - b * t
    g = t * u
    s = float((g * g).sum())
    for k in range(1, min(lags, n - 1) + 1):
        s += 2 * (1 - k / (lags + 1)) * float((g[k:] * g[:-k]).sum())
    hac = float(np.sqrt(max(s, 0.0) * n / (n - 2)) / sxx)
    ols = float(np.sqrt(float((u * u).sum()) / (n - 2) / sxx))
    # never narrower than the classical OLS error: Newey-West is biased downward in small samples (OLS residuals
    # carry a slight negative autocorrelation it then subtracts); positive autocorrelation still widens it
    return b, max(hac, ols)


def did(y: np.ndarray, X: np.ndarray, ids: list[str], start: int, n_post: int, agg: np.ndarray, cfg: dict,
        seed: int) -> DiDResult | None:
    f, h = int(cfg["fit_days"]), int(cfg["holdout_days"])
    lo = start - f - h
    if lo < 0 or start + n_post > len(y):
        raise ValueError("series too short for the windows")
    fit, hold, pre, post = slice(lo, lo + f), slice(start - h, start), slice(lo, start), slice(start, start + n_post)
    sel = select_controls(y[fit], X[fit], ids, int(cfg["top_k"]))
    if not sel:
        return None
    c = X[:, sel].mean(axis=1)                                           # equal weights
    d = y - c
    delta = float((y[post].mean() - y[pre].mean()) - (c[post].mean() - c[pre].mean()))
    yhat = c + float(d[pre].mean())
    hold_pred = c[hold] + float(d[fit].mean())
    sm = smape(np.exp(y[hold]), np.exp(hold_pred))
    # pre-trend: OLS slope of d over the pre days with a Newey-West (HAC) standard error, lag = block - 1, and a
    # t interval on n - 2 df. (A moving-block bootstrap of 28 residuals in 7-day blocks measured ~30% too narrow:
    # a 35% false-flag rate on parallel panels instead of the nominal 10%.)
    dp = d[pre]
    b, se = _slope_hac(dp, int(cfg["block_days"]) - 1)
    a = (1 - float(cfg["ci"])) / 2
    q = float(student_t.ppf(1 - a, len(dp) - 2))
    s_lo, s_hi = b - q * se, b + q * se
    pre_trend = {"slope_per_week": round(b * 7, 5), "ci_per_week": [round(s_lo * 7, 5), round(s_hi * 7, 5)],
                 "max_per_week": PRE_TREND_MAX_WEEKLY,
                 "passed": bool(s_lo <= 0 <= s_hi and abs(b * 7) < PRE_TREND_MAX_WEEKLY)}
    draws, block = int(cfg["bootstrap_draws"]), int(cfg["block_days"])
    # effect interval: noise of both windows from the pre-period residuals of d
    e = dp - dp.mean()
    e_pre = block_paths(e, len(dp), draws, block, seed)
    e_post = block_paths(e, n_post, draws, block, seed + 1)
    delta_k = delta + e_post.mean(axis=1) - e_pre.mean(axis=1)
    obs = float(np.sum(agg * np.exp(y[post])))
    cf = float(np.sum(agg * np.exp(yhat[post])))
    return DiDResult(start=start, controls=[ids[j] for j in sel], weights=[1.0 / len(sel)] * len(sel), lam=0.0,
                     holdout_smape=sm, effect_pct=float(np.exp(delta) - 1),
                     ci_lo=float(np.exp(np.quantile(delta_k, a)) - 1),
                     ci_hi=float(np.exp(np.quantile(delta_k, 1 - a)) - 1), observed=obs / float(np.sum(agg)),
                     counterfactual=cf / float(np.sum(agg)), counterfactual_level_sum=cf, pre_trend=pre_trend,
                     paths={"y": y[lo:start + n_post].tolist(), "yhat": yhat[lo:start + n_post].tolist()})
