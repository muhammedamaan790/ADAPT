"""B2/B3 unit + property tests: transforms, forecast rule, robust z incl. MAD = 0, change points (exact vs ruptures),
the detector on synthetic campaigns (step change flagged, noise not, COLLAPSE, FLAT never material, eligibility),
the classifier, and the exact decompositions (funnel identity, rate/mix sums)."""

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
import ruptures as rpt
from hypothesis import given, settings
from hypothesis import strategies as st

from adapt.detect.detector import MetricFlag, catalog, classify, evaluate
from adapt.detect.stats import (
    expected_numerator,
    impact_cba,
    l2_changepoints,
    robust_z,
    stat_fires,
    stl_forecast,
    to_transformed,
)
from adapt.diagnose.decomposition import drilldown, rate_mix, roas_decomposition


# ---- stats ------------------------------------------------------------------------------------------------------
def test_transforms_invert_exactly():
    num, den = np.array([10.0, 0.0, 30.0]), np.array([1000.0, 500.0, 2000.0])
    for tr, ps in (("logit", 0.0), ("log", 0.5), ("none", 0.0)):
        y = to_transformed(num, den, tr, ps)
        assert np.allclose(expected_numerator(y, den, tr, ps), num), tr
    assert np.isnan(to_transformed(np.array([1.0]), np.array([0.0]), "logit"))[0]
    assert np.allclose(expected_numerator(to_transformed(np.array([7.0]), None, "log1p"), None, "log1p"), 7.0)


def test_forecast_continues_trend_and_weekly_pattern_out_of_sample():
    t = np.arange(70)
    weekly = np.tile([0.0, 1, 2, 3, 2, 1, 0], 10)
    y = 10 + 0.1 * t + weekly
    fc = stl_forecast(y[:56], 14)
    assert np.allclose(fc.yhat, y[56:], atol=0.15)  # linear trend + same-weekday seasonality


def test_robust_z_and_mad_zero():
    rz = robust_z(np.array([0.0, 1.4826]), np.array([-1.0, 0.0, 1.0]))
    assert rz.status == "OK" and np.allclose(rz.z, [0.0, 1.4826 / 1.4826])
    flat = robust_z(np.array([0.0, 0.5]), np.zeros(10))
    assert flat.status == "INSUFFICIENT_VARIANCE" and flat.z[0] == 0.0 and np.isnan(flat.z[1])
    assert stat_fires(np.array([0.1, 3.6]), 3.5, 2.5, 3)
    assert stat_fires(np.array([-2.6, -2.7, -2.8]), 3.5, 2.5, 3)
    assert not stat_fires(np.array([-2.6, 2.7, -2.8]), 3.5, 2.5, 3)  # sign flips break the run
    assert not stat_fires(np.array([np.nan, 1.0]), 3.5, 2.5, 3)


def _penalised_cost(x, bkps, pen):
    starts = [0, *bkps[:-1]]
    return sum(float(((x[a:b] - x[a:b].mean()) ** 2).sum()) for a, b in zip(starts, bkps, strict=True)) + \
        pen * (len(bkps) - 1)


@settings(max_examples=80, deadline=None)
@given(st.lists(st.floats(-5, 5, allow_nan=False), min_size=8, max_size=28), st.floats(1, 20))
def test_exact_segmentation_is_never_worse_than_ruptures_pelt(xs, pen):
    """The exact solver attains the optimum; ruptures' pruned PELT can miss it with min_size (found by hypothesis:
    [0,0,0,0,0,0,4,-2], pen 1 -> ruptures [5, 8] costs 19.67, the optimum [8] costs 19.5)."""
    x = np.asarray(xs)
    ours = l2_changepoints(x, pen, 3)
    theirs = rpt.Pelt(model="l2", min_size=3, jump=1).fit(x).predict(pen=pen)
    assert _penalised_cost(x, ours, pen) <= _penalised_cost(x, theirs, pen) + 1e-9
    assert all(b - a >= 3 for a, b in zip([0, *ours[:-1]], ours, strict=True))


