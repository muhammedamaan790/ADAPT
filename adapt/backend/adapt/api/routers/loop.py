"""Stage 1 decision-loop API (C6, spec §12): Command Center, Decision Center, Scenario Lab, plus the workbench reads
the frontend probes (anomalies, optimizer context, executions, ledger). Paths and payloads follow the frontend's zod
contracts; FastAPI validates every response against api/models.py before it is sent.

Errors: 404 unknown id; 409 stale hash / expired / conflict / busy pipeline / unresolved execution; 403 role;
422 NOT_BUILT for Stage 2 features (other objectives, injected UNKNOWN faults, retry); 503 world unreachable.
Stage 1 actor: one demo manager (session auth with roles is the follow-up agreed with the frontend).
"""

from __future__ import annotations

import hashlib
import json
import math

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from adapt.api import models as m
from adapt.api import views as v
from adapt.api.runtime import Busy, Runtime
from adapt.decide import decisions as dec
from adapt.decide.optimizer import build_constraints, search_draws
from adapt.decide.run import persist_basis, policy_flags, run_optimizer
from adapt.economics.portfolio import Portfolio
from adapt.economics.state import guardrails_config, load_state, objectives_config
from adapt.execute import saga
from adapt.ingest.http import ConnectorError
from adapt.policy.engine import current_policy, policy_result, validate


def mutation_headers(x_request_id: str = Header(..., alias="X-Request-ID"),
                     idempotency_key: str = Header(..., alias="Idempotency-Key")) -> None:
    """Every mutating request carries X-Request-ID and Idempotency-Key (spec §12). Semantic idempotency is enforced
    by the engine itself: approval is bound to the decision hash and a decision has at most one execution saga."""


router = APIRouter(prefix="/api/v1", tags=["loop"])
mutating = [Depends(mutation_headers)]
ACTOR, ROLE = "demo-manager", "manager"
STAGE1_SCENARIOS = {"DEMO_01", "S1", "S2", "S3", "S4", "S5", "S7"}
# spec §14; Stage 1 supports S1-S5, S7, DEMO_01 (the world implements them and every module they need exists)
SCENARIO_CATALOG = [
    ("DEMO_01", "Golden demo", "Creative fatigue on a Meta category with low hero-SKU cover, while a Google category "
     "has rising demand and deep stock.", "Demo", []),
    ("S1", "Auction pressure", "Meta CPM +45% platform-wide for 6 days.", "Detect + diagnose", []),
    ("S2", "Creative fatigue", "One creative's CTR falls 40% over 10 days.", "Detect + diagnose", []),
    ("S3", "Hero SKU stockout", "A hero SKU stocks out while its Google campaign keeps spending.",
     "Detect + diagnose", []),
    ("S4", "Price change", "+18% price on a category.", "Detect + diagnose", []),
    ("S5", "Tracking break", "Sessions −60% while clicks stay flat.", "Detect + diagnose", []),
    ("S6", "Demand surge", "Category demand surge (positive anomaly).", "Detect + diagnose",
     ["demand evidence module", "world scenario S6"]),
    ("S7", "Human budget cut", "A manager cuts a budget; must not open an efficiency incident.", "Classification", []),
    ("S8", "Audience saturation", "Retargeting frequency 2→5 with flat reach.", "Detect + diagnose",
     ["saturation evidence module", "world scenario S8"]),
    ("S9", "ROAS trap", "A high-ROAS, low-margin, low-stock SKU against a high-margin, deep-stock SKU.",
     "Decision quality", ["world scenario S9", "opportunity ranking"]),
    ("S10", "Seasonal pattern", "Weekly or holiday pattern; must be labelled seasonal_expected.", "Classification",
     ["seasonal classifier", "world scenario S10"]),
    ("S11", "Excess inventory", "Clearance objective shifts spend toward EXCESS SKUs.", "Decision quality",
     ["INVENTORY_CLEARANCE objective", "world scenario S11"]),
    ("S12", "Fatigue + demand", "Two drivers on one campaign, ranked by simulated effect.", "Detect + diagnose",
     ["demand evidence module", "world scenario S12"]),
]
ERR = {"NOT_FOUND": 404, "FORBIDDEN": 403, "HASH_MISMATCH": 409, "EXPIRED": 409, "CONFLICT": 409,
       "ROLLBACK_CONFLICT": 409, "INVALID": 422}


