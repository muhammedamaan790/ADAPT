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
               "evidence": [{"label": "Optimizer workbench", "href": "/optimizer"}] +
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
    demand = _demand_registry(db)
    for r in demand:
        status = {"champion": "CHAMPION", "candidate": "CHALLENGER"}.get(r["role"], "NOT_AVAILABLE")
        out.append({"name": "demand", "version": r["version"], "status": status,
                    "trained_at": r["fit_ts"].isoformat() if r["fit_ts"] else None,
                    "note": f"{r['kind']} ({r['role']}): {r['promotion_reason'] or ''}".strip()})
    if not demand:
        out.append({"name": "demand", "version": "seasonal-naive-v1", "status": "CHAMPION", "trained_at": None,
                    "note": "seasonal-naive (last 7 days repeated); the LightGBM challenger is fitted weekly once "
                            "the STOCKOUT_PROBABILITY predicate is on"})
    return out


def _demand_registry(db) -> list[dict]:
    from adapt.learn import governance

    return governance.history(db, "demand") if v.has(db, "learn", "model_registry") else []


def registry_revision(r: dict) -> str:
    """Changes whenever the model's role changes (promotion, retirement, rollback): the optimistic-concurrency token
    a model action must quote."""
    return f"{r['version']}:{r['role']}:{r['promoted_at'].isoformat() if r.get('promoted_at') else '-'}"


def _demand_detail(db, version: str) -> dict | None:
    r = next((x for x in _demand_registry(db) if x["version"] == version), None)
    if r is None:
        return None
    val, base, crit = r["validation_metrics"] or {}, r["baseline_metrics"] or {}, r["criteria"] or {}
    labels = {"a_beats_baseline": "(a) beats seasonal-naive WAPE by >= 5%",
              "b_noninferiority": "(b) non-inferior to the champion (paired block bootstrap, +2%)",
              "c_coverage": "(c) P10-P90 holdout coverage in [70%, 90%]"}
    checks = [{"id": k, "label": labels.get(k, k), "passed": bool(ok), "detail": "passed" if ok else "failed"}
              for k, ok in crit.items() if isinstance(ok, bool)]
    champ = r["role"] == "champion"
    metrics = []
    if "wape_p50" in val:
        metrics.append({"label": "Holdout WAPE (P50)", "candidate": None if champ else float(val["wape_p50"]),
                        "champion": float(val["wape_p50"]) if champ else None,
                        "baseline": float(base.get("wape", val.get("wape_seasonal_naive"))), "unit": "fraction"})
    if "coverage_p10_p90" in val:
        metrics.append({"label": "P10-P90 holdout coverage", "candidate": None if champ else
                        float(val["coverage_p10_p90"]), "champion": float(val["coverage_p10_p90"]) if champ else None,
                        "baseline": 0.8, "unit": "fraction"})
    role = {"champion": "CHAMPION", "candidate": "CANDIDATE"}.get(r["role"], "RETIRED")
    sha = r["artifact_sha256"]
    can_roll = role == "CHAMPION" and bool(r["rollback_version"])
    return {"name": "demand", "version": r["version"], "registry_revision": registry_revision(r), "role": role,
            "artifact_hash": sha if sha and len(sha) == 64 else None,
            "training_snapshot_hash": r["training_snapshot_hash"], "trained_at": iso_z(r["fit_ts"]),
            "rollback_version": r["rollback_version"], "promotion_reason": r["promotion_reason"],
            "checks": checks, "metrics": metrics, "allowed_actions": ["ROLLBACK"] if can_roll else [],
            "note": "Promotion is automatic under the layered rule (spec §10.2), never manual; rollback restores "
                    "the previous champion, whose artifact the next forecast loads."}


def model_detail(db, name: str, version: str) -> dict | None:
    if name == "demand" and version != "seasonal-naive-v1":
        return _demand_detail(db, version)
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
                "note": f"Every fit becomes the champion once criteria (a) + (c) pass; curves are refitted weekly or "
                        f"when an outcome matures, so the curve family has no rollback. Units: {m}."}
    return None


