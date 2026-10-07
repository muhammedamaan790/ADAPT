"""Stage 2 wiring through the HTTP API (fixture world, auth on): every Stage 2 service function is reachable from the
product path, with its failure states. Objective switching (admin, revision-checked), the selected objective in the
optimizer workbench, sensitivity alternatives and choosing one, guarded narratives (pipeline-stored, offline
templates), the gated causal estimate on anomalies, platform health, the mirror guard on world advance (T41),
demand-model rollback, Stage 2 scenarios, and the evaluation report / uplift read from eval.json."""

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from adapt.api.main import create_app
from adapt.api.runtime import bootstrap
from adapt.config.settings import Settings
from adapt.learn import governance
from evalharness import report

PW = "stage2-password"


@pytest.fixture
def client(world, tmp_path):
    settings = Settings(data_dir=tmp_path / "data", workspace="stage2", env="test", auth_enabled=True,
                        seed_password=PW, session_secret="s" * 32, eval_report_path=tmp_path / "eval.json")
    bootstrap(settings, world_client=world)
    with TestClient(create_app(settings, world_client=world, sync_jobs=True)) as c:
        yield c, tmp_path / "eval.json"


def login(c, user):
    r = c.post("/api/v1/auth/login", json={"user_id": user, "password": PW})
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


def hdr(csrf, n):
    return {"X-Request-ID": f"t-{n}", "Idempotency-Key": f"t-{n}", "X-CSRF-Token": csrf}


def ok(r, code=200):
    assert r.status_code == code, r.text[:500]
    return r.json()


def test_objective_switch_drives_the_optimizer_workbench(client):
    c, _ = client
    manager = login(c, "maria")
    obj = ok(c.get("/api/v1/objective"))
    assert obj["objective"] == "PROFIT" and obj["can_change"]
    assert set(obj["supported_objectives"]) == {"PROFIT", "GROWTH", "INVENTORY_CLEARANCE"}
    body = {"workspace_id": "stage2", "revision": obj["revision"], "objective": "GROWTH",
            "reason": "test the growth objective"}
    assert c.put("/api/v1/objective", json=body, headers=hdr(manager, 1)).status_code == 403   # admin only

    admin = login(c, "admin")
    assert c.put("/api/v1/objective", json={**body, "revision": "stale"}, headers=hdr(admin, 2)).status_code == 409
    assert c.put("/api/v1/objective", json={**body, "objective": "ACQUISITION"},
                 headers=hdr(admin, 3)).status_code == 422                                     # not built
    new = ok(c.put("/api/v1/objective", json=body, headers=hdr(admin, 4)))
    assert new["objective"] == "GROWTH" and new["revision"] != obj["revision"]

    ctx = ok(c.get("/api/v1/optimizer/context"))
    assert ctx["objective"] == "GROWTH" and ctx["supported_objectives"] == ["GROWTH"]
    legs = [{"budget_id": x["budget_id"], "after": round(x["before"])} for x in ctx["campaigns"]]
    wi = {"decision_id": ctx["decision_id"] or "none", "decision_hash": ctx["decision_hash"] or "none",
          "objective": "GROWTH", "policy_version": ctx["policy_version"], "legs": legs}
    ev = ok(c.post("/api/v1/optimizer/whatif", json=wi, headers=hdr(admin, 5)))
    assert ev["estimate_status"] == "AVAILABLE" and "net revenue" in ev["objective_value"]["label"]
    assert ev["objective_value"]["value"] == pytest.approx(0.0, abs=1.0)                     # unchanged allocation
    ev = ok(c.post("/api/v1/optimizer/whatif", json={**wi, "objective": "PROFIT"}, headers=hdr(admin, 6)))
    assert ev["estimate_status"] == "NOT_ESTIMABLE" and "GROWTH" in ev["explanation"]
    r = c.post("/api/v1/optimizer/run", json={"objective": "PROFIT"}, headers=hdr(admin, 7))
    assert r.status_code == 409 and "GROWTH" in r.text                                     # never silently swapped


