"""Stage-1-scope evaluation (Stage 2 ★, spec §16): held-out seeds, paired strategies, detection / diagnosis, report.

  uv run python scripts/run_eval.py --bench                # one seed, 3 days: stage timings -> evidence/bench.json
  uv run python scripts/run_eval.py --seeds eval --days 60 -j 8   # PRIMARY_EVAL, seeds 101-120 -> evidence/eval.json
  uv run python scripts/run_eval.py --seeds reduced ...           # the fixed subset 101-110 (decide BEFORE results)

Each seed: seed the world (truth + 365-day history, cached under --work), then in its own process run the six
strategies on forks for --days and the observe-mode detection fork with the default scenario schedule. Precomputed
overnight; the demo never runs it live. Needs the real backbone (data/world/backbone) and the Global Ads CSV.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in ("backend", "world", "evalharness"):
    sys.path.insert(0, str(ROOT / p))

from evalharness import report, seeds  # noqa: E402
from evalharness.runner import run_decision_eval, run_detection_eval  # noqa: E402

DATA = ROOT / "data"


def code_sha() -> str:
    """The commit the report was produced from (+dirty when the tree had uncommitted changes)."""
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout.strip()
        return sha + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def seeded(seed: int, args) -> Path:
    from world.seed import seed_world
    from world.truth import WorldConfig

    out = Path(args.work) / f"seed{seed}" / "seeded"
    if (out / "sim_state.baseline.duckdb").exists():
        return out
    cfg = WorldConfig(seed=seed, backbone_dir=Path(args.backbone), global_ads_csv=Path(args.global_ads),
                      brand_scale=args.brand_scale, history_end_date=date.fromisoformat(args.history_end))
    store, _ = seed_world(cfg, out, overwrite=True)
    store.close()
    return out


def one_seed(seed: int, args) -> tuple[int, dict, dict]:
    t = time.time()
    world = seeded(seed, args)
    t_seed = time.time() - t
    work = Path(args.work) / f"seed{seed}"
    dec = run_decision_eval(world, work / "forks", args.days, args.strategies, perturb_every=7)
    det = run_detection_eval(world, work / "forks", args.detection_days) if args.detection_days else None
    dec["seconds_seeding"] = t_seed
    return seed, dec, det


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seeds", default="eval", help="eval | reduced | tuning | comma-separated list")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--detection-days", type=int, default=50)
    ap.add_argument("--strategies", nargs="*", default=None)
    ap.add_argument("-j", "--jobs", type=int, default=1)
    ap.add_argument("--work", default=str(DATA / "eval"))
    ap.add_argument("--out", default=str(ROOT / "evidence" / "eval.json"))
    ap.add_argument("--backbone", default=str(DATA / "world" / "backbone"))
    ap.add_argument("--global-ads", default=str(DATA / "raw" / "global_ads" / "global_ads_performance_dataset.csv"))
    ap.add_argument("--brand-scale", type=float, default=10.0)
    ap.add_argument("--history-end", default="2026-09-30")
    ap.add_argument("--bench", action="store_true", help="one seed, 3 days, timings to evidence/bench.json")
    args = ap.parse_args()
    if args.bench:
        import platform

        args.days, args.detection_days = 3, 0
        seed, dec, _ = one_seed(seeds.EVAL[0], args)
        bench = {"seed": seed, "days": 3, "seconds_seeding": dec["seconds_seeding"],
                 "seconds_per_day": {s: v["seconds_per_day"] for s, v in dec["strategies"].items()},
                 "machine": {"platform": platform.platform(), "processor": platform.processor(),
                             "python": platform.python_version()}}
        out = ROOT / "evidence" / "bench.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(bench, indent=2))
        per_seed = sum(bench["seconds_per_day"].values()) * 60
        print(json.dumps(bench, indent=2))
        print(f"estimated {per_seed / 3600:.1f} h per seed for 60 days (all strategies, serial)")
        return 0
    chosen = {"eval": seeds.EVAL, "reduced": seeds.EVAL_REDUCED, "tuning": seeds.TUNING}.get(
        args.seeds) or tuple(int(x) for x in args.seeds.split(","))
    results, detections = {}, {}
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for seed, dec, det in pool.map(one_seed, chosen, [args] * len(chosen)):
            results[seed] = dec
            if det:
                detections[seed] = det
            print(f"seed {seed}: " + ", ".join(f"{s} CAA {v['caa']:,.0f}" for s, v in dec["strategies"].items()))
    rep = report.build(results, detections or None)
    rep["contract"] = {"class": "PRIMARY_EVAL" if args.seeds in ("eval", "reduced") else "OTHER",
                       "days": args.days, "seeds_requested": list(chosen)}
    rep["generated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    rep["code_sha"] = code_sha()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps({k: rep[k] for k in ("N", "primary", "oracle_capture", "safety_violations", "replay")}, indent=2,
                     default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
