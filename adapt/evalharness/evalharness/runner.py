"""Strategy forks and the evaluation day loop (spec §16, §22.8).

Each strategy runs in ONE continuously evolving fork of the same seeded world for the whole horizon (inventory,
replenishment, fatigue, adstock and prior budgets propagate day to day); forks share the seed, so every exogenous
shock and every latent prospect is identical across strategies (common random numbers, T35). The world runs
in-process behind its FastAPI app (the ADAPT side still talks to it over its HTTP API, exactly as in production);
the eval process additionally reads the world's store (truth) to grade, which only evalharness may do.

Realized metrics (truth, from the world's fact tables over the evaluation days):
  CAA = sum over orders of (price - discount - COGS - ship - fee - price if returned) - spend   (paid + unpaid)
  spend, CAA per rupee spent, stock-risk days = SKU-days with demand lost to a stockout.
"""

from __future__ import annotations

import shutil
import time
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from adapt.core.db import Database
from adapt.execute.adapters import build_adapters
from adapt.ingest.http import SourceHttp
from adapt.reconcile.build import logical_now
from evalharness.strategies import REGISTRY, Envelope
from world.main import create_app


class Fork:
    def __init__(self, seeded_dir: Path, work_dir: Path, name: str, workspace: Path | None = None):
        self.dir = Path(work_dir) / name
        if self.dir.exists():
            shutil.rmtree(self.dir)
        shutil.copytree(seeded_dir, self.dir / "world")
        if workspace is not None:  # a synced day-0 workspace of the same seeded world (synced_template)
            shutil.copyfile(workspace, self.dir / "workspace.duckdb")
        self._stack = ExitStack()
        self.client = self._stack.enter_context(TestClient(create_app(world_dir=self.dir / "world")))
        self.db = Database(self.dir / "workspace.duckdb")
        self.http = SourceHttp("http://testserver", client=self.client, sleep=lambda s: None)
        self.adapters = build_adapters(self.client)  # every platform as its mock (TikTok / Amazon when seeded)
        self.store = self.client.app.state.store
        truth = self.store.ctx.truth
        self.platform_of = {c.budget_id: c.platform for c in truth.catalog.campaigns}
        self.truth = truth

    def day(self) -> int:
        return self.store.clock()[1]

    def sync_baseline(self) -> None:
        from adapt.ingest.sync import run_sync
        from adapt.reconcile.build import build_canonical

        run_sync(self.db, self.http)
        build_canonical(self.db, self.as_of())

    def as_of(self):
        return logical_now(self.truth.config.world_date(self.day()))

    def advance(self, days: int = 1) -> None:
        r = self.client.post("/control/advance", json={"days": days},
                             headers={"X-Request-ID": f"eval-adv-{self.day()}-{days}"})
        if r.status_code != 200:
            raise RuntimeError(r.text)

    def day0_total(self) -> float:
        return float(sum(a for (a,) in self.store.read("SELECT amount FROM budgets_state WHERE status = 'ENABLED'")))

    def close(self) -> None:
        self.db.close()
        self._stack.close()


def realized(store, lo: int, hi: int) -> dict:
    """Truth-side realized metrics over world days [lo, hi]."""
    rev = store.read("""SELECT coalesce(sum(unit_price_inr - discount_inr - cogs_inr - ship_cost_inr - payment_fee_inr
                                       - CASE WHEN returned THEN unit_price_inr ELSE 0 END), 0),
                               count(*) FROM fact_orders WHERE day BETWEEN ? AND ?""", [lo, hi])[0]
    spend = store.read("SELECT coalesce(sum(spend_inr), 0) FROM fact_ad_campaign_daily WHERE day BETWEEN ? AND ?",
                       [lo, hi])[0][0]
    stock_risk = store.read("""SELECT count(DISTINCT (day, sku)) FROM fact_lost_demand
                               WHERE day BETWEEN ? AND ? AND units > 0""", [lo, hi])[0][0]
    caa = float(rev[0]) - float(spend)
    return {"caa": caa, "spend": float(spend), "orders": int(rev[1]),
            "caa_per_rupee": caa / float(spend) if spend else None, "stock_risk_days": int(stock_risk)}


