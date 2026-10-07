"""Response-curve recovery against the world's hidden truth (spec §16 prediction row, §22.2 acceptance).

  uv run python -m evalharness.curve_recovery --world data/world/seed42 --workspace data/workspaces/demo.duckdb

Reads a workspace whose curves are already fitted (adapt.predict.fit_curves) and the world's sim_truth.duckdb.
For each unit with a usable curve (OK or POOLED): true marginal ROAS at current spend = the truth's purchase
elasticity at that spend x the observed average ROAS over the last 14 days (net revenue per purchase is not a curve
parameter, so the truth's dP/ds is converted at the observed revenue per spend). Target: median relative error
<= 30% on tuning seeds.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import timedelta
from pathlib import Path

import numpy as np

from adapt.core.db import Database
from adapt.predict.fit_curves import load_curves
from world.truth import CampaignTruth, Truth, expected_purchases, load_truth


def true_elasticity(members: list[CampaignTruth], spend: float) -> float:
    def purchases(s: float) -> float:
        return sum(expected_purchases(s * t.budget_share, t) for t in members)

    return (math.log(purchases(spend * 1.01)) - math.log(purchases(spend * 0.99))) / (math.log(1.01) - math.log(0.99))


def recovery(db: Database, truth: Truth, window_days: int = 14) -> dict:
    curves = load_curves(db)
    last = db.query("SELECT max(date) FROM marts.campaign_daily")[0][0]
    units = {}
    for unit, a in sorted(curves.items()):
        if a.status == "MODEL_UNAVAILABLE":
            units[unit] = {"status": a.status}
            continue
        cids = [c for (c,) in db.query("SELECT campaign_id FROM core.campaigns WHERE budget_id = ?", [unit])]
        spend, revenue = db.query("""SELECT sum(spend) / ?, sum(attributed_net_revenue) / ? FROM marts.campaign_daily
                                     WHERE campaign_id IN (SELECT unnest(?)) AND date > ?""",
                                  [window_days, window_days, cids, last - timedelta(days=window_days)])[0]
        true_m = true_elasticity([truth.campaigns[c] for c in cids], spend) * revenue / spend
        model_m = a.marginal_roas(spend)
        units[unit] = {"status": a.status, "spend": spend, "true_mroas": true_m, "model_mroas": model_m,
                       "rel_error": abs(model_m / true_m - 1)}
    errs = [u["rel_error"] for u in units.values() if "rel_error" in u]
    return {"units": len(units), "usable": len(errs),
            "status": {s: sum(u["status"] == s for u in units.values()) for s in ("OK", "POOLED", "MODEL_UNAVAILABLE")},
            "median_rel_error": float(np.median(errs)) if errs else None,
            "by_status": {s: float(np.median([u["rel_error"] for u in units.values()
                                              if u["status"] == s and "rel_error" in u]))
                          for s in ("OK", "POOLED") if any(u["status"] == s for u in units.values())},
            "per_unit": units}


def main() -> None:
    ap = argparse.ArgumentParser(description="Response-curve recovery vs world truth")
    ap.add_argument("--world", required=True, help="seeded world dir (contains sim_truth.duckdb)")
    ap.add_argument("--workspace", required=True, help="workspace DuckDB with fitted curves")
    args = ap.parse_args()
    db = Database(args.workspace)
    try:
        out = recovery(db, load_truth(Path(args.world) / "sim_truth.duckdb"))
    finally:
        db.close()
    print(json.dumps({k: v for k, v in out.items() if k != "per_unit"}, indent=2))


if __name__ == "__main__":
    main()
