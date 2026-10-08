"""Warm-up worlds 901-903 + held-out reliability world 904 (Stage 3, spec §8.4 / §17): earn simulation autonomy.

  uv run python scripts/run_warmup.py --days 60 -j 4        # -> evidence/warmup_track_record.json (+ contract report)
  uv run python scripts/run_warmup.py --import-into data/workspaces/demo.duckdb   # API stopped: single writer

Each world is seeded with the simulated TikTok + Amazon channels and run in its own process; the merged, hashed
track-record artifact is checked against the calibration contract for TikTok. If the contract fails, rerun with
more --days (up to 120, spec §17); the report says which assertion failed. The API bootstrap imports the artifact
automatically when it exists (adapt.api.runtime).
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in ("backend", "world", "evalharness"):
    sys.path.insert(0, str(ROOT / p))

from evalharness import seeds, warmup  # noqa: E402

DATA = ROOT / "data"
OUT = ROOT / "evidence" / "warmup_track_record.json"


def one(seed: int, args) -> dict:
    work = Path(args.work) / f"world{seed}"
    seeded = warmup.seed_warmup_world(seed, work / "seeded", Path(args.backbone), Path(args.global_ads),
                                      args.brand_scale, date.fromisoformat(args.history_end))
    return warmup.run_world(seed, seeded, work, args.days)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("-j", "--jobs", type=int, default=4)
    ap.add_argument("--work", default=str(DATA / "warmup"))
    ap.add_argument("--backbone", default=str(DATA / "world" / "backbone"))
    ap.add_argument("--global-ads", default=str(DATA / "raw" / "global_ads" / "global_ads_performance_dataset.csv"))
    ap.add_argument("--brand-scale", type=float, default=10.0)
    ap.add_argument("--history-end", default="2026-09-30")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--import-into", help="import the artifact into this workspace DuckDB (API must be stopped)")
    args = ap.parse_args()
    if args.import_into:
        from adapt.core.db import Database
        from adapt.learn.qualification import import_track_record

        art = json.loads(Path(args.out).read_text(encoding="utf-8"))
        db = Database(Path(args.import_into))
        try:
            print(import_track_record(db, art, datetime.now()))
        finally:
            db.close()
        return 0
    worlds = (*seeds.WARMUP, seeds.RELIABILITY)
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(one, worlds, [args] * len(worlds)))
    from adapt.learn.qualification import merge_artifacts

    art = merge_artifacts([r["artifact"] for r in results],
                          "simulation track record earned on worlds 901-903 (+904 reliability check)")
    report = warmup.assert_contract(art, "tiktok")
    art["contract"] = {k: report[k] for k in ("channel", "passed", "checks", "qualified_regions")}
    art["runs"] = [{k: r[k] for k in ("seed", "days", "seconds_per_day", "verdicts")} for r in results]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(art, indent=2, default=str), encoding="utf-8")
    print(json.dumps(art["contract"], indent=2, default=str))
    if not report["passed"]:
        print("CONTRACT FAILED: rerun with more --days (up to 120); a failed run is never imported automatically")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
