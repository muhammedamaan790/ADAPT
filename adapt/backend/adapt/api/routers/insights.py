"""Endpoints of the insight, learning, model, policy, workspace and decision-detail screens (C6 completion).

Engine-backed: opportunities, curves, creative fatigue, calibration, accuracy, feedback, models, policy, policy
history, objective, decision timeline / replay / snapshot / archive / simulate, workspaces (the active one).
Later-stage features answer with the contract's NOT_AVAILABLE / NOT_ESTIMABLE state (uplift, shadow,
qualification, eval report, creative score, Copilot in TEMPLATE mode) or 422 NOT_BUILT for mutations that would
need them (objective / policy-mode changes, model promotion, new workspaces, CSV import, guarded SQL).
"""

from __future__ import annotations

import json
import threading

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from adapt.api import agent
from adapt.api import insight_views as iv
from adapt.api import models as m
from adapt.api import views as v
from adapt.api.routers.loop import _state_cache, mutating, rt
from adapt.decide import decisions as dec
from adapt.decide.snapshot import get_artifact, state_from_dict
from adapt.policy.engine import current_policy

router = APIRouter(prefix="/api/v1", tags=["insights"])


def not_built(feature: str, stage: str = "a later stage"):
    raise HTTPException(422, detail=f"NOT_BUILT: {feature} is {stage}; nothing was changed")


def _decision_exists(db, decision_id: str) -> None:
    if not v.has(db, "intel", "decisions") or not db.query("SELECT 1 FROM intel.decisions WHERE decision_id = ?",
                                                             [decision_id]):
        raise HTTPException(404, detail=f"decision {decision_id} not found")


def _snapshot_state(db, decision_id: str):
    manifest = json.loads(db.query("SELECT manifest FROM ops.decision_snapshots WHERE decision_id = ?",
                                   [decision_id])[0][0])
    return state_from_dict(get_artifact(db, manifest["state"]))


def _run_key(r) -> str | None:
    run = v.latest_run(r.db)
    return run[0] if run else None


# ---- opportunities, curves, creatives -------------------------------------------------------------------------------
@router.get("/opportunities", response_model=list[m.Opportunity])
def opportunities(request: Request):
    r = rt(request)
    state, flags, _opt, _as_of = _state_cache(r)
    return iv.opportunities(r.db, state, flags)


@router.get("/curves/{budget_id}", response_model=m.Curve)
def curve(budget_id: str, request: Request):
    r = rt(request)
    state, _flags, _opt, _as_of = _state_cache(r)
    out = iv.curve(r.db, state, budget_id)
    if out is None:
        raise HTTPException(404, detail=f"budget {budget_id} not found")
    return out


@router.get("/creatives/fatigue", response_model=list[m.Fatigue])
def creative_fatigue(request: Request):
    r = rt(request)
    key = _run_key(r)
    cache = getattr(r, "_fatigue_cache", None)
    if not cache or cache[0] != key:
        r._fatigue_cache = (key, iv.creative_fatigue(r.db))
    return r._fatigue_cache[1]


@router.post("/creatives/score", response_model=m.CreativeScore, dependencies=mutating)
def creative_score():
    return {"status": "NOT_ESTIMABLE", "score": None,
            "explanation": "The structured creative prior (LightGBM on format, hook, CTA, category, channel and price "
                           "band) is a Stage 3 model; no score is invented."}


# ---- learning ------------------------------------------------------------------------------------------------------
@router.get("/learning/calibration", response_model=m.CalibrationOut)
def calibration(request: Request):
    return iv.calibration(rt(request).db)


@router.get("/learning/accuracy", response_model=m.AccuracyOut)
def accuracy(request: Request):
    return iv.accuracy(rt(request).db)


@router.get("/learning/uplift", response_model=m.UpliftOut)
def uplift():
    return {"status": "NOT_AVAILABLE", "rows": [],
            "note": "Strategy uplift comes from the held-out evaluation (seeds 101–120, paired strategies), which is a "
                    "Stage 2 deliverable. No uplift is shown until that report exists."}


