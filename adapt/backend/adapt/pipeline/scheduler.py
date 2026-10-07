"""Scheduling (C2). The pipeline runs once per simulated day at the pipeline hour (logical time). In the simulator
the clock moves when the Scenario Lab advances the world, so the "scheduler" is catch_up(): run every day between
the last completed run and the world's today, in order, each with its own idempotent run_id. (A wall-clock cron for
a live deployment calls the same function; no second scheduler implementation exists.)
"""

from __future__ import annotations

from datetime import timedelta

from adapt.ingest.sync import world_today
from adapt.pipeline.cycle import DDL, run_cycle
from adapt.reconcile.build import logical_now


def due_days(db, today) -> list:
    db.write(lambda cur: cur.execute(DDL))
    last = db.query("SELECT max(as_of) FROM ops.pipeline_runs WHERE status = 'COMPLETED'")[0][0]
    if last is None:
        return [today]
    return [last.date() + timedelta(days=k) for k in range(1, (today - last.date()).days + 1)]


def catch_up(db, http, adapters: dict | None = None, llm=None) -> list[dict]:
    today = world_today(http)
    out = []
    for day in due_days(db, today):
        out.append(run_cycle(db, http, logical_now(day), adapters, llm=llm))
    return out
