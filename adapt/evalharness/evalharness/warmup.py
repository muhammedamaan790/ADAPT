"""Warm-up worlds for simulation autonomy qualification (Stage 3; spec §8.4, §17 "Warm-up on separate worlds").

Each warm-up world (901-903) and the held-out reliability world (904) is an independent seeded world with its own
hidden truth, disjoint from the demo seed 42, the tuning seeds and the eval seeds, seeded WITH the simulated TikTok and
Amazon channels. ADAPT runs in Approve mode for `days` days while a scripted sim manager approves its decisions (the
`adapt` strategy of the evaluation harness, unchanged); every executed decision is measured by ADAPT's own outcome
step. The world's outcome track record (decision-time confidence region, model P(loss), verdict, realized effect per
channel) is exported as a versioned, hashed artifact; curves and world parameters never leave the world.

assert_contract() is the CI check on the merged artifact for a channel (default TikTok): >= 30 matured OPTIMIZATION
outcomes, >= 10 executed decisions with measured outcomes, a QUALIFIED region (Wilson lower >= 0.60 over 901-903 and a
reliability PASS on 904), and the P(loss) monitor not tripped. A failure is reported, never papered over: the
warm-up is extended (more days) and rerun.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from adapt.core.db import Database
from adapt.learn import qualification as q
from evalharness import seeds
from evalharness.runner import run_decision_eval

CHANNELS = ("tiktok", "amazon_sp")


def seed_warmup_world(seed: int, out: Path, backbone: Path, global_ads: Path, brand_scale: float,
                      history_end: date) -> Path:
    from world.seed import seed_world
    from world.truth import WorldConfig

    if (out / "sim_state.baseline.duckdb").exists():
        return out
    cfg = WorldConfig(seed=seed, backbone_dir=backbone, global_ads_csv=global_ads, brand_scale=brand_scale,
                      history_end_date=history_end, extra_channels=CHANNELS)
    store, _ = seed_world(cfg, out, overwrite=True)
    store.close()
    return out


def run_world(seed: int, seeded: Path, work: Path, days: int) -> dict:
    """Run ADAPT (scripted approvals) on one warm-up world and export its track record."""
    res = run_decision_eval(seeded, work / "forks", days, ["adapt"])
    db = Database(work / "forks" / "adapt" / "workspace.duckdb")
    try:
        label = "simulation track record earned on worlds 901-903 (+904 reliability check)"
        art = q.export_track_record(db, seed, label)
    finally:
        db.close()
    return {"seed": seed, "days": days, "artifact": art, "seconds_per_day": res["strategies"]["adapt"]
            ["seconds_per_day"], "verdicts": res["strategies"]["adapt"].get("verdicts")}


def assert_contract(artifact: dict, channel: str = "tiktok") -> dict:
    recs = artifact["records"]
    mine = [r for r in recs if r["channel"] == channel]
    warm = [r for r in mine if r["world_id"] in seeds.WARMUP]
    regions = {reg: q.qualify(recs, channel, reg) for reg in q.REGIONS}
    qualified = [reg for reg, x in regions.items() if x["status"] == "QUALIFIED"]
    monitor = q.ploss_monitor(recs, channel)
    checks = {
        "matured_outcomes>=30": sum(r["verdict"] in q.DECISIVE for r in warm) >= 30,
        "executed_with_outcome>=10": len({(r["world_id"], r["decision_id"]) for r in mine}) >= 10,
        "region_qualified": bool(qualified),
        "ploss_monitor_not_tripped": not monitor["tripped"],
        "artifact_hash_verified": q.verify_artifact(artifact),
    }
    return {"channel": channel, "passed": all(checks.values()), "checks": checks, "qualified_regions": qualified,
            "regions": regions, "ploss_monitor": monitor}