@router.get("/learning/feedback", response_model=list[m.FeedbackOut])
def feedback(request: Request):
    return iv.feedback(rt(request).db)


@router.get("/learning/shadow", response_model=m.ShadowOut)
def shadow():
    return {"status": "NOT_AVAILABLE", "records": [],
            "note": "Observe-mode shadow decisions are not recorded in Stage 1 (Approve mode only)."}


@router.get("/learning/qualification", response_model=m.QualificationOut)
def qualification():
    return {"status": "NOT_AVAILABLE", "pools": [],
            "note": "Confidence-region qualification (Wilson bounds over warm-up worlds 901–903 + held-out world 904) "
                    "is a later-stage feature; no region is qualified."}


@router.get("/eval/report", response_model=m.EvalReportOut)
def eval_report():
    return {"status": "NOT_AVAILABLE", "report": None,
            "note": "No precomputed evaluation report exists yet (Stage 2: held-out seeds, paired baselines, oracle)."}


# ---- models --------------------------------------------------------------------------------------------------------
@router.get("/models", response_model=list[m.ModelOut])
def models(request: Request):
    return iv.models(rt(request).db)


@router.get("/models/{name}/{version}", response_model=m.ModelDetail)
def model_detail(name: str, version: str, request: Request):
    out = iv.model_detail(rt(request).db, name, version)
    if out is None:
        raise HTTPException(404, detail=f"model {name} {version} not found")
    return out


@router.post("/models/{name}/promote", dependencies=mutating)
def model_promote(name: str):
    not_built("champion/challenger promotion", "Stage 2")


@router.post("/models/{name}/rollback", dependencies=mutating)
def model_rollback(name: str):
    not_built("model rollback (champion/challenger)", "Stage 2")


# ---- policy and objective --------------------------------------------------------------------------------------------
@router.get("/policy", response_model=m.PolicyOut)
def policy(request: Request):
    r = rt(request)
    return iv.policy(r.db, current_policy(r.db))


@router.put("/policy", response_model=m.PolicyOut, dependencies=mutating)
def policy_change():
    not_built("changing a channel's execution mode (Stage 1 is Approve mode only)")


@router.get("/policy/history", response_model=m.PolicyHistory)
def policy_history(request: Request):
    r = rt(request)
    current_policy(r.db)
    return iv.policy_history(r.db)


@router.get("/objective", response_model=m.WorkspaceObjective)
def objective(request: Request):
    return iv.objective(rt(request).settings.workspace)


@router.put("/objective", response_model=m.WorkspaceObjective, dependencies=mutating)
def objective_change():
    not_built("changing the objective (Stage 1 supports PROFIT only)")


# ---- decision detail -------------------------------------------------------------------------------------------------
@router.get("/decisions/{decision_id}/timeline", response_model=list[m.TimelineEntry])
def timeline(decision_id: str, request: Request):
    r = rt(request)
    _decision_exists(r.db, decision_id)
    return iv.timeline(r.db, decision_id)


@router.get("/decisions/{decision_id}/snapshot", response_model=m.SnapshotOut)
def snapshot(decision_id: str, request: Request):
    r = rt(request)
    _decision_exists(r.db, decision_id)
    return iv.snapshot(r.db, decision_id)


@router.get("/decisions/{decision_id}/archive", response_model=m.ArchiveOut)
def archive(decision_id: str, request: Request):
    r = rt(request)
    _decision_exists(r.db, decision_id)
    return iv.archive(r.db, decision_id)


@router.post("/decisions/{decision_id}/simulate", response_model=m.Comparison, dependencies=mutating)
def simulate(decision_id: str, body: m.SimulateBody, request: Request):
    r = rt(request)
    _decision_exists(r.db, decision_id)
    if dec.get_decision(r.db, decision_id)["decision_hash"] != body.decision_hash:
        raise HTTPException(409, detail="the decision changed; reload it before comparing")
    return iv.comparison(r.db, decision_id, _snapshot_state(r.db, decision_id))


