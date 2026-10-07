"""C3 decision object / snapshot / hash / fingerprint and C4 policy: replay hash equal, approval bound to the hash,
roles, staleness classes (EXACT expires, a 2% stock move does not, a 30% move does; T60/T69/T34), TTL, policy change
expires approvals (T44), kill switch, supersession, NaN never enters a decision (T20), policy is real."""

import copy
from dataclasses import replace
from datetime import timedelta

import numpy as np
import pytest
from tests.test_optimizer import T0, curve_unit, trap_state

from adapt.core.db import Database
from adapt.decide import decisions as dec
from adapt.decide.hashing import NonFiniteValue, canonical_bytes
from adapt.decide.optimizer import Optimizer
from adapt.decide.safety import safety_candidates
from adapt.decide.snapshot import state_from_dict, state_to_dict
from adapt.economics.portfolio import SkuState
from adapt.economics.state import guardrails_config
from adapt.policy.engine import policy_result, validate
from adapt.policy.locks import set_kill_switch


def copy_state(s):
    return state_from_dict(state_to_dict(s))


def make_run(state, run_id="run-1"):
    opt = Optimizer(state)
    r = opt.solve()
    r["decision_id"] = f"{run_id}:R"
    safety = safety_candidates(opt)
    for k, c in enumerate(safety):
        c["decision_id"] = f"{run_id}:S{k}"
    return {"run_id": run_id, "result": r, "safety": safety, "calibration_factor": 0.9}


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "w.duckdb")
    yield d
    d.close()


@pytest.fixture
def created(db):
    state = trap_state()
    ids = dec.create_decisions(db, make_run(state), state, {}, T0)
    assert ids == ["run-1:R"]
    return state, ids[0]


def test_decision_object_and_replay_hash(db, created):
    state, did = created
    d = dec.get_decision(db, did)
    assert d["status"] == "PENDING_APPROVAL" and d["class"] == "OPTIMIZATION" and d["type"] == "REALLOCATE"
    assert len(d["decision_hash"]) == 64 and d["expected"]["calibrated_pred"] == pytest.approx(
        0.9 * d["expected"]["raw_pred"], abs=1)
    assert all(c["passed"] for c in d["checks"])
    rep = dec.replay(db, did)
    assert rep["match"], rep                                         # replay reproduces the hash exactly
    assert db.query("SELECT count(*) FROM ops.decision_snapshots")[0][0] == 1


def test_snapshot_is_real(db, created):
    """Changing live inputs after creation never changes the replay (it reads only the snapshot artifacts)."""
    state, did = created
    state.skus["kb"].unit_contribution = -50.0
    state.units[0].budget = 1.0
    assert dec.replay(db, did)["match"]


def test_approval_is_bound_to_hash_and_role(db, created):
    state, did = created
    d = dec.get_decision(db, did)
    with pytest.raises(dec.DecisionError, match="FORBIDDEN"):
        dec.approve(db, did, d["decision_hash"], "v", "viewer", T0, copy_state(state))
    with pytest.raises(dec.DecisionError, match="HASH_MISMATCH"):
        dec.approve(db, did, "0" * 64, "m", "manager", T0, copy_state(state))
    out = dec.approve(db, did, d["decision_hash"], "m", "manager", T0 + timedelta(hours=1), copy_state(state))
    assert out["status"] == "APPROVED"
    with pytest.raises(dec.DecisionError, match="CONFLICT"):          # already approved
        dec.approve(db, did, d["decision_hash"], "m", "manager", T0, copy_state(state))


def _approve_with(db, did, state_now, now=T0):
    return dec.approve(db, did, dec.get_decision(db, did)["decision_hash"], "m", "manager", now, state_now)


def test_exact_change_expires_with_diff(db, created):
    state, did = created
    now = copy_state(state)
    now.skus["kb"].unit_contribution = 55.0                          # COGS changed: EXACT class
    with pytest.raises(dec.DecisionError, match="EXPIRED") as e:
        _approve_with(db, did, now)
    assert any(x["class"] == "EXACT" and x["field"] == "skus.kb" for x in e.value.details["diff"])
    assert dec.get_decision(db, did)["status"] == "EXPIRED"


def test_tolerance_class_small_stock_move_does_not_expire(db):
    state = trap_state()
    state.skus["kb"] = SkuState("kb", 100.0, 60.0, available=10_000.0, safety_stock=0.0, baseline_daily=0.0)
    did = dec.create_decisions(db, make_run(state), state, {}, T0)[0]
    now = copy_state(state)
    now.skus["kb"].available *= 0.98                                 # a sale: -2%, gate unchanged
    assert _approve_with(db, did, now)["status"] == "APPROVED"


def test_tolerance_class_large_stock_move_expires(db):
    state = trap_state()
    state.skus["kb"] = SkuState("kb", 100.0, 60.0, available=10_000.0, safety_stock=0.0, baseline_daily=0.0)
    did = dec.create_decisions(db, make_run(state), state, {}, T0)[0]
    now = copy_state(state)
    now.skus["kb"].available *= 0.70
    with pytest.raises(dec.DecisionError, match="EXPIRED") as e:
        _approve_with(db, did, now)
    assert e.value.details["diff"][0]["class"] == "TOLERANCE"


