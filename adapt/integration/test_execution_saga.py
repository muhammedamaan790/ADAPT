"""C5 execution saga against the world's mock platform APIs (bare control-plane world: budgets + injected faults).

T11 503 on the increase leg -> ACCEPTED_PARTIAL (risk-reducing kept) | compensation path | T12 rollback |
T13 external change -> CONFLICT -> reconcile | T14/T26 ROLLBACK_CONFLICT | T16/T27 duplicate and concurrent execute
-> one saga, one mutation | T17 timeout-after-success -> read-after-write VERIFIED, no double mutation |
T25 stale read-back -> UNKNOWN (never assumed) -> re-verify | T23/T30 live mode never falls back to mock |
T42 an UNKNOWN leg freezes the unit for the optimizer/policy | crash recovery | T33 ledger redaction."""

import json
import threading

import pytest
from fastapi.testclient import TestClient
from tests.test_optimizer import T0, curve_unit

from adapt.core.db import Database
from adapt.decide import decisions as dec
from adapt.decide.run import policy_flags
from adapt.decide.snapshot import state_from_dict, state_to_dict
from adapt.economics.portfolio import PortfolioState, SkuState
from adapt.execute import saga
from adapt.execute.adapters import LiveNotBuilt, MockGoogleAdapter, MockMetaAdapter, redact
from world.main import create_app

M0, M1, G2 = "238000000100", "238000000101", "30000000200"   # meta, meta, google budgets
BUDGETS = {M0: ("meta", 30000.0), M1: ("meta", 20000.0), G2: ("google", 40000.0)}
NOSLEEP = lambda s: None  # noqa: E731


@pytest.fixture
def world(tmp_path):
    with TestClient(create_app(state_path=tmp_path / "world_state.duckdb")) as client:
        for bid, (platform, amount) in BUDGETS.items():
            r = client.post("/control/budget", json={"platform": platform, "budget_id": bid, "amount": amount},
                            headers={"X-Request-ID": f"seed-{bid}"})
            assert r.status_code == 200, r.text
        yield client


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "ws.duckdb")
    yield d
    d.close()


def adapters(world):
    return {"google": MockGoogleAdapter(world), "meta": MockMetaAdapter(world)}


def state():
    units = [curve_unit(M0, 30000, 2.0, 1.0, 1.0, 60000, {"k": 1.0}, channel="meta"),
             curve_unit(M1, 20000, 2.0, 1.0, 1.0, 40000, {"k": 1.0}, channel="meta", seed=1),
             curve_unit(G2, 40000, 2.0, 1.0, 1.0, 80000, {"k": 1.0}, channel="google_search", seed=2)]
    return PortfolioState(T0, 7, units, {"k": SkuState("k", 100.0, 60.0, 1e9, 0.0, 0.0)}, n_draws=40)


def copy(s):
    return state_from_dict(state_to_dict(s))


def leg(uid, after):
    platform, before = BUDGETS[uid]
    return {"unit_id": uid, "platform": platform, "channel": "meta" if platform == "meta" else "google_search",
            "budget_id": uid, "campaign_ids": [uid], "before": before, "after": after}


def approved(db, st, legs, did="run-1-R"):
    exp = {"E": 1000.0, "P10": 500.0, "P50": 1000.0, "P90": 1500.0, "prob_loss": 0.1, "delta_net_revenue": 2000.0,
           "raw_pred": 1000.0}
    run = {"run_id": did.rsplit("-", 1)[0], "calibration_factor": 0.9, "safety": [],
           "result": {"status": "OK", "decision_id": did, "legs": legs, "expected": exp, "why_not": [],
                      "inventory_risk_after": {"kind": "PROJECTED_SHORTFALL", "by_sku": {}},
                      "unallocated": 0.0, "reserve_floor": 0.0}}
    assert dec.create_decisions(db, run, st, {}, T0) == [did]
    d = dec.get_decision(db, did)
    assert d["status"] == "PENDING_APPROVAL", d["checks"]
    dec.approve(db, did, d["decision_hash"], "maria", "manager", T0, copy(st))
    return did


def world_amount(world, bid):
    platform, _ = BUDGETS[bid]
    return adapters(world)[platform].read(bid)["amount_inr"]


def mutations(world, bid):
    return [e for e in world.get("/control/log").json()
            if e["operation"] == "platform_set_budget" and bid in json.dumps(e)]


def fault(world, platform, kind, count=1):
    r = world.post("/control/fault", json={"platform": platform, "fault": kind, "count": count},
                   headers={"X-Request-ID": f"fault-{platform}-{kind}-{count}"})
    assert r.status_code == 200, r.text


