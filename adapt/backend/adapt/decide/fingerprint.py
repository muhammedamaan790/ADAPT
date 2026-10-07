"""Dependency fingerprint and staleness (C3, spec §2, T34/T60/T69).

Three staleness classes over the dependency manifest (every unit, SKU and policy object portfolio_economics,
the optimizer and policy READ, not just the legs' entities):
- EXACT (any change expires): budgets, curve statuses, SKU unit economics (nrpu, contribution, safety stock),
  campaign_sku weights incl. __unmapped__ and the unmapped contribution rate, objective + lambda, guardrails,
  policy version, autonomy mode and kill switch -> hashed into economics_hash.
- TOLERANCE: available units per SKU (expire if they move > 10%) and each unit's inventory gate (expire if the
  label changes); without this class every sale would expire every approval.
- STATUS (expire on downgrade): open incidents on manifest campaigns; data health of the required sources.
Pacing and observed ROAS are NOT staleness inputs (they move every day and are re-estimated by the next run).
"""

from __future__ import annotations

from datetime import datetime

from adapt.decide.hashing import content_hash
from adapt.decide.optimizer import FastEval, gate_label, search_draws
from adapt.economics.portfolio import Portfolio, PortfolioState
from adapt.economics.state import guardrails_config, inputs_manifest, objectives_config

AVAILABLE_TOLERANCE = 0.10
HEALTH_RANK = {"GREEN": 0, "YELLOW": 1, "RED": 2}
DEPENDENCY_SOURCES = ("meta_ads", "google_ads", "store", "erp", "finance", "tiktok_ads", "amazon_ads",
                      "amazon_marketplace")


def _health(db, at: datetime) -> dict[str, str]:
    if not db.query("SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'ops' AND table_name = 'data_health'"):
        return {}
    return dict(db.query("""SELECT source, status FROM ops.data_health WHERE as_of <= ?
                            QUALIFY row_number() OVER (PARTITION BY source ORDER BY as_of DESC) = 1""", [at]))


def _incidents(db, campaign_ids: set[str]) -> list[str]:
    if not db.query("SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'intel' AND table_name = 'anomalies'"):
        return []
    rows = db.query("SELECT anomaly_id, entity_ids FROM intel.anomalies WHERE is_incident AND status <> 'resolved'")
    import json

    return sorted(a for a, ids in rows if set(json.loads(ids)) & campaign_ids)


def fingerprint(db, state: PortfolioState, policy_version: str, kill_switch: bool, at: datetime,
                objective: str = "PROFIT") -> dict:
    g = guardrails_config()
    oc = objectives_config()
    exact = {"inputs": inputs_manifest(state), "objective": objective, "objective_config": oc.get(objective, {}),
             "lambda": oc.get(objective, {}).get("lambda", oc["PROFIT"]["lambda"]),
             "guardrails": g, "policy_version": policy_version, "autonomy_mode": "APPROVE",
             "kill_switch": kill_switch}
    fe = FastEval(Portfolio(state, search_draws(state)), g["inventory_gate"])
    x = fe.exposure(fe.state_of(fe.pf.s0))
    tolerance = {"available": {k: s.available for k, s in sorted(state.skus.items())},
                 "gate": {u.unit_id: gate_label(float(x[i]), g["inventory_gate"]) for i, u in enumerate(state.units)}}
    cids = {c for u in state.units for c in u.campaign_ids}
    status = {"incidents": _incidents(db, cids), "health": {s: v for s, v in sorted(_health(db, at).items())
                                                           if s in DEPENDENCY_SOURCES}}
    return {"economics_hash": content_hash(exact), "exact": exact, "tolerance": tolerance, "status": status}


def staleness_diff(before: dict, now: dict) -> list[dict]:
    diff = []
    if before["economics_hash"] != now["economics_hash"]:
        b, n = before["exact"], now["exact"]
        changed = [k for k in sorted(set(b) | set(n)) if content_hash(b.get(k)) != content_hash(n.get(k))]
        if "inputs" in changed:
            bi, ni = b["inputs"], n["inputs"]
            for section in ("units", "skus"):
                for key in sorted(set(bi[section]) | set(ni[section])):
                    if content_hash(bi[section].get(key)) != content_hash(ni[section].get(key)):
                        diff.append({"class": "EXACT", "field": f"{section}.{key}",
                                     "before": bi[section].get(key), "after": ni[section].get(key)})
            changed.remove("inputs")
        diff += [{"class": "EXACT", "field": k, "before": b.get(k), "after": n.get(k)} for k in changed]
    for k, v0 in before["tolerance"]["available"].items():
        v1 = now["tolerance"]["available"].get(k)
        if v1 is None or abs(v1 - v0) > AVAILABLE_TOLERANCE * max(abs(v0), 1.0):
            diff.append({"class": "TOLERANCE", "field": f"available.{k}", "before": v0, "after": v1})
    for u, g0 in before["tolerance"]["gate"].items():
        g1 = now["tolerance"]["gate"].get(u)
        if g1 != g0:
            diff.append({"class": "TOLERANCE", "field": f"gate.{u}", "before": g0, "after": g1})
    new_inc = sorted(set(now["status"]["incidents"]) - set(before["status"]["incidents"]))
    if new_inc:
        diff.append({"class": "STATUS", "field": "incidents", "before": before["status"]["incidents"],
                     "after": now["status"]["incidents"]})
    for s, v0 in before["status"]["health"].items():
        v1 = now["status"]["health"].get(s, "RED")
        if HEALTH_RANK.get(v1, 2) > HEALTH_RANK.get(v0, 2):
            diff.append({"class": "STATUS", "field": f"health.{s}", "before": v0, "after": v1})
    return diff
