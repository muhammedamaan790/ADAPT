"""Automatic emergency safety monitor (Stage 2, spec §9.3 + §9.1 precedence and hysteresis, T31 / T47 / T58).

Daily, for every EXECUTED OPTIMIZATION decision inside its measurement window (outcome not matured), on its treated
units:
  CPA runaway      CPA since execution > 1.25 x guardrail (guardrail = the units' pre-action CPA, 28 days before)
  inventory        a mapped SKU at risk under the stage predicate (Stage 2: NB2 P(stockout) > 0.5; Stage 1:
                   projected shortfall > 0)
  P10 path         cumulative realized dCAA to date < the decision-time cumulative P10 path to date, on 2
                   consecutive daily checks (both cumulative rupees over identical day ranges, same measurement
                   method as the outcome, spec §10)
A trigger creates a SAFETY decision (revert or reduce, never pause: the last active campaign is never paused, T58),
with its own measurement basis (avoided loss): REVERT the decision's increases to their pre-action budgets when it
increased spend; otherwise REDUCE the treated units by the max daily change from their current budgets, floored at
the per-unit minimum. Approve mode (every channel in Stage 2): the decision waits for review and a P1 `safety_alert`
event is raised (the forced alert). Autonomous execution is the Stage 3 autonomy ladder.
Precedence 0: a unit under an EXECUTION_UNCERTAINTY freeze gets NO decision, only a P1 alert "emergency condition on
an entity with unverified external state; reconcile first" (T47). Hysteresis: an executed SAFETY decision puts its
entities in SAFETY_COOLDOWN (5 days, optimization may not move them); the freeze clears only after the cooldown AND
3 consecutive days of CPA below 1.10 x guardrail AND inventory recovered (P(stockout) < 0.2 / no shortfall). At most 2
automated reversals per entity per 7 days, then the entity is pinned to Approve mode (ops.autonomy_pins). A safety
action on an entity inside an open OPTIMIZATION measurement window marks that outcome CONTAMINATED_BY_SAFETY (excluded
from calibration, spec §10 1b).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import numpy as np

from adapt.economics.state import guardrails_config
from adapt.pipeline import events
from adapt.policy import locks

DDL = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.safety_checks (
    decision_id VARCHAR NOT NULL, as_of TIMESTAMP NOT NULL, trigger VARCHAR NOT NULL, value DOUBLE, threshold DOUBLE,
    fired BOOLEAN NOT NULL, details JSON, PRIMARY KEY (decision_id, as_of, trigger)
);
CREATE TABLE IF NOT EXISTS ops.autonomy_pins (
    entity_id VARCHAR PRIMARY KEY, pinned_at TIMESTAMP NOT NULL, until TIMESTAMP, reason VARCHAR
);
CREATE SCHEMA IF NOT EXISTS learn;
CREATE TABLE IF NOT EXISTS learn.outcome_flags (
    decision_id VARCHAR NOT NULL, flag VARCHAR NOT NULL, flagged_at TIMESTAMP NOT NULL, source VARCHAR,
    PRIMARY KEY (decision_id, flag)
);
"""


def _exists(db, schema, table) -> bool:
    return bool(db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
                         [schema, table]))


def _cpa(db, cids, lo, hi) -> float | None:
    marks = ", ".join("?" * len(cids))
    spend, orders = db.query(f"""SELECT sum(spend), sum(attributed_orders) FROM marts.campaign_daily
                                 WHERE campaign_id IN ({marks}) AND date BETWEEN ? AND ?""", [*cids, lo, hi])[0]
    return float(spend) / float(orders) if spend and orders else None


def open_windows(db, as_of: datetime) -> list[dict]:
    """Executed OPTIMIZATION decisions whose outcome has not matured (inside the measurement window)."""
    if not (_exists(db, "exec", "sagas") and _exists(db, "learn", "measurement_basis")):
        return []
    matured = {d for (d,) in db.query("SELECT decision_id FROM learn.outcomes")} if _exists(db, "learn", "outcomes") \
        else set()
    rows = db.query("""SELECT b.decision_id, b.decision_ts, b.basis FROM learn.measurement_basis b
                       JOIN exec.sagas s USING (decision_id)
                       WHERE b.class = 'OPTIMIZATION' AND s.kind = 'EXECUTION'
                         AND s.state IN ('SUCCEEDED', 'ACCEPTED_PARTIAL', 'RESOLVED_MANUALLY')""")
    out = []
    for did, ts, basis in rows:
        b = json.loads(basis)
        if did in matured or (as_of.date() - ts.date()).days >= b["days"]:
            continue
        legs = json.loads(db.query("SELECT payload FROM intel.decisions WHERE decision_id = ?", [did])[0][0])["legs"]
        out.append({"decision_id": did, "decision_ts": ts, "basis": b, "legs": legs})
    return out


