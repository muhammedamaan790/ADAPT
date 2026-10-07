"""View models for the Stage 1 API (C6): engine state -> the frontend's wire contracts (api/models.py).

Everything is read from what the engine already stored (decisions, snapshots, runs, sagas, outcomes, marts); nothing
analytical is recomputed per request except what-if valuation, which calls the same portfolio_economics.
Mapping notes that are contract decisions (also in docs/contracts/api_stage1.md):
- leg platform `meta`/`google` -> `Meta`/`Google`; entity = the budget's campaign name(s)
- inventory_risk_after.by_sku = projected shortfall units per SKU (Stage 1 kind PROJECTED_SHORTFALL)
- cost_of_inaction_7d = model-estimated contribution forgone by not acting (max(E[dCAA], 0) of the decision); the
  spec's anomaly-baseline definition needs the pre-anomaly path and is Stage 2
- trigger.anomaly_id = the open incident with the largest impact on the decision's campaigns; opportunity_id = run
"""

from __future__ import annotations

import json
from datetime import timedelta

import numpy as np

from adapt.decide import narrative as nar
from adapt.decide.decisions import get_decision
from adapt.learn.calibration import current_factor

PROVENANCE = ["PUBLIC-SAMPLE", "CALIBRATED", "SIMULATED"]
PLATFORM = {"meta": "Meta", "google": "Google"}
KIND = {"efficiency_anomaly": "EFFICIENCY", "tracking_issue": "TRACKING", "budget_change": "BUDGET_CHANGE"}
ANOMALY_STATUS = {"detected": "OPEN", "investigating": "OPEN", "acknowledged": "ACKNOWLEDGED",
                  "resolved": "RESOLVED"}
LEVEL = {"STRONG_EVIDENCE": "PROBABLE DRIVER", "WEAK_EVIDENCE": "WEAK EVIDENCE"}


def has(db, schema: str, table: str) -> bool:
    return bool(db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
                         [schema, table]))


def unit_names(db) -> dict[str, str]:
    if not has(db, "core", "campaigns"):
        return {}
    out: dict[str, list[str]] = {}
    for bid, name in db.query("SELECT budget_id, name FROM core.campaigns ORDER BY campaign_id"):
        out.setdefault(bid, []).append(name)
    return {b: (n[0] if len(n) == 1 else f"Shared budget: {' + '.join(n)}") for b, n in out.items()}


def latest_run(db) -> tuple | None:
    if not has(db, "ops", "pipeline_runs"):
        return None
    row = db.query("SELECT run_id, as_of, summary FROM ops.pipeline_runs WHERE status = 'COMPLETED' "
                   "ORDER BY as_of DESC LIMIT 1")
    return row[0] if row else None


def optimizer_result(db, run_id: str | None) -> dict:
    if run_id is None or not has(db, "intel", "optimizer_runs"):
        return {}
    row = db.query("SELECT result FROM intel.optimizer_runs WHERE run_id = ?", [run_id])
    return json.loads(row[0][0]) if row else {}


def _campaign_of_entities(db, ids: list[str]) -> set[str]:
    out = set(ids)
    if has(db, "core", "ads") and ids:
        marks = ", ".join("?" * len(ids))
        out |= {c for (c,) in db.query(f"SELECT DISTINCT campaign_id FROM core.ads WHERE ad_id IN ({marks})", ids)}
    return out


def related_incident(db, campaign_ids: list[str]) -> str | None:
    if not has(db, "intel", "anomalies"):
        return None
    best = None
    for aid, ids, impact in db.query("SELECT anomaly_id, entity_ids, signed_impact FROM intel.anomalies "
                                     "WHERE is_incident AND status <> 'resolved'"):
        if _campaign_of_entities(db, json.loads(ids)) & set(campaign_ids):
            if best is None or abs(impact or 0) > best[1]:
                best = (aid, abs(impact or 0))
    return best[0] if best else None


def evidence_ids(db, anomaly_id: str | None) -> list[str]:
    if anomaly_id is None or not has(db, "intel", "evidence"):
        return []
    return [e for (e,) in db.query("""SELECT evidence_id FROM intel.evidence WHERE anomaly_id = ? AND as_of =
                                      (SELECT max(as_of) FROM intel.evidence WHERE anomaly_id = ?) ORDER BY 1""",
                                   [anomaly_id, anomaly_id])]


