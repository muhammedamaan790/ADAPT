"""C2 pipeline cycle + the closed loop through the REAL execution path (fixture world):
day-0 cycle (ingest .. decide, forecasts backfilled) -> approve -> C5 saga against the world's mock platform APIs ->
advance -> scheduler catch-up cycles (verify, measure, learn) -> outcome measured with rolling-origin residuals.
Idempotent per run_id; steps of later stages are logged NOT_BUILT; duplicate events mark once (T15)."""

import json
from datetime import date

from adapt.decide import decisions as dec
from adapt.economics.state import load_state
from adapt.execute.adapters import MockGoogleAdapter, MockMetaAdapter
from adapt.execute.saga import execute_decision
from adapt.pipeline import events
from adapt.pipeline.cycle import run_cycle
from adapt.pipeline.scheduler import catch_up
from adapt.predict.forecasts import POOL_DAYS, forecast_days
from adapt.reconcile.build import logical_now

START = date(2026, 10, 1)  # fixture world day 0
STEPS = ["ingest", "reconcile", "dq_gate", "detect", "diagnose", "predict", "forecast", "optimize", "decide",
         "policy", "auto_execute", "verify", "safety_monitor", "measure", "learn"]


def test_events_mark_once(db):
    at = logical_now(START)
    first = db.write(lambda cur: events.emit(cur, "inventory_updated", "sku", "K1", at, dedupe_key="e1"))
    again = db.write(lambda cur: events.emit(cur, "inventory_updated", "sku", "K1", at, dedupe_key="e1"))
    assert first and not again
    assert len(events.dirty(db)) == 1


def test_closed_loop_through_the_real_execution_path(world, http, db):
    ad = {"google": MockGoogleAdapter(world), "meta": MockMetaAdapter(world)}
    day0 = catch_up(db, http, ad)
    assert len(day0) == 1
    run0 = day0[0]["run_id"]
    steps = db.query("SELECT step, status FROM ops.pipeline_steps WHERE run_id = ? ORDER BY seq", [run0])
    assert [s for s, _ in steps] == STEPS
    assert dict(steps)["auto_execute"] == "NOT_BUILT" and dict(steps)["safety_monitor"] == "OK"  # Stage 2 built
    assert run_cycle(db, http, logical_now(START), ad)["idempotent_replay"]           # idempotent per run_id
    assert forecast_days(db) >= POOL_DAYS

    did = db.query("SELECT decision_id FROM intel.decisions WHERE class = 'OPTIMIZATION'")[0][0]
    d = dec.get_decision(db, did)
    assert d["status"] == "PENDING_APPROVAL" and dec.replay(db, did)["match"]
    basis = json.loads(db.query("SELECT basis FROM learn.measurement_basis WHERE decision_id = ?", [did])[0][0])
    assert {u["residual_source"] for u in basis["units"].values()} == {"rolling_origin_forecasts"}

    now = logical_now(START)
    state_now = load_state(db, now)
    dec.approve(db, did, d["decision_hash"], "maria", "manager", now, state_now)
    out = execute_decision(db, did, ad, "maria", now, state_now, sleep=lambda s: None)
    assert out["state"] == "SUCCEEDED", out
    assert dec.get_decision(db, did)["status"] == "EXECUTED"

    measured = None
    for k in range(1, 15):
        world.post("/control/advance", json={"days": 1}, headers={"X-Request-ID": f"adv-{k}"})
        runs = catch_up(db, http, ad)
        assert len(runs) == 1
        m = [x for x in runs[0]["steps"]["measure"]["measured"] if x["decision_id"] == did]
        if m and m[0]["status"] == "MATURED":
            measured = (k, m[0])
            break
    assert measured is not None, "outcome never matured within 14 days"
    k, m = measured
    row = db.query("SELECT verdict, window_days, method, realized, ci_lo, ci_hi FROM learn.outcomes "
                   "WHERE outcome_id = ?", [did])[0]
    assert row[0] in ("SUCCESS", "NEUTRAL", "FAILED", "INCONCLUSIVE") and row[1] >= 3
    assert "rolling_origin_forecasts" in row[2] and row[4] <= row[3] <= row[5]
    cal = json.loads(db.query("SELECT calibration FROM learn.outcomes WHERE outcome_id = ?", [did])[0][0])
    assert cal["applied"] or cal["reason"]                     # calibrated exactly once, or the stated reason why not
    if row[0] == "INCONCLUSIVE":
        assert not cal["applied"]
    print(f"matured on day {k}: {row[0]} realized {row[3]:,.0f} CI [{row[4]:,.0f}, {row[5]:,.0f}] calibration {cal}")
    # the next cycle re-measures nothing new and refits because an outcome matured
    world.post("/control/advance", json={"days": 1}, headers={"X-Request-ID": "adv-final"})
    nxt = catch_up(db, http, ad)[0]
    assert nxt["steps"]["predict"]["refit"] and "matured outcome" in nxt["steps"]["predict"]["reason"]
    assert db.query("SELECT count(*) FROM learn.outcomes")[0][0] == 1
