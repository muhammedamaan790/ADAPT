"""B8 outcomes + calibration: verdict boundaries (T45), counterfactual effect distribution, calibration arithmetic,
negative outcome lowers the factor, idempotency, exclusions (INCONCLUSIVE, raw_pred <= 0, immaterial)."""

from datetime import datetime

import numpy as np
import pytest

from adapt.core.db import Database
from adapt.learn.calibration import INITIAL, apply_outcome, calibrate, current_factor, next_factor
from adapt.learn.outcomes import effect_distribution, verdict

AT = datetime(2026, 10, 10, 12)


@pytest.mark.parametrize(("lo", "hi", "m", "expected"), [
    (2001, 9000, 2000, "SUCCESS"), (2000, 9000, 2000, "INCONCLUSIVE"),       # lower bound must EXCEED +m
    (-9000, -2001, 2000, "FAILED"), (-9000, -2000, 2000, "INCONCLUSIVE"),
    (-2000, 2000, 2000, "NEUTRAL"), (-1500, 1500, 2000, "NEUTRAL"),
    (-2500, 1000, 2000, "INCONCLUSIVE"), (500, 2500, 2000, "INCONCLUSIVE"),
])
def test_verdict_boundaries(lo, hi, m, expected):
    assert verdict(lo, hi, m) == expected


def basis(n_draws=50, days=7, cf_rev=10_000.0, pool=(0.0,), pre_budget=4000.0, cm=0.5):
    return {"days": 14, "total_budget": 100_000.0, "pred_daily_delta_caa": [1000.0] * 14,
            "units": {"U": {"campaign_ids": ["c"], "pre_budget": pre_budget, "new_budget": pre_budget * 1.2,
                            "pacing": 1.0, "cm": cm, "cf_revenue": [[cf_rev] * 14] * n_draws,
                            "residual_pool": list(pool)}}}


def test_effect_equals_observed_minus_counterfactual():
    b = basis()
    same = {"U": {"revenue": np.full(7, 10_000.0), "spend": np.full(7, 4000.0)}}
    out = effect_distribution(b, same, 7, seed=1)
    assert out["realized"] == pytest.approx(0) and out["verdict"] == "NEUTRAL"
    assert out["materiality"] == pytest.approx(max(2000, 0.05 * 10_000 * 7 * 0.5))
    assert out["raw_pred_window"] == pytest.approx(7000)
    worse = {"U": {"revenue": np.full(7, 6000.0), "spend": np.full(7, 4800.0)}}  # spent more, earned less
    out = effect_distribution(b, worse, 7, seed=1)
    assert out["realized"] == pytest.approx(7 * (6000 * 0.5 - 4800) - 7 * (10_000 * 0.5 - 4000))
    assert out["verdict"] == "FAILED"


def test_residual_paths_widen_the_interval_and_are_reproducible():
    b = basis(pool=np.random.default_rng(0).normal(0, 0.2, 42).tolist())
    obs = {"U": {"revenue": np.full(7, 10_000.0), "spend": np.full(7, 4000.0)}}
    a1, a2 = effect_distribution(b, obs, 7, seed=7), effect_distribution(b, obs, 7, seed=7)
    assert a1 == a2 and a1["ci_hi"] - a1["ci_lo"] > 0


def test_calibration_arithmetic():
    assert calibrate(1000, 0.9) == 900 and calibrate(-500, 0.9) == -500    # never applied to raw_pred <= 0
    f, rho = next_factor(0.9, realized=-3000, raw_pred=10_000)              # realized loss -> rho = 0
    assert rho == 0 and f == pytest.approx(0.72)
    f, rho = next_factor(0.9, realized=50_000, raw_pred=10_000)             # rho capped at 1.2, factor at 1.0
    assert rho == 1.2 and f == pytest.approx(min(0.8 * 0.9 + 0.2 * 1.2, 1.0))
    f, _ = next_factor(0.3, realized=0, raw_pred=10_000)
    assert f == 0.3                                                          # floor


def test_negative_outcome_lowers_the_factor_exactly_once(tmp_path):
    db = Database(tmp_path / "w.duckdb")
    assert current_factor(db) == INITIAL
    r = apply_outcome(db, "D1", "FAILED", realized=-5000, raw_pred=20_000, total_budget=100_000, at=AT)
    assert r["applied"] and r["factor_after"] == pytest.approx(0.8 * 0.9)
    assert current_factor(db) == pytest.approx(0.72)
    again = apply_outcome(db, "D1", "FAILED", realized=-5000, raw_pred=20_000, total_budget=100_000, at=AT)
    assert not again["applied"] and current_factor(db) == pytest.approx(0.72)   # idempotent per outcome id
    assert not apply_outcome(db, "D2", "INCONCLUSIVE", 9000, 20_000, 100_000, AT)["applied"]
    assert not apply_outcome(db, "D3", "SUCCESS", 9000, -2000, 100_000, AT)["applied"]      # cuts tracked apart
    assert not apply_outcome(db, "D4", "SUCCESS", 900, 1500, 100_000, AT)["applied"]        # below max(2000, 1% B)
    r = apply_outcome(db, "D5", "SUCCESS", realized=20_000, raw_pred=20_000, total_budget=100_000, at=AT)
    assert r["factor_after"] == pytest.approx(0.8 * 0.72 + 0.2 * 1.0)
    db.close()
