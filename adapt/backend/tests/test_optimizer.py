"""B7 optimizer (PROFIT): fast evaluation is exact, ROAS trap, budget / cash-reserve contract, MODEL_UNAVAILABLE and
inventory gate block increases with named why-not reasons, unallocated budget, rounding, S3 safety candidate."""

import copy
from datetime import datetime

import numpy as np
import pytest

from adapt.decide.optimizer import Optimizer, build_constraints
from adapt.decide.safety import safety_candidates
from adapt.economics.portfolio import Portfolio, PortfolioState, SkuState, UnitState
from adapt.economics.state import guardrails_config, objectives_config
from adapt.predict.curves import CurveArtifact

T0 = datetime(2026, 10, 1, 12)


def curve_unit(uid, budget, beta, K, S, median_revenue, weights, channel="meta", noise=0.05, n=40, seed=0):
    p = np.array([beta, K, S, 0.0, 0.0])
    rng = np.random.default_rng(seed)
    draws = np.abs(p * (1 + noise * rng.standard_normal((n, 5)) * np.array([1, 1, 0, 0, 0])))
    art = CurveArtifact(uid, "OK", 1.0, float(budget), float(median_revenue), 1.0, 1.0, 0.0, p, draws)
    return UnitState(uid, channel, channel.split("_")[0], [uid], float(budget), 1.0, art, 0.0, weights, 0.0, 0.0)


def unavailable_unit(uid, budget, roas, weights):
    art = CurveArtifact(uid, "MODEL_UNAVAILABLE", 0.0, float(budget), 1.0, 1.0, 1.0, 0.0, None, None)
    return UnitState(uid, "meta", "meta", [uid], float(budget), 1.0, art, roas, weights, 0.0, 0.0)


def deep(k, nrpu=100.0, uc=60.0):
    return SkuState(k, nrpu, uc, available=1e9, safety_stock=0.0, baseline_daily=0.0)


def trap_state():
    # A: ROAS 5 but 5% unit margin -> marginal CBA per rupee 2.5 x 0.05 = 0.125 < 1 (the ROAS trap)
    # B: ROAS 4, 60% unit margin, unsaturated -> marginal 3.0 x 0.6 = 1.8 > 1 (the real opportunity)
    a = curve_unit("A", 1000, 2.0, 1.0, 1.0, 5000, {"ka": 1.0})
    b = curve_unit("B", 1000, 8.0, 3.0, 1.0, 2000, {"kb": 1.0}, channel="google_search", seed=1)
    return PortfolioState(T0, 7, [a, b], {"ka": deep("ka", uc=5.0), "kb": deep("kb", uc=60.0)}, n_draws=40)


def guard(**over):
    g = copy.deepcopy(guardrails_config())
    for path, v in over.items():
        sec, key = path.split("__")
        g[sec][key] = v
    return g


def test_fast_evaluation_equals_portfolio_economics():
    state = trap_state()
    opt = Optimizer(state)
    s = np.array([900.0, 1150.0])
    fast = opt.fe.dcaa(opt.fe.state_of(s))
    full = Portfolio(state, opt.pf.draws).evaluate(s).delta_caa
    assert np.allclose(fast, full, rtol=1e-9, atol=1e-6)
    moved = opt.fe.with_move(opt.fe.state_of(np.array([1000.0, 1000.0])), 1, 1150.0)
    moved = opt.fe.with_move(moved, 0, 900.0)
    assert np.allclose(opt.fe.dcaa(moved), full, rtol=1e-9, atol=1e-6)


def test_roas_trap_moves_budget_to_the_high_margin_unit():
    r = Optimizer(trap_state()).solve()
    assert r["status"] == "OK"
    alloc = r["allocation"]
    assert alloc["B"] > 1000 and alloc["A"] < 1000          # the highest-ROAS unit loses budget
    assert alloc["B"] <= 1200 + 1e-9 and alloc["A"] >= 800 - 1e-9   # +-20% box
    assert sum(alloc.values()) <= r["total_budget"] - r["reserve_floor"] + 1e-6
    assert r["expected"]["E"] > 0
    assert all(v % 100 == 0 for v in alloc.values())        # application allocation increment
    assert r["legs"][0]["unit_id"] == "A"                   # risk-reducing (decrease) legs first
    why = {w["unit_id"]: w for w in r["why_not"]}
    # the whole budget is in use, so A's why-not is the best feasible transfer back from B, and it loses money
    assert why["A"]["binding_constraint"] == "TOTAL_BUDGET"
    assert why["A"]["transfer"]["from_unit"] == "B" and why["A"]["transfer"]["objective_change"] < 0


