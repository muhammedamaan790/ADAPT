"""View models for the insight / management / policy screens (C6 completion), from stored engine state.

Contract files: web/src/api/insight-contracts.ts, management-contracts.ts, policy-contracts.ts,
completion-contracts.ts. Features of later stages answer with the contracts' own NOT_AVAILABLE / NOT_ESTIMABLE
states and say which module is missing; nothing is invented. Timestamps that the contracts parse with
`z.string().datetime()` are UTC with a `Z` suffix (logical times are Asia/Kolkata, +05:30).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from adapt.api import views as v
from adapt.decide import narrative as nar
from adapt.decide.decisions import get_decision
from adapt.decide.optimizer import FastEval, build_constraints, gate_label, search_draws
from adapt.decide.snapshot import artifacts_dir
from adapt.diagnose.evidence import Incident, fatigue
from adapt.economics.portfolio import Portfolio
from adapt.economics.state import guardrails_config, objectives_config
from adapt.learn.calibration import current_factor

IST = timedelta(hours=5, minutes=30)


def iso_z(dt: datetime | None) -> str | None:
    """A naive Asia/Kolkata logical time as an ISO UTC timestamp with Z (zod datetime())."""
    if dt is None:
        return None
    return (dt - IST).replace(microsecond=0).isoformat() + "Z"


# ---- opportunities and curves ------------------------------------------------------------------------------------
def opportunities(db, state, flags) -> list[dict]:
    """Spec §8.1 opportunity score: risk-adjusted dCAA (E - 0.5 (E - P10), 7 days) of +delta on one unit, everything
    else fixed, delta = min(1,000, the largest policy-feasible increase); delta = 0 -> BLOCKED with the reason."""
    g = guardrails_config()
    lam = objectives_config()["PROFIT"]["lambda"]
    c = build_constraints(state, flags, g)
    fe = FastEval(Portfolio(state, search_draws(state)), g["inventory_gate"])
    base = fe.state_of(fe.pf.s0)
    names = v.unit_names(db)
    pending = {}
    for d in v.decision_list(db):
        if d["status"] == "PENDING_APPROVAL":
            for leg in d["legs"]:
                pending.setdefault(leg["budget_id"], d["decision_id"])
    out = []
    for i, u in enumerate(state.units):
        delta = float(max(min(1000.0, c.hi[i] - u.budget, c.cap_total - fe.pf.s0.sum()), 0.0))
        row = {"id": f"OPP-{u.unit_id}", "budget_id": u.unit_id, "entity": names.get(u.unit_id, u.unit_id),
               "platform": v.PLATFORM[u.platform], "delta_budget": delta, "decision_id": pending.get(u.unit_id),
               "evidence": [{"label": "Decision Center", "href": "/decisions"}] +
                           ([{"label": "Pending decision", "href": f"/decisions/{pending[u.unit_id]}"}]
                            if u.unit_id in pending else []),
               "provenance": v.PROVENANCE, "score": None, "marginal_caa": None}
        reasons = c.unit_reasons.get(i, [])
        if not u.model_available:
            row.update(status="NOT_ESTIMABLE", reason="no usable response curve (own or pooled); cuts only")
        elif reasons:
            row.update(status="BLOCKED", reason=f"{reasons[0].replace('_', ' ').lower()}")
        elif delta <= 0:
            row.update(status="BLOCKED", reason="no feasible increase (daily change limit or budget ceiling)")
        else:
            cand = fe.with_move(base, i, u.budget + delta)
            if not fe.gate_ok(cand):
                x = float(fe.exposure(cand)[i])
                row.update(status="BLOCKED", reason=f"inventory gate {gate_label(x, g['inventory_gate'])}: "
                                                    f"{x:.0%} of revenue on SKUs with a projected shortfall")
            else:
                d = fe.dcaa(cand)
                e = float(d.mean())
                row.update(status="FEASIBLE", score=e - lam * (e - float(np.percentile(d, 10))),
                           marginal_caa=e / (delta * fe.pf.H),
                           reason=f"+{nar.inr(delta)}/day: model-estimated contribution change {nar.inr(e, True)} "
                                  "over 7 days")
        out.append(row)
    out.sort(key=lambda r: (r["status"] != "FEASIBLE", -(r["score"] or -1e18)))
    return out


def curve(db, state, unit_id: str) -> dict | None:
    i = next((k for k, u in enumerate(state.units) if u.unit_id == unit_id), None)
    if i is None:
        return None
    u = state.units[i]
    names = v.unit_names(db)
    out = {"budget_id": unit_id, "label": names.get(unit_id, unit_id),
           "unit": "daily budget (INR) → model-estimated change in contribution after ads over 7 days (INR)",
           "points": [], "reason": ""}
    if not u.model_available:
        out["reason"] = "MODEL_UNAVAILABLE: no usable own or pooled response curve; the unit can only be cut"
        return out
    fe = FastEval(Portfolio(state, search_draws(state)), guardrails_config()["inventory_gate"])
    base = fe.state_of(fe.pf.s0)
    for f in np.linspace(0.5, 1.5, 21):
        b = float(round(u.budget * f))
        out["points"].append({"budget": b, "contribution": float(fe.dcaa(fe.with_move(base, i, b)).mean())})
    diag = u.curve.diagnostics or {}
    hold = diag.get("holdout") or {}
    out["reason"] = (f"{u.curve.status}; weight on own curve {u.curve.w:.2f}; holdout skill "
                     f"{hold.get('r2_P', float('nan')):.2f}; estimated, not causal (other budgets held fixed; SKU mix "
                     "and stock rationing included)")
    return out


# ---- creatives ----------------------------------------------------------------------------------------------------
def creative_fatigue(db) -> list[dict]:
    """The fatigue evidence module run on every campaign over the last 7 days (same gates as diagnosis)."""
    last = db.query("SELECT max(date) FROM marts.campaign_daily")[0][0]
    if last is None:
        return []
    names = dict(db.query("SELECT campaign_id, name FROM core.campaigns"))
    ads = {a: (n or h or a) for a, n, h in db.query("SELECT ad_id, name, headline FROM core.ads")}
    rows = []
    for cid, platform in db.query("SELECT campaign_id, platform FROM core.campaigns ORDER BY campaign_id"):
        ev = fatigue(db, Incident(f"FAT-{cid}", platform, [cid], None, "CTR", last - timedelta(days=6), last))
        for cr in (ev.values or {}).get("creatives", []):
            freq = cr.get("freq_rise")
            review = (cr.get("score") or 0) >= 0.4
            reason = (f"lifetime CTR {-cr['decline']:+.0%} vs its first 7 delivery days; 14-day slope t "
                      f"{cr.get('t_slope')}; specificity vs siblings "
                      f"{cr['specificity']:+.2f}" if cr.get("specificity") is not None else
                      f"lifetime CTR {-cr['decline']:+.0%}; fewer than 2 comparable siblings")
            if freq is None:
                reason += "; frequency not reported by this platform"
            rows.append({"creative_id": cr["ad_id"], "name": ads.get(cr["ad_id"], cr["ad_id"]),
                         "entity": names.get(cid, cid), "ctr_change": float(-cr["decline"]),
                         "frequency": float(max(1.0 + freq, 0.0)) if freq is not None else 0.0,
                         "status": "REVIEW" if review else "STABLE",
                         "reason": ("fatigue gates pass (evidence score "
                                    f"{cr['score']:.2f}): " if review else "") + reason})
    rows.sort(key=lambda r: (r["status"] != "REVIEW", r["ctr_change"]))
    return rows


# ---- learning ------------------------------------------------------------------------------------------------------
def calibration(db) -> dict:
    ups = []
    if v.has(db, "learn", "calibration_log"):
        ups = [{"outcome_id": o, "decision_id": o, "before": float(b), "after": float(a), "at": at.isoformat()}
               for o, b, a, at in db.query("SELECT outcome_id, factor_before, factor_after, applied_at FROM "
                                           "learn.calibration_log ORDER BY applied_at")]
    return {"factor": float(current_factor(db)), "updates": ups,
            "note": "Optimism correction factor (BUDGET_REALLOCATION family): updated exactly once per matured "
                    "SUCCESS / NEUTRAL / FAILED optimization outcome with a material positive prediction; "
                    "INCONCLUSIVE outcomes and cuts never update it."}


def accuracy(db) -> dict:
    rows = v.outcome_list(db)
    scored = [r for r in rows if r["class"] == "OPTIMIZATION" and r["verdict"] != "INCONCLUSIVE"]
    mae = float(np.mean([abs(r["measured"] - r["predicted"]) for r in scored])) if scored else None
    return {"sample_count": len(scored), "mae": mae,
            "note": f"Mean absolute error of the decision-time calibrated forecast vs the measured effect over each "
                    f"outcome window, on matured SUCCESS / NEUTRAL / FAILED optimization outcomes "
                    f"({len(rows) - len(scored)} inconclusive or safety outcomes excluded)."}


def feedback(db) -> list[dict]:
    out = []
    if not v.has(db, "learn", "outcomes"):
        return out
    for oid, did, cal in db.query("SELECT outcome_id, decision_id, calibration FROM learn.outcomes "
                                  "ORDER BY measured_at DESC"):
        c = json.loads(cal) if cal else None
        out.append({"outcome_id": oid, "decision_id": did, "eligible": bool(c and c.get("applied")),
                    "reason": "applied to the optimism correction factor" if c and c.get("applied") else
                              (c or {}).get("reason") or "safety outcomes are measured as avoided loss and never "
                                                         "calibrate response curves"})
    return out


# ---- models ---------------------------------------------------------------------------------------------------------
def _curve_registry(db):
    if not v.has(db, "models", "registry"):
        return []
    return db.query("SELECT version, role, fit_ts, validation_metrics, promoted_at, promotion_reason FROM "
                    "models.registry WHERE model = 'response_curve' ORDER BY fit_ts DESC")


def models(db) -> list[dict]:
    out = []
    for ver, role, fit_ts, metrics, _p, _r in _curve_registry(db):
        m = json.loads(metrics or "{}")
        out.append({"name": "response_curve", "version": ver,
                    "status": "CHAMPION" if role == "champion" else "NOT_AVAILABLE",
                    "trained_at": fit_ts.isoformat(),
                    "note": f"Hill + adstock per budget unit; {m.get('ok', 0)} of {m.get('units', 0)} units with "
                            "their own stable curve" + ("" if role == "champion" else f" (role {role})")})
    out.append({"name": "demand", "version": "seasonal-naive-v1", "status": "CHAMPION", "trained_at": None,
                "note": "Stage 1 demand model: seasonal-naive (last 7 days repeated); LightGBM is Stage 2"})
    return out


def model_detail(db, name: str, version: str) -> dict | None:
    if name == "demand" and version == "seasonal-naive-v1":
        return {"name": name, "version": version, "registry_revision": version, "role": "CHAMPION",
                "artifact_hash": None, "training_snapshot_hash": None, "trained_at": None, "rollback_version": None,
                "promotion_reason": "Stage 1 baseline demand model (spec §7.3)", "checks": [], "metrics": [],
                "allowed_actions": [], "note": "No trained artifact: the forecast is the last 7 days repeated."}
    if name != "response_curve":
        return None
    for ver, role, fit_ts, metrics, _promoted_at, reason in _curve_registry(db):
        if ver != version:
            continue
        m = json.loads(metrics or "{}")
        rows = db.query("SELECT status, artifact_sha256, diagnostics FROM models.response_curves WHERE fit_ts = ? "
                        "ORDER BY unit_id", [fit_ts])
        shas = "".join(r[1] for r in rows)
        import hashlib

        counts = {s: sum(1 for r in rows if r[0] == s) for s in ("OK", "POOLED", "MODEL_UNAVAILABLE")}
        fam = next((json.loads(r[2]).get("family_coverage_P") for r in rows if r[2]), None)
        return {"name": name, "version": ver, "registry_revision": ver,
                "role": {"champion": "CHAMPION", "candidate": "CANDIDATE"}.get(role, "RETIRED"),
                "artifact_hash": hashlib.sha256(shas.encode()).hexdigest(),
                "training_snapshot_hash": f"marts as of {fit_ts.isoformat()}", "trained_at": iso_z(fit_ts),
                "rollback_version": None, "promotion_reason": reason,
                "checks": [{"id": "FAMILY_COVERAGE", "label": "P10–P90 holdout coverage in [70%, 90%]",
                            "passed": fam is not None and 0.7 <= fam <= 0.9,
                            "detail": f"{fam:.0%}" if fam is not None else "not computed"},
                           {"id": "HOLDOUT_SKILL", "label": "Per-unit holdout skill ≥ 0 (else pooled fallback)",
                            "passed": True, "detail": f"{counts['OK']} own curves, {counts['POOLED']} pooled, "
                                                      f"{counts['MODEL_UNAVAILABLE']} unavailable"}],
                "metrics": [{"label": "Budget units with their own stable curve", "candidate": None,
                             "champion": float(counts["OK"]), "baseline": None, "unit": "count"},
                            {"label": "Units without any usable curve", "candidate": None,
                             "champion": float(counts["MODEL_UNAVAILABLE"]), "baseline": None, "unit": "count"},
                            {"label": "Family P10–P90 holdout coverage", "candidate": None,
                             "champion": float(fam) if fam is not None else None, "baseline": 0.8,
                             "unit": "fraction"}],
                "allowed_actions": [],
                "note": f"First champion of each fit (Stage 1 governance: criteria a + c). Champion/challenger "
                        f"promotion and rollback are Stage 2. Units: {m}."}
    return None


# ---- policy and objective -------------------------------------------------------------------------------------------
def _readiness(executed: int, measured: int, checks: list[dict], kind: str) -> dict:
    return {"eligible": False, "executed_decisions": executed, "measured_outcomes": measured,
            "independent_worlds": 1, "wilson_lower": None, "reliability": "UNAVAILABLE", "guardrail_violations": 0,
            "checks": checks,
            "note": f"{kind} autonomy qualification (Wilson bound over ≥3 warm-up worlds + held-out reliability) is "
                    "a later-stage feature; Stage 1 runs in Approve mode only."}


def policy(db, pol: dict) -> dict:
    channels = []
    tracking = set()
    if v.has(db, "intel", "anomalies"):
        tracking = {p for (p,) in db.query("SELECT DISTINCT platform FROM intel.anomalies WHERE classification = "
                                           "'tracking_issue' AND is_incident AND status <> 'resolved'") if p}
    unresolved = 0
    executed = {"meta": 0, "google": 0}
    if v.has(db, "exec", "saga_legs"):
        for p, n in db.query("SELECT platform, count(DISTINCT saga_id) FROM exec.saga_legs WHERE state = 'VERIFIED' "
                             "GROUP BY 1"):
            executed[p] = int(n)
        unresolved = int(db.query("SELECT count(*) FROM exec.saga_legs WHERE state IN ('UNKNOWN', 'CONFLICT')")[0][0])
    measured = len(v.outcome_list(db))
    for platform, label in (("meta", "Meta"), ("google", "Google")):
        checks = [{"id": "TRACKING_HEALTH", "label": "No open tracking incident", "passed": platform not in tracking,
                   "detail": "open tracking incident" if platform in tracking else "healthy"},
                  {"id": "EXECUTION_HEALTH", "label": "No unresolved execution leg", "passed": unresolved == 0,
                   "detail": f"{unresolved} UNKNOWN/CONFLICT legs" if unresolved else "healthy"}]
        prod = checks + [{"id": "CHAMPION_MODELS", "label": "Champion models pass their contracts", "passed": None,
                          "detail": "evaluated with production readiness (later stage)"},
                         {"id": "ADMIN_POLICY_REVIEW", "label": "Policy version reviewed by an admin", "passed": None,
                          "detail": "no admin review workflow in Stage 1"}]
        channels.append({"channel": label, "mode": "APPROVE", "execution_mode": "MOCK", "test_account": False,
                         "serves_ads": False, "allowed_modes": [],
                         "simulation": _readiness(executed[platform], measured, checks, "Simulation"),
                         "production": _readiness(0, 0, prod, "Production"),
                         "note": "Approve mode: every decision needs a manager's hash-bound approval; execution "
                                 "goes to the mock platform API (no automatic fallback)."})
    return {"policy_version": pol["policy_version"], "revision": pol["policy_version"], "channels": channels,
            "note": "Policy bundle = guardrails + objectives + data-health weights (config files, versioned by "
                    "content hash). Changing channel modes is a later-stage feature."}


def _flatten(d, prefix=""):
    out = {}
    for k, val in (d or {}).items():
        key = f"{prefix}{k}"
        if isinstance(val, dict):
            out |= _flatten(val, key + ".")
        else:
            out[key] = json.dumps(val)
    return out


def policy_history(db) -> dict:
    if not v.has(db, "ops", "policy_versions"):
        return {"status": "NOT_AVAILABLE", "note": "no policy version recorded yet", "versions": []}
    rows = db.query("SELECT policy_version, config, created_at FROM ops.policy_versions ORDER BY created_at")
    versions, prev = [], {}
    for ver, cfg, at in rows:
        flat = _flatten(json.loads(cfg))
        changes = [{"field": k, "before": prev.get(k, ""), "after": flat.get(k, "")}
                   for k in sorted(set(flat) | set(prev)) if prev.get(k) != flat.get(k)] if prev else []
        versions.append({"version": ver, "at": iso_z(at) or iso_z(datetime(2026, 10, 1, 12)), "actor": "config",
                         "reason": "policy bundle loaded from the versioned config files" if not prev else
                                   f"{len(changes)} setting(s) changed in the config files",
                         "changes": changes[:200]})
        prev = flat
    return {"status": "AVAILABLE", "note": "Every distinct policy bundle is recorded once by content hash; any "
                                           "change expires approved-but-unexecuted decisions.",
            "versions": list(reversed(versions))}


def objective(workspace: str) -> dict:
    return {"workspace_id": workspace, "objective": "PROFIT", "revision": "objective-PROFIT-stage1",
            "supported_objectives": ["PROFIT"], "can_change": False,
            "note": "Stage 1 optimizes PROFIT only (max E[ΔCAA] − 0.5 × downside). GROWTH, ACQUISITION, "
                    "INVENTORY_CLEARANCE, MARGIN_PROTECTION and BALANCED are later-stage features."}


# ---- decision replay, timeline, snapshot, archive -------------------------------------------------------------------
def timeline(db, decision_id: str) -> list[dict]:
    d = get_decision(db, decision_id)
    href = f"/decisions/{decision_id}"
    out = [{"id": f"snapshot-{decision_id}", "at": d["created_at"].isoformat(), "label": "Decision snapshot",
            "detail": f"{d['snapshot_id']} · hash {d['decision_hash'][:12]}…", "href": href}]
    for seq, event, at, actor, _det in db.query("SELECT seq, event, event_at, actor, details FROM ops.decision_events "
                                               "WHERE decision_id = ? ORDER BY seq", [decision_id]):
        out.append({"id": f"{decision_id}-{seq}", "at": at.isoformat(), "label": v.EVENT_TEXT.get(event, event),
                    "detail": f"by {actor}", "href": href})
    if v.has(db, "exec", "sagas"):
        for sid, kind, state, at in db.query("SELECT saga_id, kind, state, updated_at FROM exec.sagas WHERE "
                                             "decision_id = ? ORDER BY created_at", [decision_id]):
            n = db.query("SELECT count(*), count(*) FILTER (WHERE state = 'VERIFIED') FROM exec.saga_legs "
                         "WHERE saga_id = ?", [sid])[0]
            out.append({"id": sid, "at": at.isoformat(), "label": f"{kind.title()} {state}",
                        "detail": f"{n[1]} of {n[0]} legs verified by read-back", "href": "/executions"})
    for o in v.outcome_list(db):
        if o["decision_id"] == decision_id:
            out.append({"id": f"outcome-{decision_id}", "at": o["matured_at"], "label": f"Outcome {o['verdict']}",
                        "detail": f"measured {nar.inr(o['measured'], True)} vs forecast "
                                  f"{nar.inr(o['predicted'], True)}", "href": "/outcomes"})
            out.append({"id": f"calibration-{decision_id}", "at": o["matured_at"], "label": "Calibration",
                        "detail": (f"factor {o['factor_before']:.2f} → {o['factor_after']:.2f}"
                                   if o["calibration_applied"] else "factor unchanged"), "href": "/learning"})
    out.sort(key=lambda e: e["at"])
    return out


def snapshot(db, decision_id: str) -> dict:
    manifest, msha, env_fp = db.query("SELECT manifest, manifest_sha256, replay_environment_fingerprint FROM "
                                      "ops.decision_snapshots WHERE decision_id = ?", [decision_id])[0]
    names = ", ".join(f"{k} {h[:10]}…" for k, h in json.loads(manifest).items())
    return {"decision": v.decision_view(db, decision_id), "evidence": v.evidence_view(db, decision_id),
            "notice": f"Snapshot manifest {msha[:16]}… ({names}); replay environment {env_fp[:16]}…. Artifacts are "
                      "content-addressed and never overwritten; hidden world truth is never part of a snapshot."}


ARTIFACT_LABEL = {"state": "Portfolio state (units, curves with 200 draws, SKUs)", "flags": "Policy flags",
                  "policy": "Policy bundle", "calibration": "Optimism correction factor",
                  "cooldown": "Cooldown set", "kill_switch": "Kill switch", "manual_allocation": "Manual allocation"}


def archive(db, decision_id: str) -> dict:
    row = db.query("SELECT d.decision_hash, s.snapshot_id, s.manifest, s.replay_environment_fingerprint, s.code_sha, "
                   "s.lock_hash, s.seed FROM intel.decisions d JOIN ops.decision_snapshots s USING (decision_id) "
                   "WHERE decision_id = ?", [decision_id])[0]
    h, snap, manifest, env_fp, code, lock, seed = row
    adir = artifacts_dir(db)
    arts = [{"id": k, "kind": "snapshot_artifact", "label": ARTIFACT_LABEL.get(k, k), "hash": a, "href": None,
             "status": "PRESENT" if Path(adir, f"{a}.json").exists() else "MISSING"}
            for k, a in json.loads(manifest).items()]
    created = db.query("SELECT created_at FROM intel.decisions WHERE decision_id = ?", [decision_id])[0][0]
    steps = [{"id": "snapshot", "at": iso_z(created), "label": "Snapshot written",
              "detail": "inputs frozen as content-addressed artifacts", "artifact_id": "state"},
             {"id": "policy", "at": iso_z(created), "label": "Policy bundle frozen",
              "detail": "the replay never reads the current mutable policy", "artifact_id": "policy"}]
    for seq, event, at in db.query("SELECT seq, event, event_at FROM ops.decision_events WHERE decision_id = ? "
                                   "ORDER BY seq", [decision_id]):
        steps.append({"id": f"event-{seq}", "at": iso_z(at), "label": v.EVENT_TEXT.get(event, event),
                      "detail": "lifecycle event (append-only)", "artifact_id": None})
    missing = any(a["status"] == "MISSING" for a in arts)
    return {"decision_id": decision_id, "decision_hash": h, "snapshot_id": snap, "environment_fingerprint": env_fp,
            "code_sha": code, "lock_hash": lock, "seed": int(seed) if seed is not None else None,
            "status": "UNAVAILABLE" if missing or not (env_fp and code and lock) else "AVAILABLE",
            "note": "Replay re-runs portfolio_economics + optimizer + policy from these artifacts only. It runs in the "
                    "current code environment; replay inside an archived git worktree + lockfile is a later-stage "
                    "script (the environment identity above is what it would check out).",
            "artifacts": arts, "steps": steps}


def comparison(db, decision_id: str, state) -> dict:
    """Decision vs the do-nothing allocation, both valued by the same portfolio_economics. Naive heuristics
    (ROAS-rank, contribution-rank) and the sensitivity alternatives are later-stage features."""
    d = get_decision(db, decision_id)
    alloc = {u.unit_id: u.budget for u in state.units} | {leg["unit_id"]: leg["after"] for leg in d["legs"]}
    econ = Portfolio(state).evaluate(alloc).summary()
    hold = {"p10": 0.0, "p50": 0.0, "p90": 0.0, "prob_loss": 0.0, "delta_net_revenue": 0.0, "raw_pred": 0.0,
            "calibrated_pred": 0.0}
    est = {"p10": econ["P10"], "p50": econ["P50"], "p90": econ["P90"], "prob_loss": econ["prob_loss"],
           "delta_net_revenue": econ["delta_net_revenue"], "raw_pred": econ["E"],
           "calibrated_pred": d["expected"]["calibrated_pred"]}
    health = []
    if v.has(db, "ops", "data_health"):
        health = [s for (s,) in db.query("SELECT score FROM ops.data_health WHERE as_of = (SELECT max(as_of) FROM "
                                         "ops.data_health)")]
    curves = [u for u in state.units if u.unit_id in {leg["unit_id"] for leg in d["legs"]}]
    usable = sum(1 for u in curves if u.model_available) / max(len(curves), 1)
    return {"decision_id": decision_id, "decision_hash": d["decision_hash"], "status": "AVAILABLE",
            "strategies": [{"name": "Hold current allocation (safe-static)",
                            "allocated": float(sum(u.budget for u in state.units)), "estimate": hold,
                            "reason": "the do-nothing reference: zero change by definition"},
                           {"name": "ADAPT recommendation", "allocated": float(sum(alloc.values())), "estimate": est,
                            "reason": "the same portfolio_economics over all 200 joint bootstrap draws"}],
            "alternatives": [],
            "confidence": [{"label": "Data quality", "value": float(min(health) / 100) if health else 0.0,
                            "meaning": "lowest data-health score among the sources (0–1)"},
                           {"label": "Treated units with a usable curve", "value": float(usable),
                            "meaning": "share of this decision's budgets valued by an own or pooled curve"}],
            "note": "Model estimates on the current state, not realized results. ROAS-rank / contribution-rank "
                    "baselines, sensitivity alternatives and the calibrated confidence index are later-stage "
                    "features; held-out comparisons come from the evaluation report."}


def copilot_answer(db, message: str) -> dict:
    """Deterministic template answer (LLM offline): grounded in stored decisions, outcomes and incidents, with
    citations to app routes only."""
    msg = message.lower()
    decisions = v.decision_list(db)
    pending = [d for d in decisions if d["status"] == "PENDING_APPROVAL"]
    if any(w in msg for w in ("outcome", "learn", "calibrat", "factor", "verdict")):
        outs = v.outcome_list(db)
        text = (f"{len(outs)} measured outcome(s); optimism correction factor {current_factor(db):.2f}. " +
                (f"Latest: {outs[0]['verdict']}, measured {nar.inr(outs[0]['measured'], True)} vs forecast "
                 f"{nar.inr(outs[0]['predicted'], True)} (forecast-counterfactual)." if outs else
                 "No outcome has matured yet."))
        ev = [{"label": "Outcome records", "href": "/outcomes"}, {"label": "Calibration history", "href": "/learning"}]
    elif any(w in msg for w in ("incident", "anomal", "drop", "why", "fatigue", "driver")):
        an = [a for a in v.anomaly_list(db) if a["status"] != "RESOLVED"][:3]
        text = ("Open incidents: " + "; ".join(f"{a['title']} (probable driver: {a['driver']})" for a in an) + "."
                if an else "No open efficiency incidents.")
        ev = [{"label": "Anomalies", "href": "/anomalies"}]
    elif pending:
        d = pending[0]
        text = f"{d['title']}. {d['summary']}"
        ev = [{"label": f"Decision {d['decision_id']}", "href": f"/decisions/{d['decision_id']}"},
              {"label": "Source health", "href": "/data"}]
    else:
        text = "No decision is waiting for approval. Advance the world in the Scenario Lab to run the next cycle."
        ev = [{"label": "Scenario Lab", "href": "/scenarios"}]
    return {"text": text, "evidence": ev, "mode": "TEMPLATE"}