def rt(request: Request) -> Runtime:
    return request.app.state.runtime


def _fail(exc: Exception):
    if isinstance(exc, dec.DecisionError):
        detail = str(exc)
        if exc.details.get("diff"):
            detail += " | changed: " + ", ".join(f"{d['class']}:{d['field']}" for d in exc.details["diff"][:6])
        raise HTTPException(ERR.get(exc.code, 409), detail=detail) from exc
    if isinstance(exc, Busy):
        raise HTTPException(409, detail=str(exc)) from exc
    if isinstance(exc, ConnectorError):
        raise HTTPException(503, detail=f"world service unreachable: {exc}") from exc
    raise exc


def _decision_or_404(db, decision_id: str) -> dict:
    try:
        return v.decision_view(db, decision_id)
    except dec.DecisionError as exc:
        _fail(exc)


def _scenario(db) -> str:
    if not v.has(db, "ops", "sim_log"):
        return "BASELINE"
    row = db.query("SELECT detail FROM ops.sim_log WHERE action = 'scenario' ORDER BY logged_at DESC LIMIT 1")
    return row[0][0] if row else "BASELINE"


def _log(db, action: str, detail: str, at) -> None:
    def work(cur):
        cur.execute("CREATE SCHEMA IF NOT EXISTS ops; CREATE TABLE IF NOT EXISTS ops.sim_log "
                    "(action VARCHAR, detail VARCHAR, logged_at TIMESTAMP)")
        cur.execute("INSERT INTO ops.sim_log VALUES (?, ?, ?)", [action, detail, at])
    db.write(work)


def _allocation(state, body: m.AllocationInput) -> dict[str, float]:
    """The edited allocation. Budgets are entered in whole rupees, so an edit within one rupee of a fractional
    current budget (e.g. a Meta budget converted from USD cents) means "unchanged", not a sub-rupee leg."""
    current = {u.unit_id: u.budget for u in state.units}
    out = dict(current)
    for leg in body.legs:
        if leg.budget_id in current and abs(float(leg.after) - current[leg.budget_id]) >= 1.0:
            out[leg.budget_id] = float(leg.after)
    return out


def _state_cache(r: Runtime) -> tuple:
    """(state, flags, run_id, as_of) of the latest completed pipeline run, cached per optimizer run."""
    run = v.latest_run(r.db)
    if run is None:
        raise HTTPException(503, detail="no completed pipeline run yet (bootstrap or advance the world)")
    opt_id = v._run_opt_id(r.db, run[0])
    cache = getattr(r, "_state_cache", None)
    if not cache or cache[2] != opt_id:
        state = load_state(r.db, run[1])
        r._state_cache = (state, policy_flags(r.db, state, run[1]), opt_id, run[1])
    return r._state_cache


# ---- Command Center ------------------------------------------------------------------------------------------------
@router.get("/overview", response_model=m.Overview)
def overview(request: Request):
    r = rt(request)
    try:
        world = r.world()
    except ConnectorError as exc:
        _fail(exc)
    return v.overview_view(r.db, r.settings.workspace, world, _scenario(r.db))


@router.get("/events", response_model=list[m.Event])
def events(request: Request):
    r = rt(request)
    out = v.event_list(r.db)
    if r.job["state"] in ("running", "failed") and r.job["started_at"]:
        out.insert(0, {"id": f"job:{r.job['started_at']}", "at": r.job["started_at"], "kind": "pipeline_job",
                       "message": f"Pipeline job {r.job['name']} {r.job['state']}"
                                  + (f": {r.job['error'].splitlines()[0]}" if r.job["error"] else ""),
                       "decision_id": None})
    return out