def _triggers(db, w: dict, as_of: datetime, state) -> list[dict]:
    g = guardrails_config()["safety"]
    start = w["decision_ts"].date()
    last = as_of.date() - timedelta(days=1)
    days = (last - start).days + 1
    found = []
    if days < int(g["min_window_days"]):
        return found
    cids = sorted({c for leg in w["legs"] for c in leg["campaign_ids"]})
    pre = _cpa(db, cids, start - timedelta(days=28), start - timedelta(days=1))
    now = _cpa(db, cids, start, last)
    if pre and now:
        guard = pre * float(g["cpa_guardrail_multiple"])
        found.append({"trigger": "CPA_RUNAWAY", "value": now, "threshold": guard * float(g["cpa_fire"]),
                      "fired": now > guard * float(g["cpa_fire"])})
    # inventory under the stage predicate, on the treated units' mapped SKUs
    if state is not None:
        from adapt.economics.portfolio import Portfolio

        treated = {leg["unit_id"] for leg in w["legs"]}
        econ = Portfolio(state).evaluate({})
        kind = econ.inventory_risk_by_sku["kind"]
        skus = {k for u in state.units if u.unit_id in treated for k in u.sku_weights}
        from adapt.economics.inventory_risk import risk_config

        thr = float(risk_config()["evidence_threshold"])
        worst = None
        for k in sorted(skus):
            e = econ.inventory_risk_by_sku["by_sku"].get(k)
            if e is None:
                continue
            v = e.get("stockout_probability", e.get("shortfall", 0.0))
            bad = v > thr if kind == "STOCKOUT_PROBABILITY" else v > 0
            if bad and (worst is None or v > worst[1]):
                worst = (k, v)
        found.append({"trigger": "INVENTORY_AT_RISK", "value": worst[1] if worst else 0.0,
                      "threshold": thr if kind == "STOCKOUT_PROBABILITY" else 0.0, "fired": worst is not None,
                      "details": {"sku": worst[0] if worst else None, "kind": kind}})
    # cumulative realized dCAA vs the cumulative P10 path, identical day ranges
    p10 = w["basis"].get("p10_cum_daily_delta_caa")
    if p10:
        from adapt.learn.outcomes import effect_distribution

        n = min(days, len(p10))
        observed = {}
        for uid, u in w["basis"]["units"].items():
            marks = ", ".join("?" * len(u["campaign_ids"]))
            rows = dict((d, (r, s)) for d, r, s in db.query(
                f"""SELECT date, sum(attributed_net_revenue), sum(spend) FROM marts.campaign_daily
                    WHERE campaign_id IN ({marks}) AND date BETWEEN ? AND ? GROUP BY 1""",
                [*u["campaign_ids"], start, start + timedelta(days=n - 1)]))
            dates = [start + timedelta(days=t) for t in range(n)]
            observed[uid] = {"revenue": np.array([float(rows.get(d, (0, 0))[0] or 0) for d in dates]),
                             "spend": np.array([float(rows.get(d, (0, 0))[1] or 0) for d in dates])}
        realized = effect_distribution(w["basis"], observed, n, seed=7)["realized"]
        found.append({"trigger": "BELOW_P10_PATH", "value": realized, "threshold": float(p10[n - 1]),
                      "fired": realized < float(p10[n - 1]), "details": {"day": n}})
    return found


def _consecutive(db, did: str, trigger: str, k: int) -> bool:
    rows = db.query("SELECT fired FROM ops.safety_checks WHERE decision_id = ? AND trigger = ? ORDER BY as_of DESC "
                    "LIMIT ?", [did, trigger, k])
    return len(rows) == k and all(r[0] for r in rows)


def _reversals_7d(db, entity: str, as_of: datetime) -> int:
    if not _exists(db, "intel", "decisions"):
        return 0
    n = 0
    for (payload,) in db.query("""SELECT payload FROM intel.decisions WHERE class = 'SAFETY' AND created_at >= ?
                                  AND decision_id LIKE 'safety-%'""", [as_of - timedelta(days=7)]):
        if entity in {leg["unit_id"] for leg in json.loads(payload)["legs"]}:
            n += 1
    return n


