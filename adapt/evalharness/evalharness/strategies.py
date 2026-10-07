"""The six canonical strategies (spec §16, §22.7, §22.8): safe-static, roas-rank, contribution-rank, safe-contribution,
adapt, oracle. All run under the same budget ceiling B (the day-0 total), reserve R (0) and execution-feasibility
envelope (±20%/day box from the current budget, per-unit minimum, 3-day cooldown per unit, max rupees moved per day,
Σ ≤ B − R, the inventory gate), re-allocate once per simulated day, and execute through the same mock platform
adapters (absolute setters + read-back). A baseline preserves spend whenever a feasible receiver exists, otherwise
leaves the amount unallocated and records why; it never breaches the envelope.

Forced interventions: every non-ADAPT strategy also applies the common safety policy (the S3 safety candidate for any
SKU at risk under the stage predicate), exactly as ADAPT's scripted approver does; each is logged.
The oracle obeys hard business / execution feasibility but none of ADAPT's epistemic gates (spec §22.9).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import numpy as np

from adapt.decide.safety import safety_candidates
from adapt.economics.state import guardrails_config, load_state
from adapt.ingest.sync import run_sync
from adapt.reconcile.build import build_canonical

STRATEGIES = ("safe-static", "roas-rank", "contribution-rank", "safe-contribution", "adapt", "oracle")


class Envelope:
    """The common feasibility envelope; B fixed at the strategy's day-0 total (same for every strategy)."""

    def __init__(self, B: float, R: float = 0.0, guardrails: dict | None = None):
        g = guardrails or guardrails_config()
        self.B, self.R = B, R
        self.box = float(g["change"]["max_daily_change_pct"])
        self.unit_min = float(g["change"]["unit_min_budget_inr"])
        self.cooldown = int(g["change"]["cooldown_days"])
        self.moved = float(g["change"]["max_rupees_moved_pct"])
        self.last_change: dict[str, int] = {}

    def bounds(self, uid: str, current: float, day: int) -> tuple[float, float]:
        if day - self.last_change.get(uid, -10_000) < self.cooldown:
            return current, current
        return max(current * (1 - self.box), min(self.unit_min, current)), current * (1 + self.box)

    def feasible(self, current: dict[str, float], new: dict[str, float], day: int) -> list[str]:
        bad = []
        for u, v in new.items():
            lo, hi = self.bounds(u, current[u], day)
            if v < lo - 1e-6 or v > hi + 1e-6:
                bad.append(f"box/cooldown {u}")
        if sum(new.values()) > self.B - self.R + 1e-6:
            bad.append("total budget")
        if sum(abs(new[u] - current[u]) for u in new) > self.moved * sum(current.values()) + 1e-6:
            bad.append("rupees moved cap")
        return bad

    def fit(self, current: dict[str, float], new: dict[str, float], day: int) -> dict[str, float]:
        """Shrink every move proportionally until the allocation is feasible (at most 60 steps), else no move."""
        for _ in range(60):
            if not self.feasible(current, new, day):
                return new
            new = {u: current[u] + 0.9 * (new[u] - current[u]) for u in new}
        return dict(current)

    def round_inside(self, current: dict[str, float], new: dict[str, float], day: int, inc: float = 100.0) -> dict:
        """Round changed budgets to the allocation increment without leaving the box."""
        out = {}
        for u, v in new.items():
            if abs(v - current[u]) <= 1e-6:
                out[u] = v
                continue
            lo, hi = self.bounds(u, current[u], day)
            out[u] = float(min(max(round(v / inc) * inc, np.ceil(lo / inc) * inc), np.floor(hi / inc) * inc))
        return out

    def commit(self, current: dict[str, float], new: dict[str, float], day: int) -> None:
        for u in new:
            if abs(new[u] - current[u]) > 1e-6:
                self.last_change[u] = day


def apply(adapters: dict, platform_of: dict[str, str], current: dict[str, float], new: dict[str, float]) -> dict:
    """The common mock executor: absolute setters with read-back; returns {budget: verified amount}."""
    done = {}
    for bid, amount in sorted(new.items()):
        if abs(amount - current[bid]) <= 1e-6:
            continue
        ad = adapters[platform_of[bid]]
        res = ad.set_budget(bid, amount, request_id=f"eval-{bid}-{amount:.0f}-{len(done)}")
        observed = ad.read(bid)["amount_inr"]
        if res.ok or abs(observed - amount) <= ad.tolerance():
            done[bid] = observed
    return done