# ---- decisions ------------------------------------------------------------------------------------------------------
def decision_view(db, decision_id: str, names: dict | None = None) -> dict:
    names = names if names is not None else unit_names(db)
    d = get_decision(db, decision_id)
    run_id, follows = db.query("SELECT run_id, supersedes FROM intel.decisions WHERE decision_id = ?",
                               [decision_id])[0]
    pv = db.query("SELECT policy_version FROM ops.decision_snapshots WHERE decision_id = ?", [decision_id])[0][0]
    res = optimizer_result(db, run_id.split("-mod-")[0] if run_id else None)
    cids = sorted({c for leg in d["legs"] for c in leg["campaign_ids"]})
    anomaly = related_incident(db, cids)
    e = d["expected"]
    why = []
    for w in d.get("why_not", []):
        rule, text, metric = nar.why_not_text(w, names)
        why.append({"entity": names.get(w["unit_id"], w["unit_id"]), "rule_id": rule, "reason": text,
                    "metric": metric})
    risk = d["inventory_risk_after"]
    return {
        "decision_id": decision_id, "title": nar.decision_title(d, names), "summary": nar.decision_summary(d, names),
        "class": d["class"], "type": d["type"], "objective": d.get("objective", "PROFIT"), "status": d["status"],
        "trigger": {"anomaly_id": anomaly, "opportunity_id": run_id},
        "legs": [{"platform": PLATFORM[leg["platform"]], "entity": names.get(leg["unit_id"], leg["unit_id"]),
                  "budget_id": leg["budget_id"], "before": leg["before"], "after": leg["after"]} for leg in d["legs"]],
        "expected": {"p10": e["p10"], "p50": e["p50"], "p90": e["p90"], "prob_loss": e["prob_loss"],
                     "delta_net_revenue": e["delta_net_revenue"], "raw_pred": e["raw_pred"],
                     "calibrated_pred": e["calibrated_pred"]},
        "inventory_risk_after": {"kind": "PROJECTED_SHORTFALL",
                                 "by_sku": {k: float(v.get("shortfall", 0.0)) if isinstance(v, dict) else float(v)
                                            for k, v in risk.get("by_sku", {}).items()}},
        "unallocated": max(float(d.get("unallocated") or 0.0), 0.0),
        "reserve_floor": float(d.get("reserve_floor") or 0.0),
        "budget_ceiling": float(res.get("total_budget") or sum(leg["before"] for leg in d["legs"])),
        "cost_of_inaction_7d": max(float(e["E"]), 0.0),
        "checks": [{"id": c["rule"], "label": nar.CHECK_LABEL.get(c["rule"], c["rule"]), "passed": c["passed"],
                    "detail": c["detail"]} for c in d["checks"]],
        "evidence_ids": evidence_ids(db, anomaly), "why_not": why, "snapshot_id": d["snapshot_id"],
        "decision_hash": d["decision_hash"], "policy_version": pv, "valuation_status": "AVAILABLE",
        "follows": follows, "provenance_inputs": PROVENANCE, "created_at": d["created_at"].isoformat(),
        "horizon_days": 7,
    }


def decision_list(db) -> list[dict]:
    if not has(db, "intel", "decisions"):
        return []
    names = unit_names(db)
    return [decision_view(db, did, names) for (did,) in
            db.query("SELECT decision_id FROM intel.decisions ORDER BY created_at DESC, decision_id")]