def exogenous_signature(store, lo: int, hi: int) -> list:
    """Strategy-independent world draws (T35): unpaid purchase ATTEMPTS per category-day (orders + demand lost to a
    stockout). Budgets cannot move them; only common random numbers make them identical across forks."""
    return store.read("""
        SELECT day, category_code, sum(n) FROM (
            SELECT day, category_code, count(*) AS n FROM fact_orders
             WHERE campaign_id IS NULL AND category_code IS NOT NULL AND day BETWEEN ? AND ? GROUP BY 1, 2
            UNION ALL
            SELECT l.day, s.category_code, sum(l.units) FROM fact_lost_demand l
            JOIN (SELECT DISTINCT sku, category_code FROM fact_orders WHERE sku IS NOT NULL) s USING (sku)
             WHERE l.campaign_id IS NULL AND l.day BETWEEN ? AND ? GROUP BY 1, 2)
        GROUP BY 1, 2 ORDER BY 1, 2""", [lo, hi, lo, hi])


def violations(store, lo: int, hi: int, box: float) -> list[dict]:
    """Guardrail breaches by EXECUTED platform mutations (must be 0): within a world day, a budget moved more than the
    box from its start-of-day amount. Replays the committed world log in sequence order."""
    import json

    amounts: dict[str, float] = {}
    start_of_day: dict[str, float] = {}
    day = None
    out = []
    for op, payload, result in store.read("SELECT operation, request_payload, result FROM world_log ORDER BY seq"):
        p, res = json.loads(payload), json.loads(result)
        if op == "set_budget":                      # control plane: seeding, scripted human edits, the sim mirror
            amounts[p["budget_id"]] = float(p["amount"])
        elif op == "reset":
            amounts, start_of_day = {}, {}
        elif op == "advance":
            day = res.get("day")
            start_of_day = dict(amounts)
        elif op == "platform_set_budget" and res.get("applied"):
            b = p["budget_id"]
            before = start_of_day.get(b, amounts.get(b))
            amounts[b] = float(res["amount"])
            if day is not None and lo <= day <= hi and before and abs(amounts[b] / before - 1) > box + 1e-3:
                out.append({"day": day, "budget_id": b, "from": before, "to": amounts[b]})
    return out


def perturbation_check(f: Fork, env: Envelope, work_dir: Path, n: int = 10, days: int = 7, seed: int = 0) -> dict:
    """Transition-function check of the oracle (spec §22.8): from the oracle fork's current state, hold the oracle's
    allocation for `days` in a short-lived sub-fork, and do the same for n random feasible perturbations of it (each
    unit moved within the box, fitted to the envelope); the suboptimality rate = the share of perturbations whose
    REALIZED CAA beats the oracle's. Sub-forks are discarded afterwards."""
    import numpy as np

    root = Path(work_dir) / f"perturb-{f.day()}"
    if root.exists():
        shutil.rmtree(root)
    base = root / "src"                                   # the snapshot; sub-forks live in a sibling directory
    base.mkdir(parents=True)
    shutil.copy(f.dir / "world" / "sim_truth.duckdb", base / "sim_truth.duckdb")
    f.store.snapshot_to(base / "sim_state.duckdb")
    current = {b: float(a) for _p, b, a, s in f.store.read("SELECT platform, budget_id, amount, status "
                                                             "FROM budgets_state") if s == "ENABLED"}
    rng = np.random.default_rng(seed + f.day())
    allocations = [current]
    for _ in range(n):
        cand = {u: v * (1 + rng.uniform(-env.box, env.box)) for u, v in current.items()}
        allocations.append(env.fit(current, cand, day=10_000))
    results = []
    for k, alloc in enumerate(allocations):
        sub = Fork(base, root / "subs", f"p{k}")
        try:
            lo = sub.day()
            for b, amount in alloc.items():
                sub.client.post("/control/budget", json={"platform": sub.platform_of[b], "budget_id": b,
                                                         "amount": round(amount, 2)},
                                headers={"X-Request-ID": f"perturb-{k}-{b}", "X-Actor-ID": "eval-perturbation"})
            sub.advance(days)
            results.append(realized(sub.store, lo, lo + days - 1)["caa"])
        finally:
            sub.close()
    shutil.rmtree(root, ignore_errors=True)
    beats = sum(r > results[0] for r in results[1:])
    return {"day": f.day(), "oracle_caa": results[0], "perturbed_caa": results[1:], "beats": beats,
            "suboptimality_rate": beats / n}