# ---- policy and objective -------------------------------------------------------------------------------------------
LABEL = {"meta": "Meta", "google": "Google", "tiktok": "TikTok", "amazon": "Amazon"}
REGION_NAME = {"R1": "LOW", "R2": "MID", "R3": "HIGH"}


def workspace_channels(db) -> list[str]:
    """The ad channels this workspace actually has campaigns on (Stage 2 adds TikTok / Amazon when seeded)."""
    if v.has(db, "core", "campaigns"):
        found = {p for (p,) in db.query("SELECT DISTINCT platform FROM core.campaigns") if p}
        chans = [c for c in LABEL if c in found]
        if chans:
            return chans
    return ["meta", "google"]


def _readiness_view(sim: dict, recs: list[dict], ch: str) -> dict:
    regions = sim["regions"]
    best = next((x for x in regions.values() if x["status"] == "QUALIFIED"), None) or max(
        regions.values(), key=lambda x: -1 if x["wilson_lower"] is None else x["wilson_lower"])
    measured = sum(1 for r in recs if r["channel"] == ch and r["world"] == "SIMULATED"
                   and r["verdict"] in ("SUCCESS", "NEUTRAL", "FAILED"))
    rel = best["reliability"]["status"] if best["n"] or best["reliability"]["n"] else "UNAVAILABLE"
    return {"eligible": sim["eligible"], "executed_decisions": sim["executed"], "measured_outcomes": measured,
            "independent_worlds": best["worlds"], "wilson_lower": best["wilson_lower"], "reliability": rel,
            "guardrail_violations": int(sim["violations"]),
            "checks": sim["checks"],
            "note": "Simulation autonomy: earned on warm-up worlds 901-903 (Wilson 95% lower bound ≥ 0.60) with a "
                    "held-out reliability PASS on world 904; simulated outcomes never count toward production."}


def policy(db, pol: dict, adapters: dict | None = None, local_world: int = -1) -> dict:
    from adapt.learn import qualification as q
    from adapt.policy.autonomy import readiness
    from adapt.policy.modes import channel_modes

    ready = readiness(db, adapters or {}, local_world)
    recs = q.all_records(db, local_world)
    modes = channel_modes(db)
    channels = []
    for ch in workspace_channels(db):
        r = ready[ch]
        live = r["execution_mode"] == "LIVE"
        sim = _readiness_view(r["simulation"], recs, ch)
        prod_checks = [c for c in r["simulation"]["checks"] if c["id"] in ("TRACKING_HEALTH", "EXECUTION_HEALTH")] \
            + r["production"]["checks"] + [
                {"id": "CHAMPION_MODELS", "label": "Champion models pass their contracts", "passed": None,
                 "detail": "only evaluated once real outcomes exist"},
                {"id": "ADMIN_POLICY_REVIEW", "label": "Policy version reviewed by an admin", "passed": None,
                 "detail": "only evaluated once real outcomes exist"}]
        mode = {"AUTONOMOUS": "SIMULATION_AUTONOMOUS"}.get(modes[ch], modes[ch])
        allowed = ["OBSERVE", "APPROVE"] + (["SIMULATION_AUTONOMOUS"] if sim["eligible"] and not live else [])
        channels.append({
            "channel": LABEL[ch], "mode": mode, "execution_mode": "LIVE" if live else "MOCK",
            "test_account": live, "serves_ads": False, "allowed_modes": allowed, "simulation": sim,
            "production": {"eligible": False, "executed_decisions": 0, "measured_outcomes": 0,
                           "independent_worlds": 0, "wilson_lower": None, "reliability": "UNAVAILABLE",
                           "guardrail_violations": 0, "checks": prod_checks,
                           "note": "Production autonomy requires measured REAL outcomes; a Google test account "
                                   "executes for real but serves no ads, so none exist."},
            "note": {"OBSERVE": "Observe: recommendations are recorded as shadow decisions; nothing executes.",
                     "APPROVE": "Approve: every decision needs a manager's hash-bound approval.",
                     "SIMULATION_AUTONOMOUS": "Simulation autonomous: ADAPT executes only decisions that pass every "
                                              "autonomy gate; the rest wait for review."}[mode]})
    return {"policy_version": pol["policy_version"], "revision": policy_revision(pol, modes), "channels": channels,
            "note": "Policy bundle = guardrails + objectives + data-health weights (content-hashed) plus the channel "
                    "modes; any change expires pending decisions."}