# ---- evidence -------------------------------------------------------------------------------------------------------
def evidence_view(db, decision_id: str) -> dict:
    d = get_decision(db, decision_id)
    cids = sorted({c for leg in d["legs"] for c in leg["campaign_ids"]})
    anomaly = related_incident(db, cids)
    last = db.query("SELECT max(date) FROM marts.campaign_daily")[0][0]
    marks = ", ".join("?" * len(cids))
    rows = db.query(f"""SELECT date, sum(attributed_net_revenue), sum(spend) FROM marts.campaign_daily
                        WHERE campaign_id IN ({marks}) AND date > ? GROUP BY 1 ORDER BY 1""",
                    [*cids, last - timedelta(days=56)])
    chart = []
    for i in range(28, len(rows)):
        same_wd = [rows[i - 7 * k][1] for k in range(1, 5)]
        chart.append({"date": rows[i][0].isoformat(), "actual": float(rows[i][1]),
                      "baseline": float(np.mean(same_wd))})
    out = {"decision_id": decision_id, "chart": chart,
           "chart_metric": "Attributed net revenue of the decision's campaigns (baseline: same weekday, prior 4 weeks)",
           "decomposition_kind": "NOT_APPLICABLE", "decomposition": [], "decomposition_total": 0.0, "drivers": []}
    if anomaly is None or not has(db, "intel", "decompositions"):
        return out
    dec_row = db.query("SELECT funnel, as_of FROM intel.decompositions WHERE anomaly_id = ? ORDER BY as_of DESC "
                       "LIMIT 1", [anomaly])
    if dec_row:
        funnel = json.loads(dec_row[0][0])
        contrib = funnel.get("contributions") or {}
        if funnel.get("status") == "OK" and contrib:
            out["decomposition_kind"] = "ROAS"
            out["decomposition"] = [{"label": f"{k} (signed log contribution)", "value": float(v)}
                                    for k, v in contrib.items()]
            out["decomposition_total"] = float(sum(contrib.values()))
            out["drivers"].append({
                "id": f"{anomaly}-funnel", "title": "Funnel decomposition (exact)", "score": 1.0,
                "level": "ACCOUNTING IDENTITY",
                "detail": "Δln ROAS = Δln CTR + Δln CVR + Δln AOV − Δln CPM; contributions sum exactly.",
                "observations": [f"{k}: {float(v):+.3f}" for k, v in contrib.items()],
                "source": "marts.campaign_daily", "available_at": dec_row[0][1].isoformat()})
    diag = db.query("SELECT ranking, as_of FROM intel.diagnoses WHERE anomaly_id = ? ORDER BY as_of DESC LIMIT 1",
                    [anomaly])
    if diag:
        ranking = json.loads(diag[0][0])
        for drv in ranking.get("drivers", []):
            level = LEVEL.get(drv.get("level"))
            if level is None:
                continue  # NOT_SUPPORTED / NOT_ASSESSABLE are listed by the anomaly view, not as drivers
            out["drivers"].append({
                "id": f"EVD-{anomaly}-{drv['module']}", "title": drv.get("label", drv["module"]),
                "score": float(min(max(drv.get("score", 0.0), 0.0), 1.0)), "level": level,
                "detail": f"Evidence score {drv.get('score', 0):.2f} (diagnostic strength, not causal confidence).",
                "observations": [f"levers: {', '.join(drv.get('levers', []))}"] +
                                ([f"signed contribution {drv['signed_contribution']:+.3f}"]
                                 if drv.get("signed_contribution") is not None else []),
                "source": "intel.evidence", "available_at": diag[0][1].isoformat()})
    return out


# ---- executions, ledger, outcomes, events ----------------------------------------------------------------------------
def _legs_of(db, saga_id: str) -> list[tuple]:
    return db.query("""SELECT leg_id, platform, mode, budget_id, expected_before, desired_after, observed_after, state,
                              sim_sync_state, attempts, updated_at, error, kind FROM exec.saga_legs
                       WHERE saga_id = ? ORDER BY seq""", [saga_id])


def execution_list(db) -> list[dict]:
    if not has(db, "exec", "sagas"):
        return []
    names = unit_names(db)
    out = []
    for sid, did, kind, state, created, _reason in db.query(
            "SELECT saga_id, decision_id, kind, state, created_at, reason FROM exec.sagas ORDER BY created_at DESC"):
        legs = _legs_of(db, sid)
        errors = [e for *_x, e, _k in legs if e]
        out.append({"execution_id": sid, "decision_id": did, "state": state,
                    "legs": [{"platform": PLATFORM[p], "entity": names.get(b, b), "budget_id": b, "before": before,
                              "after": after, "mode": mode or "MOCK", "external_state": st,
                              "sim_sync_state": sync or "NOT_REQUIRED", "read_back_budget": ob}
                             for _lid, p, mode, b, before, after, ob, st, sync, _a, _u, _e, _k in legs],
                    "started_at": created.isoformat(),
                    "detail": f"{kind.title()} saga {state}" + (f": {errors[0]}" if errors else "")})
    return out