def test_impact_cba_hand_check():
    d = {"impressions": np.array([10000.0]), "clicks": np.array([200.0]), "attributed_orders": np.array([10.0]),
         "spend": np.array([5000.0]), "attributed_net_revenue": np.array([20000.0])}
    ref = {"ctr": 0.02, "cvr": 0.05, "aov": 2000.0, "session_cvr": 0.06, "unit_contribution": 0.0}
    # ROAS expected 5, actual 4 on 5,000 spend at cm 0.4 -> 5000 x 1 x 0.4 = 2,000 shortfall
    assert impact_cba("ROAS", d, np.array([5.0]), np.array([4.0]), 0.4, ref)[0] == pytest.approx(2000.0)
    # CTR expected 3% vs 2%: 10,000 x 0.01 x CVR 0.05 x AOV 2000 x 0.4 = 4,000
    assert impact_cba("CTR", d, np.array([0.03]), np.array([0.02]), 0.4, ref)[0] == pytest.approx(4000.0)
    # CPA expected 400 -> 12.5 orders expected vs 10 actual: 2.5 x 2000 x 0.4 = 2,000
    assert impact_cba("CPA", d, np.array([400.0]), np.array([500.0]), 0.4, ref)[0] == pytest.approx(2000.0)


# ---- detector on synthetic campaigns ---------------------------------------------------------------------------------
def campaign(days=140, seed=0, ctr_drop_from=None, ctr_drop=0.0, zero_orders_from=None):
    rng = np.random.default_rng(seed)
    start = date(2026, 5, 1)
    rows = []
    for i in range(days):
        imps = int(rng.normal(20000, 800))
        ctr = 0.02 * (1 - ctr_drop if ctr_drop_from is not None and i >= ctr_drop_from else 1)
        clicks = rng.binomial(imps, ctr)
        orders = 0 if zero_orders_from is not None and i >= zero_orders_from else rng.binomial(clicks, 0.06)
        spend = imps * 0.04 * rng.lognormal(0, 0.03)
        rev = orders * 2500 * rng.lognormal(0, 0.05)
        rows.append({"date": start + timedelta(days=i), "impressions": imps, "clicks": clicks,
                     "attributed_orders": orders, "spend": spend, "attributed_net_revenue": rev,
                     "attributed_cba": rev * 0.35, "ga_sessions": int(clicks * 0.85)})
    return pd.DataFrame(rows)


SPEC = catalog()["campaign_metrics"]


def test_a_real_ctr_drop_is_flagged_and_noise_is_not():
    drop = campaign(ctr_drop_from=133, ctr_drop=0.4)
    f = evaluate("campaign", "c1", "meta", "CTR", SPEC["CTR"], drop, 7)
    assert f.flagged and f.direction == "DOWN" and f.relative_change == pytest.approx(-0.4, abs=0.07)
    assert f.signed_impact > f.threshold > 0
    quiet = [evaluate("campaign", "c1", "meta", m, SPEC[m], campaign(seed=s), 3)
             for s in range(4) for m in ("CTR", "CPM", "CVR", "ROAS")]
    assert sum(q.flagged for q in quiet) == 0


def test_collapse_path_and_eligibility():
    f = evaluate("campaign", "c1", "meta", "CVR", SPEC["CVR"], campaign(zero_orders_from=137), 3)
    assert f.collapse and f.direction == "DOWN" and f.flagged
    small = campaign()
    small[["impressions", "clicks"]] = small[["impressions", "clicks"]] // 1000  # ~560 impressions in 28 days
    assert evaluate("campaign", "c1", "meta", "CTR", SPEC["CTR"], small, 3).status == "INELIGIBLE"
    assert evaluate("campaign", "c1", "meta", "CTR", SPEC["CTR"], campaign(days=40), 3).status == \
        "INSUFFICIENT_HISTORY"


def test_flat_is_never_material():
    f = evaluate("campaign", "c1", "meta", "CTR", SPEC["CTR"], campaign(), 3)
    if f.direction == "FLAT":
        assert not f.material


def _flag(entity, metric, direction="DOWN", z=5.0, start=date(2026, 9, 28), end=date(2026, 9, 30)):
    return MetricFlag("campaign", entity, "google", metric, 3, start, end, direction=direction, flagged=True,
                      max_abs_z=z)