# ---- marts-based inputs for the heuristics -----------------------------------------------------------------------
def unit_stats(db, as_of: datetime) -> dict[str, dict]:
    last = as_of.date() - timedelta(days=1)
    camps = {}
    for bid, cid in db.query("SELECT budget_id, campaign_id FROM core.campaigns"):
        camps.setdefault(bid, []).append(cid)
    out = {}
    for bid, cids in camps.items():
        marks = ", ".join("?" * len(cids))
        r7 = db.query(f"""SELECT sum(attributed_net_revenue), sum(spend) FROM marts.campaign_daily
                          WHERE campaign_id IN ({marks}) AND date > ?""", [*cids, last - timedelta(days=7)])[0]
        r14 = db.query(f"""SELECT sum(attributed_cba), sum(spend) FROM marts.campaign_daily
                           WHERE campaign_id IN ({marks}) AND date > ?""", [*cids, last - timedelta(days=14)])[0]
        cover = db.query(f"""
            WITH w AS (SELECT sku, sum(attribution_weight) AS w FROM core.campaign_sku
                       WHERE campaign_id IN ({marks}) AND sku <> '__unmapped__' GROUP BY 1),
                 c AS (SELECT sku, any_value(on_hand) FILTER (WHERE date = ?) AS oh,
                              avg(units) AS d FROM marts.sku_daily WHERE date > ? GROUP BY 1)
            SELECT sum(w.w * least(c.oh / nullif(c.d, 0), 365)) / nullif(sum(w.w), 0) FROM w JOIN c USING (sku)""",
                         [*cids, last, last - timedelta(days=28)])[0][0]
        out[bid] = {"roas7": (r7[0] or 0) / r7[1] if r7[1] else None, "spend7": float(r7[1] or 0),
                    "poas14": (r14[0] or 0) / r14[1] if r14[1] else None, "spend14": float(r14[1] or 0),
                    "cover_days": float(cover) if cover is not None else None}
    return out


def _pairs(current, env, day, ranked, stats, can_receive, eligible_spend_key, min_spend=10_000.0, k=3):
    """Pair the bottom-k with the top-k (k = 3); each pair moves 10% of the bottom unit's budget to the top unit,
    clipped by the envelope; amounts with no feasible receiver stay unallocated."""
    elig = [u for u in ranked if stats[u][eligible_spend_key] >= min_spend]
    new = dict(current)
    unallocated = 0.0
    reasons = []
    tops, bottoms = elig[:k], list(reversed(elig))[:k]
    for top, bottom in zip(tops, bottoms, strict=False):
        if top == bottom:
            continue
        lo_b, _ = env.bounds(bottom, current[bottom], day)
        amount = min(0.10 * current[bottom], new[bottom] - lo_b)
        if amount <= 0:
            continue
        new[bottom] -= amount
        _, hi_t = env.bounds(top, current[top], day)
        give = min(amount, max(hi_t - new[top], 0.0)) if can_receive(top) else 0.0
        new[top] += give
        if give < amount:
            unallocated += amount - give
            reasons.append(f"{top}: no feasible headroom for {amount - give:,.0f}")
    return env.fit(current, new, day), unallocated, reasons


# ---- strategies ------------------------------------------------------------------------------------------------
class Strategy:
    name = "base"

    def __init__(self, fork, env: Envelope):
        self.fork, self.env = fork, env
        self.log: list[dict] = []

    def refresh(self, as_of):
        run_sync(self.fork.db, self.fork.http)
        build_canonical(self.fork.db, as_of)

    def current(self) -> dict[str, float]:
        return {b: float(a) for b, a in self.fork.db.query("SELECT budget_id, current_amount_inr FROM core.budgets")}

    def forced_safety(self, as_of, current) -> dict[str, float]:
        """The common safety policy (S3 safety candidate, auto-approved), on a curve-free state."""
        from adapt.decide.optimizer import Optimizer

        state = load_state(self.fork.db, as_of, curves={})
        cands = safety_candidates(Optimizer(state))
        new = dict(current)
        for c in cands:
            for leg in c["legs"]:
                new[leg["unit_id"]] = min(new[leg["unit_id"]], leg["after"])
        if cands:
            self.log.append({"as_of": as_of.isoformat(), "forced_intervention": [c["sku"] for c in cands]})
        return new

    def decide(self, as_of, current, day) -> dict[str, float]:
        return current

    def step(self, as_of: datetime, day: int) -> dict:
        self.refresh(as_of)
        current = self.current()
        new = self.forced_safety(as_of, current)
        new = self.decide(as_of, new, day) if new == current else new
        new = self.env.fit(current, self.env.round_inside(current, new, day), day)
        bad = self.env.feasible(current, new, day)
        if bad:
            self.log.append({"as_of": as_of.isoformat(), "infeasible_skipped": bad})
            return {}
        done = apply(self.fork.adapters, self.fork.platform_of, current, new)
        self.env.commit(current, new, day)
        return done


class SafeStatic(Strategy):
    name = "safe-static"