@router.get("/pipeline/status")
def pipeline_status(request: Request) -> dict:
    r = rt(request)
    return {**r.job, "busy": r.busy, "unresolved_executions": r.unresolved_executions()}


# ---- Decision Center -------------------------------------------------------------------------------------------------
@router.get("/decisions", response_model=list[m.Decision])
def decisions(request: Request):
    return v.decision_list(rt(request).db)


@router.get("/decisions/{decision_id}", response_model=m.Decision)
def decision(decision_id: str, request: Request):
    return _decision_or_404(rt(request).db, decision_id)


@router.get("/decisions/{decision_id}/evidence", response_model=m.EvidenceOut)
def evidence(decision_id: str, request: Request):
    try:
        return v.evidence_view(rt(request).db, decision_id)
    except dec.DecisionError as exc:
        _fail(exc)


@router.post("/decisions/{decision_id}/approve", response_model=m.Decision, dependencies=mutating)
def approve(decision_id: str, body: m.ApproveBody, request: Request):
    """Hash-bound approval; with execute=true the saga runs immediately (Stage 1: Approve mode, mock platforms)."""
    r = rt(request)
    try:
        with r.mutation():
            now = r.now()
            state_now = load_state(r.db, now)
            dec.approve(r.db, decision_id, body.decision_hash, ACTOR, ROLE, now, state_now)
            if body.execute:
                saga.execute_decision(r.db, decision_id, r.adapters, ACTOR, now, state_now)
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP status codes
        _fail(exc)
    return _decision_or_404(r.db, decision_id)


@router.post("/decisions/{decision_id}/reject", response_model=m.Decision, dependencies=mutating)
def reject(decision_id: str, body: m.RejectBody, request: Request):
    r = rt(request)
    try:
        with r.mutation():
            d = dec.get_decision(r.db, decision_id)
            if d["decision_hash"] != body.decision_hash:
                raise dec.DecisionError("HASH_MISMATCH", "the decision changed; review it again")
            dec.reject(r.db, decision_id, ACTOR, body.reason, r.now())
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return _decision_or_404(r.db, decision_id)


@router.post("/decisions/{decision_id}/modify", response_model=m.Decision, dependencies=mutating)
def modify(decision_id: str, body: m.AllocationInput, request: Request):
    """A user edit becomes a NEW decision (follows the original, separately valued, policy-checked, hash-bound);
    the original is superseded if still pending. Never an in-place mutation."""
    r = rt(request)
    if body.objective != "PROFIT":
        raise HTTPException(422, detail="NOT_BUILT: Stage 1 supports the PROFIT objective only")
    try:
        with r.mutation():
            d = dec.get_decision(r.db, decision_id)
            if d["decision_hash"] != body.decision_hash:
                raise dec.DecisionError("HASH_MISMATCH", "the proposal changed; reload before modifying")
            state, flags, opt_id, as_of = _state_cache(r)
            alloc = _allocation(state, body)
            policy = current_policy(r.db, as_of)
            prop = dec.manual_proposal(state, alloc, policy["config"])
            h = hashlib.sha256(json.dumps(alloc, sort_keys=True).encode()).hexdigest()[:10]
            run_id = f"{opt_id}-mod-{h}"
            new_id = f"{run_id}-M"
            prop["decision_id"] = new_id
            run = {"run_id": run_id, "result": prop, "safety": [], "calibration_factor":
                   d["expected"].get("optimism_correction_factor", 0.9), "manual_allocation": alloc}
            created = dec.create_decisions(r.db, run, state, flags, r.now(), actor=ACTOR, follows=decision_id)
            if not created and not r.db.query("SELECT 1 FROM intel.decisions WHERE decision_id = ?", [new_id]):
                raise dec.DecisionError("CONFLICT", "the modified allocation has no changes")
            if created:
                nd = dec.get_decision(r.db, new_id)
                persist_basis(r.db, new_id, run_id, as_of, "OPTIMIZATION", nd["expected"]["raw_pred"],
                              nd["expected"]["calibrated_pred"], state, alloc, prop["legs"])
            if dec.get_decision(r.db, decision_id)["status"] in ("DRAFT", "PENDING_APPROVAL"):
                r.db.write(lambda cur: dec.add_event(cur, decision_id, "superseded", r.now(), ACTOR,
                                                     {"superseded_by": new_id,
                                                      "invalidation_reason": "modified by the manager"}))
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return _decision_or_404(r.db, new_id)


