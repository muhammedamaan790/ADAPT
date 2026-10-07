"""Stage 2 automatic safety monitor through the real path (fixture world -> ingest -> pipeline state -> C3/C5):
an executed OPTIMIZATION decision on Meta units (decrease-only: every fixture curve is MODEL_UNAVAILABLE, so
policy forbids increases), then S1 at +80% Meta CPM -> CPA runaway -> a SAFETY decision that REDUCES the treated
units by the max daily change, floored at the unit minimum (nothing paused, T58), awaiting review with a P1 alert
(Approve mode) | an EXECUTION_UNCERTAINTY freeze on the unit -> alert only, no decision (T47) | executing the safety
decision -> SAFETY_COOLDOWN freeze and the original outcome CONTAMINATED_BY_SAFETY (no calibration) | reversal cap ->
the unit is pinned to Approve (T31)."""

import json
from datetime import date, timedelta

from adapt.decide import decisions as dec
from adapt.decide.run import measurement_basis
from adapt.economics.state import guardrails_config, load_state
from adapt.execute.adapters import MockGoogleAdapter, MockMetaAdapter
from adapt.execute.saga import execute_decision
from adapt.ingest.sync import run_sync
from adapt.learn.outcomes import measure_outcome
from adapt.policy import locks
from adapt.policy.safety_monitor import run_monitor
from adapt.predict.fit_curves import fit_curves
from adapt.reconcile.build import build_canonical, logical_now

START = date(2026, 10, 1)
NOSLEEP = lambda s: None  # noqa: E731


def refresh(db, http, day):
    run_sync(db, http)
    build_canonical(db, logical_now(day))


def executed_increase(world, http, db):
    """An approved + executed OPTIMIZATION decision cutting two Meta units by ~19%."""
    refresh(db, http, START)
    fit_curves(db, logical_now(START))
    at = logical_now(START)
    state = load_state(db, at)
    metas = sorted((u for u in state.units if u.platform == "meta" and u.budget > 2000), key=lambda u: -u.budget)
    up, down = metas[0], metas[1]
    legs = [{"unit_id": u.unit_id, "platform": "meta", "channel": u.channel, "budget_id": u.unit_id,
             "campaign_ids": u.campaign_ids, "before": u.budget,
             "after": u.budget - int(0.19 * u.budget / 100) * 100} for u in (up, down)]
    exp = {"E": 1000.0, "P10": -500.0, "P50": 1000.0, "P90": 2000.0, "prob_loss": 0.2, "delta_net_revenue": 3000.0,
           "raw_pred": 1000.0}
    run = {"run_id": "opt-test", "calibration_factor": 0.9, "safety": [],
           "result": {"status": "OK", "decision_id": "opt-test:R", "legs": legs, "expected": exp, "why_not": [],
                      "inventory_risk_after": {"kind": "STOCKOUT_PROBABILITY", "by_sku": {}}, "unallocated": 0.0,
                      "reserve_floor": 0.0, "objective": "PROFIT"}}
    assert dec.create_decisions(db, run, state, {}, at) == ["opt-test:R"]
    d = dec.get_decision(db, "opt-test:R")
    assert d["status"] == "PENDING_APPROVAL", [c for c in d["checks"] if not c["passed"]]
    alloc = [next((leg["after"] for leg in legs if leg["unit_id"] == u.unit_id), u.budget) for u in state.units]
    import numpy as np

    basis = measurement_basis(state, np.array(alloc), legs, db)
    from adapt.decide.run import DDL as RUN_DDL

    db.write(lambda cur: cur.execute(RUN_DDL))
    db.write(lambda cur: cur.execute("INSERT INTO learn.measurement_basis VALUES ('opt-test:R', 'opt-test', ?, "
                                     "'OPTIMIZATION', 'BUDGET_REALLOCATION', 1000, 900, ?)",
                                     [at, json.dumps(basis, default=float)]))
    dec.approve(db, "opt-test:R", d["decision_hash"], "maria", "manager", at, load_state(db, at))
    ad = {"google": MockGoogleAdapter(world), "meta": MockMetaAdapter(world)}
    out = execute_decision(db, "opt-test:R", ad, "maria", at, load_state(db, at), NOSLEEP)
    assert out["state"] == "SUCCEEDED"
    return up, ad