def synced_template(seeded_dir: Path, work_dir: Path) -> Path:
    """ONE synced day-0 workspace per seed (the 365-day backfill + canonical state), copied into every fork: all
    forks start from the identical seeded world, so each strategy's first day is an incremental sync instead of six
    identical backfills. Results are unchanged (the same data, the same logical times)."""
    path = Path(work_dir) / "_template" / "workspace.duckdb"
    if path.exists():
        return path
    f = Fork(seeded_dir, work_dir, "_template")
    try:
        f.sync_baseline()
    finally:
        f.close()
    return path


def run_decision_eval(seeded_dir: Path, work_dir: Path, days: int, strategies=None, progress=None,
                      perturb_every: int | None = None) -> dict:
    """All strategies on forks of one seeded world for `days` days. Returns realized metrics and logs per strategy.
    perturb_every = 7 runs the oracle's weekly perturbation check (off in CI smoke runs)."""
    names = list(strategies or REGISTRY)
    template = synced_template(seeded_dir, work_dir)
    forks = {n: Fork(seeded_dir, work_dir, n, workspace=template) for n in names}
    try:
        B = next(iter(forks.values())).day0_total()
        start = next(iter(forks.values())).day()
        strat = {n: REGISTRY[n](forks[n], Envelope(B)) for n in names}
        timing = {n: 0.0 for n in names}
        perturbations = []
        for k in range(days):
            for n in names:
                f = forks[n]
                t = time.time()
                strat[n].step(f.as_of(), f.day())
                timing[n] += time.time() - t
                if n == "oracle" and perturb_every and k % perturb_every == 0:
                    perturbations.append(perturbation_check(f, strat[n].env, work_dir))
                f.advance(1)
            if progress:
                progress(k + 1, days)
        lo, hi = start, start + days - 1
        out = {"start_day": start, "days": days, "budget_ceiling": B, "reserve": 0.0, "strategies": {}}
        sigs = {}
        for n in names:
            f = forks[n]
            out["strategies"][n] = {**realized(f.store, lo, hi),
                                    "violations": violations(f.store, lo, hi, strat[n].env.box),
                                    # spec §22.7: every forced safety intervention is listed per strategy
                                    "forced_interventions": sum(1 for e in strat[n].log
                                                                if e.get("forced_intervention")),
                                    "seconds_per_day": timing[n] / max(days, 1), "log": strat[n].log}
            sigs[n] = exogenous_signature(f.store, lo, hi)
        ref = sigs[names[0]]
        out["common_random_numbers"] = all(sigs[n] == ref for n in names)
        if perturbations:
            out["oracle_perturbation"] = {"checks": perturbations, "suboptimality_rate": sum(
                p["beats"] for p in perturbations) / sum(len(p["perturbed_caa"]) for p in perturbations)}
        if "adapt" in forks:
            out["strategies"]["adapt"].update(adapt_quality(forks["adapt"].db))
        return out
    finally:
        for f in forks.values():
            f.close()