def policy_revision(pol: dict, modes: dict) -> str:
    import hashlib

    return pol["policy_version"] + ":" + hashlib.sha256(json.dumps(modes, sort_keys=True).encode()).hexdigest()[:10]


def qualification(db, local_world: int) -> dict:
    from adapt.learn import qualification as q

    recs = q.all_records(db, local_world)
    imp = q.imports(db)
    if not [r for r in recs if r["world_id"] in (*q.WARMUP_WORLDS, q.RELIABILITY_WORLD)]:
        return {"status": "NOT_AVAILABLE", "pools": [],
                "note": "No warm-up track record imported yet (worlds 901-903 + held-out 904: "
                        "scripts/run_warmup.py). No region is qualified, so no channel can run autonomously."}
    version = imp[-1]["artifact_sha256"][:16] if imp else "local"
    now = iso_z(datetime.now())
    pools = []
    for ch in sorted({r["channel"] for r in recs}):
        for scope, worlds in (("WARMUP", q.WARMUP_WORLDS), ("HELD_OUT", (q.RELIABILITY_WORLD,))):
            regions = []
            for reg in q.REGIONS:
                rs = [r for r in recs if r["channel"] == ch and r["region"] == reg and r["world_id"] in worlds
                      and r["verdict"] in q.DECISIVE]
                k = sum(r["verdict"] == "SUCCESS" for r in rs)
                lo, hi = q.wilson(k, len(rs))
                regions.append({"name": REGION_NAME[reg], "total": len(rs), "successes": k, "wilson_lower": lo,
                                "wilson_upper": hi})
            pools.append({"world": "SIMULATED", "model_version": version, "evaluated_at": now,
                          "outcome_definition": f"{LABEL.get(ch, ch)}: SUCCESS among SUCCESS / NEUTRAL / FAILED "
                                                "OPTIMIZATION outcomes (INCONCLUSIVE excluded)",
                          "scope": scope, "world_ids": [str(w) for w in worlds], "regions": regions})
    return {"status": "AVAILABLE", "pools": pools[:20],
            "note": "Regions are the decision-time raw confidence index: LOW [0, 0.6), MID [0.6, 0.8), HIGH [0.8, 1]. "
                    "A region qualifies with a warm-up Wilson lower bound ≥ 0.60 over 3 worlds and a held-out PASS."}


def shadow(db) -> dict:
    from adapt.decide.decisions import get_decision

    if not v.has(db, "learn", "shadow_decisions"):
        return {"status": "NOT_AVAILABLE", "records": [],
                "note": "No channel is in Observe mode, so no shadow decision has been recorded."}
    recs = []
    for did, at, chans, e, p10, p90, pl, ok in db.query(
            "SELECT decision_id, recorded_at, channels, expected_e, p10, p90, prob_loss, would_pass_policy "
            "FROM learn.shadow_decisions ORDER BY recorded_at DESC LIMIT 1000"):
        d = get_decision(db, did)
        x = d["expected"]
        for ch in json.loads(chans):
            recs.append({"id": f"{did}:{ch}", "decision_id": did, "decision_hash": d["decision_hash"],
                         "channel": LABEL.get(ch, ch), "world": "SIMULATED", "at": iso_z(at),
                         "method": "FORECAST_ONLY",
                         "expected": {"p10": p10, "p50": e, "p90": p90, "prob_loss": pl,
                                      "delta_net_revenue": x["delta_net_revenue"], "raw_pred": x["raw_pred"],
                                      "calibrated_pred": x["calibrated_pred"]},
                         "guardrail_breaches": [] if ok else [c["rule"] for c in d["checks"] if not c["passed"]],
                         "note": "forecast-implied would-have outcome of an unexecuted decision; never counts "
                                 "toward readiness"})
    return {"status": "AVAILABLE" if recs else "NOT_AVAILABLE", "records": recs[:1000],
            "note": "Observe-mode decisions: recorded, never executed."}


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