def test_alternatives_narratives_causal_and_brief(client):
    c, _ = client
    manager = login(c, "maria")
    decisions = ok(c.get("/api/v1/decisions"))
    d = next(x for x in decisions if x["class"] == "OPTIMIZATION" and x["status"] == "PENDING_APPROVAL")
    assert d["inventory_risk_after"]["kind"] in ("PROJECTED_SHORTFALL", "STOCKOUT_PROBABILITY")

    nar = ok(c.get(f"/api/v1/decisions/{d['decision_id']}/narrative"))
    assert nar["source"] == "template" and nar["badge"] == "evidence linked · values checked"
    assert nar["sentences"] and all(s["atom_ids"] for s in nar["sentences"])
    assert ok(c.get("/api/v1/overview"))["brief"].startswith("Daily brief:")              # the pipeline's brief
    assert c.get("/api/v1/decisions/nope/narrative").status_code == 404

    for a in ok(c.get("/api/v1/anomalies")):
        assert a["causal"]["status"] in ("ESTIMABLE", "NOT_ESTIMABLE")
        if a["causal"]["status"] == "NOT_ESTIMABLE":
            assert a["causal"]["effect_pct"] is None and a["causal"]["reason"]
        assert ok(c.get(f"/api/v1/anomalies/{a['anomaly_id']}/narrative"))["kind"] == "incident"

    comp = ok(c.post(f"/api/v1/decisions/{d['decision_id']}/simulate", json={"decision_hash": d["decision_hash"]},
                     headers=hdr(manager, 1)))
    alts = {a["id"]: a for a in comp["alternatives"]}
    assert set(alts) <= {"conservative", "aggressive"}
    if not alts:
        pytest.skip("this fixture decision has no executable sensitivity scenario")
    name = next(iter(alts))
    url = f"/api/v1/decisions/{d['decision_id']}/alternatives/{name}/choose"
    assert c.post(url, json={"decision_hash": "stale"}, headers=hdr(manager, 2)).status_code == 409
    new = ok(c.post(url, json={"decision_hash": d["decision_hash"]}, headers=hdr(manager, 3)))
    assert new["decision_id"] != d["decision_id"] and new["status"] == "PENDING_APPROVAL"
    assert ok(c.get(f"/api/v1/decisions/{d['decision_id']}"))["status"] == "SUPERSEDED"


def test_platform_health_and_the_mirror_guard_on_advance(client):
    c, _ = client
    manager = login(c, "maria")
    h = ok(c.get("/api/v1/platforms/health"))
    assert {p["platform"] for p in h["platforms"]} >= {"google", "meta"}
    assert all(p["mode"] == "MOCK" and p["ok"] for p in h["platforms"]) and h["sim_out_of_sync"] == []

    # a verified live change whose mirror is still pending: the world must not advance (spec §9.4, T41)
    db = c.app.state.runtime.db
    from adapt.execute import mirror

    db.write(lambda cur: (cur.execute(mirror.DDL), cur.execute(
        "INSERT INTO exec.leg_mirror VALUES ('leg-x', 'budget-x', 1000, 'MIRROR_PENDING', ?, 0, NULL, ?, NULL)",
        [datetime(2026, 10, 1), datetime(2026, 10, 1)])))
    assert ok(c.get("/api/v1/platforms/health"))["sim_out_of_sync"][0]["state"] == "MIRROR_PENDING"
    day = c.app.state.runtime.world()["day"]
    r = c.post("/api/v1/sim/advance?days=1", json={}, headers=hdr(manager, 1))
    assert r.status_code == 409 and "SIM OUT OF SYNC" in r.text
    assert c.app.state.runtime.world()["day"] == day                                       # nothing advanced
    db.write(lambda cur: cur.execute("UPDATE exec.leg_mirror SET state = 'MIRRORED'"))
    ok(c.post("/api/v1/sim/advance?days=1", json={}, headers=hdr(manager, 2)))
    assert c.app.state.runtime.world()["day"] == day + 1
    r = c.post("/api/v1/executions/none/reconcile?target=sim", json={"decision_hash": "x", "reason": "mirror"},
               headers=hdr(manager, 3))
    assert r.status_code == 409                                                            # no live platform (mock)


