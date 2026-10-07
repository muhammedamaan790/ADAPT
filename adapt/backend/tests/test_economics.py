"""B6 portfolio_economics: nrpu hand check, zero change, deficit-aware headroom, rationing, conservation invariants
(Hypothesis), absolute vs delta CAA (spec §8.1, §0.5 Stage 1 P0 tests)."""

from datetime import datetime

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from adapt.economics.portfolio import Portfolio, PortfolioState, SkuState, UnitState, portfolio_economics
from adapt.metrics.formulas import nrpu, unit_contribution
from adapt.predict.curves import CurveArtifact

T0 = datetime(2026, 10, 1, 12)


def prop_unit(uid, budget, roas, weights, u=0.0, ucr=0.0, pacing=1.0):
    """Proportional (MODEL_UNAVAILABLE) unit: revenue = roas x spend, identical across draws."""
    return UnitState(uid, "meta", "meta", [uid], budget, pacing, None, roas, weights, u, ucr)


def curve_unit(uid, budget, beta, K, S, median_revenue, weights, n=20, noise=0.0, seed=0, u=0.0, ucr=0.0):
    p = np.array([beta, K, S, 0.0, 0.0])
    rng = np.random.default_rng(seed)
    draws = p * (1 + noise * rng.standard_normal((n, 5)) * np.array([1, 1, 0, 0, 0]))
    art = CurveArtifact(uid, "OK", 1.0, float(budget), float(median_revenue), 1.0, 1.0, 0.0, p, np.abs(draws))
    return UnitState(uid, "meta", "meta", [uid], budget, 1.0, art, 0.0, weights, u, ucr)


def sku(k, n=100.0, uc=30.0, available=1e9, ss=0.0, baseline=0.0):
    return SkuState(k, n, uc, available, ss, baseline)


def test_nrpu_hand_check_through_the_portfolio():
    # price 100, COGS 50, no discount, ship/fee 0, return rate 20% -> nrpu 80, 80 net revenue = 1.0 unit, CBA 30
    n = nrpu(100, 0.0, 0.2)
    assert n == pytest.approx(80) and unit_contribution(n, 50, 0, 0) == pytest.approx(30)
    assert nrpu(100, 0.10, 0.0) == pytest.approx(90)
    state = PortfolioState(T0, 1, [prop_unit("A", 1000, 0.8, {"k": 1.0})], {"k": sku("k", n, 30.0)}, n_draws=3)
    e = portfolio_economics(state, {"A": 1100})  # +100 spend -> +80 net revenue -> +1 unit -> +30 CBA
    assert e.units_by_sku["k"] == pytest.approx(1.0)
    assert e.delta_net_revenue == pytest.approx([80.0] * 3)
    assert e.delta_caa == pytest.approx([30.0 - 100.0] * 3)


def test_zero_change_gives_zero_delta_but_absolute_caa():
    state = PortfolioState(T0, 7, [curve_unit("A", 1000, 2, 1, 1, 5000, {"k": 1.0}, noise=0.1)],
                           {"k": sku("k")}, other_cba_daily=1000.0, n_draws=20)
    e = portfolio_economics(state, {"A": 1000})
    assert np.all(e.delta_caa == 0) and np.all(e.delta_net_revenue == 0) and e.delta_spend == 0
    assert e.units_by_sku["k"] == 0
    assert np.all(e.abs_caa != 0)  # CAA0 = curve revenue x margin - spend + non-modelled contribution


def deficit_state(atp_baseline: float):
    # ATP = available - ss - baseline x H; H = 1, nrpu 1 so 1 rupee of revenue = 1 unit
    return PortfolioState(T0, 1, [prop_unit("A", 1000, 1.0, {"k": 1.0}), prop_unit("B", 1000, 1.0, {"k": 1.0})],
                          {"k": SkuState("k", 1.0, 0.5, available=0.0, safety_stock=0.0,
                                         baseline_daily=-atp_baseline)}, n_draws=1)


def test_releases_first_offset_an_existing_deficit():
    # spec example: ATP = -100, releases 120 -> headroom 20 (not 120)
    e = portfolio_economics(deficit_state(-100.0), {"A": 880, "B": 1150})
    assert e.units_by_sku["k"] == pytest.approx(-120 + 20)
    assert e.rationing_binds and e.baseline_deficit_skus == ["k"]
    # spec example: A -100, B +150, ATP 50 -> headroom 150, B gets all 150
    e = portfolio_economics(deficit_state(50.0), {"A": 900, "B": 1150})
    assert e.units_by_sku["k"] == pytest.approx(50.0) and not e.rationing_binds


def test_rationed_units_earn_nothing_and_waste_spend():
    state = PortfolioState(T0, 1, [prop_unit("A", 1000, 1.0, {"k": 1.0})],
                           {"k": SkuState("k", 1.0, 0.5, available=10.0, safety_stock=0.0, baseline_daily=0.0)},
                           n_draws=1)
    e = portfolio_economics(state, {"A": 1100})   # wants +100 units, only 10 available
    assert e.units_by_sku["k"] == pytest.approx(10)
    assert e.delta_caa[0] == pytest.approx(10 * 0.5 - 100)
    assert e.wasted_spend == pytest.approx(90.0)
    assert e.inventory_risk_by_sku["kind"] == "PROJECTED_SHORTFALL"