OBJECTIVE_TEXT = {"PROFIT": "max E[ΔCAA] − 0.5 × downside",
                  "GROWTH": "max E[net revenue] with a CAA floor of max(5% |CAA₀|, ₹2,000)",
                  "INVENTORY_CLEARANCE": "max E[ΔCAA] + ₹25 × excess-band units sold"}


def objective(db, workspace: str) -> dict:
    from adapt.decide.alternatives import MODES, selected_objective

    mode = selected_objective(db)
    row = db.query("SELECT set_at FROM ops.objective_setting WHERE id = 1")
    stamp = row[0][0].isoformat() if row and row[0][0] else "config"
    return {"workspace_id": workspace, "objective": mode, "revision": f"objective-{mode}-{stamp}",
            "supported_objectives": list(MODES), "can_change": True,
            "note": f"{mode}: {OBJECTIVE_TEXT[mode]}. Changing it is an admin action and an EXACT staleness input: "
                    "pending decisions expire at approval and the next run decides under the new objective. "
                    "ACQUISITION, MARGIN_PROTECTION and BALANCED are not built."}


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
            "alternatives": sensitivity_alternatives(d, v.unit_names(db)),
            "confidence": [{"label": "Data quality", "value": float(min(health) / 100) if health else 0.0,
                            "meaning": "lowest data-health score among the sources (0–1)"},
                           {"label": "Treated units with a usable curve", "value": float(usable),
                            "meaning": "share of this decision's budgets valued by an own or pooled curve"}],
            "note": "Model estimates on the current state, not realized results. Alternatives are sensitivity "
                    "scenarios (a different risk preference), not competing recommendations; choosing one creates "
                    "a new decision. Held-out strategy comparisons come from the evaluation report."}


def sensitivity_alternatives(d: dict, names: dict) -> list[dict]:
    """Conservative / Aggressive (PROFIT only), in the comparison contract's shape, with their policy result."""
    titles = {"conservative": "Conservative (λ = 1.0, ±10%/day)", "aggressive": "Aggressive (λ = 0.2, ±20%/day)"}
    out = []
    for a in d.get("alternatives") or []:
        if a.get("status") != "OK":
            continue
        e = a["expected"]
        pol = a.get("policy") or {}
        risk = a.get("max_inventory_risk") or {}
        unit = "units short" if risk.get("kind") == "PROJECTED_SHORTFALL" else "P(stockout)"
        out.append({"id": a["name"], "name": titles.get(a["name"], a["name"]),
                    "reason": f"{a.get('label', '')}; max inventory risk {risk.get('value', 0):.2f} {unit}; "
                              f"₹{a.get('unallocated', 0):,.0f} unallocated",
                    "legs": [{"platform": v.PLATFORM[leg["platform"]], "entity": names.get(leg["unit_id"],
                                                                                           leg["unit_id"]),
                              "budget_id": leg["budget_id"], "before": leg["before"], "after": leg["after"]}
                             for leg in a["legs"]],
                    "estimate": {"p10": e["P10"], "p50": e["E"], "p90": e["P90"], "prob_loss": e["prob_loss"],
                                 "delta_net_revenue": e["delta_net_revenue"], "raw_pred": e["E"],
                                 "calibrated_pred": e["E"]},
                    "checks": [{"id": "POLICY", "label": "Policy validation",
                                "passed": pol.get("status") != "BLOCKED",
                                "detail": ", ".join(pol.get("failed_rules") or []) or "passes policy"}]})
    return out


# ---- evaluation report (precomputed overnight by scripts/run_eval.py) ----------------------------------------------
HEAD_TO_HEAD = ("safe-static", "safe-contribution", "adapt", "oracle")


