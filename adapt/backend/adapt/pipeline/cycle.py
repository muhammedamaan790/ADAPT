"""The pipeline cycle (C2, spec §2): one idempotent run per run_id, every step logged.

ingest -> DQ gate -> reconcile (canonical + marts + health) -> detect -> diagnose -> predict (refit when due, else
inference with the champion) -> store tomorrow's forecasts -> optimize -> decide (snapshot, hash, fingerprint) ->
policy -> auto-execute [NOT_BUILT: Stage 1 is Approve mode only] -> verify (re-verify UNKNOWN legs) ->
safety monitor [NOT_BUILT: Stage 2] -> measure matured outcomes of executed decisions -> learn (calibration).

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
from adapt.execute.saga import reverify
from adapt.ingest.sync import run_sync
from adapt.learn.outcomes import measure_outcome
from adapt.pipeline import events
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


def _brief(x) -> dict:
    """Scalars of a step's result (lists become their length) for the run log."""
    if not isinstance(x, dict):
        return {"result": str(type(x).__name__)}
    return {k: (len(v) if isinstance(v, (list, tuple)) else v) for k, v in x.items() if not isinstance(v, dict)}


def run_cycle(db, http, as_of: datetime, adapters: dict | None = None, run_id: str | None = None,
              sleep=time.sleep) -> dict:
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
    step("auto_execute", lambda: {"status": "NOT_BUILT", "reason": "Stage 1 is Approve mode only"})
    step("verify", lambda: {"resolved": len(reverify(db, adapters, as_of, sleep)) if adapters else 0})
    step("safety_monitor", lambda: {"status": "NOT_BUILT", "reason": "automatic safety monitor is Stage 2"})

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

    def finish(cur):
        cur.execute("UPDATE ops.pipeline_runs SET status = 'COMPLETED', finished_at = ?, summary = ? WHERE run_id = ?",
                    [datetime.now(), json.dumps(summary, default=str), run_id])

    db.write(finish)
    return summary
