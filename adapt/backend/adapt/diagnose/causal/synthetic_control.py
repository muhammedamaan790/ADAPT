"""Synthetic control maths (Stage 2 ★, spec §7.1 level 3). Pure numpy/scipy: every function is unit-tested on
hand-made panels. Data access, gates and persistence live in estimate.py.

On log daily series of the treated metric y (T,) and the eligible controls X (T, J), with the intervention at index s:
  fit window     = [s - holdout - fit, s - holdout)   control selection, weights, lambda
  holdout window = [s - holdout, s)                     pre-fit sMAPE gate only (untouched)
  post window    = [s, s + n_post)
Controls: top K by Pearson correlation with y over the fit window (ties broken by entity id).
Weights:  min ||y_c - X_c w||^2 + lambda ||w||^2  s.t.  w >= 0, sum w = 1  (SLSQP; lambda = r tr(X_c'X_c) / J),
          where _c = deviation from the series' own fit-window mean ("demeaned" level; the counterfactual is the
          treated series' fit mean plus the weighted control deviations). Plain simplex weights on log levels cannot
          match a treated unit whose ROAS level differs from every control's, so they would fail the pre-fit gate on
          nearly every campaign; the demeaned variant (Doudchenko & Imbens 2016; Ferman & Pinto 2021) keeps the convex
          weights and the gates and only drops the level-matching requirement.
Effect on the ORIGINAL scale: effect% = sum_t a_t e^{y_t} / sum_t a_t e^{yhat_t} - 1, with a_t the day's
aggregation weight (spend for ROAS: the window ROAS = sum revenue / sum spend; 1 for a level metric).
Interval: moving-block bootstrap of the pre-period log residuals (7-day blocks) WITH REFIT: each draw rebuilds the
fit window as fitted values + a resampled residual path, refits the weights and the level on it, predicts the post
window and adds a second resampled residual path. The in-sample (fit-window) residuals are inflated by
sqrt(n / (n - p)), p = active weights + the level (degrees-of-freedom correction). Adding residual noise to one
fixed counterfactual (no refit) ignores the uncertainty of the estimated weights and level; measured on hand-made
panels it gave a +-1 pp interval around estimates that were 2-3 pp off, i.e. a placebo false-effect rate far above
the spec's 10%.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize


def select_controls(y_fit: np.ndarray, X_fit: np.ndarray, ids: list[str], k: int) -> list[int]:
    """Indices of the top-k controls by Pearson correlation with y over the fit window (ties: entity id)."""
    scores = []
    for j in range(X_fit.shape[1]):
        x = X_fit[:, j]
        r = float(np.corrcoef(y_fit, x)[0, 1]) if x.std() > 0 and y_fit.std() > 0 else -np.inf
        scores.append((-r if np.isfinite(r) else np.inf, ids[j], j))
    return [j for _, _, j in sorted(scores)[:k]]


def fit_weights(y_c: np.ndarray, X_c: np.ndarray, ridge: float) -> tuple[np.ndarray, float]:
    """Simplex-constrained ridge weights (convex). Returns (w, lambda)."""
    J = X_c.shape[1]
    lam = ridge * float(np.trace(X_c.T @ X_c)) / J
    G, b = X_c.T @ X_c + lam * np.eye(J), X_c.T @ y_c

    def f(w):
        r = y_c - X_c @ w
        return float(r @ r + lam * (w @ w))

    def grad(w):
        return 2 * (G @ w - b)

    res = minimize(f, np.full(J, 1.0 / J), jac=grad, method="SLSQP", bounds=[(0.0, 1.0)] * J,
                   constraints=[{"type": "eq", "fun": lambda w: float(w.sum() - 1.0), "jac": lambda w: np.ones(J)}],
                   options={"maxiter": 500, "ftol": 1e-12})
    w = np.clip(res.x, 0.0, None)
    return w / w.sum(), lam


def smape(actual: np.ndarray, pred: np.ndarray) -> float:
    a, p = np.asarray(actual, dtype=float), np.asarray(pred, dtype=float)
    denom = (np.abs(a) + np.abs(p)) / 2
    return float(np.mean(np.where(denom > 0, np.abs(a - p) / np.where(denom > 0, denom, 1.0), 0.0)))


def block_paths(resid: np.ndarray, length: int, draws: int, block: int, seed: int) -> np.ndarray:
    """(draws, length) moving-block resampled residual paths (7-day blocks keep the weekly autocorrelation)."""
    rng = np.random.default_rng(seed)
    n = len(resid)
    b = min(block, n)
    n_blocks = int(np.ceil(length / b))
    starts = rng.integers(0, n - b + 1, size=(draws, n_blocks))
    idx = (starts[:, :, None] + np.arange(b)[None, None, :]).reshape(draws, -1)[:, :length]
    return resid[idx]


@dataclass
class SCResult:
    start: int
    controls: list[str]
    weights: list[float]
    lam: float
    holdout_smape: float
    effect_pct: float
    ci_lo: float
    ci_hi: float
    observed: float                 # window metric, original scale
    counterfactual: float
    counterfactual_level_sum: float  # sum_t a_t e^{yhat_t} (e.g. counterfactual revenue for ROAS)
    paths: dict = field(default_factory=dict)


def synthetic_control(y: np.ndarray, X: np.ndarray, ids: list[str], start: int, n_post: int, agg: np.ndarray,
                      cfg: dict, seed: int) -> SCResult | None:
    """One synthetic-control estimate with the intervention at index `start`. None if the controls cannot be
    selected (fewer than one with variance). y, X are log series covering at least [start - fit - holdout,
    start + n_post); agg is the post window's aggregation weights (length n_post)."""
    f, h = int(cfg["fit_days"]), int(cfg["holdout_days"])
    lo = start - f - h
    if lo < 0 or start + n_post > len(y):
        raise ValueError("series too short for the windows")
    fit = slice(lo, lo + f)
    sel = select_controls(y[fit], X[fit], ids, int(cfg["top_k"]))
    if not sel:
        return None
    Xs = X[:, sel]
    if cfg.get("level", "demeaned") == "demeaned":
        y_mu, x_mu = float(y[fit].mean()), Xs[fit].mean(axis=0)
    else:
        y_mu, x_mu = 0.0, np.zeros(len(sel))
    w, lam = fit_weights(y[fit] - y_mu, Xs[fit] - x_mu, float(cfg["ridge"]))
    yhat = y_mu + (Xs - x_mu) @ w
    hold = slice(start - h, start)
    post = slice(start, start + n_post)
    sm = smape(np.exp(y[hold]), np.exp(yhat[hold]))
    obs = float(np.sum(agg * np.exp(y[post])))
    cf = float(np.sum(agg * np.exp(yhat[post])))
    resid = (y - yhat)[lo:start].copy()
    p_eff = int((w > 1e-6).sum()) + 1                       # active weights + the level
    resid[:f] *= np.sqrt(f / max(f - p_eff, 1))             # in-sample residuals understate out-of-sample error
    draws, block = int(cfg["bootstrap_draws"]), int(cfg["block_days"])
    e_fit = block_paths(resid, f, draws, block, seed)
    e_post = block_paths(resid, n_post, draws, block, seed + 1)
    level = cfg.get("level", "demeaned") == "demeaned"
    cf_k = np.empty(draws)
    for k in range(draws):
        y_star = yhat[fit] + e_fit[k]
        mu_k = float(y_star.mean()) if level else 0.0
        w_k, _ = fit_weights(y_star - mu_k, Xs[fit] - x_mu, float(cfg["ridge"]))
        cf_k[k] = np.sum(agg * np.exp(mu_k + (Xs[post] - x_mu) @ w_k + e_post[k]))
    eff_k = obs / cf_k - 1
    a = (1 - float(cfg["ci"])) / 2
    return SCResult(start=start, controls=[ids[j] for j in sel], weights=[float(x) for x in w], lam=lam,
                    holdout_smape=sm, effect_pct=obs / cf - 1, ci_lo=float(np.quantile(eff_k, a)),
                    ci_hi=float(np.quantile(eff_k, 1 - a)), observed=obs / float(np.sum(agg)),
                    counterfactual=cf / float(np.sum(agg)), counterfactual_level_sum=cf,
                    paths={"y": y[lo:start + n_post].tolist(), "yhat": yhat[lo:start + n_post].tolist()})
