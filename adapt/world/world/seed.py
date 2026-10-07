"""Seed a world: truth file + a state DB that has already lived through the history (spec §10, §17).

  uv run python -m world.seed --seed 42 --out data/world/seed42

History runs through the same world.step as live days. The manager plans each world month on its 1st and tweaks
every budget weekly around that plan (benchmarks.yaml history_manager); every change is a logged "human" edit
(actor history-manager), so ADAPT later sees them in budget history (classifier S7).
Every request id is deterministic, so two seedings of the same seed produce identical semantic state hashes.
"""

from __future__ import annotations

import argparse
import math
import time
from datetime import date
from pathlib import Path

from world.priors import benchmarks
from world.rng import generator
from world.state import WorldStore
from world.step import make_store, manager_budgets
from world.truth import Truth, WorldConfig, build_truth, write_truth

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
BASELINE_NAME = "sim_state.baseline.duckdb"  # the world right after its history; /control/reset restores it


ScheduledScenario = tuple[str, int, dict]  # (key, start world day, params)


def seed_world(cfg: WorldConfig, out_dir: str | Path, overwrite: bool = False,
               scenarios: list[ScheduledScenario] | None = None) -> tuple[WorldStore, Truth]:
    """Seed truth + history. `scenarios` are activated when the history clock reaches their start day, so their
    targets resolve against the real state at that moment (e.g. DEMO_01 at day -10: the story is in place on day 0).
    """
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
    pending = sorted(scenarios or [], key=lambda x: x[1])
    for key, start, _ in pending:
        if not -n_hist <= start <= 0:
            raise ValueError(f"scheduled scenario {key} start {start} is outside the history [-{n_hist}, 0]")
    sigma = benchmarks()["history_manager"]["weekly_adjust_sigma"]
    months = truth.history_budgets.groupby("from_day", sort=True)["to_day"].max()
    for from_day, to_day in months.items():
        cum = dict(store.read("SELECT creative_id, cum_impressions FROM creative_state"))
        plan = manager_budgets(truth, cum, int(from_day), int(to_day))
        for seg_from in range(int(from_day), int(to_day) + 1, 7):  # weekly tweaks around the month's plan
            seg_to = min(seg_from + 6, int(to_day))
            for budget_id, amount in sorted(plan.items()):
                z = float(generator(s, seg_from, budget_id, "history:manager_adjust").standard_normal())
                tweaked = float(round(amount * math.exp(sigma * z - sigma ** 2 / 2) / 100.0) * 100.0)
                store.commit("set_budget", f"seed{s}:budget:{budget_id}:{seg_from}", "history-manager",
                             {"platform": platform_of[budget_id], "budget_id": budget_id, "amount": tweaked,
                              "status": "ENABLED"})
            day = seg_from
            while day <= seg_to:
                if pending and pending[0][1] == day:
                    key, start, params = pending.pop(0)
                    store.commit("activate_scenario", f"seed{s}:scenario:{key}:{start}", "seeder",
                                 {"key": key, "start_day": start, "params": params})
                    continue
                stop = min(seg_to, pending[0][1] - 1 if pending and pending[0][1] <= seg_to else seg_to)
                store.commit("advance", f"seed{s}:advance:{day}", "seeder", {"days": stop - day + 1})
                day = stop + 1
    for key, start, params in pending:  # scheduled exactly at day 0: active from the first live day
        store.commit("activate_scenario", f"seed{s}:scenario:{key}:{start}", "seeder",
                     {"key": key, "start_day": start, "params": params})
    store.snapshot_to(out / BASELINE_NAME)
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
    ap.add_argument("--demo", action="store_true", help="schedule DEMO_01 at day -10 (the golden demo world)")
    ap.add_argument("--channels", default="", help="Stage 2 SIMULATED channels to add: tiktok,amazon_sp")
    args = ap.parse_args()
    cfg = WorldConfig(seed=args.seed, backbone_dir=Path(args.backbone), global_ads_csv=Path(args.global_ads),
                      brand_scale=args.brand_scale, history_end_date=date.fromisoformat(args.history_end),
                      extra_channels=tuple(c for c in args.channels.split(",") if c))
    out = Path(args.out) if args.out else DATA_DIR / "world" / f"seed{args.seed}"
    t0 = time.time()
    store, _ = seed_world(cfg, out, overwrite=args.overwrite,
                          scenarios=[("DEMO_01", -10, {})] if args.demo else None)
    print(f"seeded {out} in {time.time() - t0:.1f}s; clock={store.clock()}")
    store.close()


if __name__ == "__main__":
    main()
