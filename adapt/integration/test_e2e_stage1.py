"""Stage 1 gate (spec §0.5): the closed loop through the HTTP API the frontend uses, on the fixture world.

world data -> ingest -> detect -> diagnose -> deterministic inventory risk -> PROFIT optimize -> unallocated /
reserve -> why-not -> policy -> approve (hash-bound) -> mock execute + read-back verify -> advance -> outcome ->
learning (refit after a matured outcome; calibration exactly once or a stated reason) -> next decision; plus S3
(BLOCK SCALE + safety candidate in review), a stale-hash 409, rejection and Scenario Lab reset.
The browser half of the gate is the frontend's Playwright journey (D5) against this same API."""

import json
import os
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adapt.api.main import create_app
from adapt.api.runtime import bootstrap
from adapt.config.settings import Settings

H = {"X-Request-ID": "t", "Idempotency-Key": "t"}
START = date(2026, 10, 1)


def hdr(n: int) -> dict:
    return {"X-Request-ID": f"req-{n}", "Idempotency-Key": f"idem-{n}"}


@pytest.fixture
def api(world, tmp_path):
    settings = Settings(data_dir=tmp_path / "data", workspace="e2e")
    out = bootstrap(settings, world_client=world)
    assert out["runs"] and settings.workspace_db_path.with_name("e2e.baseline.duckdb").exists()
    with TestClient(create_app(settings, world_client=world, sync_jobs=True)) as c:
        yield c


SAMPLES = os.environ.get("ADAPT_CONTRACT_SAMPLES")  # dir: dump real responses for the frontend's zod check


def ok(r, code=200):
    assert r.status_code == code, r.text
    body = r.json()
    if SAMPLES and r.request.method == "GET" and body not in ([], {}):
        name = r.request.url.path.replace("/api/v1/", "").replace("/", "__").replace(":", "_")
        Path(SAMPLES).mkdir(parents=True, exist_ok=True)
        (Path(SAMPLES) / f"{name}.json").write_text(json.dumps(body))
    return body


