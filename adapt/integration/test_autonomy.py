"""Stage 3 autonomy ladder through the HTTP API (fixture world, auth on).

T38: Autonomous mode cannot be enabled without simulation readiness (a raw-index HIGH never authorizes anything).
With a qualifying warm-up track record imported, the channel becomes eligible and an admin can switch it; the mode
is an EXACT staleness input (the pending decision expires); the next run's decisions on the autonomous channel are
either executed by ADAPT (actor recorded) or downgraded to review with every failed gate named; a non-GREEN data
dependency downgrades. Observe mode records shadow decisions and refuses approval."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from adapt.api.main import create_app
from adapt.api.runtime import bootstrap
from adapt.config.settings import Settings
from adapt.decide import decisions as dec
from adapt.economics.state import load_state
from adapt.learn import qualification as q
from adapt.policy import autonomy

PW = "autonomy-password"


@pytest.fixture
def client(world, tmp_path):
    settings = Settings(data_dir=tmp_path / "data", workspace="auto", env="test", auth_enabled=True,
                        seed_password=PW, session_secret="a" * 32, eval_report_path=tmp_path / "none.json",
                        warmup_track_record_path=tmp_path / "none-warmup.json")
    bootstrap(settings, world_client=world)
    with TestClient(create_app(settings, world_client=world, sync_jobs=True)) as c:
        yield c


def login(c, user):
    r = c.post("/api/v1/auth/login", json={"user_id": user, "password": PW})
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


def hdr(csrf, n):
    return {"X-Request-ID": f"a-{n}", "Idempotency-Key": f"a-{n}", "X-CSRF-Token": csrf}


def ok(r, code=200):
    assert r.status_code == code, r.text[:600]
    return r.json()


def qualifying_artifact(channels=("google", "meta")) -> dict:
    """A warm-up track record that qualifies every region: 3 worlds x 15 successes + 1 failure, 904 with 14 + 2."""
    recs = []
    for ch in channels:
        for reg in q.REGIONS:
            for w, (s, f) in {901: (15, 1), 902: (15, 1), 903: (15, 1), 904: (14, 2)}.items():
                for i in range(s + f):
                    recs.append({"world": "SIMULATED", "world_id": w, "decision_id": f"{ch}-{reg}-{w}-{i}",
                                 "channel": ch, "region": reg, "raw_index": 0.7, "prob_loss": 0.1,
                                 "verdict": "SUCCESS" if i < s else "FAILED",
                                 "realized": 5000.0 if i < s else -1000.0, "executed_by": "sim-manager",
                                 "guardrail_violation": False})
    return q.merge_artifacts([{"world_ids": [901, 902, 903, 904], "records": recs}], "test warm-up")


def channel(policy, label):
    return next(c for c in policy["channels"] if c["channel"] == label)


def put_mode(c, csrf, n, label, mode, code=200):
    pol = ok(c.get("/api/v1/policy"))
    return ok(c.put("/api/v1/policy", json={"policy_version": pol["policy_version"], "revision": pol["revision"],
                                            "channel": label, "mode": mode, "reason": "autonomy ladder test"},
                    headers=hdr(csrf, n)), code)


def test_autonomy_requires_evidence_then_executes_or_downgrades(client):
    c = client
    admin = login(c, "admin")
    pol = ok(c.get("/api/v1/policy"))
    g = channel(pol, "Google")
    assert g["mode"] == "APPROVE" and "SIMULATION_AUTONOMOUS" not in g["allowed_modes"]
    assert not g["simulation"]["eligible"] and not g["production"]["eligible"]
    put_mode(c, admin, 1, "Google", "SIMULATION_AUTONOMOUS", 409)                      # T38: no evidence
    put_mode(c, admin, 2, "Google", "PRODUCTION_AUTONOMOUS", 422)                      # never in this build
    assert ok(c.get("/api/v1/learning/qualification"))["status"] == "NOT_AVAILABLE"

    db = c.app.state.runtime.db
    q.import_track_record(db, qualifying_artifact(), datetime(2026, 10, 1))
    pol = ok(c.get("/api/v1/policy"))
    g = channel(pol, "Google")
    assert g["simulation"]["eligible"] and "SIMULATION_AUTONOMOUS" in g["allowed_modes"]
    assert g["simulation"]["reliability"] == "PASS" and g["simulation"]["wilson_lower"] >= 0.6
    quals = ok(c.get("/api/v1/learning/qualification"))
    assert quals["status"] == "AVAILABLE" and {p["scope"] for p in quals["pools"]} == {"WARMUP", "HELD_OUT"}

    pending = [d for d in ok(c.get("/api/v1/decisions")) if d["status"] == "PENDING_APPROVAL"]
    assert all("confidence" in d and 0 <= d["confidence"]["overall"] <= 1 for d in pending)
    for label in ("Google", "Meta"):
        put_mode(c, admin, f"m-{label}", label, "SIMULATION_AUTONOMOUS")
    if pending:  # the mode map is an EXACT staleness input: an approval now finds the decision expired
        d = pending[0]
        r = c.post(f"/api/v1/decisions/{d['decision_id']}/approve",
                   json={"decision_hash": d["decision_hash"], "execute": False}, headers=hdr(admin, 3))
        assert r.status_code == 409 and "expired" in r.text.lower()

    ok(c.post("/api/v1/sim/advance?days=1", json={}, headers=hdr(admin, 4)))
    latest = max(d["created_at"] for d in ok(c.get("/api/v1/decisions")))
    new = [d for d in ok(c.get("/api/v1/decisions")) if d["created_at"] == latest and d["class"] == "OPTIMIZATION"]
    for d in new:
        a = d.get("autonomy")
        if d["status"] in ("SUPERSEDED", "BLOCKED"):
            continue
        assert a is not None, d["decision_id"]
        ids = {x["id"] for x in a["gates"]}
        assert {"PROB_LOSS", "PORTFOLIO_MOVEMENT", "DATA_DEPENDENCIES_GREEN", "MODELS_AVAILABLE"} <= ids
        if a["result"] == "EXECUTED":
            assert all(x["passed"] for x in a["gates"]) and d["status"] in ("EXECUTED", "PARTIAL")
            ledger = ok(c.get("/api/v1/ledger"))
            assert any(e.get("decision_id") == d["decision_id"] for e in ledger)
        else:
            assert any(not x["passed"] for x in a["gates"]) and d["status"] == "PENDING_APPROVAL"


def test_a_non_green_dependency_downgrades_the_autonomous_candidate(client):
    c = client
    login(c, "viewer")
    db = c.app.state.runtime.db
    d = next(x for x in ok(c.get("/api/v1/decisions")) if x["class"] == "OPTIMIZATION")
    full = dec.get_decision(db, d["decision_id"])
    as_of = db.query("SELECT max(as_of) FROM ops.data_health")[0][0]
    state = load_state(db, as_of)
    recs = q.all_records(db, -1) + qualifying_artifact()["records"]
    db.write(lambda cur: cur.execute("UPDATE ops.data_health SET status = 'YELLOW' WHERE source = 'erp' "
                                     "AND as_of = ?", [as_of]))
    gates = {x["id"]: x for x in autonomy.gates(db, full, state, as_of, recs)}
    assert not gates["DATA_DEPENDENCIES_GREEN"]["passed"] and "erp YELLOW" in gates["DATA_DEPENDENCIES_GREEN"]["detail"]


def test_observe_mode_records_shadow_decisions_and_refuses_approval(client):
    c = client
    admin = login(c, "admin")
    for label in ("Google", "Meta"):
        put_mode(c, admin, f"o-{label}", label, "OBSERVE")
    ok(c.post("/api/v1/sim/advance?days=1", json={}, headers=hdr(admin, 1)))
    sh = ok(c.get("/api/v1/learning/shadow"))
    decisions = ok(c.get("/api/v1/decisions"))
    latest = max(d["created_at"] for d in decisions)
    opt = [d for d in decisions if d["created_at"] == latest and d["class"] == "OPTIMIZATION"
           and d["status"] == "PENDING_APPROVAL"]
    if not opt:
        pytest.skip("the fixture run produced no optimization decision on this day")
    assert sh["status"] == "AVAILABLE" and all(r["method"] == "FORECAST_ONLY" for r in sh["records"])
    assert {r["decision_id"] for r in sh["records"]} >= {opt[0]["decision_id"]}
    r = c.post(f"/api/v1/decisions/{opt[0]['decision_id']}/approve",
               json={"decision_hash": opt[0]["decision_hash"], "execute": True}, headers=hdr(admin, 2))
    assert r.status_code == 409 and "Observe" in r.text