def test_ttl_expires(db, created):
    state, did = created
    with pytest.raises(dec.DecisionError, match="EXPIRED") as e:
        _approve_with(db, did, copy_state(state), now=T0 + timedelta(hours=7))
    assert any(x["class"] == "TTL" for x in e.value.details["diff"])


def test_policy_change_expires_pending_approval(db, created):
    state, did = created
    g = guardrails_config()
    saved = copy.deepcopy(g)
    try:
        g["change"]["max_daily_change_pct"] = 0.05                   # tighten one parameter (policy is real)
        with pytest.raises(dec.DecisionError, match="EXPIRED") as e:
            _approve_with(db, did, copy_state(state))
        assert any(x["class"] in ("POLICY", "EXACT") for x in e.value.details["diff"])
        # the regenerated decision respects the new limit (budgets large enough for a 5% move to survive rounding)
        state2 = copy_state(state)
        for u in state2.units:
            u.budget *= 100
            u.curve.median_spend *= 100
            u.curve.median_revenue *= 100
        did2 = dec.create_decisions(db, make_run(state2, "run-2"), state2, {}, T0)[0]
        legs = dec.get_decision(db, did2)["legs"]
        assert all(abs(leg["after"] / leg["before"] - 1) <= 0.05 + 1e-9 for leg in legs)
    finally:
        g.clear()
        g.update(saved)


def test_kill_switch_expires(db, created):
    state, did = created
    set_kill_switch(db, True, "admin", T0, "test")
    with pytest.raises(dec.DecisionError, match="EXPIRED"):
        _approve_with(db, did, copy_state(state))


def test_newer_run_supersedes_pending_decision(db, created):
    state, did = created
    new = dec.create_decisions(db, make_run(state, "run-2"), state, {}, T0)[0]
    assert dec.get_decision(db, did)["status"] == "SUPERSEDED"
    assert dec.get_decision(db, new)["status"] == "PENDING_APPROVAL"
    ev = db.query("SELECT details FROM ops.decision_events WHERE decision_id = ? AND event = 'superseded'", [did])
    assert "invalidation_reason" in ev[0][0]


def test_create_is_idempotent_per_run(db, created):
    state, _ = created
    assert dec.create_decisions(db, make_run(state), state, {}, T0) == []
    assert db.query("SELECT count(*) FROM intel.decisions")[0][0] == 1


def test_nan_never_enters_a_decision():
    with pytest.raises(NonFiniteValue):
        canonical_bytes({"expected": {"E": float("nan")}})
    with pytest.raises(NonFiniteValue):
        canonical_bytes({"legs": [{"after": np.inf}]})


def test_policy_is_real():
    state = trap_state()
    ok = {"A": 900.0, "B": 1100.0}
    assert policy_result(validate(state, ok, {}, "OPTIMIZATION"), "OPTIMIZATION")["status"] == "PENDING_APPROVAL"
    too_big = {"A": 1000.0, "B": 1300.0}                               # +30% > the 20% box, and over budget
    res = policy_result(validate(state, too_big, {}, "OPTIMIZATION"), "OPTIMIZATION")
    assert res["status"] == "BLOCKED" and {"MAX_DAILY_CHANGE", "TOTAL_BUDGET"} <= set(res["failed_rules"])
    frozen = policy_result(validate(state, ok, {"B": ["EXECUTION_FREEZE"]}, "OPTIMIZATION"), "OPTIMIZATION")
    assert "FROZEN_OR_FIXED_UNITS" in frozen["failed_rules"]
    # safety may cut under a tracking freeze but never raise spend, and never touch an execution freeze
    cut = {"A": 900.0}
    assert policy_result(validate(state, cut, {"A": ["TRACKING_FREEZE"]}, "SAFETY"), "SAFETY")["status"] == \
        "PENDING_APPROVAL"
    assert "FROZEN_OR_FIXED_UNITS" in policy_result(
        validate(state, cut, {"A": ["EXECUTION_FREEZE"]}, "SAFETY"), "SAFETY")["failed_rules"]
    assert "SAFETY_ONLY_REDUCES" in policy_result(validate(state, {"A": 1100.0}, {}, "SAFETY"), "SAFETY")[
        "failed_rules"]


def test_inventory_gate_rejects_scale_into_short_sku():
    state = trap_state()
    state.skus["kb"] = SkuState("kb", 100.0, 60.0, available=50.0, safety_stock=10.0, baseline_daily=20.0)
    res = policy_result(validate(state, {"A": 900.0, "B": 1100.0}, {}, "OPTIMIZATION"), "OPTIMIZATION")
    assert "INVENTORY_GATE" in res["failed_rules"]


def test_safety_decision_requires_review(db):
    u = curve_unit("G", 1000, 2.0, 1.0, 1.0, 3000, {"k": 1.0}, channel="google_search", noise=0.0)
    state = replace(trap_state(), units=[u], skus={"k": SkuState("k", 100.0, 40.0, 300.0, 50.0, 40.0)})
    ids = dec.create_decisions(db, make_run(state), state, {}, T0)
    safety = [i for i in ids if ":S" in i]
    assert safety
    d = dec.get_decision(db, safety[0])
    assert d["class"] == "SAFETY" and d["status"] == "PENDING_APPROVAL" and d["requires_review"]
    assert dec.replay(db, safety[0])["match"]
