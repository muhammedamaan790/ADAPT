"""Stage 2 objectives and sensitivity (spec §8.2, §8.3): GROWTH maximises net revenue under the sign-safe CAA floor
(why-not OBJECTIVE_CAA_FLOOR), INVENTORY CLEARANCE shifts spend toward EXCESS-band SKUs within guardrails (S11),
Conservative / Aggressive are risk-preference scenarios validated by policy, the selected objective is an admin
setting in the EXACT fingerprint, choosing a scenario creates a superseding decision, and replay reproduces a
decision that carries scenarios."""

from datetime import timedelta

import numpy as np
import pytest
from tests.test_optimizer import T0, curve_unit

from adapt.core.db import Database
from adapt.decide import decisions as dec
from adapt.decide.alternatives import (
    choose_alternative,
    objectives_for,
    selected_objective,
    sensitivity,
    set_objective,
)
from adapt.decide.optimizer import Optimizer
from adapt.economics.portfolio import PortfolioState, SkuState


def deep(k, uc, available=1e9, baseline=0.0):
    return SkuState(k, 100.0, uc, available, 0.0, baseline, available)


def growth_state():
    # thin margin (uc 12 on nrpu 100): PROFIT cuts, while net revenue still rises with spend
    a = curve_unit("A", 1000, 6.0, 2.0, 1.0, 3000, {"ka": 1.0})
    b = curve_unit("B", 1000, 6.0, 2.0, 1.0, 3000, {"kb": 1.0}, channel="google_search", seed=1)
    return PortfolioState(T0, 7, [a, b], {"ka": deep("ka", 12.0), "kb": deep("kb", 12.0)}, n_draws=40)


def test_growth_maximises_revenue_under_the_caa_floor():
    s = growth_state()
    profit = Optimizer(s, objectives=objectives_for("PROFIT")).solve()
    growth = Optimizer(s, objectives=objectives_for("GROWTH")).solve()
    assert growth["objective"] == "GROWTH" and growth["status"] == "OK"
    assert growth["expected"]["delta_net_revenue"] > profit["expected"]["delta_net_revenue"]
    floor = growth["caa_floor"]
    assert growth["caa0"] + growth["expected"]["E"] >= floor - 1.0          # E[abs CAA(s')] >= CAA0 - tolerance
    assert floor == pytest.approx(growth["caa0"] - max(0.05 * abs(growth["caa0"]), 2000.0))
    reasons = {w.get("binding_constraint") or w.get("reason") for w in growth["why_not"]}
    assert reasons & {"OBJECTIVE_CAA_FLOOR", "MAX_DAILY_CHANGE", "TOTAL_BUDGET", "DAILY_RUPEES_MOVED_CAP"}


def test_growth_tolerance_is_sign_safe_for_a_negative_caa0():
    s = growth_state()
    s.other_cba_daily = -1e6                                                # a loss-making status quo
    g = Optimizer(s, objectives=objectives_for("GROWTH"))
    assert g.caa0 < 0 and g.caa_tolerance == pytest.approx(max(0.05 * abs(g.caa0), 2000.0))


def test_clearance_moves_spend_toward_excess_skus_s11():
    a = curve_unit("A", 1000, 4.0, 2.0, 1.0, 3000, {"excess": 1.0})       # stock with ~400 days of cover
    b = curve_unit("B", 1000, 4.0, 2.0, 1.0, 3000, {"normal": 1.0}, channel="google_search", seed=1)
    s = PortfolioState(T0, 7, [a, b], {"excess": deep("excess", 30.0, available=4000.0, baseline=10.0),
                                       "normal": deep("normal", 30.0, available=300.0, baseline=10.0)}, n_draws=40)
    profit = Optimizer(s, objectives=objectives_for("PROFIT")).solve()
    clear = Optimizer(s, objectives=objectives_for("INVENTORY_CLEARANCE")).solve()
    assert clear["allocation"]["A"] >= profit["allocation"]["A"] and clear["allocation"]["A"] > 1000.0
    assert clear["allocation"]["A"] <= 1000.0 * 1.2 + 1e-6                 # within the +-20% guardrail
    assert clear["excess_units_sold"] > 0


def test_sensitivity_scenarios_are_bounded_and_policy_validated():
    s = growth_state()
    alts = sensitivity(s, {})
    assert [a["name"] for a in alts] == ["conservative", "aggressive"]
    cons, aggr = alts
    assert all(abs(leg["after"] / leg["before"] - 1) <= 0.10 + 1e-9 for leg in cons["legs"])
    assert all(abs(leg["after"] / leg["before"] - 1) <= 0.20 + 1e-9 for leg in aggr["legs"])
    assert cons["lambda"] == 1.0 and aggr["lambda"] == 0.2
    assert all(a["policy"]["status"] == "PENDING_APPROVAL" for a in alts) and "not a competing" in cons["label"]
    assert sensitivity(s, {}, objectives=objectives_for("GROWTH")) == []   # no risk preference outside PROFIT


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "w.duckdb")
    yield d
    d.close()


def test_selected_objective_is_an_admin_setting_in_the_fingerprint(db):
    assert selected_objective(db) == "PROFIT"
    with pytest.raises(PermissionError):
        set_objective(db, "GROWTH", "maria", "manager", T0)
    with pytest.raises(ValueError):
        set_objective(db, "ACQUISITION", "admin1", "admin", T0)            # not built in Stage 2
    set_objective(db, "GROWTH", "admin1", "admin", T0)
    assert selected_objective(db) == "GROWTH"
    from adapt.decide.fingerprint import fingerprint

    s = growth_state()
    a = fingerprint(db, s, "pv-1", False, T0, "PROFIT")
    b = fingerprint(db, s, "pv-1", False, T0, "GROWTH")
    assert a["economics_hash"] != b["economics_hash"]


def test_choose_a_scenario_supersedes_and_replay_matches(db):
    s = growth_state()
    opt = Optimizer(s)
    r = opt.solve()
    r["decision_id"] = "run-9:R"
    r["alternatives"] = sensitivity(s, {}, portfolios=(opt.pf, opt.full))
    run = {"run_id": "run-9", "result": r, "safety": [], "calibration_factor": 0.9}
    assert dec.create_decisions(db, run, s, {}, T0) == ["run-9:R"]
    d = dec.get_decision(db, "run-9:R")
    assert [a["name"] for a in d["alternatives"]] == ["conservative", "aggressive"]
    assert dec.replay(db, "run-9:R")["match"]                               # scenarios recomputed, hash equal
    new = choose_alternative(db, "run-9:R", "conservative", "maria", "manager", T0 + timedelta(minutes=5))
    assert dec.get_decision(db, "run-9:R")["status"] == "SUPERSEDED"
    d2 = dec.get_decision(db, new)
    assert d2["status"] == "PENDING_APPROVAL" and d2["risk_preference"] == "conservative"
    assert d2["decision_hash"] != d["decision_hash"]
    assert db.query("SELECT supersedes FROM intel.decisions WHERE decision_id = ?", [new])[0][0] == "run-9:R"
    with pytest.raises(dec.DecisionError):
        choose_alternative(db, "run-9:R", "aggressive", "maria", "manager", T0)   # no longer pending
    assert np.isfinite(d2["expected"]["E"])