def ledger_list(db) -> list[dict]:
    if not has(db, "exec", "saga_legs"):
        return []
    names = unit_names(db)
    out = []
    for lid, sid, did, kind, p, mode, b, before, after, ob, st, att, upd, err in db.query(
            """SELECT leg_id, saga_id, decision_id, kind, platform, mode, budget_id, expected_before, desired_after,
                      observed_after, state, attempts, updated_at, error FROM exec.saga_legs ORDER BY updated_at"""):
        base = {"execution_id": sid, "decision_id": did, "budget_id": b, "entity": names.get(b, b),
                "platform": PLATFORM[p], "mode": mode or "MOCK", "at": upd.isoformat()}
        action = "RESTORE_SETTINGS" if kind == "ROLLBACK" else "SET_BUDGET"
        if att:
            out.append({**base, "ledger_id": f"{lid}:send", "action": action, "before": before, "after": after,
                        "request_id": f"{lid}:a{att}", "note": f"{att} attempt(s); absolute set"})
        if st == "VERIFIED":
            out.append({**base, "ledger_id": f"{lid}:verify", "action": "VERIFY", "before": before,
                        "after": ob if ob is not None else after, "request_id": f"{lid}:read",
                        "note": "read-back confirms the requested budget"})
        if st in ("RECONCILED_VERIFIED", "ABANDONED"):
            out.append({**base, "ledger_id": f"{lid}:reconcile", "action": "RECONCILE", "before": before,
                        "after": ob if ob is not None else before, "request_id": f"{lid}:reconcile",
                        "note": err or st})
    return out


def outcome_list(db) -> list[dict]:
    if not has(db, "learn", "outcomes"):
        return []
    out = []
    for oid, did, cls, at, verdict, method, realized, cf_caa, _obs, raw_w, cal_json in db.query(
            """SELECT outcome_id, decision_id, class, measured_at, verdict, method, realized, counterfactual_caa,
                      observed_caa, raw_pred_window, calibration FROM learn.outcomes ORDER BY measured_at DESC"""):
        cal = json.loads(cal_json) if cal_json else None
        f_dec = json.loads(db.query("SELECT payload FROM intel.decisions WHERE decision_id = ?", [did])[0][0])[
            "expected"].get("optimism_correction_factor", 0.9)
        before = cal.get("factor_before", f_dec) if cal and cal.get("applied") else f_dec
        after = cal.get("factor_after", before) if cal and cal.get("applied") else before
        out.append({"outcome_id": oid, "decision_id": did, "world": "SIMULATED", "class": cls, "verdict": verdict,
                    "predicted": float(raw_w * f_dec if raw_w > 0 else raw_w), "measured": float(realized),
                    "counterfactual": float(cf_caa), "factor_before": float(before), "factor_after": float(after),
                    "matured_at": at.isoformat(), "method": method,
                    "calibration_applied": bool(cal and cal.get("applied"))})
    return out


EVENT_TEXT = {"created": "Decision created", "submitted": "Waiting for approval", "blocked": "Blocked by policy",
              "approved": "Approved", "rejected": "Rejected", "expired": "Expired: inputs changed",
              "superseded": "Superseded by a newer proposal"}


def event_list(db, limit: int = 100) -> list[dict]:
    ev = []
    if has(db, "ops", "decision_events"):
        for did, seq, event, at, actor in db.query(
                "SELECT decision_id, seq, event, event_at, actor FROM ops.decision_events"):
            ev.append({"id": f"{did}:{seq}", "at": at.isoformat(), "kind": f"decision_{event}",
                       "message": f"{EVENT_TEXT.get(event, event)} ({actor})", "decision_id": did})
    if has(db, "ops", "events"):
        for eid, typ, etype, ent, at, payload in db.query(
                "SELECT event_id, type, entity_type, entity_id, event_at, payload FROM ops.events"):
            p = json.loads(payload or "{}")
            did = ent if etype == "decision" else None
            msg = {"action_executed": f"Budget {ent} set to {nar.inr(p.get('after'))}/day and verified",
                   "outcome_matured": f"Outcome matured: {p.get('verdict')}"}.get(typ, f"{typ} on {etype} {ent}")
            ev.append({"id": eid, "at": at.isoformat(), "kind": typ, "message": msg, "decision_id": did})
    if has(db, "ops", "pipeline_runs"):
        for rid, as_of, st in db.query("SELECT run_id, as_of, status FROM ops.pipeline_runs"):
            ev.append({"id": rid, "at": as_of.isoformat(), "kind": "pipeline_run",
                       "message": f"Pipeline run {rid} {st.lower()}", "decision_id": None})
    if has(db, "exec", "sagas"):
        for sid, did, kind, state, at in db.query(
                "SELECT saga_id, decision_id, kind, state, updated_at FROM exec.sagas"):
            ev.append({"id": f"{sid}:{state}", "at": at.isoformat(), "kind": "execution",
                       "message": f"{kind.title()} {state}", "decision_id": did})
    ev.sort(key=lambda e: e["at"], reverse=True)
    return ev[:limit]


