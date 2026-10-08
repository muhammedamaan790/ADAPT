"""The pipeline cycle (C2, spec §2): one idempotent run per run_id, every step logged.

ingest -> DQ gate -> reconcile (canonical + marts + health) -> detect -> diagnose -> predict (refit when due, else
inference with the champion) -> store tomorrow's forecasts -> optimize -> decide (snapshot, hash, fingerprint) ->
policy -> auto-execute (Stage 3: qualified decisions on AUTONOMOUS channels; Observe -> shadow) ->
verify (re-verify UNKNOWN legs) ->
safety monitor (Stage 2) -> measure matured outcomes of executed decisions -> learn (calibration) -> narrate
(Stage 2: daily brief + this run's incidents and pending decisions, guarded LLM or offline templates; display only,
never an input to a decision, so the evaluation harness skips it).

Idempotency: a COMPLETED run_id returns its stored summary without recomputing. A failed or interrupted run_id
re-runs from the start; every step is itself idempotent (CREATE OR REPLACE builds, run-keyed detection, decisions
keyed by run, outcomes keyed by decision). Refit triggers (spec §2): no champion, champion older than 7 days, or a
matured outcome since the last fit (dirty marks); otherwise the run is inference-only with the cached artifacts.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

from adapt.decide.decisions import create_decisions
from adapt.decide.run import run_optimizer
from adapt.detect.detector import run_detection
from adapt.diagnose.run import run_diagnosis
from adapt.execute import mirror as sim_mirror
from adapt.execute.saga import reverify
from adapt.ingest.sync import run_sync
from adapt.learn.outcomes import measure_outcome
from adapt.pipeline import events
from adapt.policy.safety_monitor import run_monitor as run_safety_monitor
from adapt.predict.demand import demand_config, fit_demand
from adapt.predict.fit_curves import fit_curves, load_curves
from adapt.predict.forecasts import POOL_DAYS, backfill_forecasts, forecast_days, store_forecasts
from adapt.reconcile.build import build_canonical

REFIT_DAYS = 7
MEASURABLE_SAGA_STATES = ("SUCCEEDED", "ACCEPTED_PARTIAL", "RESOLVED_MANUALLY")

DDL = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.pipeline_runs (
    run_id VARCHAR PRIMARY KEY, as_of TIMESTAMP NOT NULL, status VARCHAR NOT NULL, started_at TIMESTAMP,
    finished_at TIMESTAMP, summary JSON
);
CREATE TABLE IF NOT EXISTS ops.pipeline_steps (
    run_id VARCHAR NOT NULL, step VARCHAR NOT NULL, seq INTEGER NOT NULL, status VARCHAR NOT NULL,
    seconds DOUBLE, detail JSON, PRIMARY KEY (run_id, step)
);
"""


def run_id_for(as_of: datetime) -> str:
    return f"run-{as_of:%Y%m%dT%H%M}"


def _needs_refit(db, as_of: datetime) -> str | None:
    if not db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = 'models' "
                    "AND table_name = 'response_curves'"):
        return "no champion"
    last = db.query("SELECT max(fit_ts) FROM models.response_curves")[0][0]
    if last is None:
        return "no champion"
    if as_of - last >= timedelta(days=REFIT_DAYS):
        return f"champion is {(as_of - last).days} days old (weekly refresh)"
    events.dirty(db)  # ensures the events table exists
    matured = db.query("SELECT count(*) FROM ops.events WHERE type = 'outcome_matured' AND event_at > ?", [last])[0][0]
    if matured:
        return f"{matured} matured outcome(s) since the last fit"
    return None


def _needs_demand_refit(db, as_of: datetime) -> bool:
    from adapt.economics.inventory_risk import risk_config

    if risk_config()["predicate"] != "STOCKOUT_PROBABILITY":
        return False  # Stage 1: the seasonal-naive demand model needs no fit
    if not db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = 'models' "
                    "AND table_name = 'demand_fits'"):
        return True
    last = db.query("SELECT max(fit_ts) FROM models.demand_fits")[0][0]
    return last is None or as_of - last >= timedelta(days=demand_config()["refit_days"])


def _needs_weekly(db, schema: str, table: str, as_of: datetime) -> bool:
    if not db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
                    [schema, table]):
        return True
    last = db.query(f"SELECT max(fit_ts) FROM {schema}.{table}")[0][0]
    return last is None or as_of - last >= timedelta(days=REFIT_DAYS)