# ---- optimizer workbench -------------------------------------------------------------------------------------------
@router.get("/optimizer/context", response_model=m.OptimizerContext)
def optimizer_context(request: Request):
    r = rt(request)
    state, flags, opt_id, as_of = _state_cache(r)
    res = v.optimizer_result(r.db, opt_id)
    g = guardrails_config()
    c = build_constraints(state, flags, g)
    names = v.unit_names(r.db)
    pend = [d for d in v.decision_list(r.db) if d["status"] == "PENDING_APPROVAL" and d["class"] == "OPTIMIZATION"]
    why = {w["unit_id"]: w for w in res.get("why_not", [])}
    pf = Portfolio(state, search_draws(state))
    camps = []
    for i, u in enumerate(state.units):
        spend = u.pacing * u.budget
        marg = None
        if u.model_available:
            marg = float(u.curve.marginal_roas(spend) * pf.cm[i] - 1.0)
        note = (v.nar.why_not_text(why[u.unit_id], names)[1] if u.unit_id in why else
                "recommended change" if abs(res.get("allocation", {}).get(u.unit_id, u.budget) - u.budget) > 1e-6
                else "")
        # whole-rupee view: bounds always contain round(current budget), which the backend reads as "unchanged"
        cur = round(u.budget)
        lo_i, hi_i = min(math.ceil(c.lo[i] - 1e-6), cur), max(math.floor(c.hi[i] + 1e-6), cur)
        after = min(max(round(res.get("allocation", {}).get(u.unit_id, u.budget)), lo_i), hi_i)
        camps.append({"platform": v.PLATFORM[u.platform], "entity": names.get(u.unit_id, u.unit_id),
                      "budget_id": u.unit_id, "before": u.budget, "after": float(after),
                      "margin": float(pf.cm[i]), "roas": float(u.observed_roas), "marginal_caa": marg,
                      "inventory_gate": (res.get("inventory_gate", {}).get(u.unit_id) or {}).get("gate", "ALLOW"),
                      "min_budget": float(lo_i), "max_budget": float(hi_i), "note": note})
    return {"decision_id": pend[0]["decision_id"] if pend else None,
            "decision_hash": pend[0]["decision_hash"] if pend else None, "supported_objectives": ["PROFIT"],
            # whole rupees: the sum of rounded budgets may exceed a fractional ceiling by a few rupees
            "objective": "PROFIT", "budget_ceiling": float(math.ceil(c.B)), "reserve_floor": float(c.R),
            "max_daily_change": float(g["change"]["max_daily_change_pct"]),
            "policy_version": current_policy(r.db, as_of)["policy_version"],
            "horizon_days": int(objectives_config()["economics"]["horizon_days"]), "campaigns": camps}