# ---- anomalies -------------------------------------------------------------------------------------------------------
def _anomaly_row_view(db, row, names_by_campaign) -> dict | None:
    (aid, scope, key, ids, platform, metric, direction, cls, status_, ws, we, actual, expected, rel, impact,
     stat, z, shift, collapse, first_at) = row
    kind = KIND.get(cls)
    if kind is None or platform not in PLATFORM:
        return None
    ids = json.loads(ids)
    entity = names_by_campaign.get(key, key) if scope == "campaign" else f"{scope} {key}"
    diag = db.query("SELECT top_driver, top_level FROM intel.diagnoses WHERE anomaly_id = ? ORDER BY as_of DESC "
                    "LIMIT 1", [aid]) if has(db, "intel", "diagnoses") else []
    driver = (diag[0][0] or "UNKNOWN") if diag else "NOT_DIAGNOSED"
    reason = None
    if has(db, "ops", "anomaly_status_log"):
        r = db.query("SELECT reason FROM ops.anomaly_status_log WHERE anomaly_id = ? "
                     "ORDER BY logged_at DESC LIMIT 1", [aid])
        reason = r[0][0] if r else None
    camps = _campaign_of_entities(db, ids)
    decision = None
    if has(db, "intel", "decisions"):
        for (did, payload) in db.query("SELECT decision_id, payload FROM intel.decisions ORDER BY created_at DESC"):
            if camps & {c for leg in json.loads(payload)["legs"] for c in leg["campaign_ids"]}:
                decision = did
                break
    return {
        "anomaly_id": aid,
        "title": f"{metric} {direction.lower()} {abs(rel or 0):.0%} on {entity}" if rel is not None else
                 f"{metric} {direction.lower()} on {entity}",
        "entity": entity, "platform": PLATFORM[platform], "metric": metric, "kind": kind,
        "status": ANOMALY_STATUS.get(status_, "OPEN"), "direction": direction, "actual": float(actual or 0),
        "baseline": float(expected or 0), "change": float(rel or 0), "impact": float(impact or 0),
        "impact_label": "contribution (CBA) impact over the window", "detected_at": first_at.isoformat(),
        "decision_id": decision, "driver": driver, "provenance_inputs": PROVENANCE,
        "gates": [{"id": "STAT", "label": "Robust z vs out-of-sample forecast", "passed": bool(stat),
                   "detail": f"max |z| {z:.1f}" if z is not None else "not assessable"},
                  {"id": "SHIFT", "label": "Change point", "passed": bool(shift), "detail": ""},
                  {"id": "MATERIAL", "label": "Material contribution impact", "passed": True,
                   "detail": f"{nar.inr(impact)} over {ws.isoformat()}..{we.isoformat()}"},
                  {"id": "COLLAPSE", "label": "Collapse state", "passed": not bool(collapse),
                   "detail": "a numerator or denominator reached zero" if collapse else ""}],
        "causal": {"status": "NOT_ESTIMABLE", "reason": "the synthetic-control estimator is Stage 2; Stage 1 shows "
                   "exact accounting and evidence-scored probable drivers only", "effect_pct": None,
                   "lower_pct": None, "upper_pct": None, "assumptions": []},
        "resolution_reason": reason,
    }


ANOMALY_COLS = ("anomaly_id, scope, entity_key, entity_ids, platform, metric, direction, classification, status, "
                "window_start, window_end, actual, expected, relative_change, signed_impact, stat_fired, max_abs_z, "
                "shift_fired, collapse, first_detected_at")


