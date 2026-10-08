"""Run structural STRESS_EVAL worlds separately; never merge into PRIMARY_EVAL.

The current world's paid/unpaid prospects are independent: zero cannibalization
is a declared reference control, not a newly introduced structural difference.
The other profiles change future weekly demand and GA4 report availability.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for package in ("backend", "world", "evalharness"):
    sys.path.insert(0, str(ROOT / package))

from adapt.decide.hashing import content_hash  # noqa: E402
from evalharness.runner import run_decision_eval  # noqa: E402
from world.seed import seed_world  # noqa: E402
from world.truth import WorldConfig  # noqa: E402

PROFILES = {1001: "no_cannibalization", 1002: "double_weekly_seasonality", 1003: "tracking_lag_2d"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backbone", type=Path, default=ROOT / "data/world/backbone")
    parser.add_argument(
        "--global-ads", type=Path, default=ROOT / "data/raw/global_ads/global_ads_performance_dataset.csv"
    )
    parser.add_argument("--work", type=Path, default=ROOT / "data/stress")
    parser.add_argument("--out", type=Path, default=ROOT / "evidence/stress.json")
    parser.add_argument("--days", type=int, default=60)
    args = parser.parse_args()
    if not 1 <= args.days <= 180 or not args.backbone.is_dir() or not args.global_ads.is_file():
        parser.error("Profiled backbone/Global Ads inputs and 1–180 days are required")
    worlds = []
    for seed, profile in PROFILES.items():
        source = args.work / f"seed{seed}/seeded"
        store, truth = seed_world(
            WorldConfig(seed, args.backbone, args.global_ads, stress_profile=profile), source, overwrite=True
        )
        store.close()
        result = run_decision_eval(
            source,
            args.work / f"seed{seed}/forks",
            args.days,
            progress=lambda day, total, profile=profile: print(f"{profile}: {day}/{total}", flush=True),
        )
        worlds.append(
            {
                "seed": seed,
                "profile": profile,
                "result": result,
                "structural_difference": profile != "no_cannibalization",
                "note": "Reference: independent prospect truth already has zero cross-channel cannibalization"
                if profile == "no_cannibalization"
                else "Unseen structural perturbation in future days",
            }
        )
    payload = {
        "class": "STRESS_EVAL",
        "worlds": worlds,
        "seeds": list(PROFILES),
        "note": "Descriptive robustness results; never headline evidence or autonomy qualification.",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"payload": payload, "sha256": content_hash(payload)}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