@router.post("/optimizer/run", response_model=m.Decision, dependencies=mutating)
def optimizer_run(body: m.OptimizerRunBody, request: Request):
    r = rt(request)
    if body.objective != "PROFIT":
        raise HTTPException(422, detail="NOT_BUILT: Stage 1 supports the PROFIT objective only")
    try:
        with r.mutation():
            run = v.latest_run(r.db)
            if run is None:
                raise HTTPException(503, detail="no completed pipeline run yet")
            out = run_optimizer(r.db, run[1])
            dec.create_decisions(r.db, out, out["state"], out["flags"], run[1])
            did = out["result"].get("decision_id")
            if not did or not r.db.query("SELECT 1 FROM intel.decisions WHERE decision_id = ?", [did]):
                raise HTTPException(409, detail="the optimizer recommends no change at the current allocation")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return _decision_or_404(r.db, did)


@router.post("/optimizer/whatif", response_model=m.Evaluation, dependencies=mutating)
def whatif(body: m.AllocationInput, request: Request):
    """Value an edited allocation with the same portfolio_economics + policy validation (not executable)."""
    r = rt(request)
    if body.objective != "PROFIT":
        return {"decision_id": body.decision_id, "decision_hash": body.decision_hash, "objective": body.objective,
                "objective_value": None, "allocated": 0.0, "unallocated": 0.0,
                "checks": [{"id": "OBJECTIVE", "label": "Objective supported", "passed": False,
                            "detail": "Stage 1 values PROFIT only"}],
                "estimate_status": "NOT_ESTIMABLE", "estimate": None,
                "explanation": "This objective is a Stage 2 feature; no estimate is fabricated."}
    state, flags, _opt_id, _as_of = _state_cache(r)
    alloc = _allocation(state, body)
    econ = Portfolio(state).evaluate(alloc)
    s = econ.summary()
    lam = objectives_config()["PROFIT"]["lambda"]
    checks = validate(state, alloc, flags, "OPTIMIZATION")
    c = build_constraints(state, flags, guardrails_config())
    total = sum(alloc.values())
    return {"decision_id": body.decision_id, "decision_hash": body.decision_hash, "objective": "PROFIT",
            "objective_value": {"value": s["E"] - lam * (s["E"] - s["P10"]),
                                "label": "Risk-adjusted contribution change (7 days)", "unit": "INR"},
            "allocated": total, "unallocated": max(c.B - total, 0.0),
            "checks": [{"id": x["rule"], "label": v.nar.CHECK_LABEL.get(x["rule"], x["rule"]), "passed": x["passed"],
                        "detail": x["detail"]} for x in checks],
            "estimate_status": "AVAILABLE",
            "estimate": {"p10": s["P10"], "p50": s["P50"], "p90": s["P90"], "prob_loss": s["prob_loss"],
                         "delta_net_revenue": s["delta_net_revenue"], "raw_pred": s["E"],
                         "calibrated_pred": s["E"] * 0.9 if s["E"] > 0 else s["E"]},
            "explanation": "Valued with portfolio_economics over all 200 joint bootstrap draws; "
                           + ("passes policy." if policy_result(checks, "OPTIMIZATION")["status"] != "BLOCKED"
                              else "fails policy: " + ", ".join(x["rule"] for x in checks if not x["passed"]))}


# ---- anomalies -----------------------------------------------------------------------------------------------------
@router.get("/anomalies", response_model=list[m.Anomaly])
def anomalies(request: Request):
    return v.anomaly_list(rt(request).db)


@router.get("/anomalies/{anomaly_id}", response_model=m.Anomaly)
def anomaly(anomaly_id: str, request: Request):
    a = v.anomaly_view(rt(request).db, anomaly_id)
    if a is None:
        raise HTTPException(404, detail=f"anomaly {anomaly_id} not found")
    return a


