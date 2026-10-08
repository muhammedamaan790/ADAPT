"""Stage 1 decision-loop API (C6, spec §12): Command Center, Decision Center, Scenario Lab, plus the workbench reads
the frontend probes (anomalies, optimizer context, executions, ledger). Paths and payloads follow the frontend's zod
contracts; FastAPI validates every response against api/models.py before it is sent.

Errors: 404 unknown id; 409 stale hash / expired / conflict / busy pipeline / unresolved execution / simulation out
of sync with a verified live change; 403 role; 422 NOT_BUILT for features not built (injected UNKNOWN faults,
retry, objectives other than PROFIT / GROWTH / INVENTORY_CLEARANCE); 503 world unreachable.
Stage 1 actor: one demo manager (session auth with roles is the follow-up agreed with the frontend).
"""

from __future__ import annotations

import hashlib
import json
import math

import numpy as np
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from adapt.api import models as m
from adapt.api import views as v
from adapt.api.auth import DEMO_USER, User
from adapt.api.runtime import Busy, Runtime
from adapt.decide import decisions as dec
from adapt.decide.alternatives import MODES, objectives_for, selected_objective
from adapt.decide.optimizer import Optimizer, build_constraints, search_draws
from adapt.decide.run import persist_basis, policy_flags, run_optimizer
from adapt.economics.portfolio import Portfolio
from adapt.economics.state import guardrails_config, load_state, objectives_config
from adapt.execute import mirror, saga
from adapt.execute.adapters import platform_health
from adapt.ingest.http import ConnectorError
from adapt.policy.engine import current_policy, policy_result, validate


def mutation_headers(x_request_id: str = Header(..., alias="X-Request-ID"),
                     idempotency_key: str = Header(..., alias="Idempotency-Key")) -> None:
    """Every mutating request carries X-Request-ID and Idempotency-Key (spec §12). Semantic idempotency is enforced
    by the engine itself: approval is bound to the decision hash and a decision has at most one execution saga."""


router = APIRouter(prefix="/api/v1", tags=["loop"])
mutating = [Depends(mutation_headers)]


def actor(request: Request) -> User:
    """The signed-in user, recorded on every approval, rejection and execution (the demo manager when auth is off)."""
    return getattr(request.state, "user", DEMO_USER)