def test_happy_path_verified_by_read_back(world, db):
    st = state()
    did = approved(db, st, [leg(G2, 44000.0), leg(M0, 26000.0)])
    out = saga.execute_decision(db, did, adapters(world), "maria", T0, copy(st), NOSLEEP)
    assert out["state"] == "SUCCEEDED" and [x["state"] for x in out["legs"]] == ["VERIFIED", "VERIFIED"]
    assert out["legs"][0]["budget_id"] == M0                       # risk-reducing leg first
    assert world_amount(world, G2) == pytest.approx(44000.0) and world_amount(world, M0) == pytest.approx(26000, abs=1)
    assert dec.get_decision(db, did)["status"] == "EXECUTED"
    assert db.query("SELECT count(*) FROM ops.entity_reservations WHERE released_at IS NULL")[0][0] == 0
    assert db.query("SELECT count(*) FROM ops.entity_freezes WHERE cleared_at IS NULL")[0][0] == 0
    assert db.query("SELECT count(*) FROM ops.events WHERE type = 'action_executed'")[0][0] == 2


def test_duplicate_and_concurrent_execution_is_one_saga(world, db):
    st = state()
    did = approved(db, st, [leg(G2, 44000.0), leg(M0, 26000.0)])
    errors, results = [], []

    def go():
        try:
            results.append(saga.execute_decision(db, did, adapters(world), "maria", T0, copy(st), NOSLEEP))
        except dec.DecisionError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 1 and len(errors) == 1 and errors[0].code == "CONFLICT"
    with pytest.raises(dec.DecisionError):
        saga.execute_decision(db, did, adapters(world), "maria", T0, copy(st), NOSLEEP)
    assert len(mutations(world, G2)) == 1 and db.query("SELECT count(*) FROM exec.sagas")[0][0] == 1


def test_timeout_after_success_is_verified_without_a_second_mutation(world, db):
    st = state()
    did = approved(db, st, [leg(G2, 44000.0), leg(M0, 26000.0)])
    fault(world, "google", "timeout_after_success")
    out = saga.execute_decision(db, did, adapters(world), "maria", T0, copy(st), NOSLEEP)
    g = next(x for x in out["legs"] if x["budget_id"] == G2)
    assert out["state"] == "SUCCEEDED" and g["state"] == "VERIFIED" and g["attempts"] == 1
    assert len(mutations(world, G2)) == 1 and world_amount(world, G2) == pytest.approx(44000.0)


def test_503_on_the_increase_leg_keeps_the_risk_reducing_leg(world, db):
    st = state()
    did = approved(db, st, [leg(G2, 44000.0), leg(M0, 26000.0)])
    fault(world, "google", "unavailable", count=3)
    out = saga.execute_decision(db, did, adapters(world), "maria", T0, copy(st), NOSLEEP)
    states = {x["budget_id"]: x["state"] for x in out["legs"]}
    assert states == {M0: "VERIFIED", G2: "FAILED"} and out["state"] == "ACCEPTED_PARTIAL"
    assert dec.get_decision(db, did)["status"] == "PARTIAL"
    assert world_amount(world, G2) == pytest.approx(40000.0)       # the failed increase never applied
    assert db.query("SELECT count(*) FROM ops.entity_freezes WHERE cleared_at IS NULL")[0][0] == 0


def test_failed_leg_after_a_non_risk_reducing_leg_is_compensated(world, db):
    st = state()
    did = approved(db, st, [leg(M0, 25000.0), leg(M1, 22000.0), leg(G2, 42000.0)])
    fault(world, "google", "unavailable", count=3)
    out = saga.execute_decision(db, did, adapters(world), "maria", T0, copy(st), NOSLEEP)
    assert out["state"] == "COMPENSATED"
    assert world_amount(world, M1) == pytest.approx(20000, abs=1) and world_amount(world, M0) == pytest.approx(
        30000, abs=1)
    assert dec.get_decision(db, did)["status"] == "PARTIAL"


def test_external_change_is_a_conflict_until_reconciled(world, db):
    st = state()
    did = approved(db, st, [leg(G2, 44000.0), leg(M0, 26000.0)])
    world.post("/control/budget", json={"platform": "meta", "budget_id": M0, "amount": 31000.0},
               headers={"X-Request-ID": "human-edit"})
    out = saga.execute_decision(db, did, adapters(world), "maria", T0, copy(st), NOSLEEP)
    assert out["legs"][0]["state"] == "CONFLICT" and out["legs"][1]["state"] == "PLANNED"
    assert len(mutations(world, G2)) == 0                           # aborted: nothing else was sent
    assert dec.get_decision(db, did)["status"] == "EXECUTING"
    frozen = {e for (_, e, *_r) in db.query("SELECT entity_type, entity_id FROM ops.entity_freezes "
                                            "WHERE cleared_at IS NULL")}
    assert M0 in frozen
    assert "EXECUTION_FREEZE" in policy_flags(db, st, T0).get(M0, [])   # T42: the optimizer pins the unit
    with pytest.raises(dec.DecisionError, match="FORBIDDEN"):
        saga.reconcile_conflict(db, out["legs"][0]["leg_id"], "accept_observed", "v", "viewer", T0)
    res = saga.reconcile_conflict(db, out["legs"][0]["leg_id"], "accept_observed", "maria", "manager", T0)
    assert res["state"] == "BLOCKED" and dec.get_decision(db, did)["status"] == "BLOCKED"
    assert db.query("SELECT count(*) FROM ops.entity_freezes WHERE cleared_at IS NULL")[0][0] == 0


