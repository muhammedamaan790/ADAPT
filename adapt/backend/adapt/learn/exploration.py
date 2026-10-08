"""Opt-in bootstrap-draw exploration. No posterior-sampling or autonomous claim.

Only unallocated money above the cash reserve can fund a proposal. The chosen
candidate is valued again using every joint draw and passes the normal policy.
"""

from __future__ import annotations

import hashlib

import numpy as np

from adapt.decide import decisions as dec
from adapt.decide.optimizer import Optimizer
from adapt.decide.run import persist_basis
from adapt.economics.portfolio import Portfolio
from adapt.policy.engine import cooldown_units, current_policy, kill_switch_active, validate

DDL = """CREATE TABLE IF NOT EXISTS ops.exploration_settings
 (id INTEGER PRIMARY KEY, enabled BOOLEAN, actor VARCHAR, changed_at TIMESTAMP);"""


def enabled(db):
    from adapt.policy.autonomy import has

    rows = (
        db.query("SELECT enabled FROM ops.exploration_settings WHERE id=1")
        if has(db, "ops", "exploration_settings")
        else []
    )
    return bool(rows and rows[0][0])


def propose(db, state, flags, at):
    if not enabled(db):
        return {"status": "DISABLED", "decision_ids": [], "note": "Exploration is off by default"}
    pol = current_policy(db, at)
    opt = Optimizer(state, flags)
    base = opt.s0
    capacity = min(0.02 * opt.c.B, max(0.0, opt.c.B - opt.c.R - float(base.sum())))
    if capacity < 100 or kill_switch_active(db):
        return {"status": "NOT_FEASIBLE", "decision_ids": [], "note": "No reserve-safe 2% headroom, or kill switch"}
    seed = int(hashlib.sha256(f"exploration-v1|{at.isoformat()}".encode()).hexdigest()[:8], 16)
    draw = int(np.random.default_rng(seed).integers(0, state.n_draws))
    cool = cooldown_units(db, at)
    choices = []
    pf = Portfolio(state)
    for i, unit in enumerate(state.units):
        if not unit.model_available or flags.get(unit.unit_id) or unit.unit_id in cool:
            continue
        amount = np.floor(min(capacity, opt.c.hi[i] - base[i]) / 100) * 100
        if amount <= 0:
            continue
        allocation = {u.unit_id: float(base[j] + (amount if j == i else 0)) for j, u in enumerate(state.units)}
        checks = validate(state, allocation, flags, "EXPLORATION", pol["config"]["guardrails"], cool, False)
        if all(c["passed"] for c in checks):
            econ = pf.evaluate(allocation)
            choices.append((float(econ.delta_caa[draw]), unit.unit_id, allocation))
    if not choices:
        return {"status": "NOT_FEASIBLE", "decision_ids": [], "note": "No policy-passing receiver"}
    sampled, _, allocation = max(choices, key=lambda c: (c[0], c[1]))
    if sampled <= 0:
        return {"status": "NOT_FEASIBLE", "decision_ids": [], "note": "Sampled draw prefers the status quo"}
    prop = dec.manual_proposal(state, allocation, pol["config"])
    rid = f"EXP-{seed:08x}"
    prop["decision_id"] = rid
    run = {
        "run_id": rid,
        "result": prop,
        "safety": [],
        "state": state,
        "flags": flags,
        "calibration_factor": 1.0,
        "decision_class": "EXPLORATION",
        "manual_allocation": allocation,
        "exploration": {
            "version": "bootstrap-exploration-v1",
            "seed": seed,
            "draw": draw,
            "cap_rupees": capacity,
            "cap_fraction": 0.02,
        },
    }
    ids = dec.create_decisions(db, run, state, flags, at)
    for did in ids:
        persist_basis(
            db,
            did,
            rid,
            at,
            "EXPLORATION",
            prop["expected"]["E"],
            prop["expected"]["E"],
            state,
            allocation,
            prop["legs"],
        )
    return {
        "status": "REQUIRES_REVIEW",
        "decision_ids": ids,
        "seed": seed,
        "draw": draw,
        "note": "One bootstrap draw selected the candidate; all draws value its risk. Human approval required.",
    }