def adapt_quality(db) -> dict:
    """Replay rate and outcome verdict counts of the ADAPT fork."""
    from adapt.decide import decisions as dec

    out = {"replay": None, "verdicts": {}}
    if db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = 'intel' AND table_name = 'decisions'"):
        ids = [d for (d,) in db.query("SELECT decision_id FROM intel.decisions")]
        matched = 0
        for d in ids:
            r = dec.replay(db, d)
            matched += bool(r.get("match")) or r.get("reason") == "CHOSEN_SCENARIO"
        out["replay"] = {"decisions": len(ids), "matched": matched}
    if db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = 'learn' AND table_name = 'outcomes'"):
        out["verdicts"] = dict(db.query("SELECT verdict, count(*) FROM learn.outcomes GROUP BY 1"))
    return out


# ---- detection / diagnosis (observe mode: no execution) ------------------------------------------------------------
DEFAULT_SCHEDULE = [("S1", 3), ("S7", 8), ("S2", 12), ("S3", 26), ("S5", 34), ("S4", 42)]


def run_detection_eval(seeded_dir: Path, work_dir: Path, days: int, schedule=None, progress=None) -> dict:
    """One observe-mode fork: scheduled scenarios, daily sync + canonical + detect + diagnose, then graded against the
    world's GT incidents with the §16 contract."""
    from adapt.detect.detector import run_detection
    from adapt.diagnose.run import run_diagnosis
    from adapt.ingest.sync import run_sync
    from adapt.reconcile.build import build_canonical

    f = Fork(seeded_dir, work_dir, "detection", workspace=synced_template(seeded_dir, work_dir))
    try:
        start = f.day()
        for key, offset in schedule or DEFAULT_SCHEDULE:
            if offset < days:
                r = f.client.post("/control/scenario", json={"key": key, "start_day": start + offset},
                                  headers={"X-Request-ID": f"sched-{key}-{offset}"})
                if r.status_code != 200:
                    raise RuntimeError(r.text)
        run_sync(f.db, f.http)
        for k in range(days):
            f.advance(1)
            as_of = f.as_of()
            run_sync(f.db, f.http)
            build_canonical(f.db, as_of)
            run_detection(f.db, as_of, f"eval-det-{k}")
            run_diagnosis(f.db, as_of)
            if progress:
                progress(k + 1, days)
        return grade_detection(f, start)
    finally:
        f.close()


def grade_detection(f: Fork, start: int) -> dict:
    import json

    from evalharness.matching import detection_metrics, diagnosis_metrics, negative_set

    hist_end = f.truth.config.history_end_date
    adapt = []
    for aid, ids, metric, direction, cls, first in f.db.query(
            """SELECT anomaly_id, entity_ids, metric, direction, classification, first_detected_at
               FROM intel.anomalies WHERE is_incident OR classification = 'budget_change'"""):
        diag = f.db.query("SELECT ranking FROM intel.diagnoses WHERE anomaly_id = ? ORDER BY as_of LIMIT 1", [aid])
        ranked = []
        if diag:
            r = json.loads(diag[0][0])
            ranked = [d["module"] for d in r["drivers"]
                      if d["level"] in ("STRONG_EVIDENCE", "WEAK_EVIDENCE") and not d.get("offsetting")]
        # detection day = the world day of the last complete day the incident was first seen on
        det_day = (first.date() - timedelta(days=1) - hist_end).days - 1
        adapt.append({"anomaly_id": aid, "entity_ids": json.loads(ids), "metric": metric, "direction": direction,
                      "classification": cls, "detection_day": det_day, "drivers": ranked,
                      "top_driver": ranked[0] if ranked else None})
    keys = ["incident_id", "scenario", "driver", "entity_ids", "metric_set", "direction", "injection_start_day",
            "onset_day", "injection_end_day"]
    gt = []
    for row in f.store.read(f"SELECT {', '.join(keys)} FROM gt_incidents ORDER BY incident_id"):
        g = dict(zip(keys, row, strict=True))
        for k in ("entity_ids", "metric_set", "direction"):
            g[k] = json.loads(g[k])
        gt.append(g)
    return {"start_day": start, "gt_incidents": len(gt), "adapt_incidents": len(adapt),
            "detection": detection_metrics(adapt, gt), "diagnosis": diagnosis_metrics(adapt, gt),
            "negative_set": negative_set(adapt, gt)}