@router.post("/anomalies/{anomaly_id}/status", response_model=m.Anomaly, dependencies=mutating)
def anomaly_status(anomaly_id: str, body: m.AnomalyStatusBody, request: Request):
    r = rt(request)
    if v.anomaly_view(r.db, anomaly_id) is None:
        raise HTTPException(404, detail=f"anomaly {anomaly_id} not found")
    internal = {"OPEN": "investigating", "ACKNOWLEDGED": "acknowledged", "RESOLVED": "resolved"}[body.status]
    try:
        with r.mutation():
            now = r.now()

            def work(cur):
                cur.execute("CREATE TABLE IF NOT EXISTS ops.anomaly_status_log (anomaly_id VARCHAR, status VARCHAR, "
                            "reason VARCHAR, actor VARCHAR, logged_at TIMESTAMP)")
                cur.execute("INSERT INTO ops.anomaly_status_log VALUES (?, ?, ?, ?, ?)",
                            [anomaly_id, body.status, body.reason or None, ACTOR, now])
                cur.execute("UPDATE intel.anomalies SET status = ? WHERE anomaly_id = ?", [internal, anomaly_id])

            r.db.write(work)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return v.anomaly_view(r.db, anomaly_id)


# ---- executions ----------------------------------------------------------------------------------------------------
@router.get("/executions", response_model=list[m.Execution])
def executions(request: Request):
    return v.execution_list(rt(request).db)


@router.get("/outcomes", response_model=list[m.Outcome])
def outcomes(request: Request):
    return v.outcome_list(rt(request).db)


@router.get("/ledger", response_model=list[m.LedgerEntry])
def ledger(request: Request):
    return v.ledger_list(rt(request).db)


def _execution(r: Runtime, execution_id: str, body: m.RecoveryBody) -> tuple[str, dict]:
    row = r.db.query("SELECT decision_id, kind FROM exec.sagas WHERE saga_id = ?", [execution_id]) \
        if v.has(r.db, "exec", "sagas") else []
    if not row:
        raise HTTPException(404, detail=f"execution {execution_id} not found")
    did = row[0][0]
    d = dec.get_decision(r.db, did)
    if d["decision_hash"] != body.decision_hash:
        raise dec.DecisionError("HASH_MISMATCH", "the execution belongs to another decision revision")
    return did, d


def _exec_out(r: Runtime, execution_id: str) -> dict:
    return next(e for e in v.execution_list(r.db) if e["execution_id"] == execution_id)


@router.post("/executions/{execution_id}/verify", response_model=m.Execution, dependencies=mutating)
def verify(execution_id: str, body: m.RecoveryBody, request: Request):
    r = rt(request)
    try:
        with r.mutation():
            _execution(r, execution_id, body)
            saga.reverify(r.db, r.adapters, r.now())
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return _exec_out(r, execution_id)


@router.post("/executions/{execution_id}/rollback", response_model=m.Execution, dependencies=mutating)
def rollback(execution_id: str, body: m.RecoveryBody, request: Request):
    r = rt(request)
    try:
        with r.mutation():
            did, _d = _execution(r, execution_id, body)
            out = saga.rollback(r.db, did, r.adapters, ACTOR, r.now())
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return _exec_out(r, out["saga_id"])


@router.post("/executions/{execution_id}/reconcile", response_model=m.Execution, dependencies=mutating)
def reconcile(execution_id: str, body: m.RecoveryBody, request: Request):
    r = rt(request)
    try:
        with r.mutation():
            _execution(r, execution_id, body)
            conflicts = [lid for (lid,) in r.db.query("SELECT leg_id FROM exec.saga_legs WHERE saga_id = ? "
                                                      "AND state = 'CONFLICT'", [execution_id])]
            if conflicts:
                for lid in conflicts:
                    saga.reconcile_conflict(r.db, lid, "accept_observed", ACTOR, ROLE, r.now())
            else:
                saga.resolve_manually(r.db, execution_id, body.final_resolution or "COMPENSATED", r.adapters,
                                      ACTOR, ROLE, r.now())
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return _exec_out(r, execution_id)


@router.post("/executions/{execution_id}/retry", response_model=m.Execution, dependencies=mutating)
def retry(execution_id: str, body: m.RecoveryBody, request: Request):
    raise HTTPException(422, detail="NOT_BUILT: Stage 1 does not retry legs; generate a fresh decision instead")


