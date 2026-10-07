"""C6 completion: every endpoint the frontend's insight / learning / model / policy / workspace / decision-detail
screens call, on a bootstrapped fixture workspace through the HTTP API. Engine-backed endpoints return real data;
later-stage features return the contract's NOT_AVAILABLE / NOT_ESTIMABLE state or 422 NOT_BUILT."""

import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adapt.api.main import create_app
from adapt.api.runtime import bootstrap
from adapt.config.settings import Settings

SAMPLES = os.environ.get("ADAPT_CONTRACT_SAMPLES")


def hdr(n) -> dict:
    return {"X-Request-ID": f"s-{n}", "Idempotency-Key": f"s-{n}"}


@pytest.fixture
def api(world, tmp_path):
    settings = Settings(data_dir=tmp_path / "data", workspace="screens",
                        eval_report_path=tmp_path / "no-eval.json")
    bootstrap(settings, world_client=world)
    with TestClient(create_app(settings, world_client=world, sync_jobs=True)) as c:
        yield c


def get(api, path, code=200, name=None):
    r = api.get(f"/api/v1{path}")
    assert r.status_code == code, f"{path}: {r.text[:500]}"
    body = r.json()
    if SAMPLES and code == 200:
        Path(SAMPLES).mkdir(parents=True, exist_ok=True)
        (Path(SAMPLES) / f"{name or path.strip('/').replace('/', '__')}.json").write_text(json.dumps(body))
    return body


def test_screen_endpoints(api):
    d = next(x for x in get(api, "/decisions") if x["class"] == "OPTIMIZATION")
    did = d["decision_id"]
    assert ":" not in did                                                   # ids satisfy ^[a-zA-Z0-9_-]{1,80}$

    opp = get(api, "/opportunities")
    assert opp and {o["status"] for o in opp} <= {"FEASIBLE", "BLOCKED", "NOT_ESTIMABLE"}
    assert all((o["score"] is None) == (o["status"] != "FEASIBLE") for o in opp)
    unit = next(o["budget_id"] for o in opp)
    cv = get(api, f"/curves/{unit}", name="curve")
    assert cv["budget_id"] == unit and cv["reason"]
    get(api, "/curves/nope", code=404)
    fat = get(api, "/creatives/fatigue")
    assert all(f["status"] in ("REVIEW", "STABLE") and f["frequency"] >= 0 for f in fat)

    cal = get(api, "/learning/calibration")
    assert cal["factor"] == pytest.approx(0.9) and cal["updates"] == []
    assert get(api, "/learning/accuracy")["sample_count"] == 0
    assert get(api, "/learning/uplift")["status"] == "NOT_AVAILABLE"
    assert get(api, "/learning/feedback") == []
    assert get(api, "/learning/shadow")["status"] == "NOT_AVAILABLE"
    assert get(api, "/learning/qualification")["status"] == "NOT_AVAILABLE"
    assert get(api, "/eval/report")["report"] is None

    models = get(api, "/models")
    champ = next(mo for mo in models if mo["name"] == "response_curve")
    detail = get(api, f"/models/response_curve/{champ['version']}", name="model_detail")
    assert detail["role"] == "CHAMPION" and len(detail["artifact_hash"]) == 64 and detail["allowed_actions"] == []
    assert api.post("/api/v1/models/response_curve/promote", json={}, headers=hdr(1)).status_code == 422

    pol = get(api, "/policy")
    assert {c["channel"] for c in pol["channels"]} == {"Meta", "Google"}
    assert all(c["mode"] == "APPROVE" and not c["simulation"]["eligible"] for c in pol["channels"])
    hist = get(api, "/policy/history")
    assert hist["status"] == "AVAILABLE" and hist["versions"][0]["version"] == pol["policy_version"]
    obj = get(api, "/objective")
    assert obj["objective"] == "PROFIT" and obj["can_change"] and "GROWTH" in obj["supported_objectives"]
    assert api.put("/api/v1/objective", json={}, headers=hdr(2)).status_code == 422

    tl = get(api, f"/decisions/{did}/timeline", name="timeline")
    assert tl[0]["label"] == "Decision snapshot" and all(e["href"].startswith("/") for e in tl)
    snap = get(api, f"/decisions/{did}/snapshot", name="snapshot")
    assert snap["decision"]["decision_id"] == did
    arc = get(api, f"/decisions/{did}/archive", name="archive")
    assert arc["decision_hash"] == d["decision_hash"] and all(a["status"] == "PRESENT" for a in arc["artifacts"])
    comp = api.post(f"/api/v1/decisions/{did}/simulate", json={"decision_hash": d["decision_hash"]}, headers=hdr(3))
    assert comp.status_code == 200, comp.text
    comp = comp.json()
    assert comp["strategies"][1]["estimate"]["raw_pred"] == pytest.approx(d["expected"]["raw_pred"], rel=1e-6, abs=1)
    if SAMPLES:
        (Path(SAMPLES) / "simulate.json").write_text(json.dumps(comp))
    for _ in range(60):                                                      # replay runs in a background thread
        rep = get(api, f"/decisions/{did}/replay", name="replay")
        if rep["status"] != "UNAVAILABLE":
            break
        time.sleep(1)
    assert rep["status"] == "VERIFIED" and rep["actual_hash"] == d["decision_hash"]

    ws = get(api, "/workspaces")
    assert ws["active_id"] == "screens"
    act = api.post("/api/v1/workspaces/screens/activate", json={}, headers=hdr(4))
    assert act.status_code == 200 and act.json()["active_id"] == "screens"
    assert api.post("/api/v1/workspaces/other/activate", json={}, headers=hdr(5)).status_code == 404
    for path in ("/workspaces", "/ingest/upload", "/ingest/mapping/confirm", "/copilot/sql"):
        assert api.post(f"/api/v1{path}", json={}, headers=hdr(path)).status_code == 422, path
    score = api.post("/api/v1/creatives/score", json={"text": "bold hook"}, headers=hdr(6)).json()
    assert score["status"] == "NOT_ESTIMABLE" and score["score"] is None

    chat = api.post("/api/v1/copilot/chat", json={"message": "Why should I approve this?"}, headers=hdr(7))
    assert chat.status_code == 200 and chat.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[5:]) for line in chat.text.split("\n") if line.startswith("data:")]
    assert events[-1] == {"type": "done"} and events[0]["type"] == "answer"
    assert events[0]["reply"]["mode"] == "TEMPLATE" and all(e["href"].startswith("/")
                                                           for e in events[0]["reply"]["evidence"])
    if SAMPLES:
        (Path(SAMPLES) / "copilot_reply.json").write_text(json.dumps(events[0]["reply"]))
