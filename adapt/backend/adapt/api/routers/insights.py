"""Endpoints of the insight, learning, model, policy, workspace and decision-detail screens (C6 completion).

Engine-backed: opportunities, curves, creative fatigue, calibration, accuracy, feedback, models (+ demand rollback),
policy, policy history, objective (GET/PUT, Stage 2 modes), decision timeline / replay / snapshot / archive /
simulate (+ sensitivity alternatives and choosing one), guarded narratives, the evaluation report and strategy
uplift (from evidence/eval.json), workspaces (the active one). Later-stage features answer with the contract's
NOT_AVAILABLE / NOT_ESTIMABLE state (shadow, qualification, creative score, Copilot in TEMPLATE mode) or 422
NOT_BUILT for mutations that would need them (policy-mode changes, new workspaces, CSV import, guarded SQL).
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
from adapt.api.routers.loop import _fail, _state_cache, actor, mutating, rt
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
    if r.brands.active():
        return []  # engine response curves; an uploaded-data workspace has its proposals instead
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
    if r.brands.active():
        return []  # uploads carry no creative-level data
    key = _run_key(r)
    cache = getattr(r, "_fatigue_cache", None)
    if not cache or cache[0] != key:
        r._fatigue_cache = (key, iv.creative_fatigue(r.db))
    return r._fatigue_cache[1]


@router.post("/creatives/score", response_model=m.CreativeScore, dependencies=mutating)
def creative_score(body: m.CreativeScoreBody, request: Request):
    """The structured creative prior (Stage 3): attributes named in the brief, scored only when the prior beat the
    category-mean baseline on its holdout; otherwise NOT_ESTIMABLE (never an invented score)."""
    from adapt.predict.creative_model import score_text

    return score_text(rt(request).db, body.text)


# ---- learning ------------------------------------------------------------------------------------------------------
@router.get("/learning/calibration", response_model=m.CalibrationOut)
def calibration(request: Request):
    r = rt(request)
    if r.brands.active():
        from adapt.api import brand_engine as be

        return be.calibration(r.db)
    return iv.calibration(r.db)


@router.get("/learning/accuracy", response_model=m.AccuracyOut)
def accuracy(request: Request):
    r = rt(request)
    if r.brands.active():
        from adapt.api import brand_engine as be

        return be.accuracy(r.db)
    return iv.accuracy(r.db)


@router.get("/learning/uplift", response_model=m.UpliftOut)
def uplift(request: Request):
    return iv.uplift(rt(request).settings.eval_report_path)


@router.get("/learning/feedback", response_model=list[m.FeedbackOut])
def feedback(request: Request):
    r = rt(request)
    if r.brands.active():
        from adapt.api import brand_engine as be

        return be.feedback(r.db)
    return iv.feedback(r.db)


@router.get("/learning/shadow", response_model=m.ShadowOut)
def shadow(request: Request):
    return iv.shadow(rt(request).db)


@router.get("/learning/qualification", response_model=m.QualificationOut)
def qualification(request: Request):
    r = rt(request)
    return iv.qualification(r.db, _world_id(r))


def _world_id(r) -> int:
    try:
        return int(r.world().get("seed"))
    except Exception:  # noqa: BLE001 - an unreachable world only means no local world id
        return -1


@router.get("/eval/report", response_model=m.EvalReportOut)
def eval_report(request: Request):
    return iv.eval_report(rt(request).settings.eval_report_path)


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
    raise HTTPException(422, detail="promotion is automatic under the layered rule (spec §10.2: baseline, champion "
                                    "non-inferiority, coverage) and is never a manual action; nothing was changed")


@router.post("/models/{name}/rollback", response_model=m.ModelActionOut, dependencies=mutating)
def model_rollback(name: str, body: m.ModelActionBody, request: Request):
    """Restore the previous champion (spec §10.2). Only the demand family keeps a rollback target: response curves
    are refitted on the newest data and every fit is its own champion."""
    from adapt.learn import governance

    r = rt(request)
    if name != "demand":
        raise HTTPException(422, detail=f"{name} has no rollback target; only the demand model keeps one")
    try:
        with r.mutation():
            champ = governance.champion(r.db, name)
            if champ is None or champ["version"] != body.version:
                raise HTTPException(409, detail="the champion changed; refresh the registry")
            if iv.registry_revision(champ) != body.registry_revision:
                raise HTTPException(409, detail="the registry changed since it was loaded; refresh it")
            if not champ.get("rollback_version"):
                raise HTTPException(409, detail="this champion has no previous champion to restore")
            out = governance.rollback(r.db, name, actor(request).user_id, r.now())
            restored = governance.champion(r.db, name)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return {"name": name, "version": out["champion"], "registry_revision": iv.registry_revision(restored),
            "message": f"{out['retired']} retired; {out['champion']} is the champion again (reason: "
                       f"{body.reason})"}


# ---- policy and objective --------------------------------------------------------------------------------------------
@router.get("/policy", response_model=m.PolicyOut)
def policy(request: Request):
    r = rt(request)
    return iv.policy(r.db, current_policy(r.db), r.adapters, _world_id(r))


@router.put("/policy", response_model=m.PolicyOut, dependencies=mutating)
def policy_change(body: m.PolicyModeBody, request: Request):
    """Change one channel's autonomy mode (admin, spec §9.2). SIMULATION_AUTONOMOUS only with simulation readiness
    (T38); PRODUCTION_AUTONOMOUS never (no real outcomes exist). The mode map is an EXACT staleness input, so every
    pending decision expires at approval and the next run decides under the new mode."""
    from adapt.policy.autonomy import set_mode
    from adapt.policy.modes import channel_modes

    r = rt(request)
    channel = {lab: ch for ch, lab in iv.LABEL.items()}.get(body.channel)
    if channel is None:
        raise HTTPException(404, detail=f"unknown channel {body.channel}")
    if body.mode == "PRODUCTION_AUTONOMOUS":
        raise HTTPException(422, detail="PRODUCTION_AUTONOMOUS requires measured REAL outcomes; none exist")
    mode = {"SIMULATION_AUTONOMOUS": "AUTONOMOUS"}.get(body.mode, body.mode)
    try:
        with r.mutation():
            pol = current_policy(r.db)
            if iv.policy_revision(pol, channel_modes(r.db)) != body.revision:
                raise HTTPException(409, detail="the policy changed since it was loaded; refresh it")
            u = actor(request)
            set_mode(r.db, channel, mode, u.user_id, u.role, r.now(), r.adapters, _world_id(r), body.reason)
            r._state_cache = None
    except HTTPException:
        raise
    except PermissionError as exc:
        raise HTTPException(403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return iv.policy(r.db, current_policy(r.db), r.adapters, _world_id(r))


@router.get("/policy/history", response_model=m.PolicyHistory)
def policy_history(request: Request):
    r = rt(request)
    current_policy(r.db)
    return iv.policy_history(r.db)


@router.get("/objective", response_model=m.WorkspaceObjective)
def objective(request: Request):
    r = rt(request)
    return iv.objective(r.db, r.settings.workspace)


@router.put("/objective", response_model=m.WorkspaceObjective, dependencies=mutating)
def objective_change(body: m.ObjectiveChangeBody, request: Request):
    """Admin setting (spec §8.2, §9.5): the next pipeline run decides under the new objective; pending decisions
    expire at approval, because the objective is an EXACT staleness input."""
    from adapt.decide.alternatives import MODES, set_objective

    r = rt(request)
    if body.workspace_id != r.settings.workspace:
        raise HTTPException(404, detail=f"workspace {body.workspace_id} does not exist")
    if body.objective not in MODES:
        raise HTTPException(422, detail=f"NOT_BUILT: the {body.objective} objective; built: {', '.join(MODES)}")
    try:
        with r.mutation():
            if iv.objective(r.db, r.settings.workspace)["revision"] != body.revision:
                raise HTTPException(409, detail="the objective changed since it was loaded; refresh it")
            u = actor(request)
            set_objective(r.db, body.objective, u.user_id, u.role, r.now())
            r._state_cache = None
    except HTTPException:
        raise
    except PermissionError as exc:
        raise HTTPException(403, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return iv.objective(r.db, r.settings.workspace)


# ---- decision detail -------------------------------------------------------------------------------------------------
def _engine_only(request: Request) -> None:
    """Timeline, snapshot, replay, simulation, alternatives and narratives come from the forecasting engine, which an
    uploaded-data workspace does not run: a clear 404 instead of a missing-table error."""
    if rt(request).brands.active():
        raise HTTPException(404, detail="not available in an uploaded-data workspace: its proposals are rule-based "
                                        "(see the decision's checks and evidence); the simulated world runs the engine")


@router.get("/decisions/{decision_id}/timeline", response_model=list[m.TimelineEntry])
def timeline(decision_id: str, request: Request):
    _engine_only(request)
    r = rt(request)
    _decision_exists(r.db, decision_id)
    return iv.timeline(r.db, decision_id)


@router.get("/decisions/{decision_id}/snapshot", response_model=m.SnapshotOut)
def snapshot(decision_id: str, request: Request):
    _engine_only(request)
    r = rt(request)
    _decision_exists(r.db, decision_id)
    return iv.snapshot(r.db, decision_id)


@router.get("/decisions/{decision_id}/archive", response_model=m.ArchiveOut)
def archive(decision_id: str, request: Request):
    _engine_only(request)
    r = rt(request)
    _decision_exists(r.db, decision_id)
    return iv.archive(r.db, decision_id)


@router.post("/decisions/{decision_id}/simulate", response_model=m.Comparison, dependencies=mutating)
def simulate(decision_id: str, body: m.SimulateBody, request: Request):
    _engine_only(request)
    r = rt(request)
    _decision_exists(r.db, decision_id)
    if dec.get_decision(r.db, decision_id)["decision_hash"] != body.decision_hash:
        raise HTTPException(409, detail="the decision changed; reload it before comparing")
    return iv.comparison(r.db, decision_id, _snapshot_state(r.db, decision_id))


@router.post("/decisions/{decision_id}/alternatives/{name}/choose", response_model=m.Decision,
             dependencies=mutating)
def choose_alternative(decision_id: str, name: str, body: m.ChooseAlternativeBody, request: Request):
    _engine_only(request)
    """Choose a sensitivity scenario instead (spec §8.3): a NEW decision with its own snapshot and hash, superseding
    the recommended one, under that explicit risk preference; it then needs its own approval."""
    from adapt.decide.alternatives import choose_alternative as choose

    r = rt(request)
    _decision_exists(r.db, decision_id)
    try:
        with r.mutation():
            if dec.get_decision(r.db, decision_id)["decision_hash"] != body.decision_hash:
                raise dec.DecisionError("HASH_MISMATCH", "the decision changed; reload it before choosing")
            u = actor(request)
            new_id = choose(r.db, decision_id, name, u.user_id, u.role, r.now())
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return v.decision_view(r.db, new_id)


def _narrative(r, kind: str, ref_id: str, make) -> dict:
    """The narrative the pipeline stored for this package; generated once on demand for older items."""
    nar = v.latest_narrative(r.db, kind, ref_id)
    if nar is None:
        nar = make(r.db, ref_id, r.llm, r.now())
    return nar


@router.get("/decisions/{decision_id}/narrative", response_model=m.NarrativeOut)
def decision_narrative(decision_id: str, request: Request):
    _engine_only(request)
    from adapt.agent.narrator import narrate_decision

    r = rt(request)
    _decision_exists(r.db, decision_id)
    return _narrative(r, "decision", decision_id, narrate_decision)


@router.get("/anomalies/{anomaly_id}/narrative", response_model=m.NarrativeOut)
def anomaly_narrative(anomaly_id: str, request: Request):
    _engine_only(request)
    from adapt.agent.narrator import narrate_incident

    r = rt(request)
    if v.anomaly_view(r.db, anomaly_id) is None:
        raise HTTPException(404, detail=f"anomaly {anomaly_id} not found")
    return _narrative(r, "incident", anomaly_id, narrate_incident)


@router.get("/decisions/{decision_id}/replay", response_model=m.ReplayOut)
def replay(decision_id: str, request: Request):
    _engine_only(request)
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
def _registry(r):
    from adapt.ingest.csv_upload import Registry

    return Registry(r.settings.workspace_db_path.parent)


def _workspaces(r) -> dict:
    """The simulated-world engine workspace first, then the brand workspaces (empty until their uploads)."""
    ws = r.settings.workspace
    try:
        seed = r.world().get("seed", "?")
    except Exception:  # noqa: BLE001 - the list must render while the world service is down
        seed = "?"
    brand = r.brands.active()
    return {"active_id": brand["id"] if brand else ws,
            "items": ([{"id": ws, "name": f"Simulated world (seed {seed})", "currency": "INR",
                        "timezone": "Asia/Kolkata"}]
                      + [{"id": b["id"], "name": b["name"], "currency": "INR", "timezone": "Asia/Kolkata"}
                         for b in r.brands.items()])[:20]}


@router.get("/workspaces", response_model=m.WorkspaceList)
def workspaces(request: Request):
    return _workspaces(rt(request))


@router.post("/workspaces", response_model=m.Workspace, dependencies=mutating)
def workspace_create(body: m.WorkspaceCreate, request: Request):
    """A new, empty brand workspace: every number is 0 until CSVs are uploaded into it."""
    from adapt.api.brands import BrandError

    try:
        b = rt(request).brands.create(body.name)
    except BrandError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    return {"id": b["id"], "name": b["name"], "currency": "INR", "timezone": "Asia/Kolkata"}


@router.post("/workspaces/{workspace_id}/activate", response_model=m.WorkspaceList, dependencies=mutating)
def workspace_activate(workspace_id: str, request: Request):
    r = rt(request)
    ids = {w["id"] for w in _workspaces(r)["items"]}
    if workspace_id not in ids:
        raise HTTPException(404, detail=f"workspace {workspace_id} does not exist")
    r.brands.activate(None if workspace_id == r.settings.workspace else workspace_id)
    r._state_cache = None
    if r.brands.active():  # re-check the uploads on open, so proposals follow the current rules
        from adapt.api import brand_engine as be

        be.cycle(r.db)
    return _workspaces(r)


@router.get("/ingest/templates/{kind}")
def ingest_template(kind: str) -> dict:
    from adapt.ingest.csv_upload import UploadError, template

    try:
        return template(kind)
    except UploadError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.post("/ingest/mapping/suggest", dependencies=mutating)
def ingest_suggest(body: m.MappingSuggestBody) -> dict:
    """The rapidfuzz + synonym field mapper: the best CSV column per required field, with its score."""
    from adapt.ingest.csv_upload import UploadError, suggest_mapping

    try:
        return {"type": body.type, "mapping": suggest_mapping(body.type, body.headers)}
    except UploadError as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@router.post("/ingest/upload", response_model=m.ImportAck, dependencies=mutating)
def ingest_upload(body: m.UploadBody, request: Request):
    """Server-side data contract, then stage (idempotent per file content)."""
    from adapt.ingest.csv_upload import UploadError, stage

    r = rt(request)
    brand = r.brands.active()
    try:
        return stage(_registry(r), body.type, body.records, body.source_currency, body.source_timezone,
                     target=brand["id"] if brand else None)
    except UploadError as exc:
        raise HTTPException(422, detail=f"{exc}: " + "; ".join(exc.errors[:10])) from exc


@router.post("/ingest/mapping/confirm", response_model=m.ImportAck, dependencies=mutating)
def ingest_confirm(body: m.ConfirmBody, request: Request):
    from adapt.api.brands import import_rows
    from adapt.ingest.csv_upload import UploadError, confirm

    r = rt(request)
    brand = r.brands.active()

    def sink(kind, rows, import_id, who):
        up = (_registry(r).load()["uploads"].get(import_id) or {})
        if not brand or up.get("target") != brand["id"]:
            raise UploadError("this file was staged for another workspace; switch back to it to confirm")
        from adapt.api import brand_engine as be

        bdb = r.brands.db(brand["id"])
        res = import_rows(bdb, kind, rows, import_id, who)
        run = be.cycle(bdb)
        found = [f"{len(run['decisions'])} proposal(s)" if run["decisions"] else "",
                 f"{run['anomalies']} anomaly signal(s)" if run["anomalies"] else "",
                 f"{run['outcomes']} outcome(s) measured" if run["outcomes"] else ""]
        found = [f for f in found if f]
        return (f"imported {res['rows']} {kind} rows into {brand['name']}"
                + (f" ({res['replaced']} earlier rows with the same key replaced)" if res["replaced"] else "")
                + "; the numbers now include them" + (f". Checked: {', '.join(found)}" if found else ""))

    try:
        return confirm(_registry(r), body.import_id, body.mapping, actor(request).user_id, sink=sink)
    except UploadError as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@router.post("/copilot/sql", response_model=m.SqlResult, dependencies=mutating)
def copilot_sql(body: m.SqlBody, request: Request):
    """Guarded read-only SQL (spec §9.5): one SELECT over allowlisted marts, on a separate read-only copy."""
    from adapt.agent import sql

    try:
        return sql.run(rt(request).db.path, body.query, body.limit)
    except sql.SqlRejected as exc:
        raise HTTPException(422, detail=f"SQL_REJECTED: {exc}") from exc


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
