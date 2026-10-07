"""B5 response-curve maths: analytic Jacobian = finite differences, parameter recovery on synthetic data, adstock,
joint bootstrap indices, artifact prediction (shrinkage, adstock continuation, diminishing marginal ROAS)."""

import numpy as np
import pytest
import yaml

from adapt.predict.curves import (
    CurveArtifact,
    adstock,
    block_indices,
    bootstrap,
    fit,
    hill,
    jacobian,
    predict,
)
from adapt.predict.fit_curves import MODELS_PATH

CFG = yaml.safe_load(MODELS_PATH.read_text(encoding="utf-8"))["response_curve"]


def test_adstock_recursion_and_derivative():
    a, da = adstock(np.array([1.0, 0.0, 0.0]), 0.5, 2.0)
    assert np.allclose(a, [1 + 0.5 * 2, 0.5 * 2.0, 0.25 * 2.0])
    h = 1e-6
    a2, _ = adstock(np.array([1.0, 0.0, 0.0]), 0.5 + h, 2.0)
    assert np.allclose(da, (a2 - a) / h, atol=1e-4)


def test_hill_shape():
    assert hill(np.array([0.0]), 1, 2)[0] == 0
    assert hill(np.array([1.0]), 1, 2)[0] == pytest.approx(0.5)  # half-saturation at K
    assert hill(np.array([1e6]), 1, 2)[0] == pytest.approx(1.0)


def test_analytic_jacobian_matches_finite_differences():
    rng = np.random.default_rng(0)
    x, b = rng.uniform(0.5, 1.5, 40), rng.uniform(0.8, 1.2, 40)
    p = np.array([2.0, 1.1, 1.7, 0.3, 0.4])
    J = jacobian(p, x, b, 1.0)
    for k in range(5):
        dp = np.zeros(5)
        dp[k] = 1e-6
        num = (predict(p + dp, x, b, 1.0) - predict(p - dp, x, b, 1.0)) / 2e-6
        assert np.allclose(J[:, k], num, rtol=1e-4, atol=1e-6), k


def synthetic(p, n=160, noise=0.03, seed=1):
    rng = np.random.default_rng(seed)
    x = np.clip(1 + 0.4 * np.sin(np.arange(n) / 9) + rng.normal(0, 0.25, n), 0.1, None)
    b = 1 + 0.1 * np.sin(np.arange(n) / 30)
    y = predict(p, x, b, x[:7].mean()) * rng.lognormal(0, noise, n)
    return x, y, b


def test_fit_recovers_marginal_response():
    true = np.array([3.0, 1.2, 1.6, 0.2, 0.3])
    x, y, b = synthetic(true)
    f = fit([(x, y, b, x[:7].mean())], CFG)
    assert f.success and f.r2 > 0.8
    art = CurveArtifact("u", "OK", 1.0, 1.0, 1.0, 1.0, 1.0, adstock(x, f.params[3], x[:7].mean())[0][-1], f.params,
                        np.array([f.params]))
    truth = CurveArtifact("u", "OK", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, true, np.array([true]))
    for s in (0.8, 1.0, 1.2):
        assert art.marginal_roas(s) == pytest.approx(truth.marginal_roas(s), rel=0.25)


def test_joint_block_indices():
    idx = block_indices(120, 7, 50, 3)
    assert idx.shape == (50, 120) and idx.min() >= 0 and idx.max() < 120
    assert np.all(np.diff(idx[:, :7], axis=1) == 1)  # contiguous 7-day blocks
    assert np.array_equal(idx, block_indices(120, 7, 50, 3))  # same seed -> same joint draws


def test_bootstrap_spreads_parameters_around_the_fit():
    x, y, b = synthetic(np.array([3.0, 1.2, 1.6, 0.2, 0.3]), noise=0.08)
    s = (x, y, b, x[:7].mean())
    base = fit([s], CFG)
    draws = bootstrap(s, base, block_indices(len(x), 7, 30, 9), CFG)
    assert draws.shape == (30, 5) and np.all(np.isfinite(draws))
    assert np.median(draws[:, 0]) == pytest.approx(base.params[0], rel=0.3)
    assert draws[:, 0].std() > 0


def test_artifact_shrinkage_and_diminishing_returns():
    p_unit, p_pool = np.array([4.0, 1.0, 1.5, 0.0, 0.0]), np.array([2.0, 1.0, 1.5, 0.0, 0.0])
    art = CurveArtifact("u", "OK", 0.25, 1000.0, 5000.0, 1.0, 0.0, 0.0, p_unit, np.array([p_unit]),
                        p_pool, np.array([p_pool]), 0.0)
    r = art.revenue(np.array([1000.0]))[0]
    assert r == pytest.approx(5000 * (0.25 * 4 * 0.5 + 0.75 * 2 * 0.5))  # Hill(1;1,1.5)=0.5
    m = [art.marginal_roas(s) for s in (500.0, 1000.0, 2000.0, 4000.0)]
    assert all(a > b for a, b in zip(m, m[1:], strict=False))  # diminishing marginal ROAS
    unavailable = CurveArtifact("u", "MODEL_UNAVAILABLE", 0, 1, 1, 1, 0, 0, None, None)
    with pytest.raises(ValueError):
        unavailable.revenue(np.array([1.0]))