def test_unmapped_share_enters_dcaa_without_inventory_units():
    state = PortfolioState(T0, 1, [prop_unit("A", 1000, 1.0, {"k": 0.7}, u=0.3, ucr=0.4)],
                           {"k": SkuState("k", 1.0, 0.5, 1e9, 0, 0)}, n_draws=1)
    e = portfolio_economics(state, {"A": 1100})
    assert e.units_by_sku["k"] == pytest.approx(70)            # only the mapped 70% becomes units
    assert e.delta_caa[0] == pytest.approx(70 * 0.5 + 30 * 0.4 - 100)
    assert e.delta_net_revenue[0] == pytest.approx(100)
    assert e.exposure_by_unit["A"] == pytest.approx(0.3)       # unknown exposure counts in the gate


@st.composite
def portfolios(draw):
    n_units = draw(st.integers(1, 4))
    n_skus = draw(st.integers(1, 4))
    skus = {f"k{j}": SkuState(f"k{j}", draw(st.floats(5, 200)), draw(st.floats(-20, 100)),
                              draw(st.floats(0, 500)), draw(st.floats(0, 50)), draw(st.floats(0, 60)))
            for j in range(n_skus)}
    units, alloc = [], {}
    for i in range(n_units):
        raw = [draw(st.floats(0, 1)) for _ in range(n_skus)]
        u = draw(st.floats(0, 0.5))
        tot = sum(raw) or 1.0
        weights = {f"k{j}": (1 - u) * r / tot for j, r in enumerate(raw) if r > 0}
        if not weights:
            u = 1.0
        budget = draw(st.floats(500, 5000))
        if draw(st.booleans()):
            unit = curve_unit(f"u{i}", budget, draw(st.floats(0.5, 5)), draw(st.floats(0.5, 3)),
                              draw(st.floats(0.6, 2.5)), draw(st.floats(500, 20000)), weights, n=8,
                              noise=0.2, seed=i, u=u, ucr=draw(st.floats(-0.2, 0.6)))
        else:
            unit = prop_unit(f"u{i}", budget, draw(st.floats(0.5, 6)), weights, u, draw(st.floats(-0.2, 0.6)),
                             pacing=draw(st.floats(0.5, 1.0)))
        units.append(unit)
        alloc[unit.unit_id] = budget * draw(st.floats(0.5, 1.5))
    return PortfolioState(T0, draw(st.integers(1, 7)), units, skus, n_draws=8), alloc


@settings(max_examples=150, deadline=None)
@given(portfolios())
def test_conservation_invariants(case):
    state, alloc = case
    pf = Portfolio(state)
    e = pf.evaluate(alloc)
    s1 = np.array([alloc[u.unit_id] for u in state.units])
    # budget: reported delta spend is exactly sum (s' - s) x pacing x H
    assert e.delta_spend == pytest.approx(float(((s1 - pf.s0) * pf.pacing).sum() * state.horizon), rel=1e-9, abs=1e-6)
    # inventory: per draw and SKU, effective increases never exceed max(ATP + released, 0)
    dR = np.stack([pf.unit_paths(i, s1[i]) - pf.R0[:, i, :] for i in range(len(s1))], axis=1).sum(-1)
    du = dR[:, :, None] * pf.W[None] / pf.nrpu[None, None, :]
    released = np.clip(-du, 0, None).sum(1)
    requested = np.clip(du, 0, None).sum(1)
    head = np.maximum(pf.atp[None, :] + released, 0)
    eff_pos = np.minimum(requested, head)
    assert np.all(eff_pos <= head + 1e-6)
    assert np.allclose(np.array([e.units_by_sku[k] for k in pf.sku_ids]), (eff_pos - released).mean(0), atol=1e-6)
    # no rationing -> booked net revenue equals the curve increment exactly
    if not e.rationing_binds:
        assert np.allclose(e.delta_net_revenue, dR.sum(1), rtol=1e-9, atol=1e-6)
    # daily path sums to the horizon total
    assert np.allclose(e.daily_delta_caa.sum(1), e.delta_caa, rtol=1e-9, atol=1e-6)
    # absolute = status quo + delta
    base = pf.evaluate({u.unit_id: u.budget for u in state.units})
    assert np.allclose(e.abs_caa - base.abs_caa, e.delta_caa, atol=1e-6)


@settings(max_examples=60, deadline=None)
@given(portfolios())
def test_currency_neutrality(case):
    state, alloc = case
    e1 = portfolio_economics(state, alloc)
    fx = 83.0  # every rupee input expressed in another currency and back
    for u in state.units:
        u.budget /= fx
        if u.curve is not None:
            u.curve.median_spend /= fx
            u.curve.median_revenue /= fx
    for k in state.skus.values():
        k.nrpu /= fx
        k.unit_contribution /= fx
    e2 = portfolio_economics(state, {k: v / fx for k, v in alloc.items()})
    assert np.allclose(e1.delta_caa, e2.delta_caa * fx, rtol=1e-6, atol=1e-6)