def run_monitor(db, as_of: datetime, state=None) -> dict:
    """The pipeline's safety_monitor step. Returns what fired, the decisions / alerts created, cooldowns cleared."""
    from adapt.decide.decisions import create_decisions
    from adapt.decide.run import DDL as RUN_DDL
    from adapt.decide.run import measurement_basis
    from adapt.economics.state import load_state

    db.write(lambda cur: (cur.execute(DDL), cur.execute(RUN_DDL)))
    g = guardrails_config()["safety"]
    windows = open_windows(db, as_of)
    if windows and state is None:
        state = load_state(db, as_of)
    frozen = {(e, r) for (_t, e, _b, r, _s) in locks.active_freezes(db)}
    uncertain = {e for e, r in frozen if r == "EXECUTION_UNCERTAINTY"}
    created, alerts, fired_all = [], [], []
    for w in windows:
        checks = _triggers(db, w, as_of, state)

        def log(cur, w=w, checks=checks):
            for c in checks:
                cur.execute("INSERT OR REPLACE INTO ops.safety_checks VALUES (?, ?, ?, ?, ?, ?, ?)",
                            [w["decision_id"], as_of, c["trigger"], c["value"], c["threshold"], c["fired"],
                             json.dumps(c.get("details"), default=str)])

        db.write(log)
        fired = [c for c in checks if c["fired"] and (c["trigger"] != "BELOW_P10_PATH" or
                                                       _consecutive(db, w["decision_id"], c["trigger"],
                                                                    int(g["p10_consecutive_checks"])))]
        if not fired:
            continue
        fired_all.append({"decision_id": w["decision_id"], "triggers": [c["trigger"] for c in fired]})
        increased = [leg for leg in w["legs"] if leg["after"] > leg["before"]]
        blocked = [leg["unit_id"] for leg in w["legs"]
                   if leg["unit_id"] in uncertain or set(leg["campaign_ids"]) & uncertain]
        if blocked:  # precedence 0: never mutate an entity with unverified external state (T47)
            msg = "emergency condition on an entity with unverified external state; reconcile first"
            trig = [c["trigger"] for c in fired]
            db.write(lambda cur, w=w, b=blocked, msg=msg, trig=trig: events.emit(
                cur, "safety_alert", "decision", w["decision_id"], as_of,
                {"priority": "P1", "message": msg, "units": b, "triggers": trig},
                dedupe_key=f"safety_alert:uncertain:{w['decision_id']}:{as_of:%Y%m%d}"))
            alerts.append({"decision_id": w["decision_id"], "reason": "EXECUTION_UNCERTAINTY", "units": blocked})
            continue
        current = {u.unit_id: u.budget for u in state.units}
        gc = guardrails_config()["change"]
        if increased:  # revert the increases (from the current budget back to the pre-action one)
            legs = [{**leg, "before": current.get(leg["unit_id"], leg["after"]), "after": leg["before"],
                     "action": "REVERT"} for leg in increased]
        else:          # reduce the treated units by the max daily change, floored at the unit minimum
            legs = []
            for leg in w["legs"]:
                b = current.get(leg["unit_id"], leg["after"])
                after = max(float(np.ceil(b * (1 - gc["max_daily_change_pct"]) / 100) * 100),  # inside the box
                            float(gc["unit_min_budget_inr"]))
                if after < b - 1e-6:
                    legs.append({**leg, "before": b, "after": after, "action": "REDUCE"})
        legs = [leg for leg in legs if leg["after"] < leg["before"] - 1e-6]
        if not legs:
            continue
        did = f"safety-{w['decision_id']}-{as_of:%Y%m%d}"
        pinned = [leg["unit_id"] for leg in legs if _reversals_7d(db, leg["unit_id"], as_of) >= g["max_reversals_7d"]]
        alloc = np.array([next((leg["after"] for leg in legs if leg["unit_id"] == u.unit_id), u.budget)
                          for u in state.units])
        from adapt.economics.portfolio import Portfolio

        econ = Portfolio(state).evaluate(alloc)
        cand = {"class": "SAFETY", "status": "REQUIRES_REVIEW", "sku": None, "decision_id": did,
                "trigger": "SAFETY_MONITOR:" + "+".join(c["trigger"] for c in fired), "legs": legs,
                "expected": econ.summary(), "inventory_risk_after": econ.inventory_risk_by_sku,
                "risk_kind": econ.inventory_risk_by_sku["kind"], "follows": w["decision_id"]}
        run = {"run_id": f"safety-run-{as_of:%Y%m%d}", "result": {"status": "NONE"}, "safety": [cand],
               "calibration_factor": 1.0}
        ids = create_decisions(db, run, state, {}, as_of, actor="safety-monitor")
        basis = measurement_basis(state, alloc, legs, db)

        def persist(cur, ids=ids, basis=basis, w=w, pinned=pinned, fired=fired, run_id=run["run_id"],
                    e=cand["expected"]["E"]):
            for d in ids:
                cur.execute("INSERT OR REPLACE INTO learn.measurement_basis VALUES (?, ?, ?, 'SAFETY', NULL, ?, ?, ?)",
                            [d, run_id, as_of, e, e, json.dumps(basis, default=float)])
                events.emit(cur, "safety_alert", "decision", d, as_of,
                            {"priority": "P1", "message": "automatic safety decision awaiting review (Approve mode)",
                             "follows": w["decision_id"], "triggers": [c["trigger"] for c in fired]},
                            dedupe_key=f"safety_alert:{d}")
            for unit in pinned:
                cur.execute("INSERT OR REPLACE INTO ops.autonomy_pins VALUES (?, ?, NULL, ?)",
                            [unit, as_of, f">= {g['max_reversals_7d']} automated reversals in 7 days"])

        db.write(persist)
        created += ids
    cleared = apply_cooldowns(db, as_of, state)
    return {"open_windows": len(windows), "fired": fired_all, "decisions": created, "alerts": alerts,
            "cooldowns": cleared}


