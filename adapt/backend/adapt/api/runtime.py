"""API runtime (C6): the world client, execution adapters, a mutation lock and background pipeline jobs.

- Mutations are serialised by one lock; while a pipeline job runs, mutations get 409 ("pipeline running") instead
  of queueing, so a stale view can never approve against a half-rebuilt state.
- World advance runs the pipeline for every new day (~40 s/day on seed 42), longer than the browser's 15 s
  timeout, so it runs as a background job: the request returns {ok:true}; /overview, /events and /pipeline/status
  show progress. Advance and reset are refused while any execution is unresolved (spec §9.4, frontend contract).
- Workspace baseline: `python -m adapt.api.runtime --bootstrap` syncs, builds and runs the day-0 cycle, then copies
  the workspace to `<workspace>.baseline.duckdb`. /sim/reset restores the world AND that workspace baseline, so the
  app never keeps data from a world that no longer exists.
"""

from __future__ import annotations

import argparse
import shutil
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime

import httpx

from adapt.config.settings import Settings, get_settings
from adapt.core.db import Database
from adapt.execute.adapters import build_adapters
from adapt.ingest.http import SourceHttp
from adapt.ingest.sync import run_sync, world_today
from adapt.pipeline.scheduler import catch_up
from adapt.reconcile.build import logical_now

UNRESOLVED_SAGA_STATES = ("PENDING", "EXECUTING", "PARTIAL", "COMPENSATING", "COMPENSATION_FAILED",
                          "HUMAN_RESOLUTION_REQUIRED")


class Busy(RuntimeError):
    """409: another mutation or a pipeline job is running."""


class Runtime:
    def __init__(self, settings: Settings, db: Database, world_client: httpx.Client | None = None):
        self.settings = settings
        self.db = db
        self.client = world_client or httpx.Client(base_url=settings.world_url, timeout=httpx.Timeout(60.0))
        base = str(self.client.base_url).rstrip("/") or settings.world_url
        self.http = SourceHttp(base, client=self.client)
        self.adapters = build_adapters(self.client, modes={"google": settings.google_execution_mode,
                                                           "meta": settings.meta_execution_mode})
        self._lock = threading.Lock()
        self.job: dict = {"name": None, "state": "idle", "started_at": None, "finished_at": None, "error": None}
        self._thread: threading.Thread | None = None

    # ---- time -------------------------------------------------------------------------------------------------------
    def world(self) -> dict:
        return self.http.get("/health")

    def now(self) -> datetime:
        return logical_now(world_today(self.http))

    # ---- locking and jobs -------------------------------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @contextmanager
    def mutation(self):
        if self.busy:
            raise Busy(f"pipeline job '{self.job['name']}' is running; retry when it finishes")
        if not self._lock.acquire(blocking=False):
            raise Busy("another change is in progress; refresh and retry")
        try:
            yield
        finally:
            self._lock.release()

    def start_job(self, name: str, fn, sync: bool = False) -> dict:
        if self.busy:
            raise Busy(f"pipeline job '{self.job['name']}' is already running")

        def run():
            with self._lock:
                self.job.update(name=name, state="running", started_at=datetime.now().isoformat(),
                                finished_at=None, error=None)
                try:
                    fn()
                    self.job.update(state="completed")
                except Exception as exc:  # surfaced on /pipeline/status and /events, never swallowed
                    self.job.update(state="failed", error=f"{exc!r}\n{traceback.format_exc(limit=3)}")
                finally:
                    self.job["finished_at"] = datetime.now().isoformat()

        if sync:
            run()
            return dict(self.job)
        self._thread = threading.Thread(target=run, name=f"adapt-{name}", daemon=True)
        self._thread.start()
        return dict(self.job)

    def wait(self, timeout: float = 3600.0) -> dict:
        if self._thread is not None:
            self._thread.join(timeout)
        return dict(self.job)

    # ---- pipeline ---------------------------------------------------------------------------------------------------
    def unresolved_executions(self) -> int:
        if not self.db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = 'exec' "
                             "AND table_name = 'sagas'"):
            return 0
        marks = ", ".join("?" * len(UNRESOLVED_SAGA_STATES))
        return int(self.db.query(f"SELECT count(*) FROM exec.sagas WHERE state IN ({marks})",
                                 list(UNRESOLVED_SAGA_STATES))[0][0])

    def catch_up(self) -> list[dict]:
        return catch_up(self.db, self.http, self.adapters)

    # ---- workspace baseline -----------------------------------------------------------------------------------------
    @property
    def baseline_path(self):
        p = self.settings.workspace_db_path
        return p.with_name(f"{p.stem}.baseline.duckdb")

    def snapshot_baseline(self) -> None:
        path = self.settings.workspace_db_path
        self.db.close()
        shutil.copyfile(path, self.baseline_path)
        self.db = Database(path)

    def restore_baseline(self) -> None:
        if not self.baseline_path.exists():
            raise Busy("no workspace baseline: run `python -m adapt.api.runtime --bootstrap` first")
        path = self.settings.workspace_db_path
        self.db.close()
        shutil.copyfile(self.baseline_path, path)
        wal = path.with_name(path.name + ".wal")
        if wal.exists():
            wal.unlink()
        self.db = Database(path)


def bootstrap(settings: Settings | None = None, world_client: httpx.Client | None = None) -> dict:
    """First-time workspace: full sync, day-0 pipeline cycle (canonical state, curves, forecasts, decisions), then
    the baseline copy that /sim/reset restores."""
    settings = settings or get_settings()
    rt = Runtime(settings, Database(settings.workspace_db_path), world_client)
    t = time.time()
    try:
        run_sync(rt.db, rt.http)
        runs = rt.catch_up()
        rt.snapshot_baseline()
    finally:
        rt.db.close()
    return {"seconds": round(time.time() - t, 1), "runs": [r["run_id"] for r in runs],
            "baseline": str(rt.baseline_path)}


def main() -> None:
    ap = argparse.ArgumentParser(description="ADAPT API runtime utilities")
    ap.add_argument("--bootstrap", action="store_true", help="sync + day-0 cycle + workspace baseline")
    args = ap.parse_args()
    if args.bootstrap:
        print(bootstrap())


if __name__ == "__main__":
    main()