def s1_for(world, http, db, days=4):
    world.post("/control/scenario", json={"key": "S1", "params": {"magnitude": 0.8, "days": 20}},
               headers={"X-Request-ID": "s1"})  # +45% CPM is offset by the executed cut (CPA x1.22 < 1.25)
    world.post("/control/advance", json={"days": days}, headers={"X-Request-ID": "adv"})
    day = START + timedelta(days=days)
    refresh(db, http, day)
    return logical_now(day)


def test_cpa_runaway_creates_a_revert_safety_decision_with_a_p1_alert(world, http, db):
    up, ad = executed_increase(world, http, db)
    as_of = s1_for(world, http, db)
    out = run_monitor(db, as_of)
    assert any("CPA_RUNAWAY" in f["triggers"] for f in out["fired"]), db.query("SELECT * FROM ops.safety_checks")
    assert out["decisions"]
    sd = dec.get_decision(db, out["decisions"][0])
    assert sd["class"] == "SAFETY" and sd["status"] == "PENDING_APPROVAL" and sd["requires_review"], \
        [c for c in sd["checks"] if not c["passed"]]
    executed = {leg["unit_id"]: leg["after"] for leg in dec.get_decision(db, "opt-test:R")["legs"]}
    unit_min = guardrails_config()["change"]["unit_min_budget_inr"]
    assert sd["legs"] and all(leg["before"] == executed[leg["unit_id"]] for leg in sd["legs"])
    assert all(unit_min <= leg["after"] < leg["before"] for leg in sd["legs"])                     # reduce, T58
    alerts = db.query("SELECT payload FROM ops.events WHERE type = 'safety_alert'")
    assert alerts and json.loads(alerts[0][0])["priority"] == "P1"
    assert run_monitor(db, as_of)["decisions"] == []                                              # idempotent per day
    assert db.query("SELECT count(*) FROM intel.decisions WHERE class = 'SAFETY'")[0][0] == 1

    # executing it -> SAFETY_COOLDOWN + the original outcome is contaminated (excluded from calibration)
    d = dec.get_decision(db, out["decisions"][0])
    dec.approve(db, d["decision_id"], d["decision_hash"], "maria", "manager", as_of, load_state(db, as_of))
    assert execute_decision(db, d["decision_id"], ad, "maria", as_of, load_state(db, as_of), NOSLEEP)["state"] == \
        "SUCCEEDED"
    run_monitor(db, as_of)
    assert ("budget", up.unit_id, "SAFETY_COOLDOWN") in {(t, e, r) for t, e, _b, r, _s in locks.active_freezes(db)}
    world.post("/control/advance", json={"days": 12}, headers={"X-Request-ID": "adv-late"})
    late = START + timedelta(days=16)
    refresh(db, http, late)
    m = measure_outcome(db, "opt-test:R", logical_now(late))
    assert m["status"] == "MATURED" and m["calibration"]["applied"] is False
    assert "CONTAMINATED_BY_SAFETY" in m["calibration"]["reason"]


def test_uncertain_external_state_gets_an_alert_and_no_decision_t47(world, http, db):
    up, _ = executed_increase(world, http, db)
    db.write(lambda cur: locks.freeze(cur, [("budget", up.unit_id, up.unit_id)], "EXECUTION_UNCERTAINTY", "SAGA-x",
                                      "SAGA-x:0", logical_now(START)))
    as_of = s1_for(world, http, db)
    out = run_monitor(db, as_of)
    assert out["fired"] and out["decisions"] == [] and out["alerts"][0]["reason"] == "EXECUTION_UNCERTAINTY"
    msg = json.loads(db.query("SELECT payload FROM ops.events WHERE type = 'safety_alert'")[0][0])["message"]
    assert "reconcile first" in msg


def test_reversal_cap_pins_the_unit_to_approve_t31(world, http, db):
    up, _ = executed_increase(world, http, db)
    as_of = s1_for(world, http, db, days=2)
    for k in range(3):  # the monitor fires on consecutive days while CPA stays high
        run_monitor(db, as_of + timedelta(days=k))
    n = db.query("SELECT count(*) FROM intel.decisions WHERE decision_id LIKE 'safety-%'")[0][0]
    assert n >= 3
    pins = dict(db.query("SELECT entity_id, reason FROM ops.autonomy_pins"))
    assert up.unit_id in pins and "automated reversals" in pins[up.unit_id]