def test_cash_reserve_contract():
    state = trap_state()
    r = Optimizer(state, guardrails=guard(budget__cash_reserve_rupees=300, budget__total_budget_inr=2500)).solve()
    assert r["reserve_floor"] == 300 and not r["reserve_baseline_infeasible"]
    assert r["unallocated"] >= 300 - 1e-6 and sum(r["allocation"].values()) <= 2500 - 300 + 1e-6
    # reserve larger than the free room: the baseline already breaks it -> no change may increase total spend
    r = Optimizer(state, guardrails=guard(budget__cash_reserve_rupees=5000)).solve()
    assert r["reserve_baseline_infeasible"] and sum(r["allocation"].values()) <= 2000 + 1e-6


def test_model_unavailable_unit_is_never_increased():
    state = PortfolioState(T0, 7, [unavailable_unit("U", 1000, 50.0, {"k": 1.0}),
                                   curve_unit("B", 1000, 2.0, 1.0, 1.0, 2000, {"k": 1.0})],
                           {"k": deep("k")}, n_draws=40)
    r = Optimizer(state).solve()
    assert r["allocation"]["U"] <= 1000
    why = {w["unit_id"]: w for w in r["why_not"]}
    assert why["U"]["binding_constraint"] == "MODEL_UNAVAILABLE"


def test_inventory_gate_blocks_scale_up():
    # B would be scaled (as in the trap state) but its only SKU is already short -> exposure 1.0 -> BLOCK
    state = trap_state()
    state.skus["kb"] = SkuState("kb", 100.0, 60.0, available=50.0, safety_stock=10.0, baseline_daily=20.0)
    r = Optimizer(state).solve()
    assert r["allocation"]["B"] <= 1000
    assert r["inventory_gate"]["B"]["gate"] == "BLOCK"
    why = {w["unit_id"]: w for w in r["why_not"]}
    assert why["B"]["binding_constraint"] == "INVENTORY_GATE"


def test_unallocated_budget_when_no_unit_is_worth_more_spend():
    # both units saturated with thin margins: every rupee loses money -> cut and leave the cash unallocated
    a = curve_unit("A", 1000, 1.0, 0.3, 1.0, 1500, {"k": 1.0})
    b = curve_unit("B", 1000, 1.0, 0.3, 1.0, 1500, {"k": 1.0}, seed=2)
    state = PortfolioState(T0, 7, [a, b], {"k": deep("k", uc=20.0)}, n_draws=40)
    r = Optimizer(state).solve()
    assert sum(r["allocation"].values()) < 2000
    assert r["unallocated"] > 0 and r["unallocated_reason"]["marginal_value_per_step"] <= 0


def test_fixed_flags_pin_units():
    r = Optimizer(trap_state(), flags={"B": ["DATA_DEPENDENCY"]}).solve()
    assert r["allocation"]["B"] == 1000
    assert {w["unit_id"]: w for w in r["why_not"]}["B"]["binding_constraint"] == "DATA_DEPENDENCY"
    c = build_constraints(trap_state(), {"B": ["EXECUTION_FREEZE"]})
    assert c.lo[1] == c.hi[1] == 1000


def test_s3_safety_candidate_removes_the_projected_shortfall():
    # SKU k: available 300 - ss 50, baseline 40/day x 7 = 280 > 250 -> shortfall 30 units at the status quo
    u = curve_unit("G", 1000, 2.0, 1.0, 1.0, 3000, {"k": 1.0}, channel="google_search", noise=0.0)
    state = PortfolioState(T0, 7, [u], {"k": SkuState("k", 100.0, 40.0, 300.0, 50.0, 40.0)}, n_draws=40)
    opt = Optimizer(state)
    cands = safety_candidates(opt)
    assert len(cands) == 1
    c = cands[0]
    assert c["class"] == "SAFETY" and c["status"] == "REQUIRES_REVIEW" and c["shortfall_before"] == pytest.approx(30)
    assert c["legs"][0]["after"] < 1000 and c["freed_budget"] > 0
    if not c["max_feasible_cut_reached"]:
        assert c["remaining_shortfall"] == pytest.approx(0, abs=1e-6)
    # the cut is capped at the largest feasible decrease (20% box)
    assert c["legs"][0]["after"] >= 800 - 1e-9


def test_objective_config_is_profit_only():
    assert objectives_config()["PROFIT"]["lambda"] == 0.5