def _brief(x) -> dict:
    """Scalars of a step's result (lists become their length) for the run log."""
    if not isinstance(x, dict):
        return {"result": str(type(x).__name__)}
    return {k: (len(v) if isinstance(v, (list, tuple)) else v) for k, v in x.items() if not isinstance(v, dict)}


def narrate_run(db, as_of: datetime, llm=None) -> dict:
    """The narrator step: the daily brief, every open incident detected in this run and every decision awaiting
    approval, each persisted to intel.narratives (one row per package hash, so an unchanged package is not redone)."""
    from adapt.agent.brief import daily_brief
    from adapt.agent.narrator import narrate_decision, narrate_incident

    out = {"brief": daily_brief(db, as_of, llm)["source"], "incidents": 0, "decisions": 0}
    if db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = 'intel' AND table_name = 'anomalies'"):
        for (aid,) in db.query("SELECT anomaly_id FROM intel.anomalies WHERE is_incident AND status <> 'resolved' "
                               "AND last_detected_at = ? ORDER BY anomaly_id", [as_of]):
            narrate_incident(db, aid, llm, as_of)
            out["incidents"] += 1
    if db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = 'ops' "
                "AND table_name = 'decision_events'"):
        for (did,) in db.query("""SELECT decision_id FROM ops.decision_events
                                  QUALIFY row_number() OVER (PARTITION BY decision_id ORDER BY seq DESC) = 1
                                  AND event = 'submitted' ORDER BY decision_id"""):
            narrate_decision(db, did, llm, as_of)
            out["decisions"] += 1
    return out