def anomaly_list(db) -> list[dict]:
    if not has(db, "intel", "anomalies"):
        return []
    names = dict(db.query("SELECT campaign_id, name FROM core.campaigns")) if has(db, "core", "campaigns") else {}
    rows = db.query(f"SELECT {ANOMALY_COLS} FROM intel.anomalies ORDER BY abs(signed_impact) DESC NULLS LAST")
    return [v for v in (_anomaly_row_view(db, r, names) for r in rows) if v is not None]


def anomaly_view(db, anomaly_id: str) -> dict | None:
    names = dict(db.query("SELECT campaign_id, name FROM core.campaigns")) if has(db, "core", "campaigns") else {}
    rows = db.query(f"SELECT {ANOMALY_COLS} FROM intel.anomalies WHERE anomaly_id = ?", [anomaly_id])
    return _anomaly_row_view(db, rows[0], names) if rows else None


# ---- overview --------------------------------------------------------------------------------------------------------
def _window(db, last, days, offset=0):
    lo, hi = last - timedelta(days=offset + days - 1), last - timedelta(days=offset)
    b = db.query("SELECT sum(net_revenue), sum(spend), sum(cba), sum(caa) FROM marts.brand_daily "
                 "WHERE date BETWEEN ? AND ?", [lo, hi])[0]
    a = db.query("SELECT sum(attributed_cba), sum(spend) FROM marts.campaign_daily WHERE date BETWEEN ? AND ?",
                 [lo, hi])[0]
    return {"net_revenue": b[0] or 0.0, "spend": b[1] or 0.0, "cba": b[2] or 0.0, "caa": b[3] or 0.0,
            "att_cba": a[0] or 0.0, "att_spend": a[1] or 0.0}


def _rel(now, prev):
    return None if prev in (None, 0) or now is None else float(now / prev - 1)