def test_stage1_closed_loop_through_the_api(api):
    health = ok(api.get("/api/v1/health"))
    assert health["execution_modes"] == {"google": "mock", "meta": "mock"}

    ov = ok(api.get("/api/v1/overview"))
    assert ov["world_day"] == 0 and ov["scenario"] == "BASELINE" and ov["metrics"] and ov["sources"]
    assert ov["calibration"] == pytest.approx(0.9) and sum(ov["counts"].values()) == 0
    assert any(a["kind"] == "opportunity" for a in ov["attention"])

    decisions = ok(api.get("/api/v1/decisions"))
    d = next(x for x in decisions if x["class"] == "OPTIMIZATION" and x["status"] == "PENDING_APPROVAL")
    assert d["inventory_risk_after"]["kind"] == "PROJECTED_SHORTFALL" and d["checks"]
    assert all(c["passed"] for c in d["checks"]) and d["why_not"] and d["legs"]
    assert d["budget_ceiling"] >= sum(leg["after"] for leg in d["legs"])
    assert {leg["platform"] for leg in d["legs"]} <= {"Meta", "Google"} and d["title"] and d["summary"]
    assert "follows" not in d                                       # zod .optional(): omitted, never null
    ev = ok(api.get(f"/api/v1/decisions/{d['decision_id']}/evidence"))
    assert ev["decision_id"] == d["decision_id"] and ev["chart"]
    ok(api.get("/api/v1/anomalies"))
    ctx = ok(api.get("/api/v1/optimizer/context"))
    assert ctx["decision_id"] == d["decision_id"] and ctx["supported_objectives"] == ["PROFIT"]
    assert ok(api.get("/api/v1/executions")) == [] and ok(api.get("/api/v1/ledger")) == []

    # mutations require the request headers
    assert api.post(f"/api/v1/decisions/{d['decision_id']}/approve", json={"decision_hash": d["decision_hash"]}
                    ).status_code == 422

    # what-if and modify: a user edit becomes a new, separately valued decision that follows the original
    leg = d["legs"][0]
    edit = {"decision_id": d["decision_id"], "decision_hash": d["decision_hash"], "objective": "PROFIT",
            "policy_version": d["policy_version"],
            "legs": [{"budget_id": x["budget_id"], "after": int(round(x["after"] + (100 if x is leg else 0)))}
                     for x in d["legs"]]}
    val = ok(api.post("/api/v1/optimizer/whatif", json=edit, headers=hdr(1)))
    assert val["estimate_status"] == "AVAILABLE" and val["estimate"] is not None and val["checks"]
    mod = ok(api.post(f"/api/v1/decisions/{d['decision_id']}/modify", json=edit, headers=hdr(2)))
    assert mod["follows"] == d["decision_id"] and mod["decision_id"] != d["decision_id"]
    assert ok(api.get(f"/api/v1/decisions/{d['decision_id']}"))["status"] == "SUPERSEDED"

    # stale hash -> 409; the right hash approves AND executes (mock platforms, read-back verified)
    r = api.post(f"/api/v1/decisions/{mod['decision_id']}/approve", json={"decision_hash": "0" * 64, "execute": True},
                 headers=hdr(3))
    assert r.status_code == 409
    done = ok(api.post(f"/api/v1/decisions/{mod['decision_id']}/approve",
                       json={"decision_hash": mod["decision_hash"], "execute": True}, headers=hdr(4)))
    assert done["status"] == "EXECUTED"
    ex = ok(api.get("/api/v1/executions"))
    assert ex[0]["state"] == "SUCCEEDED" and all(x["external_state"] == "VERIFIED" for x in ex[0]["legs"])
    assert all(x["read_back_budget"] == pytest.approx(x["after"], abs=1) for x in ex[0]["legs"])
    assert any(e["action"] == "VERIFY" for e in ok(api.get("/api/v1/ledger")))
    again = api.post(f"/api/v1/decisions/{mod['decision_id']}/approve",
                     json={"decision_hash": mod["decision_hash"], "execute": True}, headers=hdr(5))
    assert again.status_code == 409                                  # duplicate execution is impossible

    # advance until the outcome matures; each advance runs the pipeline for the new day
    outcome = None
    for k in range(14):
        ok(api.post("/api/v1/sim/advance?days=1", json={}, headers=hdr(10 + k)))
        outs = ok(api.get("/api/v1/outcomes"))
        if outs:
            outcome = outs[0]
            break
    assert outcome is not None and outcome["decision_id"] == mod["decision_id"]
    assert outcome["verdict"] in ("SUCCESS", "NEUTRAL", "FAILED", "INCONCLUSIVE")
    assert "forecast counterfactual" in outcome["method"]
    ov = ok(api.get("/api/v1/overview"))
    assert sum(ov["counts"].values()) == 1
    if outcome["calibration_applied"]:
        assert ov["calibration"] == pytest.approx(outcome["factor_after"])
    else:
        assert ov["calibration"] == pytest.approx(0.9)
    # learning reaches the next forecast: the following cycle refits the curves because an outcome matured
    ok(api.post("/api/v1/sim/advance?days=1", json={}, headers=hdr(40)))
    status = ok(api.get("/api/v1/pipeline/status"))
    assert status["state"] == "completed"
    events = ok(api.get("/api/v1/events"))
    assert any(e["kind"] == "outcome_matured" for e in events)
    assert any(e["kind"] == "action_executed" for e in events)

    # S3: stockout -> BLOCK SCALE / LIMIT on the exposed units + a deterministic safety candidate in review
    ok(api.post("/api/v1/sim/scenario/S3", json={}, headers=hdr(50)))
    ok(api.post("/api/v1/sim/advance?days=2", json={}, headers=hdr(51)))
    assert ok(api.get("/api/v1/overview"))["scenario"] == "S3"
    safety = [x for x in ok(api.get("/api/v1/decisions"))
              if x["class"] == "SAFETY" and x["status"] == "PENDING_APPROVAL"]
    assert safety, "S3 produced no safety candidate"
    s = safety[0]
    assert all(leg["after"] < leg["before"] for leg in s["legs"]) and "Safety" in s["title"]
    rej = ok(api.post(f"/api/v1/decisions/{s['decision_id']}/reject",
                      json={"decision_hash": s["decision_hash"], "reason": "reviewed: restock confirmed"},
                      headers=hdr(52)))
    assert rej["status"] == "REJECTED"
    assert api.post("/api/v1/sim/scenario/S6", json={}, headers=hdr(53)).status_code == 422   # not Stage 1

    # Scenario Lab reset: world and workspace back to the day-0 baseline
    ok(api.post("/api/v1/sim/reset?seed=42", json={}, headers=hdr(60)))
    ov = ok(api.get("/api/v1/overview"))
    assert ov["world_day"] == 0 and ov["scenario"] == "BASELINE" and sum(ov["counts"].values()) == 0
    assert ok(api.get("/api/v1/executions")) == []