# ---- Scenario Lab ---------------------------------------------------------------------------------------------------
def _world_post(r: Runtime, path: str, body: dict, rid: str) -> None:
    resp = r.client.post(path, json=body, headers={"X-Request-ID": rid, "X-Actor-ID": ACTOR})
    if resp.status_code >= 400:
        raise HTTPException(409 if resp.status_code == 409 else 502, detail=f"world: {resp.text[:300]}")


@router.get("/sim/scenarios", response_model=m.ScenarioCatalog)
def sim_scenarios():
    return {"stage": "Stage 1", "note": "Stage 1 runs S1–S5, S7 and DEMO_01 against the world service. The others "
            "need modules or world scenarios that are not built yet and cannot be loaded.",
            "items": [{"key": k, "title": t, "description": d, "category": c,
                       "status": "AVAILABLE" if k in STAGE1_SCENARIOS else "NOT_BUILT", "missing_modules": miss}
                      for k, t, d, c, miss in SCENARIO_CATALOG]}


@router.post("/sim/scenario/{key}", response_model=m.Ack, dependencies=mutating)
def sim_scenario(key: str, request: Request):
    r = rt(request)
    if key not in STAGE1_SCENARIOS:
        raise HTTPException(422, detail=f"NOT_BUILT: {key} is not a Stage 1 scenario")
    try:
        with r.mutation():
            if r.unresolved_executions():
                raise HTTPException(409, detail="an execution is unresolved; resolve it before changing the world")
            now = r.now()
            _world_post(r, "/control/scenario", {"key": key}, f"api-scenario-{key}-{now.isoformat()}")
            _log(r.db, "scenario", key, now)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return {"ok": True}


@router.post("/sim/advance", response_model=m.Ack, dependencies=mutating)
def sim_advance(request: Request, days: int = Query(1, ge=1, le=14)):
    """Advance the world `days` days, then (background job) run the pipeline for every new day."""
    r = rt(request)
    try:
        with r.mutation():
            if r.unresolved_executions():
                raise HTTPException(409, detail="an execution is unresolved; world advance is blocked")
            now = r.now()
            _world_post(r, "/control/advance", {"days": days}, f"api-advance-{now.isoformat()}-{days}")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    r.start_job(f"advance+{days}", r.catch_up, sync=request.app.state.sync_jobs)
    return {"ok": True}


@router.post("/sim/reset", response_model=m.Ack, dependencies=mutating)
def sim_reset(request: Request, seed: int = Query(42)):
    """Restore the world to its seeded baseline AND the workspace to its baseline copy (no stale app data)."""
    r = rt(request)
    try:
        world = r.world()
        if world.get("seed") != seed:
            raise HTTPException(422, detail=f"this world service serves seed {world.get('seed')}, not {seed}")
        with r.mutation():
            if not r.baseline_path.exists():
                raise Busy("no workspace baseline: run `python -m adapt.api.runtime --bootstrap` first")
            _world_post(r, "/control/reset", {"seed": seed}, f"api-reset-{seed}-{world.get('day')}")
            r.restore_baseline()
            request.app.state.db = r.db
            r._state_cache = None
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return {"ok": True}


@router.post("/sim/fault/{kind}", response_model=m.Ack, dependencies=mutating)
def sim_fault(kind: str, request: Request):
    r = rt(request)
    if kind == "FAILED":
        try:
            with r.mutation():
                _world_post(r, "/control/fault", {"platform": "google", "fault": "unavailable", "count": 3},
                            f"api-fault-{r.now().isoformat()}")
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            _fail(exc)
        return {"ok": True}
    raise HTTPException(422, detail="NOT_BUILT: UNKNOWN arises from stale platform read-back, which the world's mock "
                                    "APIs cannot inject yet; FAILED is supported")
