"""Seed a world: truth file + a state DB that has already lived through the history (spec §10, §17).

  uv run python -m world.seed --seed 42 --out data/world/seed42

History runs through the same world.step as live days. Budgets change on the 1st of each world month as
logged "human" edits (actor history-manager), so ADAPT later sees them in budget history (classifier S7).
Every request id is deterministic, so two seedings of the same seed produce identical semantic state hashes.
"""

from __future__ import annotations

import argparse
import time
from datetime import date
from pathlib import Path

from world.state import WorldStore
from world.step import make_store, manager_budgets
from world.truth import Truth, WorldConfig, build_truth, write_truth

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def seed_world(cfg: WorldConfig, out_dir: str | Path, overwrite: bool = False) -> tuple[WorldStore, Truth]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    truth = build_truth(cfg)
    write_truth(truth, out / "sim_truth.duckdb", overwrite=overwrite)
    state_path = out / "sim_state.duckdb"
    if state_path.exists():
        if not overwrite:
            raise FileExistsError(f"{state_path} exists (overwrite=True reseeds)")
        state_path.unlink()
    store = make_store(state_path, truth)
    s = cfg.seed
    n_hist = truth.history_days
    store.commit("reset", f"seed{s}:reset", "seeder", {"seed": s, "start_day": -n_hist})
    store.commit("init_world", f"seed{s}:init", "seeder", {})
    platform_of = {c.budget_id: c.platform for c in truth.catalog.campaigns}
    months = truth.history_budgets.groupby("from_day", sort=True)["to_day"].max()
    for from_day, to_day in months.items():
        cum = dict(store.read("SELECT creative_id, cum_impressions FROM creative_state"))
        for budget_id, amount in sorted(manager_budgets(truth, cum, int(from_day), int(to_day)).items()):
            store.commit("set_budget", f"seed{s}:budget:{budget_id}:{from_day}", "history-manager",
                         {"platform": platform_of[budget_id], "budget_id": budget_id, "amount": amount,
                          "status": "ENABLED"})
        store.commit("advance", f"seed{s}:advance:{from_day}", "seeder", {"days": int(to_day) - int(from_day) + 1})
    return store, truth


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed a world (truth + simulated history)")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--backbone", default=str(DATA_DIR / "world" / "backbone"))
    ap.add_argument("--global-ads", default=str(DATA_DIR / "raw" / "global_ads" / "global_ads_performance_dataset.csv"))
    ap.add_argument("--brand-scale", type=float, default=10.0)
    ap.add_argument("--history-end", default="2026-09-30", help="world date of the last history day (YYYY-MM-DD)")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()
    cfg = WorldConfig(seed=args.seed, backbone_dir=Path(args.backbone), global_ads_csv=Path(args.global_ads),
                      brand_scale=args.brand_scale, history_end_date=date.fromisoformat(args.history_end))
    out = Path(args.out) if args.out else DATA_DIR / "world" / f"seed{args.seed}"
    t0 = time.time()
    store, _ = seed_world(cfg, out, overwrite=args.overwrite)
    print(f"seeded {out} in {time.time() - t0:.1f}s; clock={store.clock()}")
    store.close()


if __name__ == "__main__":
    main()