@router.get("/decisions/{decision_id}/replay", response_model=m.ReplayOut)
def replay(decision_id: str, request: Request):
    """Replay re-runs economics + optimizer + policy from the snapshot (seconds to tens of seconds), so it runs in a
    background thread and is cached per decision; until it finishes the answer is UNAVAILABLE (in progress)."""
    r = rt(request)
    _decision_exists(r.db, decision_id)
    expected = dec.get_decision(r.db, decision_id)["decision_hash"]
    cache = r.__dict__.setdefault("_replays", {})
    entry = cache.get(decision_id)
    if entry is None:
        holder: dict = {}

        def work():
            try:
                holder["result"] = dec.replay(r.db, decision_id)
            except Exception as exc:  # reported, never swallowed
                holder["error"] = repr(exc)

        t = threading.Thread(target=work, name=f"replay-{decision_id}", daemon=True)
        entry = cache[decision_id] = {"thread": t, "holder": holder}
        t.start()
    entry["thread"].join(10)
    if entry["thread"].is_alive():
        return {"status": "UNAVAILABLE", "expected_hash": expected, "actual_hash": None,
                "message": "Replay is running from the snapshot artifacts; refresh in a few seconds."}
    if "error" in entry["holder"]:
        cache.pop(decision_id, None)
        return {"status": "UNAVAILABLE", "expected_hash": expected, "actual_hash": None,
                "message": f"Replay could not run: {entry['holder']['error'][:300]}"}
    res = entry["holder"]["result"]
    if res.get("replay_hash") is None:
        return {"status": "UNAVAILABLE", "expected_hash": expected, "actual_hash": None,
                "message": f"Replay unavailable: {res.get('reason')}"}
    return {"status": "VERIFIED" if res["match"] else "MISMATCH", "expected_hash": expected,
            "actual_hash": res["replay_hash"],
            "message": ("Re-running economics, optimizer and policy from the snapshot reproduced the decision hash. "
                        if res["match"] else "The replay produced a different hash; investigate before trusting it. ")
            + f"Environment: {res.get('environment')}."}


# ---- workspaces, import, Copilot, SQL --------------------------------------------------------------------------------
def _workspaces(r) -> dict:
    ws = r.settings.workspace
    return {"active_id": ws, "items": [{"id": ws, "name": f"{ws} (seed-{r.world().get('seed', '?')} world)",
                                        "currency": "INR", "timezone": "Asia/Kolkata"}]}


@router.get("/workspaces", response_model=m.WorkspaceList)
def workspaces(request: Request):
    return _workspaces(rt(request))


@router.post("/workspaces", response_model=m.Workspace, dependencies=mutating)
def workspace_create():
    not_built("creating workspaces (one workspace per CSV upload)", "Stage 3")


@router.post("/workspaces/{workspace_id}/activate", response_model=m.WorkspaceList, dependencies=mutating)
def workspace_activate(workspace_id: str, request: Request):
    r = rt(request)
    if workspace_id != r.settings.workspace:
        raise HTTPException(404, detail=f"workspace {workspace_id} does not exist; Stage 1 serves one workspace")
    return _workspaces(r)


@router.post("/ingest/upload", dependencies=mutating)
def ingest_upload():
    not_built("the CSV upload wizard", "Stage 3")


@router.post("/ingest/mapping/confirm", dependencies=mutating)
def ingest_confirm():
    not_built("the CSV upload wizard", "Stage 3")


@router.post("/copilot/sql", dependencies=mutating)
def copilot_sql():
    not_built("guarded read-only SQL over a separate marts copy (sqlglot allowlist)", "Stage 3")


@router.post("/copilot/chat", dependencies=mutating)
def copilot_chat(body: m.CopilotBody, request: Request):
    """Ask ADAPT (api/agent.py): Groq tool-calling over the workspace's read-only views, every figure checked against
    the tool results; the deterministic template answer when the LLM is offline. Streamed as SSE: `status` events
    while tools run, then `answer` and `done`."""
    history = [t.model_dump() for t in body.history]

    def stream():
        try:
            for event in agent.answer(request, body.message, history):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as exc:  # surfaced to the browser as the contract's error event, never a broken stream
            yield f"data: {json.dumps({'type': 'error', 'message': f'The assistant failed: {exc}'})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