def overview_view(db, workspace: str, world: dict, scenario: str) -> dict:
    from adapt.api.routers.data import SOURCE_META, _freshness_text
    from adapt.ingest.connectors.base import sources_config

    run = latest_run(db)
    as_of = run[1].isoformat() if run else ""
    last = db.query("SELECT max(date) FROM marts.brand_daily")[0][0] if has(db, "marts", "brand_daily") else None
    metrics, series = [], []
    if last is not None:
        cur, prev = _window(db, last, 7), _window(db, last, 7, 7)
        mer = lambda w: w["net_revenue"] / w["spend"] if w["spend"] else None  # noqa: E731
        poas = lambda w: w["att_cba"] / w["att_spend"] if w["att_spend"] else None  # noqa: E731
        res = optimizer_result(db, _run_opt_id(db, run[0])) if run else {}
        risk = [k for k, v in (res.get("inventory_risk_after") or {}).get("by_sku", {}).items()
                if v.get("shortfall", 0) > 0]
        spec = [("net_revenue", "Net revenue (7d)", cur["net_revenue"], "money", _rel(cur["net_revenue"],
                 prev["net_revenue"]), "GMV − discounts − refunds (tax excluded)", "marts.brand_daily", None),
                ("spend", "Ad spend (7d)", cur["spend"], "money", _rel(cur["spend"], prev["spend"]),
                 "Σ platform spend (INR)", "marts.brand_daily", None),
                ("caa", "Contribution after ads (7d)", cur["caa"], "money", _rel(cur["caa"], prev["caa"]),
                 "CBA − ad spend", "marts.brand_daily", None),
                ("mer", "MER (7d)", mer(cur), "ratio", _rel(mer(cur), mer(prev)), "net revenue / spend",
                 "marts.brand_daily", None if cur["spend"] else "ZERO_DENOMINATOR"),
                ("poas", "POAS (7d)", poas(cur), "ratio", _rel(poas(cur), poas(prev)),
                 "attributed CBA / spend", "marts.campaign_daily", None if cur["att_spend"] else "ZERO_DENOMINATOR"),
                ("inventory_risk_skus", "SKUs with projected shortfall", float(len(risk)), "count", None,
                 "count of SKUs with projected shortfall > 0 over 7 days (Stage 1)", "intel.optimizer_runs", None)]
        metrics = [{"key": k, "label": lab, "value": None if v is None else float(v), "format": f, "change": ch,
                    "reason": r, "formula": form, "source": src, "available_at": as_of or last.isoformat(),
                    "provenance_inputs": PROVENANCE} for k, lab, v, f, ch, form, src, r in spec]
        rows = db.query("SELECT date, net_revenue FROM marts.brand_daily WHERE date > ? ORDER BY date",
                        [last - timedelta(days=56)])
        for i in range(28, len(rows)):
            series.append({"date": rows[i][0].isoformat(), "actual": float(rows[i][1] or 0),
                           "baseline": float(np.mean([rows[i - 7 * k][1] or 0 for k in range(1, 5)]))})
    sources = []
    if has(db, "ops", "data_health"):
        cfg = sources_config()["sources"]
        for src, newest, age, score, st in db.query(
                """SELECT source, newest_date, age_hours, score, status FROM ops.data_health
                   WHERE as_of = (SELECT max(as_of) FROM ops.data_health) ORDER BY source"""):
            sources.append({"id": src, "name": SOURCE_META[src][0], "kind": SOURCE_META[src][1], "score": score,
                            "status": st, "freshness": _freshness_text(newest, age),
                            "provenance": cfg[src]["provenance"]})
    decisions = decision_list(db)
    pending = [d for d in decisions if d["status"] == "PENDING_APPROVAL"]
    executing = [d for d in decisions if d["status"] == "EXECUTING"]
    attention = []
    for a in anomaly_list(db):
        if a["status"] != "RESOLVED" and a["kind"] != "BUDGET_CHANGE":
            attention.append({"id": a["anomaly_id"], "decision_id": a["decision_id"], "kind": "incident",
                              "title": a["title"], "description": f"Probable driver: {a['driver']}",
                              "impact": abs(a["impact"]), "label": "Incident"})
    for d in pending:
        attention.append({"id": d["decision_id"], "decision_id": d["decision_id"],
                          "kind": "inventory" if d["class"] == "SAFETY" else "opportunity", "title": d["title"],
                          "description": d["summary"], "impact": abs(d["expected"]["p50"]),
                          "label": "Review" if d["class"] == "SAFETY" else "Decision"})
    outs = outcome_list(db)
    for o in outs[:3]:
        attention.append({"id": o["outcome_id"], "decision_id": o["decision_id"], "kind": "outcome",
                          "title": f"Outcome {o['verdict']}", "description": o["method"],
                          "impact": abs(o["measured"]), "label": "Outcome"})
    attention.sort(key=lambda x: -x["impact"])
    counts = {"success": 0.0, "neutral": 0.0, "failed": 0.0, "inconclusive": 0.0}
    for o in outs:
        counts[o["verdict"].lower()] += 1
    factor = current_factor(db)
    incidents = sum(1 for a in attention if a["kind"] == "incident")
    loop_state = ("Approve" if pending else "Execute" if executing else
                  "Measure" if any(e["state"] in ("SUCCEEDED", "ACCEPTED_PARTIAL") for e in execution_list(db))
                  and not outs else "Data")
    labels = ["Data", "Detect", "Diagnose", "Decide", "Approve", "Execute", "Measure", "Learn"]
    cur_i = labels.index(loop_state)
    loop = [{"label": lab, "state": "complete" if i < cur_i else "current" if i == cur_i else "waiting"}
            for i, lab in enumerate(labels)] if loop_state != "Data" else \
        [{"label": lab, "state": "complete"} for lab in labels]
    verdicts = {k.upper(): int(v) for k, v in counts.items()}
    return {"workspace": workspace, "decision_ts": as_of, "world_day": int(world.get("day", 0)),
            "scenario": scenario, "brief": nar.brief(int(world.get("day", 0)), incidents,
                                                     [{"expected": {"E": d["expected"]["p50"]}} for d in pending],
                                                     len(executing), verdicts, factor),
            "metrics": metrics, "sources": sources, "attention": attention[:12], "series": series, "loop": loop,
            "calibration": float(factor), "counts": counts}


def _run_opt_id(db, pipeline_run_id: str) -> str | None:
    """The optimizer run produced by a pipeline run: same logical as_of (never parsed out of decision ids)."""
    if not has(db, "intel", "optimizer_runs"):
        return None
    row = db.query("""SELECT o.run_id FROM intel.optimizer_runs o JOIN ops.pipeline_runs p ON p.as_of = o.as_of
                      WHERE p.run_id = ? ORDER BY o.run_id LIMIT 1""", [pipeline_run_id])
    if not row:
        row = db.query("SELECT run_id FROM intel.optimizer_runs ORDER BY as_of DESC LIMIT 1")
    return row[0][0] if row else None
