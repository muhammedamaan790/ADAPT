"""DiD (Stage 3, spec §7.1 level 3, §22.9): the exact log-scale formula, interval coverage on hand-made panels with a
known injected effect, the pre-trend gate (no evidence of a material violation, never "proof"), and the method choice
(DiD for inventory / price incidents, synthetic control otherwise)."""

import numpy as np
import pytest

from adapt.diagnose.causal.did import PRE_TREND_MAX_WEEKLY, did
from adapt.diagnose.causal.estimate import causal_config, method_for

CFG = causal_config()


def panel(effect=0.0, trend=0.0, n_controls=10, days=63, start=56, seed=0):
    """Treated = the controls' common factor + its own level and noise; `trend` adds a treated-only drift per day."""
    rng = np.random.default_rng(seed)
    t = np.arange(days)
    common = 0.15 * np.sin(2 * np.pi * t / 7) + np.cumsum(rng.normal(0, 0.02, days))
    X = np.column_stack([np.log(1.5 + 0.3 * j) + common + rng.normal(0, 0.03, days) for j in range(n_controls)])
    y = np.log(2.2) + common + rng.normal(0, 0.03, days) + trend * t
    y[start:] += np.log(1 + effect)
    return y, X, [f"C{j:02d}" for j in range(n_controls)]


def test_the_exact_log_scale_formula():
    y, X, ids = panel(0.0, seed=3)
    r = did(y, X, ids, 56, 7, np.ones(7), CFG, seed=1)
    sel = [ids.index(c) for c in r.controls]
    c = X[:, sel].mean(axis=1)
    pre, post = slice(28, 56), slice(56, 63)
    delta = (y[post].mean() - y[pre].mean()) - (c[post].mean() - c[pre].mean())
    assert r.effect_pct == pytest.approx(np.exp(delta) - 1, abs=1e-12)
    assert r.weights == [pytest.approx(1 / len(sel))] * len(sel)            # equal weights


@pytest.mark.parametrize("effect", [-0.20, 0.0, 0.15])
def test_recovers_an_injected_effect_with_a_covering_interval(effect):
    errs, hits, trend_ok = [], 0, 0
    for seed in range(20):
        y, X, ids = panel(effect, seed=seed)
        r = did(y, X, ids, 56, 7, np.ones(7), CFG, seed=1)
        errs.append(abs(r.effect_pct - effect))
        hits += r.ci_lo <= effect <= r.ci_hi
        trend_ok += r.pre_trend["passed"]
    assert np.median(errs) < 0.03 and hits >= 15                           # 90% CI: >= 75% of 20 panels
    # parallel by construction: a 90% slope interval wrongly flags ~10% of panels, never most of them
    assert trend_ok >= 15


def test_a_diverging_pre_trend_fails_the_gate():
    y, X, ids = panel(0.0, trend=0.01, seed=5)                              # +7% per week treated-only drift
    r = did(y, X, ids, 56, 7, np.ones(7), CFG, seed=1)
    assert not r.pre_trend["passed"] and abs(r.pre_trend["slope_per_week"]) >= PRE_TREND_MAX_WEEKLY


def test_method_choice():
    assert method_for("inventory") == "did" and method_for("price") == "did"
    assert method_for("auction") == method_for("creative_fatigue") == method_for(None) == "synthetic_control"