class StaleRead(MockGoogleAdapter):
    """Eventually consistent platform: read-back shows the old value for the first `stale` reads after a send."""

    def __init__(self, client, stale):
        super().__init__(client)
        self.stale, self.sent, self.old = stale, False, None

    def set_budget(self, budget_id, amount_inr, request_id=None):
        self.old = super().read(budget_id)
        self.sent = True
        return super().set_budget(budget_id, amount_inr, request_id)

    def read(self, budget_id):
        if self.sent and self.stale > 0:
            self.stale -= 1
            return self.old
        return super().read(budget_id)


def test_stale_read_back_is_unknown_never_assumed_then_reverified(world, db):
    st = state()
    did = approved(db, st, [leg(G2, 44000.0), leg(M0, 26000.0)])
    ad = adapters(world) | {"google": StaleRead(world, stale=10)}
    out = saga.execute_decision(db, did, ad, "maria", T0, copy(st), NOSLEEP)
    g = next(x for x in out["legs"] if x["budget_id"] == G2)
    assert g["state"] == "UNKNOWN" and out["state"] == "PARTIAL"
    assert dec.get_decision(db, did)["status"] == "PARTIAL"
    assert "EXECUTION_FREEZE" in policy_flags(db, st, T0).get(G2, [])
    done = saga.reverify(db, adapters(world), T0, NOSLEEP)
    assert done and done[0]["state"] == "SUCCEEDED"
    assert dec.get_decision(db, did)["status"] == "EXECUTED"
    assert db.query("SELECT count(*) FROM ops.entity_freezes WHERE cleared_at IS NULL")[0][0] == 0


class Crash(BaseException):
    pass


class CrashAfterSend(MockGoogleAdapter):
    def set_budget(self, budget_id, amount_inr, request_id=None):
        super().set_budget(budget_id, amount_inr, request_id)
        raise Crash("process died after the request left")


def test_crash_after_send_recovers_from_the_write_ahead_ledger(world, db):
    st = state()
    did = approved(db, st, [leg(G2, 44000.0), leg(M0, 26000.0)])
    with pytest.raises(Crash):
        saga.execute_decision(db, did, adapters(world) | {"google": CrashAfterSend(world)}, "maria", T0, copy(st),
                              NOSLEEP)
    assert db.query("SELECT state FROM exec.saga_legs WHERE budget_id = ?", [G2])[0][0] == "SENT"
    out = saga.recover(db, adapters(world), T0, NOSLEEP)
    assert out[0]["state"] == "SUCCEEDED" and len(mutations(world, G2)) == 1   # verified, not re-sent


def test_live_mode_never_falls_back_to_mock(world, db):
    st = state()
    did = approved(db, st, [leg(G2, 36000.0)])
    out = saga.execute_decision(db, did, adapters(world) | {"google": LiveNotBuilt("google")}, "maria", T0, copy(st),
                                NOSLEEP)
    assert out["state"] == "BLOCKED" and out["legs"][0]["state"] == "PLANNED"
    assert "live adapter not built" in out["legs"][0]["error"]
    assert len(mutations(world, G2)) == 0 and dec.get_decision(db, did)["status"] == "BLOCKED"


def test_rollback_and_rollback_conflict(world, db):
    st = state()
    did = approved(db, st, [leg(G2, 44000.0), leg(M0, 26000.0)])
    saga.execute_decision(db, did, adapters(world), "maria", T0, copy(st), NOSLEEP)
    world.post("/control/budget", json={"platform": "google", "budget_id": G2, "amount": 45000.0},
               headers={"X-Request-ID": "human-after"})
    with pytest.raises(dec.DecisionError, match="ROLLBACK_CONFLICT"):
        saga.rollback(db, did, adapters(world), "maria", T0, NOSLEEP)
    world.post("/control/budget", json={"platform": "google", "budget_id": G2, "amount": 44000.0},
               headers={"X-Request-ID": "human-undo"})
    rb = saga.rollback(db, did, adapters(world), "maria", T0, NOSLEEP)
    assert rb["kind"] == "ROLLBACK" and rb["state"] == "SUCCEEDED"
    assert world_amount(world, G2) == pytest.approx(40000.0) and world_amount(world, M0) == pytest.approx(30000, abs=1)


def test_reservations_serialise_sagas_and_ledger_is_redacted(world, db):
    st = state()
    first = approved(db, st, [leg(G2, 36000.0)], "run-1-R")
    saga.execute_decision(db, first, adapters(world) | {"google": StaleRead(world, stale=10)}, "maria", T0, copy(st),
                          NOSLEEP)                                   # leaves G2 UNKNOWN: reservation held
    second = approved(db, st, [leg(G2, 38000.0)], "run-2-R")
    with pytest.raises(Exception, match="reserved by saga"):
        saga.execute_decision(db, second, adapters(world), "maria", T0, copy(st), NOSLEEP)
    ledger = json.dumps(db.query("SELECT request, response FROM exec.saga_legs"))
    assert "refresh_token" not in ledger and "Bearer" not in ledger
    assert redact({"Authorization": "Bearer x", "client_secret": "s", "ok": 1}) == {
        "Authorization": "[REDACTED]", "client_secret": "[REDACTED]", "ok": 1}