def apply_cooldowns(db, as_of: datetime, state=None) -> dict:
    """Executed SAFETY decisions -> SAFETY_COOLDOWN freezes (+ CONTAMINATED_BY_SAFETY on open measurement windows);
    clear a cooldown only after cooldown_days AND recovery_days of CPA < cpa_clear x guardrail AND inventory
    recovered (P(stockout) < 0.2 / no shortfall)."""
    g = guardrails_config()["safety"]
    if not _exists(db, "exec", "sagas"):
        return {"started": 0, "cleared": 0}
    started = 0
    rows = db.query("""SELECT s.saga_id, s.decision_id, s.updated_at, d.payload FROM exec.sagas s
                       JOIN intel.decisions d USING (decision_id)
                       WHERE d.class = 'SAFETY' AND s.kind = 'EXECUTION'
                         AND s.state IN ('SUCCEEDED', 'ACCEPTED_PARTIAL')""")
    existing = {s for (s,) in db.query("SELECT DISTINCT source_saga_id FROM ops.entity_freezes "
                                       "WHERE reason = 'SAFETY_COOLDOWN'")} if _exists(db, "ops", "entity_freezes") \
        else set()
    windows = open_windows(db, as_of)
    for saga_id, _did, at, payload in rows:
        if saga_id in existing:
            continue
        legs = json.loads(payload)["legs"]
        ents = [("budget", leg["budget_id"], leg["budget_id"]) for leg in legs]

        def work(cur, ents=ents, saga_id=saga_id, at=at, legs=legs):
            locks.freeze(cur, ents, "SAFETY_COOLDOWN", saga_id, None, at)
            touched = {leg["unit_id"] for leg in legs}
            for w in windows:
                if touched & {leg["unit_id"] for leg in w["legs"]}:
                    cur.execute("INSERT OR IGNORE INTO learn.outcome_flags VALUES (?, 'CONTAMINATED_BY_SAFETY', ?, ?)",
                                [w["decision_id"], at, saga_id])

        db.write(work)
        started += 1
    cleared = 0
    if _exists(db, "ops", "entity_freezes"):
        from adapt.economics.inventory_risk import risk_config

        rc = risk_config()
        for fid, eid, created_at in db.query("""SELECT freeze_id, entity_id, created_at FROM ops.entity_freezes
                                               WHERE reason = 'SAFETY_COOLDOWN' AND cleared_at IS NULL"""):
            if as_of - created_at < timedelta(days=int(g["cooldown_days"])):
                continue
            cids = [c for (c,) in db.query("SELECT campaign_id FROM core.campaigns WHERE budget_id = ?", [eid])]
            if not cids:
                continue
            last = as_of.date() - timedelta(days=1)
            pre = _cpa(db, cids, created_at.date() - timedelta(days=28), created_at.date() - timedelta(days=1))
            ok_days = 0
            for k in range(int(g["recovery_days"])):
                d = last - timedelta(days=k)
                c = _cpa(db, cids, d, d)
                if pre and c is not None and c < pre * float(g["cpa_guardrail_multiple"]) * float(g["cpa_clear"]):
                    ok_days += 1
            inv_ok = True
            if state is not None:
                from adapt.economics.portfolio import Portfolio

                econ = Portfolio(state).evaluate({})
                unit = next((u for u in state.units if u.unit_id == eid), None)
                if unit is not None:
                    for k in unit.sku_weights:
                        e = econ.inventory_risk_by_sku["by_sku"].get(k, {})
                        if e.get("stockout_probability", 0.0) >= float(rc["recovery_threshold"]) or \
                                e.get("shortfall", 0.0) > 0:
                            inv_ok = False
            if ok_days == int(g["recovery_days"]) and inv_ok:
                db.write(lambda cur, fid=fid: cur.execute(
                    "UPDATE ops.entity_freezes SET cleared_at = ?, cleared_by = 'safety-monitor', clear_reason = ? "
                    "WHERE freeze_id = ?", [as_of, "cooldown + recovery window passed", fid]))
                cleared += 1
    return {"started": started, "cleared": cleared}


def contaminated(db, decision_id: str) -> bool:
    return _exists(db, "learn", "outcome_flags") and bool(db.query(
        "SELECT 1 FROM learn.outcome_flags WHERE decision_id = ? AND flag = 'CONTAMINATED_BY_SAFETY'", [decision_id]))