def test_classifier_budget_tracking_and_efficiency():
    flags = [_flag("A", "SPEND"), _flag("A", "ROAS", "UP", z=1.5), _flag("B", "SESSION_CLICK"), _flag("C", "CTR")]
    labels = classify(flags, budget_events={"bA": [date(2026, 9, 28)]}, camp_budget={"A": "bA"},
                      spend_change={"A": math.log(0.6)}, clicks_change={"B": 0.02})
    assert labels[("A", "SPEND")] == "budget_change"
    assert labels[("A", "ROAS")] == "budget_change"   # efficiency within band (|z| < 2) -> explained by the budget
    assert labels[("B", "SESSION_CLICK")] == "tracking_issue"
    assert labels[("C", "CTR")] == "efficiency_anomaly"
    no_event = classify([_flag("A", "SPEND")], {}, {"A": "bA"}, {"A": math.log(0.6)}, {})
    assert no_event[("A", "SPEND")] == "delivery_change"
    unstable = classify([_flag("B", "SESSION_CLICK")], {}, {}, {}, {"B": math.log(0.5)})
    assert unstable[("B", "SESSION_CLICK")] == "measurement_change"


# ---- decomposition (exactness) ----------------------------------------------------------------------------------
pos = st.floats(1, 1e6, allow_nan=False)


@settings(max_examples=80, deadline=None)
@given(pos, pos, pos, pos, pos, pos, pos, pos, pos, pos)
def test_funnel_contributions_sum_exactly_to_log_roas_change(i1, c1, o1, r1, s1, i2, c2, o2, r2, s2):
    pre = {"impressions": i1, "clicks": c1, "orders": o1, "revenue": r1, "spend": s1}
    post = {"impressions": i2, "clicks": c2, "orders": o2, "revenue": r2, "spend": s2}
    d = roas_decomposition(pre, post)
    assert d.status == "OK"
    assert sum(d.contributions.values()) == pytest.approx(math.log((r2 / s2) / (r1 / s1)), abs=1e-9)
    assert sum(d.share_of_movement.values()) == pytest.approx(1.0) or sum(d.contributions.values()) == 0


def test_funnel_hand_check_and_collapse():
    pre = {"impressions": 100000, "clicks": 2000, "orders": 100, "revenue": 250000, "spend": 50000}
    post = {"impressions": 100000, "clicks": 1200, "orders": 60, "revenue": 150000, "spend": 50000}
    d = roas_decomposition(pre, post)
    assert d.contributions["CTR"] == pytest.approx(math.log(0.6)) and d.contributions["CVR"] == pytest.approx(0)
    assert d.share_of_movement["CTR"] == pytest.approx(1.0) and d.pct_effect["CTR"] == pytest.approx(-0.4)
    dead = roas_decomposition(pre, {**post, "orders": 0, "revenue": 0})
    assert dead.status == "COLLAPSE" and dead.collapsed_factor == "CVR" and dead.contributions == {}


seg = st.dictionaries(st.sampled_from(list("abcdefg")), st.tuples(st.floats(1, 1e5), st.floats(0, 1e5)),
                      min_size=1, max_size=6)


@settings(max_examples=80, deadline=None)
@given(seg, seg)
def test_rate_mix_sums_exactly_including_new_and_vanished_segments(pre, post):
    effects, m_pre, m_post = rate_mix(pre, post)
    assert sum(e.combined for e in effects) == pytest.approx(m_post - m_pre, rel=1e-9, abs=1e-9)


def test_drilldown_finds_the_responsible_segment():
    pre = {"ad1": (1000.0, 3000.0), "ad2": (1000.0, 3000.0), "ad3": (1000.0, 3000.0)}
    post = {"ad1": (1000.0, 3000.0), "ad2": (1000.0, 1000.0), "ad3": (1000.0, 3000.0)}  # only ad2 collapsed
    flat = {"x": (3000.0, 9000.0)}
    r = drilldown({"creative": (pre, post), "ad_set": (flat, {"x": (3000.0, 7000.0)})})
    assert r.dimension == "ad_set" or [s.segment for s in r.segments] == ["ad2"]
    r2 = drilldown({"creative": (pre, post)})
    assert [s.segment for s in r2.segments] == ["ad2"] and r2.explained_share == pytest.approx(1.0)
