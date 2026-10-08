"""Stage 3 priors (spec §7.3, §22.2): the method-of-moments CVR prior formula with its fallbacks and coverage gate,
and the structured creative prior's acceptance against the category-mean baseline."""

import numpy as np
import pytest

from adapt.predict import creative_model as cm
from adapt.predict import cvr_bayes as cv


def cell(sku, clicks, purchases, cat="C1", channel="meta"):
    return {"sku": sku, "channel": channel, "category": cat, "clicks": float(clicks), "purchases": float(purchases)}


def test_mom_prior_matches_the_spec_formula():
    group = [cell(f"S{k}", 200 + 50 * k, 10 + 3 * k) for k in range(6)]
    a0, b0 = cv.mom_prior(group)
    n = np.array([g["clicks"] for g in group])
    r = np.array([g["purchases"] / g["clicks"] for g in group])
    N, K = n.sum(), len(group)
    mu = (n * r).sum() / N
    var = max((n * (r - mu) ** 2).sum() / N - mu * (1 - mu) * K / N, 1e-6)
    kappa = np.clip(mu * (1 - mu) / var - 1, 2, 1000)
    assert a0 == pytest.approx(mu * kappa) and b0 == pytest.approx((1 - mu) * kappa)


def test_fallbacks_channel_then_uniform():
    few = [cell(f"S{k}", 300, 15, cat="RARE") for k in range(2)]               # K < 5 in the category
    many = [cell(f"T{k}", 300 + k, 12 + k, cat="BIG") for k in range(6)]
    pr = cv.priors(few + many)
    assert pr[("BIG", "meta")][2] == "category" and pr[("RARE", "meta")][2] == "channel"
    lonely = cv.priors([cell("X", 10, 1, channel="google")])
    assert lonely[("C1", "google")] == (1.0, 1.0, "uniform")
    assert cv.mom_prior([cell(f"Z{k}", 100, 0) for k in range(6)]) is None     # mu = 0 -> fallback


def test_coverage_gate_on_a_stationary_panel():
    rng = np.random.default_rng(0)
    p = rng.beta(4, 80, size=40)
    train = [cell(f"S{k}", 400, rng.binomial(400, p[k])) for k in range(40)]
    hold = [cell(f"S{k}", 400, rng.binomial(400, p[k])) for k in range(40)]
    out = cv.coverage(train, hold)
    assert out["cells"] == 40 and 0.65 <= out["coverage_80"] <= 0.95            # calibrated when CVR is stable
    drift = [cell(f"S{k}", 400, rng.binomial(400, min(p[k] * 2.5, 0.9))) for k in range(40)]
    assert cv.coverage(train, drift)["coverage_80"] < 0.5                       # non-stationary CVR fails the gate


def creative_rows(n=120, seed=1, signal=True):
    rng = np.random.default_rng(seed)
    hooks, fmts = ["discount", "urgency", "benefit", "new arrival"], ["video", "image", "carousel"]
    eff = {"discount": 0.6, "urgency": 0.3, "benefit": 0.0, "new arrival": -0.3}
    rows = []
    for k in range(n):
        h, f = hooks[k % 4], fmts[(k // 4) % 3]
        z = -4.0 + ((eff[h] + (0.2 if f == "video" else 0.0)) if signal else 0.0) + rng.normal(0, 0.15)
        rows.append({"ad_id": str(k), "format": f, "hook": h, "cta": "shop now", "category": "C1", "channel": "meta",
                     "launch": k, "ctr": float(1 / (1 + np.exp(-z)))})
    return rows


def test_creative_prior_beats_a_flat_category_mean_when_attributes_matter():
    out = cm.evaluate(creative_rows())
    assert out["status"] == "OK" and out["passed"] and out["spearman_model"] > 0.5


def test_creative_prior_is_not_accepted_without_signal():
    out = cm.evaluate(creative_rows(signal=False, seed=4))
    assert out["status"] == "OK" and out["spearman_model"] < 0.5                # no structure to learn


def test_attribute_scoring_needs_an_accepted_prior_and_trained_levels(monkeypatch):
    from adapt.core.db import Database

    db = Database(":memory:")
    assert cm.attribute_domains(db)["status"] == "NOT_ESTIMABLE"
    assert cm.score_attributes(db, {"hook": "discount"})["score"] is None
    rows = creative_rows()
    monkeypatch.setattr(cm, "_champion", lambda _db: cm.fit(rows))
    monkeypatch.setattr(cm, "creatives", lambda _db: rows)
    domains = cm.attribute_domains(db)
    assert domains["status"] == "AVAILABLE" and domains["domains"]["hook"] == sorted(
        {"discount", "urgency", "benefit", "new arrival"})
    good = cm.score_attributes(db, {"hook": "discount", "format": "video"})
    bad = cm.score_attributes(db, {"hook": "new arrival", "format": "image"})
    assert good["status"] == "AVAILABLE" and good["score"] > bad["score"]
    for attrs in ({"hook": "free shipping"}, {"price_band": "low"}, {}):
        with pytest.raises(ValueError):
            cm.score_attributes(db, attrs)
    db.close()
