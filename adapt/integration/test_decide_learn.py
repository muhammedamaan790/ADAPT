"""B6-B8 through the real path (fixture world -> ingest -> reconcile -> curves -> economics -> optimizer -> world
budget change -> advance -> ingest -> outcome -> calibration).

C5's execution saga is not built yet: legs are applied with the world's /control/budget (a stand-in, never the
product's execution path), which is enough to test that measurement reads what actually happened."""

import json
from datetime import date, timedelta

import numpy as np

from adapt.decide.run import run_optimizer
from adapt.economics.state import load_state
from adapt.ingest.sync import run_sync
from adapt.learn.outcomes import measure_outcome
from adapt.predict.fit_curves import fit_curves
from adapt.reconcile.build import build_canonical, logical_now

START = date(2026, 10, 1)  # fixture world day 0


def refresh(db, http, day: date):
    run_sync(db, http)
    build_canonical(db, logical_now(day))


def apply_legs(world, legs, tag):
    for leg in legs:
        r = world.post("/control/budget", json={"platform": leg["platform"], "budget_id": leg["budget_id"],
                                                "amount": leg["after"]},
                       headers={"X-Request-ID": f"{tag}-{leg['budget_id']}", "X-Actor-ID": "test-executor"})
        assert r.status_code == 200, r.text


def test_state_and_optimizer_on_the_fixture_world(world, http, db):
    refresh(db, http, START)
    fit_curves(db, logical_now(START))
    state = load_state(db, logical_now(START))
    assert state.units and state.skus
    for u in state.units:
        assert abs(sum(u.sku_weights.values()) + u.unmapped_share - 1) < 1e-6 and 0 < u.pacing <= 1.2
    for s in state.skus.values():
        assert s.nrpu > 0 and s.unit_contribution < s.nrpu
    run = run_optimizer(db, logical_now(START))
    r = run["result"]
    assert r["status"] == "OK"
    total = sum(r["allocation"].values())
    assert total <= r["total_budget"] - r["reserve_floor"] + 1e-6
    assert abs(r["unallocated"] - (r["total_budget"] - total)) < 1e-6
    for leg in r["legs"]:
        lo, hi = leg["before"] * 0.8, leg["before"] * 1.2
        assert lo - 1e-6 <= leg["after"] <= hi + 1e-6
    unavailable = {u.unit_id for u in state.units if not u.model_available}
    assert all(r["allocation"][u] <= r["baseline"][u] + 1e-9 for u in unavailable)   # never scaled up
    reasons = {w["binding_constraint"] or w["reason"] for w in r["why_not"]}
    assert None not in reasons                                                         # never a generic reason
    assert r["expected"]["calibrated_pred"] == (r["expected"]["raw_pred"] * run["calibration_factor"]
                                                if r["expected"]["raw_pred"] > 0 else r["expected"]["raw_pred"])
    assert db.query("SELECT count(*) FROM intel.optimizer_runs")[0][0] == 1


def test_s3_blocks_scale_and_yields_a_safety_candidate_whose_outcome_is_measured(world, http, db):
    run_sync(db, http)
    r = world.post("/control/scenario", json={"key": "S3"}, headers={"X-Request-ID": "scn-S3"})
    assert r.status_code == 200, r.text
    world.post("/control/advance", json={"days": 2}, headers={"X-Request-ID": "adv-2"})
    day = START + timedelta(days=2)
    refresh(db, http, day)
    fit_curves(db, logical_now(day))
    sku =json.loads(world.app.state.store.read("SELECT params FROM scenario_activation")[0][0])["sku"]

    run = run_optimizer(db, logical_now(day))
    res = run["result"]
    risk = res["inventory_risk_after"]["by_sku"][sku]
    if res["inventory_risk_after"]["kind"] == "PROJECTED_SHORTFALL":                  # Stage 1 predicate
        assert risk["status"] in ("AT_RISK", "SHORT") and risk["shortfall"] > 0
    else:                                                                              # Stage 2: NB2 P(stockout)
        assert risk["status"] in ("AT_RISK", "SHORT") and risk["stockout_probability"] > 0.3
    gated = [uid for uid, g in res["inventory_gate"].items() if g["gate"] in ("LIMIT", "BLOCK")]
    assert gated, res["inventory_gate"]
    assert all(res["allocation"][u] <= res["baseline"][u] + 1e-9
               for u in gated if res["inventory_gate"][u]["gate"] == "BLOCK")              # BLOCK SCALE
    cands = [c for c in run["safety"] if c["sku"] == sku]
    assert cands, [c["sku"] for c in run["safety"]]
    cand = cands[0]
    assert cand["class"] == "SAFETY" and cand["status"] == "REQUIRES_REVIEW"
    assert all(leg["after"] < leg["before"] for leg in cand["legs"]) and cand["freed_budget"] > 0
    assert cand["remaining_risk"] <= cand["risk_before"]

    # approve (human) -> apply -> advance the 3-day safety window -> measure
    apply_legs(world, cand["legs"], "safety")
    assert measure_outcome(db, cand["decision_id"], logical_now(day))["status"] == "PENDING"
    world.post("/control/advance", json={"days": 3}, headers={"X-Request-ID": "adv-3"})
    end = day + timedelta(days=3)
    refresh(db, http, end)
    out = measure_outcome(db, cand["decision_id"], logical_now(end))
    assert out["status"] == "MATURED" and out["window_days"] == 3
    assert out["verdict"] in ("SUCCESS", "NEUTRAL", "FAILED", "INCONCLUSIVE")
    assert out["ci_lo"] <= out["realized"] <= out["ci_hi"] and np.isfinite(out["observed_caa"])
    assert out["calibration"] is None                         # SAFETY outcomes never calibrate the curves
    again = measure_outcome(db, cand["decision_id"], logical_now(end + timedelta(days=1)))
    assert again["idempotent_replay"] and again["verdict"] == out["verdict"]
    assert db.query("SELECT count(*) FROM learn.outcomes")[0][0] == 1