def run_cycle(db, http, as_of: datetime, adapters: dict | None = None, run_id: str | None = None,
              sleep=time.sleep, llm=None, narrate: bool = True) -> dict:
    run_id = run_id or run_id_for(as_of)
    db.write(lambda cur: cur.execute(DDL))
    done = db.query("SELECT summary FROM ops.pipeline_runs WHERE run_id = ? AND status = 'COMPLETED'", [run_id])
    if done:
        return {**json.loads(done[0][0]), "idempotent_replay": True}

    def start(cur):
        cur.execute("INSERT OR REPLACE INTO ops.pipeline_runs VALUES (?, ?, 'RUNNING', ?, NULL, NULL)",
                    [run_id, as_of, datetime.now()])
        cur.execute("DELETE FROM ops.pipeline_steps WHERE run_id = ?", [run_id])

    db.write(start)
    summary: dict = {"run_id": run_id, "as_of": as_of.isoformat(), "steps": {}}
    seq = [0]

    def step(name: str, fn):
        t = time.time()
        seq[0] += 1
        try:
            detail = fn()
            status = "NOT_BUILT" if isinstance(detail, dict) and detail.get("status") == "NOT_BUILT" else "OK"
        except Exception as exc:
            err = json.dumps({"error": repr(exc)})
            db.write(lambda cur: cur.execute(
                "INSERT OR REPLACE INTO ops.pipeline_steps VALUES (?, ?, ?, 'FAILED', ?, ?)",
                [run_id, name, seq[0], time.time() - t, err]))
            db.write(lambda cur: cur.execute("UPDATE ops.pipeline_runs SET status = 'FAILED', finished_at = ? "
                                             "WHERE run_id = ?", [datetime.now(), run_id]))
            raise
        summary["steps"][name] = detail
        db.write(lambda cur: cur.execute("INSERT OR REPLACE INTO ops.pipeline_steps VALUES (?, ?, ?, ?, ?, ?)",
                                         [run_id, name, seq[0], status, time.time() - t,
                                          json.dumps(detail, default=str)]))
        return detail

    ctx: dict = {}
    step("ingest", lambda: {"connectors": run_sync(db, http).get("status")})
    canon = step("reconcile", lambda: build_canonical(db, as_of))
    step("dq_gate", lambda: {"health": canon.get("health"),
                             "red": [s for s, h in (canon.get("health") or {}).items() if h["status"] == "RED"]})
    step("detect", lambda: _brief(run_detection(db, as_of, run_id)))
    step("diagnose", lambda: _brief(run_diagnosis(db, as_of)))

    def predict():
        reason = _needs_refit(db, as_of)
        out = {"refit": bool(reason), "reason": reason or "inference only (champion is current)"}
        if reason:
            out["fit"] = fit_curves(db, as_of)
        ctx["curves"] = load_curves(db)
        if _needs_demand_refit(db, as_of):  # Stage 2: weekly LightGBM candidate vs the champion (spec §2, §10.2)
            out["demand"] = fit_demand(db, as_of)
        if _needs_weekly(db, "models", "cvr_prior", as_of):  # Stage 3: the CVR prior, weekly, acceptance-gated
            from adapt.predict.cvr_bayes import fit_cvr

            out["cvr_prior"] = _brief(fit_cvr(db, as_of))
        return out

    step("predict", predict)

    def forecast():
        out = {}
        if forecast_days(db) < POOL_DAYS:
            out["backfill"] = backfill_forecasts(db, as_of)
        out["stored"] = store_forecasts(db, as_of, ctx["curves"], run_id)
        return out

    step("forecast", forecast)

    def optimize():
        ctx["run"] = run_optimizer(db, as_of)
        r = ctx["run"]["result"]
        return {"status": r.get("status"), "legs": len(r.get("legs", [])), "safety": len(ctx["run"]["safety"]),
                "E": (r.get("expected") or {}).get("E")}

    step("optimize", optimize)
    step("decide", lambda: {"created": create_decisions(db, ctx["run"], ctx["run"]["state"], ctx["run"]["flags"],
                                                        as_of)})
    step("policy", lambda: {"evaluated_in": "decide (validate + Approve-mode result per decision)"})
    def autonomous():  # Stage 3 autonomy ladder: qualified decisions on AUTONOMOUS channels only (spec §9.1)
        from adapt.policy.autonomy import auto_execute, world_id

        return _brief(auto_execute(db, adapters or {}, as_of, ctx["run"]["state"], world_id(http)))

    step("auto_execute", autonomous)
    def verify():
        out = {"resolved": len(reverify(db, adapters, as_of, sleep)) if adapters else 0}
        live = (adapters or {}).get("google")
        if getattr(live, "mode", None) == "LIVE":  # hybrid mirror retrier (spec §9.4): never touches the live change
            out["mirror"] = sim_mirror.retry_pending(db, getattr(live, "mirror", None), as_of)
        return out

    step("verify", verify)
    step("safety_monitor", lambda: _brief(run_safety_monitor(db, as_of)))

    def measure():
        if not db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = 'exec' AND table_name = 'sagas'"):
            return {"measured": []}
        out = []
        marks = ", ".join("?" * len(MEASURABLE_SAGA_STATES))
        for (did,) in db.query(f"""SELECT s.decision_id FROM exec.sagas s
                                   JOIN learn.measurement_basis b USING (decision_id)
                                   WHERE s.kind = 'EXECUTION' AND s.state IN ({marks})""",
                               list(MEASURABLE_SAGA_STATES)):
            res = measure_outcome(db, did, as_of)
            if res["status"] == "MATURED" and not res.get("idempotent_replay"):
                db.write(lambda cur, d=did, r=res: events.emit(
                    cur, "outcome_matured", "decision", d, as_of, {"verdict": r["verdict"]},
                    dedupe_key=f"outcome_matured:{d}"))
            out.append({"decision_id": did, "status": res["status"], "verdict": res.get("verdict")})
        return {"measured": out}

    step("measure", measure)
    step("learn", lambda: {"calibration": "applied inside measure (idempotent per outcome)",
                           "dirty_cleared": db.write(lambda cur: events.clear(cur, run_id))})
    if narrate:  # display-side steps (the evaluation harness skips them)
        step("narrate", lambda: narrate_run(db, as_of, llm))

        def copilot_marts():  # Stage 3: the read-only marts copy the Copilot's SQL runs on (spec §9.5)
            from adapt.agent.sql import refresh_copy

            return refresh_copy(db)

        step("copilot_marts", copilot_marts)

    def finish(cur):
        cur.execute("UPDATE ops.pipeline_runs SET status = 'COMPLETED', finished_at = ?, summary = ? WHERE run_id = ?",
                    [datetime.now(), json.dumps(summary, default=str), run_id])

    db.write(finish)
    return summary