def test_stage2_scenarios_load(client):
    c, _ = client
    manager = login(c, "maria")
    cat = {i["key"]: i for i in ok(c.get("/api/v1/sim/scenarios"))["items"]}
    assert all(cat[k]["status"] == "AVAILABLE" for k in ("S6", "S8", "S10", "S11", "S12"))
    assert cat["S9"]["status"] == "NOT_BUILT"
    ok(c.post("/api/v1/sim/scenario/S6", json={}, headers=hdr(manager, 1)))
    assert c.post("/api/v1/sim/scenario/S9", json={}, headers=hdr(manager, 2)).status_code == 422


def test_demand_model_rollback(client):
    c, _ = client
    admin = login(c, "admin")
    db = c.app.state.runtime.db
    t0, t1 = datetime(2026, 9, 1), datetime(2026, 9, 8)
    governance.register_champion(db, "demand", "seasonal-naive", t0, "SEASONAL_NAIVE", "first champion")
    governance.register_champion(db, "demand", "demand-v2", t1, "LIGHTGBM", "promoted", artifact_sha256="a" * 64)
    models = {(m["name"], m["version"]): m for m in ok(c.get("/api/v1/models"))}
    assert models[("demand", "demand-v2")]["status"] == "CHAMPION"
    detail = ok(c.get("/api/v1/models/demand/demand-v2"))
    assert detail["allowed_actions"] == ["ROLLBACK"] and detail["rollback_version"] == "seasonal-naive"

    body = {"version": "demand-v2", "registry_revision": detail["registry_revision"],
            "artifact_hash": detail["artifact_hash"], "reason": "regression seen in the holdout"}
    assert c.post("/api/v1/models/demand/rollback", json={**body, "registry_revision": "old"},
                  headers=hdr(admin, 1)).status_code == 409
    out = ok(c.post("/api/v1/models/demand/rollback", json=body, headers=hdr(admin, 2)))
    assert out["name"] == "demand" and out["version"] == "seasonal-naive"
    assert governance.champion(db, "demand")["version"] == "seasonal-naive"            # the forecast loads this one
    assert c.post("/api/v1/models/response_curve/rollback", json=body, headers=hdr(admin, 3)).status_code == 422
    assert c.post("/api/v1/models/demand/promote", json=body, headers=hdr(admin, 4)).status_code == 422


def test_evaluation_report_and_uplift_from_eval_json(client):
    c, path = client
    login(c, "viewer")
    assert ok(c.get("/api/v1/eval/report"))["status"] == "NOT_AVAILABLE"
    assert ok(c.get("/api/v1/learning/uplift"))["status"] == "NOT_AVAILABLE"

    def run(seed, bump):
        strategies = {s: {"caa": 1000.0 * (k + 1) + bump, "spend": 5000.0, "orders": 10, "caa_per_rupee": 0.2,
                          "stock_risk_days": k, "violations": [], "forced_interventions": int(s == "safe-static"),
                          "seconds_per_day": 1.0, "log": []}
                      for k, s in enumerate(("safe-static", "roas-rank", "contribution-rank", "safe-contribution",
                                             "adapt", "oracle"))}
        return {"start_day": 0, "days": 60, "budget_ceiling": 5000.0, "reserve": 0.0, "strategies": strategies,
                "common_random_numbers": True}

    rep = report.build({101: run(101, 0.0), 102: run(102, 50.0)})
    rep.update(contract={"class": "PRIMARY_EVAL", "days": 60, "seeds_requested": [101, 102]},
               generated_at="2026-10-08T00:00:00Z", code_sha="abc123")
    path.write_text(json.dumps(rep, default=str))
    env = ok(c.get("/api/v1/eval/report"))
    assert env["status"] == "AVAILABLE" and env["report"]["seeds"] == [101, 102]
    rows = env["report"]["rows"]
    assert len(rows) == 8 and {r["strategy"] for r in rows} == {"safe-static", "safe-contribution", "adapt", "oracle"}
    assert next(r for r in rows if r["strategy"] == "safe-static")["forced_interventions"] == 1
    assert env["report"]["summary"]["primary"]["mean"] == pytest.approx(4000.0)
    up = ok(c.get("/api/v1/learning/uplift"))
    assert up["status"] == "AVAILABLE" and len(up["rows"]) == 6 and "U(adapt vs safe-static)" in up["note"]