class RoasRank(Strategy):
    name = "roas-rank"

    def decide(self, as_of, current, day):
        stats = unit_stats(self.fork.db, as_of)
        ranked = sorted((u for u in current if stats.get(u, {}).get("roas7") is not None),
                        key=lambda u: (-stats[u]["roas7"], u))
        new, un, why = _pairs(current, self.env, day, ranked, stats, lambda u: True, "spend7")
        if un:
            self.log.append({"as_of": as_of.isoformat(), "unallocated": un, "why": why})
        return new


class ContributionRank(Strategy):
    name = "contribution-rank"

    def can_receive(self, stats, u) -> bool:
        c = stats[u]["cover_days"]
        return c is None or c >= 7

    def decide(self, as_of, current, day):
        stats = unit_stats(self.fork.db, as_of)
        ranked = sorted((u for u in current if stats.get(u, {}).get("poas14") is not None),
                        key=lambda u: (-stats[u]["poas14"], u))
        new, un, why = _pairs(current, self.env, day, ranked, stats, lambda u: self.can_receive(stats, u), "spend14")
        if un:
            self.log.append({"as_of": as_of.isoformat(), "unallocated": un, "why": why})
        return new


class SafeContribution(ContributionRank):
    name = "safe-contribution"

    def decide(self, as_of, current, day):
        new = super().decide(as_of, current, day)
        stats = unit_stats(self.fork.db, as_of)
        if not any((stats[u]["poas14"] or 0) > 1.2 for u in stats if u in current):
            for u in sorted(current):
                p = stats.get(u, {}).get("poas14")
                if p is not None and p < 1.0 and abs(new[u] - current[u]) < 1e-6:
                    lo, _ = self.env.bounds(u, current[u], day)
                    new[u] = lo  # losing money after ads: cut up to 20%/day, never reallocated
        return new


class Adapt(Strategy):
    """The full product: one pipeline cycle; a scripted sim manager approves every pending decision of the run (still
    subject to staleness), executed through the C5 saga."""
    name = "adapt"

    def step(self, as_of: datetime, day: int) -> dict:
        from adapt.decide import decisions as dec
        from adapt.execute.saga import execute_decision
        from adapt.pipeline.cycle import run_cycle

        db = self.fork.db
        summary = run_cycle(db, self.fork.http, as_of, self.fork.adapters, sleep=lambda s: None)
        executed = {}
        pending = [d for (d,) in db.query("SELECT decision_id FROM intel.decisions WHERE created_at = ? "
                                          "ORDER BY class DESC, decision_id", [as_of])]
        for did in pending:
            d = dec.get_decision(db, did)
            if d["status"] != "PENDING_APPROVAL":
                continue
            try:
                state_now = load_state(db, as_of)
                dec.approve(db, did, d["decision_hash"], "sim-manager", "manager", as_of, state_now)
                out = execute_decision(db, did, self.fork.adapters, "sim-manager", as_of, state_now,
                                       sleep=lambda s: None)
                executed[did] = out["state"]
            except dec.DecisionError as exc:
                self.log.append({"as_of": as_of.isoformat(), "decision": did, "skipped": exc.code})
        self.log.append({"as_of": as_of.isoformat(), "steps": {k: v for k, v in summary["steps"].items()
                                                               if k in ("optimize", "decide", "measure")},
                         "executed": executed})
        return executed


class Oracle(Strategy):
    """Clairvoyant benchmark under TRUE parameters: greedy on world/truth_economics.expected_caa over the same hard
    envelope. Not an upper bound; its suboptimality is checked by perturbation (decision_eval)."""
    name = "oracle"

    def decide(self, as_of, current, day):
        from world.truth_economics import expected_caa, snapshot

        snap = snapshot(self.fork.store)
        truth = self.fork.store.ctx.truth
        new = dict(current)
        step = 0.02
        val = expected_caa(truth, snap, new)
        for _ in range(200):
            best = None
            for u in sorted(new):
                lo, hi = self.env.bounds(u, current[u], day)
                for sign in (1, -1):
                    cand = dict(new)
                    cand[u] = float(np.clip(new[u] * (1 + sign * step), lo, hi))
                    if abs(cand[u] - new[u]) < 1 or self.env.feasible(current, cand, day):
                        continue
                    v = expected_caa(truth, snap, cand)
                    if v > val + 1.0 and (best is None or v > best[0]):
                        best = (v, cand)
            if best is None:
                break
            val, new = best
        self.log.append({"as_of": as_of.isoformat(), "truth_caa_7d": val})
        return new


REGISTRY = {c.name: c for c in (SafeStatic, RoasRank, ContributionRank, SafeContribution, Adapt, Oracle)}


def dump(strategy: Strategy) -> str:
    return json.dumps(strategy.log, default=str)
