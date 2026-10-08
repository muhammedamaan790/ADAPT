"""Stage 3 exploration (spec §10.3) on the real path (60x fixture world): off by default; deterministic per run; it
creates nothing when no unit qualifies (here 10 of 11 units have no usable curve, which exploration may never touch,
and the 11th loses money on an increase); and the policy engine caps an exploration at max_budget_share of the budget.
The selection rule itself (one run-keyed draw, its argmax, every eligibility filter) is unit-tested on a controlled
portfolio in backend/tests/test_exploration_rule.py, because no fixture world has an eligible, winning unit.
Untouched units only: a separate decision on a budget the recommendation also sets would conflict at pre-read or
expire on its fingerprint, so exploration acts on steady-state days, never on a first big reallocation."""

from datetime import date

from adapt.decide import exploration
from adapt.decide.alternatives import objectives_for
from adapt.decide.optimizer import Optimizer
from adapt.decide.run import policy_flags, run_optimizer
from adapt.economics.state import load_state
from adapt.ingest.http import SourceHttp
from adapt.ingest.sync import run_sync
from adapt.policy.engine import validate
from adapt.predict.fit_curves import fit_curves
from adapt.reconcile.build import build_canonical, logical_now

START = date(2026, 10, 1)


def test_exploration_off_by_default_deterministic_and_policy_capped(world_large, db):
    http = SourceHttp("http://testserver", client=world_large, sleep=lambda s: None)
    as_of = logical_now(START)
    run_sync(db, http)
    build_canonical(db, as_of)
    fit_curves(db, as_of)
    assert run_optimizer(db, as_of, persist=False)["exploration"] == []                      # default: disabled

    state = load_state(db, as_of)
    flags = policy_flags(db, state, as_of)
    opt = Optimizer(state, flags, objectives=objectives_for("PROFIT"))
    hold = {"status": "OK", "decision_id": "opt-test-R", "legs": [],
            "allocation": {u.unit_id: u.budget for u in state.units}}
    opt.c.B = float(sum(u.budget for u in state.units)) / 0.95                              # 5% left unallocated
    cfg = {"enabled": True, "max_budget_share": 0.02}
    first = exploration.candidate(opt, hold, as_of, cfg)
    assert first == exploration.candidate(opt, hold, as_of, cfg)                             # run-keyed draw
    eligible = [u for i, u in enumerate(state.units) if i not in opt.c.unit_reasons and u.model_available]
    assert first is None or first["legs"][0]["unit_id"] in {u.unit_id for u in eligible}

    u = state.units[0]
    small = {x.unit_id: x.budget for x in state.units} | {u.unit_id: u.budget + 0.01 * opt.c.B}
    big = {x.unit_id: x.budget for x in state.units} | {u.unit_id: u.budget + 0.5 * opt.c.B}
    rules = lambda alloc: {c["rule"]: c for c in validate(state, alloc, flags, "EXPLORATION")}  # noqa: E731
    assert "COOLDOWN" in rules(small)                                                          # lowest precedence
    assert not rules(big)["EXPLORATION_SHARE"]["passed"]
    cut = {x.unit_id: x.budget for x in state.units} | {u.unit_id: u.budget * 0.9}
    assert not rules(cut)["EXPLORATION_SHARE"]["passed"]                                      # increases only