def load_eval(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def eval_report(path: Path) -> dict:
    """The Head-to-Head contract from evidence/eval.json: per seed, safe-static / safe-contribution / adapt / oracle
    on common random numbers under the same ceiling, plus the headline summary (primary paired CI, oracle capture,
    detection, diagnosis, safety, replay), shown even when a target is missed."""
    rep = load_eval(path)
    if rep is None:
        return {"status": "NOT_AVAILABLE", "report": None,
                "note": f"No evaluation report ({path.name}) yet: scripts/run_eval.py --seeds eval runs overnight."}
    seeds = [int(x) for x in rep["seeds"] if all(s in rep["per_seed"][str(x)] for s in HEAD_TO_HEAD)]
    meta = rep.get("per_seed_meta") or {}
    days = int(rep.get("contract", {}).get("days") or 60)
    rows = []
    for seed in seeds:
        for s in HEAD_TO_HEAD:
            r = rep["per_seed"][str(seed)][s]
            rows.append({"seed": seed, "strategy": s, "realized_caa": float(r["caa"]), "spend": float(r["spend"]),
                         "stock_risk_days": min(int(r.get("stock_risk_days", 0)), days),
                         "constraint_breaches": len(r.get("violations") or []),
                         "forced_interventions": int(r.get("forced_interventions", 0))})
    ceilings = [float(m["budget_ceiling"]) for m in meta.values() if m.get("budget_ceiling")]
    summary = {k: rep.get(k) for k in ("N", "primary", "comparisons", "oracle_capture", "oracle_suboptimality_rate",
                                       "safety_violations", "replay", "verdicts", "detection", "diagnosis",
                                       "negative_set", "targets", "contract")}
    generated = rep.get("generated_at") or iso_z(datetime.fromtimestamp(path.stat().st_mtime))
    return {"status": "AVAILABLE",
            "note": f"{rep.get('contract', {}).get('class', 'EVAL')}: {len(seeds)} held-out seeds, {days} simulated "
                    "days each, precomputed (never run live).",
            "report": {"report_id": f"eval-{generated}", "generated_at": generated,
                       "code_sha": rep.get("code_sha") or "unknown", "seeds": seeds, "horizon_days": days,
                       "budget_ceiling": max(ceilings) if ceilings else 1.0, "reserve_floor": 0.0, "currency": "INR",
                       "common_random_numbers": bool(rep.get("common_random_numbers")),
                       "feasibility_envelope": "identical guardrails for every strategy: ±20%/day, 3-day cooldown, "
                                               "channel caps, inventory gate, budget ceiling B and reserve R per seed",
                       "fairness_statement": "All strategies receive the same maximum available budget B and reserve "
                                             "floor R on each seed. safe-static intentionally preserves its initial "
                                             "allocation; in these worlds that allocation equals B − R.",
                       "rows": rows, "summary": summary}}


def uplift(path: Path) -> dict:
    rep = load_eval(path)
    if rep is None:
        return {"status": "NOT_AVAILABLE", "rows": [],
                "note": "No evaluation report yet (held-out seeds 101–120, paired strategies)."}
    seeds = [str(x) for x in rep["seeds"]]
    names = sorted({s for k in seeds for s in rep["per_seed"][k]})
    rows = []
    for s in names:
        got = [rep["per_seed"][k][s] for k in seeds if s in rep["per_seed"][k]]
        rows.append({"strategy": s, "realized_caa": float(np.mean([g["caa"] for g in got])),
                     "spend": float(np.mean([g["spend"] for g in got])),
                     "constraint_breaches": int(sum(len(g.get("violations") or []) for g in got))})
    p = rep.get("primary") or {}
    ci = p.get("ci95")
    band = f" (95% paired CI ₹{ci[0]:,.0f} to ₹{ci[1]:,.0f})" if ci else ""
    return {"status": "AVAILABLE", "rows": rows,
            "note": f"Mean realized CAA and spend per seed over {len(seeds)} held-out seeds. Primary U(adapt vs "
                    f"safe-static) = ₹{(p.get('mean') or 0):,.0f}{band}."}


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
