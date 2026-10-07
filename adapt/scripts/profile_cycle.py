"""Profile the daily pipeline cycle on a seeded world (spec §2 target: one simulated day <= 20 s).

  uv run python scripts/profile_cycle.py --world data/world/seed42 --days 3 [--cprofile]

Copies the seeded world to --work, serves it in-process (the world app's TestClient, no ports), backfills the
workspace (day 0, includes the first fits), then advances one day at a time and runs the cycle. Prints per-step
seconds from ops.pipeline_steps for every day and writes them to --out. --cprofile adds a cProfile of the last day
(top functions by cumulative time).
"""

from __future__ import annotations

import argparse
import cProfile
import io
import json
import pstats
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in ("backend", "world"):
    sys.path.insert(0, str(ROOT / p))

from fastapi.testclient import TestClient  # noqa: E402

from adapt.core.db import Database  # noqa: E402
from adapt.execute.adapters import build_adapters  # noqa: E402
from adapt.ingest.http import SourceHttp  # noqa: E402
from adapt.ingest.sync import run_sync, world_today  # noqa: E402
from adapt.pipeline.cycle import run_cycle  # noqa: E402
from adapt.pipeline.scheduler import catch_up  # noqa: E402
from adapt.reconcile.build import logical_now  # noqa: E402
from world.main import create_app  # noqa: E402


def steps(db, run_id: str) -> dict[str, float]:
    return {s: round(float(t), 2) for s, t in db.query(
        "SELECT step, seconds FROM ops.pipeline_steps WHERE run_id = ? ORDER BY seq", [run_id])}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--world", default=str(ROOT / "data" / "world" / "seed42"))
    ap.add_argument("--work", default=str(ROOT / "data" / "profile"))
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--cprofile", action="store_true")
    ap.add_argument("--reuse", action="store_true", help="keep an existing day-0 workspace in --work")
    ap.add_argument("--out", default=str(ROOT / "data" / "profile" / "cycle_profile.json"))
    args = ap.parse_args()
    work = Path(args.work)
    if not args.reuse:
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    wdir = work / "world"
    if not wdir.exists():
        shutil.copytree(args.world, wdir)
    out = {"days": []}
    with TestClient(create_app(world_dir=wdir)) as world:
        http = SourceHttp("http://testserver", client=world, sleep=lambda s: None)
        db = Database(work / "workspace.duckdb")
        adapters = build_adapters(world)
        if not db.query("SELECT 1 FROM information_schema.tables WHERE table_name = 'pipeline_runs'"):
            t = time.time()
            run_sync(db, http)
            out["sync_seconds"] = round(time.time() - t, 1)
            t = time.time()
            r = catch_up(db, http, adapters)[-1]
            out["day0"] = {"seconds": round(time.time() - t, 1), "steps": steps(db, r["run_id"])}
            print("day 0:", json.dumps(out["day0"]), flush=True)
        prof = cProfile.Profile() if args.cprofile else None
        for k in range(args.days):
            world.post("/control/advance", json={"days": 1}, headers={"X-Request-ID": f"profile-{time.time()}"})
            as_of = logical_now(world_today(http))
            last = prof is not None and k == args.days - 1
            t = time.time()
            if last:
                prof.enable()
            r = run_cycle(db, http, as_of, adapters, sleep=lambda s: None)
            if last:
                prof.disable()
            day = {"as_of": as_of.isoformat(), "seconds": round(time.time() - t, 1), "steps": steps(db, r["run_id"])}
            out["days"].append(day)
            print(f"day {k + 1}:", json.dumps(day), flush=True)
        db.close()
    if prof is not None:
        s = io.StringIO()
        pstats.Stats(prof, stream=s).sort_stats("cumulative").print_stats(45)
        out["cprofile"] = s.getvalue()
        print(out["cprofile"])
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
