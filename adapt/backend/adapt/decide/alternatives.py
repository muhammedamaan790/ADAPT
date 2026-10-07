"""Sensitivity analysis (Stage 2, spec §8.3 "Decision vs. sensitivity analysis") and the selected objective.

The Recommended allocation is THE decision: the optimum under the selected objective (default PROFIT, lambda 0.5).
Conservative (lambda 1.0, +-10%/day) and Aggressive (lambda 0.2, +-20%/day, the full feasible budget up to B - R
used while marginal value > 0) are risk-preference SCENARIOS, re-solved with the same optimizer, the same feasible
set and the same policy validation; ADAPT never compares utilities across lambdas to call one "best". Each scenario
reports E[dCAA], P10, model P(loss), max inventory risk (stage kind) and its policy result. Choosing one creates a
new decision that supersedes the recommended one, with its own snapshot and hash (choose_alternative).
Only PROFIT has a risk preference; other objectives get no lambda scenarios (stated in the output).

The selected objective is an admin policy setting (ops.objective_setting, GET/PUT /objective): an EXACT staleness
input, so changing it expires every pending decision.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime

from adapt.economics.state import guardrails_config, objectives_config

DDL = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.objective_setting (
    id INTEGER PRIMARY KEY, mode VARCHAR NOT NULL, set_at TIMESTAMP, actor VARCHAR
);
"""
MODES = ("PROFIT", "GROWTH", "INVENTORY_CLEARANCE")


def selected_objective(db) -> str:
    db.write(lambda cur: cur.execute(DDL))
    row = db.query("SELECT mode FROM ops.objective_setting WHERE id = 1")
    return row[0][0] if row else objectives_config().get("selected", "PROFIT")


def set_objective(db, mode: str, actor: str, role: str, at: datetime) -> dict:
    if role != "admin":
        raise PermissionError("objective changes require the admin role (spec §9.5)")
    if mode not in MODES:
        raise ValueError(f"objective {mode} is not built; Stage 2 offers {', '.join(MODES)}")

    def work(cur):
        cur.execute(DDL)
        cur.execute("INSERT OR REPLACE INTO ops.objective_setting VALUES (1, ?, ?, ?)", [mode, at, actor])

    db.write(work)
    return {"mode": mode, "set_at": at.isoformat(), "actor": actor}


def objectives_for(mode: str, base: dict | None = None) -> dict:
    oc = copy.deepcopy(base or objectives_config())
    oc["selected"] = mode
    return oc


def sensitivity(state, flags, guardrails: dict | None = None, objectives: dict | None = None,
                portfolios=None, cooldown: set[str] | None = None, kill_switch: bool = False) -> list[dict]:
    """Conservative / Aggressive scenarios for a PROFIT decision (each validated by the policy engine)."""
    from adapt.decide.optimizer import Optimizer
    from adapt.policy.engine import policy_result, validate

    oc = objectives or objectives_config()
    if oc.get("selected", "PROFIT") != "PROFIT":
        return []
    g0 = guardrails or guardrails_config()
    out = []
    for name in ("conservative", "aggressive"):
        p = oc["sensitivity"][name]
        g = copy.deepcopy(g0)
        g["change"]["max_daily_change_pct"] = min(float(p["max_daily_change_pct"]),
                                                  float(g0["change"]["max_daily_change_pct"]))
        r = Optimizer(state, flags, guardrails=g, objectives=oc, mode="PROFIT", lam=float(p["lambda"]),
                      portfolios=portfolios).solve()
        if r.get("status") != "OK":
            out.append({"name": name, "status": r.get("status")})
            continue
        alloc = {u.unit_id: u.budget for u in state.units} | {leg["unit_id"]: leg["after"] for leg in r["legs"]}
        checks = validate(state, alloc, flags, "OPTIMIZATION", g0, cooldown or set(), kill_switch)
        risk = r["inventory_risk_after"]
        key = "shortfall" if risk["kind"] == "PROJECTED_SHORTFALL" else "stockout_probability"
        out.append({
            "name": name, "status": "OK", "lambda": float(p["lambda"]),
            "max_daily_change_pct": g["change"]["max_daily_change_pct"],
            "legs": [{k: leg[k] for k in ("unit_id", "platform", "channel", "budget_id", "campaign_ids", "before",
                                          "after")} for leg in r["legs"]],
            "expected": {"E": r["expected"]["E"], "P10": r["expected"]["P10"], "P90": r["expected"]["P90"],
                         "prob_loss": r["expected"]["prob_loss"],
                         "delta_net_revenue": r["expected"]["delta_net_revenue"]},
            "max_inventory_risk": {"kind": risk["kind"], "value": max((v.get(key, 0.0)
                                                                      for v in risk["by_sku"].values()), default=0.0)},
            "unallocated": r["unallocated"], "policy": policy_result(checks, "OPTIMIZATION"),
            "label": "sensitivity scenario (a different risk preference), not a competing recommendation"})
    return out


def choose_alternative(db, decision_id: str, name: str, actor: str, role: str, at: datetime) -> str:
    """The user picks a sensitivity scenario: a NEW decision (own snapshot + hash) superseding the recommended one,
    under that explicit risk preference. Only from a decision still awaiting approval."""
    from adapt.decide import decisions as dec
    from adapt.decide.snapshot import get_artifact, state_from_dict

    if role not in dec.ROLES_THAT_APPROVE:
        raise dec.DecisionError("FORBIDDEN", f"role {role} cannot choose a risk preference")
    d = dec.get_decision(db, decision_id)
    if d["status"] != "PENDING_APPROVAL":
        raise dec.DecisionError("CONFLICT", f"decision is {d['status']}, not PENDING_APPROVAL")
    alt = next((a for a in d.get("alternatives", []) if a.get("name") == name and a.get("status") == "OK"), None)
    if alt is None:
        raise dec.DecisionError("NOT_FOUND", f"no executable alternative {name} on {decision_id}")
    manifest = json.loads(db.query("SELECT manifest FROM ops.decision_snapshots WHERE decision_id = ?",
                                   [decision_id])[0][0])
    state = state_from_dict(get_artifact(db, manifest["state"]))
    flags = get_artifact(db, manifest["flags"])
    factor = get_artifact(db, manifest["calibration"])["factor"]
    new_id = f"{decision_id}:{name.upper()[:4]}"
    exp = alt["expected"]
    run = {"run_id": db.query("SELECT run_id FROM intel.decisions WHERE decision_id = ?", [decision_id])[0][0],
           "calibration_factor": factor, "safety": [],
           "result": {"status": "OK", "decision_id": new_id, "legs": alt["legs"], "objective": "PROFIT",
                      "lambda": alt["lambda"], "risk_preference": name,
                      "expected": {**exp, "P50": exp["E"], "raw_pred": exp["E"]}, "why_not": [],
                      "inventory_risk_after": {"kind": alt["max_inventory_risk"]["kind"], "by_sku": {}},
                      "unallocated": alt["unallocated"], "reserve_floor": d.get("reserve_floor", 0.0),
                      "alternatives": []}}
    created = dec.create_decisions(db, run, state, flags, at, actor=actor, supersedes=decision_id)
    return created[0] if created else new_id
