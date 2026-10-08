"""Exploration (Stage 3, spec §10 "Learning" 3): OFF by default; when enabled, at most 2% of the budget per run.

Bootstrap-draw randomized exploration (NOT Thompson sampling: there is no Bayesian posterior): pick one joint bootstrap
draw with a run-keyed seed and give the exploration amount to the unit with the largest contribution change under
THAT draw, among eligible units only:
  - untouched by the recommended allocation (so the two decisions never set the same budget),
  - inventory gate ALLOW at the recommended allocation plus the increase,
  - a usable curve, positive contribution margin, mapped (no MIX_UNCERTAIN), and no policy flag (cooldown, tracking
    freeze, open incident, data dependency, execution freeze, measurement lock),
  - room inside the +-20%/day box and inside the unallocated budget above the reserve.
Nothing is created when no eligible unit has a positive change under the drawn draw. Exploration has the lowest
precedence (§9.1): it never runs on an entity where anything above it is active (the policy re-validates). Its
outcomes mature by the OPTIMIZATION rule and never feed the optimism correction factor.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

import numpy as np

from adapt.economics.state import objectives_config


def config() -> dict:
    return {"enabled": False, "max_budget_share": 0.02, **(objectives_config().get("exploration") or {})}


def candidate(opt, result: dict, as_of: datetime, cfg: dict | None = None) -> dict | None:
    cfg = cfg or config()
    if not cfg.get("enabled") or result.get("status") != "OK":
        return None
    state, pf, c = opt.state, opt.full, opt.c
    s_rec = np.array([result["allocation"][u.unit_id] for u in state.units], dtype=float)
    free = float(c.B - s_rec.sum() - c.R)
    amount = np.floor(min(float(cfg["max_budget_share"]) * c.B, free) / opt.inc) * opt.inc
    if amount < opt.inc:
        return None
    seed = int(hashlib.sha256(f"explore|{as_of.isoformat()}".encode()).hexdigest()[:8], 16)
    d = int(np.random.default_rng(seed).integers(state.n_draws))
    base = pf.evaluate(s_rec)
    best = None
    for i, u in enumerate(state.units):
        if abs(s_rec[i] - opt.s0[i]) > 1e-9 or i in c.unit_reasons or not u.model_available:
            continue
        if pf.cm[i] <= 0 or u.unmapped_share > 0.20:
            continue
        up = min(amount, float(c.hi[i]) - opt.s0[i])
        up = np.floor(up / opt.inc) * opt.inc
        if up < opt.inc:
            continue
        s = s_rec.copy()
        s[i] += up
        st = opt.fe.state_of(s)
        if not opt.fe.gate_ok(st):
            continue
        gain = float(pf.evaluate(s).delta_caa[d] - base.delta_caa[d])
        if gain > 0 and (best is None or gain > best[0]):
            best = (gain, i, up)
    if best is None:
        return None
    gain, i, up = best
    u = state.units[i]
    s = opt.s0.copy()
    s[i] += up
    econ = pf.evaluate(s)
    return {"class": "EXPLORATION", "decision_id": f"{result['decision_id'][:-2]}-X",
            "legs": [{"unit_id": u.unit_id, "platform": u.platform, "channel": u.channel,
                      "campaign_ids": u.campaign_ids, "budget_id": u.unit_id, "is_shared": u.is_shared,
                      "before": float(opt.s0[i]), "after": float(s[i])}],
            "expected": econ.summary(), "inventory_risk_after": econ.inventory_risk_by_sku,
            "unallocated": float(c.B - s.sum()), "reserve_floor": float(c.R), "why_not": [],
            "exploration": {"draw": d, "gain_under_draw": gain, "amount": float(up),
                            "method": "bootstrap-draw randomized (not Thompson sampling)"}}