BUILT_SCENARIOS = {"DEMO_01", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S10", "S11", "S12"}
# spec §14: the world implements these and every module they need exists (Stage 2 adds S6, S8, S10-S12). S9, the
# ROAS trap, is a decision-quality property of opportunity ranking, not an injected world scenario.
SCENARIO_CATALOG = [
    ("DEMO_01", "Golden demo", "Creative fatigue on a Meta category with low hero-SKU cover, while a Google category "
     "has rising demand and deep stock.", "Demo", []),
    ("S1", "Auction pressure", "Meta CPM +45% platform-wide for 6 days.", "Detect + diagnose", []),
    ("S2", "Creative fatigue", "One creative's CTR falls 40% over 10 days.", "Detect + diagnose", []),
    ("S3", "Hero SKU stockout", "A hero SKU stocks out while its Google campaign keeps spending.",
     "Detect + diagnose", []),
    ("S4", "Price change", "+18% price on a category.", "Detect + diagnose", []),
    ("S5", "Tracking break", "Sessions −60% while clicks stay flat.", "Detect + diagnose", []),
    ("S6", "Demand surge", "Category demand surge (positive anomaly).", "Detect + diagnose", []),
    ("S7", "Human budget cut", "A manager cuts a budget; must not open an efficiency incident.", "Classification", []),
    ("S8", "Audience saturation", "Retargeting frequency 2→5 with flat reach.", "Detect + diagnose", []),
    ("S9", "ROAS trap", "A high-ROAS, low-margin, low-stock SKU against a high-margin, deep-stock SKU.",
     "Decision quality", ["an injected ROAS-trap world scenario (checked by the opportunity ranking test)"]),
    ("S10", "Seasonal pattern", "Weekly or holiday pattern; must be labelled seasonal_expected.", "Classification",
     []),
    ("S11", "Excess inventory", "Clearance objective shifts spend toward EXCESS SKUs.", "Decision quality", []),
    ("S12", "Fatigue + demand", "Two drivers on one campaign, ranked by simulated effect.", "Detect + diagnose", []),
]
ERR = {"NOT_FOUND": 404, "FORBIDDEN": 403, "HASH_MISMATCH": 409, "EXPIRED": 409, "CONFLICT": 409,
       "ROLLBACK_CONFLICT": 409, "INVALID": 422}


def rt(request: Request) -> Runtime:
    return request.app.state.runtime


def _fail(exc: Exception):
    if isinstance(exc, mirror.SimOutOfSync):
        raise HTTPException(409, detail=f"SIM OUT OF SYNC: {exc}") from exc
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


def _objective(r: Runtime, requested: str) -> str:
    """The workspace objective (an admin setting); a request for another one is refused, never silently swapped."""
    if requested not in MODES:
        raise HTTPException(422, detail=f"NOT_BUILT: the {requested} objective; built: {', '.join(MODES)}")
    mode = selected_objective(r.db)
    if requested != mode:
        raise HTTPException(409, detail=f"the workspace objective is {mode}; change it on the objective page first")
    return mode


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
    brand = r.brands.active()
    if brand:  # uploaded data only: no world service needed
        from adapt.api.brands import brand_overview

        return brand_overview(r.db, brand)
    try:
        world = r.world()
    except ConnectorError as exc:
        _fail(exc)
    return v.overview_view(r.db, r.settings.workspace, world, _scenario(r.db))


@router.get("/events", response_model=list[m.Event])
def events(request: Request):
    r = rt(request)
    if _brand_db(r):
        from adapt.api import brand_engine as be

        return be.events(r.db)
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
    r = rt(request)
    if _brand_db(r):
        from adapt.api import brand_engine as be

        return be.decisions(r.db)
    return v.decision_list(r.db)


@router.get("/decisions/{decision_id}", response_model=m.Decision)
def decision(decision_id: str, request: Request):
    r = rt(request)
    if _brand_db(r):
        from adapt.api import brand_engine as be

        d = be.decision(r.db, decision_id)
        if d is None:
            raise HTTPException(404, detail=f"decision {decision_id} not found")
        return d
    return _decision_or_404(r.db, decision_id)


@router.get("/decisions/{decision_id}/evidence", response_model=m.EvidenceOut)
def evidence(decision_id: str, request: Request):
    r = rt(request)
    if _brand_db(r):
        from adapt.api import brand_engine as be

        e = be.evidence(r.db, decision_id)
        if e is None:
            raise HTTPException(404, detail=f"decision {decision_id} not found")
        return e
    try:
        return v.evidence_view(rt(request).db, decision_id)
    except dec.DecisionError as exc:
        _fail(exc)


@router.post("/decisions/{decision_id}/approve", response_model=m.Decision, dependencies=mutating)
def approve(decision_id: str, body: m.ApproveBody, request: Request):
    """Hash-bound approval; with execute=true the saga runs immediately (Stage 1: Approve mode, mock platforms)."""
    r = rt(request)
    if _brand_db(r):
        from adapt.api import brand_engine as be

        return _brand_call(be.approve, r.db, decision_id, body.decision_hash, actor(request).user_id)
    try:
        with r.mutation():
            now = r.now()
            state_now = load_state(r.db, now)
            u = actor(request)
            dec.approve(r.db, decision_id, body.decision_hash, u.user_id, u.role, now, state_now)
            if body.execute:
                saga.execute_decision(r.db, decision_id, r.adapters, actor(request).user_id, now, state_now)
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP status codes
        _fail(exc)
    return _decision_or_404(r.db, decision_id)


@router.post("/decisions/{decision_id}/reject", response_model=m.Decision, dependencies=mutating)
def reject(decision_id: str, body: m.RejectBody, request: Request):
    r = rt(request)
    if _brand_db(r):
        from adapt.api import brand_engine as be

        return _brand_call(be.reject, r.db, decision_id, body.decision_hash, actor(request).user_id, body.reason)
    try:
        with r.mutation():
            d = dec.get_decision(r.db, decision_id)
            if d["decision_hash"] != body.decision_hash:
                raise dec.DecisionError("HASH_MISMATCH", "the decision changed; review it again")
            dec.reject(r.db, decision_id, actor(request).user_id, body.reason, r.now())
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return _decision_or_404(r.db, decision_id)


@router.post("/decisions/{decision_id}/modify", response_model=m.Decision, dependencies=mutating)
def modify(decision_id: str, body: m.AllocationInput, request: Request):
    """A user edit becomes a NEW decision (follows the original, separately valued, policy-checked, hash-bound);
    the original is superseded if still pending. Never an in-place mutation."""
    r = rt(request)
    mode = _objective(r, body.objective)
    try:
        with r.mutation():
            d = dec.get_decision(r.db, decision_id)
            if d["decision_hash"] != body.decision_hash:
                raise dec.DecisionError("HASH_MISMATCH", "the proposal changed; reload before modifying")
            state, flags, opt_id, as_of = _state_cache(r)
            alloc = _allocation(state, body)
            policy = current_policy(r.db, as_of)
            prop = dec.manual_proposal(state, alloc, policy["config"])
            prop["objective"] = mode
            h = hashlib.sha256(json.dumps(alloc, sort_keys=True).encode()).hexdigest()[:10]
            run_id = f"{opt_id}-mod-{h}"
            new_id = f"{run_id}-M"
            prop["decision_id"] = new_id
            run = {"run_id": run_id, "result": prop, "safety": [], "calibration_factor":
                   d["expected"].get("optimism_correction_factor", 0.9), "manual_allocation": alloc}
            created = dec.create_decisions(r.db, run, state, flags, r.now(), actor=actor(request).user_id,
                                           follows=decision_id)
            if not created and not r.db.query("SELECT 1 FROM intel.decisions WHERE decision_id = ?", [new_id]):
                raise dec.DecisionError("CONFLICT", "the modified allocation has no changes")
            if created:
                nd = dec.get_decision(r.db, new_id)
                persist_basis(r.db, new_id, run_id, as_of, "OPTIMIZATION", nd["expected"]["raw_pred"],
                              nd["expected"]["calibrated_pred"], state, alloc, prop["legs"])
            if dec.get_decision(r.db, decision_id)["status"] in ("DRAFT", "PENDING_APPROVAL"):
                r.db.write(lambda cur: dec.add_event(cur, decision_id, "superseded", r.now(), actor(request).user_id,
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
    selected = selected_objective(r.db)  # the workbench runs the workspace objective (changed on its own page)
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
            "decision_hash": pend[0]["decision_hash"] if pend else None, "supported_objectives": [selected],
            # whole rupees: the sum of rounded budgets may exceed a fractional ceiling by a few rupees
            "objective": selected, "budget_ceiling": float(math.ceil(c.B)), "reserve_floor": float(c.R),
            "max_daily_change": float(g["change"]["max_daily_change_pct"]),
            "policy_version": current_policy(r.db, as_of)["policy_version"],
            "horizon_days": int(objectives_config()["economics"]["horizon_days"]), "campaigns": camps}


@router.post("/optimizer/run", response_model=m.Decision, dependencies=mutating)
def optimizer_run(body: m.OptimizerRunBody, request: Request):
    r = rt(request)
    _objective(r, body.objective)
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
    mode = selected_objective(r.db)
    if body.objective != mode:
        why = (f"the workspace objective is {mode}" if body.objective in MODES else
               f"the {body.objective} objective is not built")
        return {"decision_id": body.decision_id, "decision_hash": body.decision_hash, "objective": body.objective,
                "objective_value": None, "allocated": 0.0, "unallocated": 0.0,
                "checks": [{"id": "OBJECTIVE", "label": "Objective supported", "passed": False, "detail": why}],
                "estimate_status": "NOT_ESTIMABLE", "estimate": None,
                "explanation": f"Not valued: {why}; no estimate is fabricated."}
    state, flags, _opt_id, _as_of = _state_cache(r)
    alloc = _allocation(state, body)
    econ = Portfolio(state).evaluate(alloc)
    s = econ.summary()
    if mode == "PROFIT":
        lam = objectives_config()["PROFIT"]["lambda"]
        value = {"value": s["E"] - lam * (s["E"] - s["P10"]), "label": "Risk-adjusted contribution change (7 days)",
                 "unit": "INR"}
    elif mode == "GROWTH":
        value = {"value": s["delta_net_revenue"], "label": "Expected net revenue change (7 days)", "unit": "INR"}
    else:  # INVENTORY_CLEARANCE: the optimizer's own objective, E[dCAA] + h x excess-band units sold
        opt = Optimizer(state, flags, objectives=objectives_for(mode))
        st = opt.fe.state_of(np.array([alloc[u.unit_id] for u in state.units], dtype=float))
        value = {"value": opt.value(st), "label": "Contribution change + holding value of excess units sold (7 days)",
                 "unit": "INR"}
    checks = validate(state, alloc, flags, "OPTIMIZATION")
    c = build_constraints(state, flags, guardrails_config())
    total = sum(alloc.values())
    return {"decision_id": body.decision_id, "decision_hash": body.decision_hash, "objective": mode,
            "objective_value": value,
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
    r = rt(request)
    if _brand_db(r):
        from adapt.api import brand_engine as be

        return be.anomalies(r.db)
    return v.anomaly_list(r.db)


@router.get("/anomalies/{anomaly_id}", response_model=m.Anomaly)
def anomaly(anomaly_id: str, request: Request):
    r = rt(request)
    if _brand_db(r):
        from adapt.api import brand_engine as be

        a = be.anomaly(r.db, anomaly_id)
    else:
        a = v.anomaly_view(r.db, anomaly_id)
    if a is None:
        raise HTTPException(404, detail=f"anomaly {anomaly_id} not found")
    return a


@router.post("/anomalies/{anomaly_id}/status", response_model=m.Anomaly, dependencies=mutating)
def anomaly_status(anomaly_id: str, body: m.AnomalyStatusBody, request: Request):
    r = rt(request)
    if _brand_db(r):
        from adapt.api import brand_engine as be

        a = be.set_anomaly_status(r.db, anomaly_id, body.status, body.reason)
        if a is None:
            raise HTTPException(404, detail=f"anomaly {anomaly_id} not found")
        return a
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
                            [anomaly_id, body.status, body.reason or None, actor(request).user_id, now])
                cur.execute("UPDATE intel.anomalies SET status = ? WHERE anomaly_id = ?", [internal, anomaly_id])

            r.db.write(work)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return v.anomaly_view(r.db, anomaly_id)


# ---- executions ----------------------------------------------------------------------------------------------------
@router.get("/executions", response_model=list[m.Execution])
def executions(request: Request):
    r = rt(request)
    if _brand_db(r):
        from adapt.api import brand_engine as be

        return be.executions(r.db)
    return v.execution_list(r.db)


@router.get("/outcomes", response_model=list[m.Outcome])
def outcomes(request: Request):
    r = rt(request)
    if _brand_db(r):
        from adapt.api import brand_engine as be

        return be.outcomes(r.db)
    return v.outcome_list(r.db)


@router.get("/ledger", response_model=list[m.LedgerEntry])
def ledger(request: Request):
    r = rt(request)
    if _brand_db(r):
        from adapt.api import brand_engine as be

        return be.ledger(r.db)
    return v.ledger_list(r.db)


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
            out = saga.rollback(r.db, did, r.adapters, actor(request).user_id, r.now())
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return _exec_out(r, out["saga_id"])


@router.post("/executions/{execution_id}/reconcile", response_model=m.Execution, dependencies=mutating)
def reconcile(execution_id: str, body: m.RecoveryBody, request: Request,
              target: str = Query("platform", pattern="^(platform|sim)$")):
    """target=platform: resolve CONFLICT legs or a saga awaiting human resolution. target=sim: apply a failed
    simulation mirror after a FRESH live read-back confirms the verified amount (the live change is never touched)."""
    r = rt(request)
    if target == "sim":
        return _reconcile_sim(r, execution_id, body, request)
    try:
        with r.mutation():
            _execution(r, execution_id, body)
            conflicts = [lid for (lid,) in r.db.query("SELECT leg_id FROM exec.saga_legs WHERE saga_id = ? "
                                                      "AND state = 'CONFLICT'", [execution_id])]
            if conflicts:
                for lid in conflicts:
                    saga.reconcile_conflict(r.db, lid, "accept_observed", actor(request).user_id,
                                           actor(request).role, r.now())
            else:
                saga.resolve_manually(r.db, execution_id, body.final_resolution or "COMPENSATED", r.adapters,
                                      actor(request).user_id, actor(request).role, r.now())
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return _exec_out(r, execution_id)


def _reconcile_sim(r: Runtime, execution_id: str, body: m.RecoveryBody, request: Request) -> dict:
    live = r.adapters.get("google")
    if getattr(live, "mode", None) != "LIVE":
        raise HTTPException(409, detail="no live platform: simulation mirrors exist only in hybrid (live) mode")
    try:
        with r.mutation():
            _execution(r, execution_id, body)
            failed = [lid for (lid,) in r.db.query("SELECT leg_id FROM exec.saga_legs WHERE saga_id = ? "
                                                   "AND sim_sync_state = 'MIRROR_FAILED'", [execution_id])]
            if not failed:
                raise HTTPException(409, detail="no MIRROR_FAILED leg on this execution")
            u = actor(request)
            for lid in failed:
                mirror.resolve_manually(r.db, lid, live, live.mirror, u.user_id, u.role, r.now())
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return _exec_out(r, execution_id)


@router.get("/platforms/health", response_model=m.PlatformsOut)
def platforms_health(request: Request):
    """Per platform: the execution mode fixed at startup and whether it can execute (a failing live check disables
    Approve for that platform's legs; spec §9.4), plus legs whose verified live change is not yet mirrored."""
    r = rt(request)
    out = []
    for platform, h in platform_health(r.adapters).items():
        out.append({"platform": platform, "mode": h.get("mode", "MOCK"), "ok": bool(h.get("ok")),
                    "label": h.get("label") or f"{h.get('mode', 'MOCK')} · {platform}", "reason": h.get("reason"),
                    "checks": {k: bool(x) for k, x in (h.get("checks") or {}).items()}})
    sync = mirror.divergent(r.db) if v.has(r.db, "exec", "leg_mirror") else []
    return {"platforms": out, "sim_out_of_sync": sync}


@router.post("/executions/{execution_id}/retry", response_model=m.Execution, dependencies=mutating)
def retry(execution_id: str, body: m.RecoveryBody, request: Request):
    raise HTTPException(422, detail="NOT_BUILT: Stage 1 does not retry legs; generate a fresh decision instead")


# ---- Scenario Lab ---------------------------------------------------------------------------------------------------
def _brand_db(r: Runtime):
    """The active brand workspace's database (its uploads and rule-based loop), or None in the engine workspace."""
    return r.db if r.brands.active() else None


def _brand_call(fn, *args):
    from adapt.api.brand_engine import BrandDecisionError

    try:
        return fn(*args)
    except BrandDecisionError as exc:
        raise HTTPException(exc.status, detail=str(exc)) from exc


def _require_world(r: Runtime) -> None:
    """The simulator drives the engine workspace only; a brand workspace changes through its uploads."""
    brand = r.brands.active()
    if brand:
        raise HTTPException(409, detail=f"{brand['name']} holds your uploaded data, which the simulator does not "
                                        "drive; switch to the simulated world workspace to inject scenarios or "
                                        "advance days")


def _world_post(r: Runtime, path: str, body: dict, rid: str, actor_id: str) -> None:
    resp = r.client.post(path, json=body, headers={"X-Request-ID": rid, "X-Actor-ID": actor_id})
    if resp.status_code >= 400:
        raise HTTPException(409 if resp.status_code == 409 else 502, detail=f"world: {resp.text[:300]}")


@router.get("/sim/scenarios", response_model=m.ScenarioCatalog)
def sim_scenarios():
    return {"stage": "Stage 2", "note": "Every scenario the world injects can be loaded (S1–S8, S10–S12, DEMO_01). "
            "S9 is checked by the opportunity-ranking test rather than injected into the world.",
            "items": [{"key": k, "title": t, "description": d, "category": c,
                       "status": "AVAILABLE" if k in BUILT_SCENARIOS else "NOT_BUILT", "missing_modules": miss}
                      for k, t, d, c, miss in SCENARIO_CATALOG]}


@router.post("/sim/scenario/{key}", response_model=m.Ack, dependencies=mutating)
def sim_scenario(key: str, request: Request):
    r = rt(request)
    _require_world(r)
    if key not in BUILT_SCENARIOS:
        raise HTTPException(422, detail=f"NOT_BUILT: {key} is not an injectable world scenario")
    try:
        with r.mutation():
            if r.unresolved_executions():
                raise HTTPException(409, detail="an execution is unresolved; resolve it before changing the world")
            now = r.now()
            _world_post(r, "/control/scenario", {"key": key}, f"api-scenario-{key}-{now.isoformat()}",
                        actor(request).user_id)
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
    _require_world(r)
    try:
        with r.mutation():
            if r.unresolved_executions():
                raise HTTPException(409, detail="an execution is unresolved; world advance is blocked")
            now = r.now()
            live = r.adapters.get("google")
            if getattr(live, "mode", None) == "LIVE":  # one more mirror attempt before the divergence check
                mirror.retry_pending(r.db, live.mirror, now)
            try:  # the only advance path: refused while a verified live change is not mirrored (spec §9.4, T41)
                mirror.advance_world(r.db, r.client, days, f"api-advance-{now.isoformat()}-{days}",
                                     actor_id=actor(request).user_id)
            except RuntimeError as exc:
                if isinstance(exc, mirror.SimOutOfSync):
                    raise
                raise HTTPException(409 if "HTTP 409" in str(exc) else 502, detail=f"world: {exc}") from exc
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
    _require_world(r)
    try:
        world = r.world()
        if world.get("seed") != seed:
            raise HTTPException(422, detail=f"this world service serves seed {world.get('seed')}, not {seed}")
        with r.mutation():
            if not r.baseline_path.exists():
                raise Busy("no workspace baseline: run `python -m adapt.api.runtime --bootstrap` first")
            _world_post(r, "/control/reset", {"seed": seed}, f"api-reset-{seed}-{world.get('day')}",
                        actor(request).user_id)
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
    _require_world(r)
    if kind == "FAILED":
        try:
            with r.mutation():
                _world_post(r, "/control/fault", {"platform": "google", "fault": "unavailable", "count": 3},
                            f"api-fault-{r.now().isoformat()}", actor(request).user_id)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            _fail(exc)
        return {"ok": True}
    raise HTTPException(422, detail="NOT_BUILT: UNKNOWN arises from stale platform read-back, which the world's mock "
                                    "APIs cannot inject yet; FAILED is supported")
