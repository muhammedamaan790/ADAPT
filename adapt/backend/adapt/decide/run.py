"""One optimizer run as of a logical time (B7): state -> policy flags -> PROFIT optimum + S3 safety candidates ->
calibrated prediction -> persisted run, plus each proposal's frozen measurement basis for outcome measurement (B8).

Flags that fix or cap units come from the same canonical/ops state the policy engine (C4) will read:
- DATA_DEPENDENCY: a required source (the unit's ad platform, store, ERP, finance) is YELLOW or RED -> fixed
- TRACKING_FREEZE: an open tracking_issue incident on the unit's platform -> fixed
- EXECUTION_FREEZE / SAFETY_COOLDOWN: open rows in ops.entity_freezes (created by the saga, C5) -> fixed
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta

import numpy as np

from adapt.decide.optimizer import Optimizer
from adapt.decide.safety import safety_candidates
from adapt.economics.portfolio import Portfolio
from adapt.economics.state import load_state
from adapt.learn.calibration import FAMILY, calibrate, current_factor
from adapt.predict.forecasts import residual_pool

PLATFORM_SOURCE = {"meta": "meta_ads", "google": "google_ads", "tiktok": "tiktok_ads", "amazon": "amazon_ads"}
COMMON_SOURCES = ("store", "erp", "finance")
MEASUREMENT_DAYS = 14  # outcome windows mature by day 14 at the latest (spec §10)

DDL = """
CREATE SCHEMA IF NOT EXISTS intel;
CREATE SCHEMA IF NOT EXISTS learn;
CREATE TABLE IF NOT EXISTS intel.optimizer_runs (
    run_id VARCHAR PRIMARY KEY, as_of TIMESTAMP NOT NULL, objective VARCHAR NOT NULL, status VARCHAR NOT NULL,
    calibration_factor DOUBLE, result JSON NOT NULL, safety JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS learn.measurement_basis (
    decision_id VARCHAR PRIMARY KEY, run_id VARCHAR NOT NULL, decision_ts TIMESTAMP NOT NULL, class VARCHAR NOT NULL,
    family VARCHAR, raw_pred DOUBLE, calibrated_pred DOUBLE, basis JSON NOT NULL
);
"""


def _table_exists(db, schema: str, table: str) -> bool:
    return bool(db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
                         [schema, table]))


def policy_flags(db, state, as_of: datetime) -> dict[str, list[str]]:
    flags: dict[str, list[str]] = {}
    health = {}
    if _table_exists(db, "ops", "data_health"):
        health = dict(db.query("""SELECT source, status FROM ops.data_health WHERE as_of <= ?
                                  QUALIFY row_number() OVER (PARTITION BY source ORDER BY as_of DESC) = 1""", [as_of]))
    tracking = set()
    if _table_exists(db, "intel", "anomalies"):
        tracking = {p for (p,) in db.query("""SELECT DISTINCT platform FROM intel.anomalies
            WHERE classification = 'tracking_issue' AND is_incident AND status <> 'resolved'
              AND last_detected_at >= ?""", [as_of - timedelta(days=7)]) if p}
    frozen: dict[str, list[str]] = {}
    if _table_exists(db, "ops", "entity_freezes"):
        for eid, bid, reason in db.query("SELECT entity_id, budget_id, reason FROM ops.entity_freezes "
                                         "WHERE cleared_at IS NULL"):
            for key in {eid, bid} - {None}:
                frozen.setdefault(key, []).append(
                    "EXECUTION_FREEZE" if reason == "EXECUTION_UNCERTAINTY" else reason)
    for u in state.units:
        r = []
        deps = [PLATFORM_SOURCE.get(u.platform), *COMMON_SOURCES]
        if any(health.get(d) in ("YELLOW", "RED") for d in deps if d):
            r.append("DATA_DEPENDENCY")
        if u.platform in tracking:
            r.append("TRACKING_FREEZE")
        for key in [u.unit_id, *u.campaign_ids]:
            r += frozen.get(key, [])
        if r:
            flags[u.unit_id] = sorted(set(r))
    return flags


def oos_residual_pool(unit) -> list[float]:
    if unit.curve is not None:
        pool = unit.curve.diagnostics.get("oos_rel_residuals")
        if pool:
            return [float(x) for x in pool]
    return [0.0]


def measurement_basis(state, allocation: np.ndarray, legs: list[dict], db=None) -> dict:
    """Frozen at decision time: no-action counterfactual revenue paths (all joint draws, the pre-action budgets) and
    the predicted delta path, over MEASUREMENT_DAYS, for the treated units; their contribution margin per rupee of
    attributed net revenue, pacing, and out-of-sample relative residual pools (spec §10 forecast counterfactual):
    the 56 days of rolling-origin residuals of the stored daily forecasts when available, else the curve candidate's
    P + D out-of-sample errors (labelled, so the outcome says which pool it used)."""
    st14 = replace(state, horizon=MEASUREMENT_DAYS)
    pf = Portfolio(st14)
    treated = sorted({leg["unit_id"] for leg in legs})
    econ = pf.evaluate(allocation)
    units = {}
    for uid in treated:
        i = pf.unit_index[uid]
        u = state.units[i]
        pool = residual_pool(db, uid, u.campaign_ids, state.as_of.date()) if db is not None else None
        units[uid] = {"campaign_ids": u.campaign_ids, "pre_budget": float(pf.s0[i]), "new_budget": float(allocation[i]),
                      "pacing": float(pf.pacing[i]), "cm": float(pf.cm[i]),
                      "cf_revenue": pf.R0[:, i, :].round(2).tolist(),
                      "residual_pool": pool if pool is not None else oos_residual_pool(u),
                      "residual_source": "rolling_origin_forecasts" if pool is not None else
                      ((u.curve.diagnostics.get("oos_model") or "none") if u.curve is not None else "none")}
    return {"days": MEASUREMENT_DAYS, "units": units,
            "pred_daily_delta_caa": econ.daily_delta_caa.mean(0).tolist(),
            # the safety monitor's reference (spec §9.3): the 10th percentile of the cumulative dCAA draws per day
            "p10_cum_daily_delta_caa": np.percentile(np.cumsum(econ.daily_delta_caa, axis=1), 10, axis=0).tolist(),
            "total_budget": float(pf.s0.sum())}


def run_optimizer(db, as_of: datetime, flags: dict | None = None, persist: bool = True) -> dict:
    from adapt.decide.alternatives import objectives_for, selected_objective, sensitivity, start_sensitivity
    from adapt.policy.engine import cooldown_units
    from adapt.policy.locks import kill_switch_active

    state = load_state(db, as_of)
    flags = policy_flags(db, state, as_of) if flags is None else flags
    oc = objectives_for(selected_objective(db))
    started = start_sensitivity(state, flags, objectives=oc)  # scenario solves run in the pool meanwhile
    opt = Optimizer(state, flags, objectives=oc)
    result = opt.solve()
    safety = safety_candidates(opt) if result.get("status") == "OK" else []
    if result.get("status") == "OK":
        result["alternatives"] = sensitivity(state, flags, objectives=oc, portfolios=(opt.pf, opt.full),
                                             cooldown=cooldown_units(db, as_of), kill_switch=kill_switch_active(db),
                                             started=started)
    else:
        for fut in started.values():
            fut.cancel()
    factor = current_factor(db)
    run_id = "opt-" + hashlib.sha256(f"{as_of.isoformat()}|{json.dumps(result.get('allocation'), sort_keys=True)}"
                                     .encode()).hexdigest()[:12]
    if result.get("status") == "OK":
        result["expected"]["calibrated_pred"] = calibrate(result["expected"]["raw_pred"], factor)
        result["expected"]["optimism_correction_factor"] = factor
        result["decision_id"] = f"{run_id}-R"
        result["class"] = "OPTIMIZATION"
        for k, cand in enumerate(safety):
            cand["decision_id"] = f"{run_id}-S{k}"
    out = {"run_id": run_id, "as_of": as_of.isoformat(), "result": result, "safety": safety,
           "calibration_factor": factor, "flags": flags}
    if persist:
        _persist(db, as_of, state, opt, out)
    out["state"] = state  # in-memory only (decision creation snapshots it); never persisted from here
    return out


def persist_basis(db, decision_id: str, run_id: str, decision_ts: datetime, cls: str, raw_pred: float,
                  calibrated_pred: float, state, allocation: dict, legs: list[dict]) -> None:
    """Freeze the measurement basis of a decision created outside an optimizer run (a user modification)."""
    s = np.array([float(allocation.get(u.unit_id, u.budget)) for u in state.units])
    basis = measurement_basis(state, s, legs, db)

    def work(cur):
        cur.execute(DDL)
        cur.execute("INSERT OR REPLACE INTO learn.measurement_basis VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [decision_id, run_id, decision_ts, cls, FAMILY if cls == "OPTIMIZATION" else None, raw_pred,
                     calibrated_pred, json.dumps(basis, default=float)])

    db.write(work)


def _persist(db, as_of: datetime, state, opt: Optimizer, out: dict) -> None:
    result, safety = out["result"], out["safety"]
    proposals = []
    if result.get("status") == "OK" and result["legs"]:
        s = np.array([result["allocation"][u.unit_id] for u in state.units])
        proposals.append((result["decision_id"], "OPTIMIZATION", FAMILY, result["expected"]["raw_pred"],
                          result["expected"]["calibrated_pred"], measurement_basis(state, s, result["legs"], db)))
    for cand in safety:
        s = opt.s0.copy()
        for leg in cand["legs"]:
            s[opt.pf.unit_index[leg["unit_id"]]] = leg["after"]
        proposals.append((cand["decision_id"], "SAFETY", None, cand["expected"]["E"], cand["expected"]["E"],
                          measurement_basis(state, s, cand["legs"], db)))

    def work(cur):
        cur.execute(DDL)
        cur.execute("INSERT OR REPLACE INTO intel.optimizer_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [out["run_id"], as_of, result.get("objective") or "PROFIT", result.get("status"),
                     out["calibration_factor"],
                     json.dumps(result, default=float), json.dumps(safety, default=float)])
        for did, cls, fam, raw, cal, basis in proposals:
            cur.execute("INSERT OR REPLACE INTO learn.measurement_basis VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [did, out["run_id"], as_of, cls, fam, raw, cal, json.dumps(basis, default=float)])

    db.write(work)
