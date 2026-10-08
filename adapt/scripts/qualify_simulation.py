"""Earn simulation track-record evidence on 901–903; reliability on 904. Never fabricate PASS.

python scripts/qualify_simulation.py --backbone PATH --global-ads PATH --days 60
python scripts/qualify_simulation.py --import-artifact evidence/qualification.json
    --workspace data/workspaces/demo.duckdb
The workspace must be offline for the import (DuckDB refuses a second writer).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for package in ("backend", "world", "evalharness"):
    sys.path.insert(0, str(ROOT / package))

from adapt.core.db import Database  # noqa: E402
from adapt.decide.decisions import get_decision  # noqa: E402
from adapt.decide.hashing import content_hash  # noqa: E402
from adapt.learn.qualification import VERSION, import_artifact, qualify  # noqa: E402
from adapt.pipeline.cycle import run_pipeline  # noqa: E402
from evalharness.runner import Fork  # noqa: E402
from evalharness.strategies import Adapt, Envelope  # noqa: E402
from world.seed import seed_world  # noqa: E402
from world.truth import WorldConfig  # noqa: E402


def collect(db, seed):
    rows = []
    for did, verdict, realized, at in db.query(
        "SELECT decision_id, verdict, realized, measured_at FROM learn.outcomes "
        "WHERE class='OPTIMIZATION' AND verdict IN ('SUCCESS','NEUTRAL','FAILED') ORDER BY decision_id"
    ):
        d = get_decision(db, did)
        verified = db.query("SELECT state FROM exec.sagas WHERE decision_id=? AND kind='EXECUTION'", [did])
        if not verified or verified[0][0] != "SUCCEEDED":
            continue
        conf = db.query(
            "SELECT raw_index FROM ops.decision_confidence WHERE decision_id=? AND decision_hash=?",
            [did, d["decision_hash"]],
        )
        if not conf:
            continue
        for platform in sorted({leg["platform"] for leg in d["legs"]}):
            rows.append(
                {
                    "world_id": seed,
                    "channel": "amazon_sp" if platform == "amazon" else platform,
                    "decision_id": did,
                    "decision_hash": d["decision_hash"],
                    "execution_mode": "MOCK",
                    "verified": True,
                    "class": "OPTIMIZATION",
                    "confidence": conf[0][0],
                    "verdict": verdict,
                    "realized": realized,
                    "prob_loss": d["expected"]["prob_loss"],
                    "violations": sum(not c["passed"] for c in d["checks"]),
                    "measured_at": at.isoformat(),
                    "method": "observed CAA minus frozen MODEL_ESTIMATE counterfactual",
                }
            )
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backbone", type=Path, default=ROOT / "data/world/backbone")
    parser.add_argument(
        "--global-ads", type=Path, default=ROOT / "data/raw/global_ads/global_ads_performance_dataset.csv"
    )
    parser.add_argument("--work", type=Path, default=ROOT / "data/qualification")
    parser.add_argument("--out", type=Path, default=ROOT / "evidence/qualification.json")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--import-artifact", type=Path)
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args()
    if args.import_artifact:
        if not args.workspace:
            parser.error("--workspace required for offline import")
        db = Database(args.workspace)
        try:
            import_artifact(db, json.loads(args.import_artifact.read_text(encoding="utf-8")))
        finally:
            db.close()
        print("Imported hash-verified simulated track record; eligibility remains evidence-gated.")
        return
    if not 1 <= args.days <= 180 or not args.backbone.is_dir() or not args.global_ads.is_file():
        parser.error("1–180 days and the profiled backbone/Global Ads inputs are required")
    all_rows = []
    for seed in (901, 902, 903, 904):
        source = args.work / f"seed{seed}/seeded"
        # Fresh deterministic seeds; no cross-world curve/feature reuse.
        store, _ = seed_world(
            WorldConfig(
                seed, args.backbone, args.global_ads, history_end_date=date(2026, 9, 30), extra_channels=("tiktok",)
            ),
            source,
            overwrite=True,
        )
        store.close()
        fork = Fork(source, args.work / f"seed{seed}", "earned")
        try:
            strategy = Adapt(fork, Envelope(fork.day0_total()))
            for day in range(args.days):
                strategy.step(fork.as_of(), day)
                fork.advance()
                print(f"world={seed} day={day + 1}/{args.days}", flush=True)
            # Let the last approved actions mature without approving fresh proposals.
            for _ in range(15):
                run_pipeline(fork.db, fork.http, fork.as_of(), adapters=fork.adapters, sleep=lambda _: None)
                fork.advance()
            all_rows.extend(collect(fork.db, seed))
        finally:
            fork.close()
    payload = {
        "model_version": VERSION,
        "worlds": [901, 902, 903, 904],
        "rows": all_rows,
        "days": args.days,
        "scope": "SIMULATION_POLICY_QUALIFICATION",
        "counterfactual": "MODEL_ESTIMATE",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"payload": payload, "sha256": content_hash(payload)}, indent=2), encoding="utf-8")
    summary = {
        ch: qualify([r for r in all_rows if r["channel"] == ch]) for ch in sorted({r["channel"] for r in all_rows})
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
