"""Stage 1 deterministic SAFETY candidate (B7, spec §0.5 S3): for every SKU whose projected shortfall over H is > 0 at
the current allocation, the smallest common fractional cut on the units mapped to it that removes the shortfall,
capped at the largest feasible decrease (max daily change, per-unit minimum, fixed units untouched; a shared budget
is one unit). If even the largest cut leaves a shortfall, the candidate is that cut with remaining_shortfall.
Freed budget stays unallocated (a safety candidate never reallocates). Status REQUIRES_REVIEW (Approve mode).
"""

from __future__ import annotations

import numpy as np

from adapt.decide.optimizer import FIXED_REASONS, Optimizer


def safety_candidates(opt: Optimizer, min_weight: float = 0.01) -> list[dict]:
    fe, pf, c = opt.fe, opt.pf, opt.c
    s0 = opt.s0
    base = fe.state_of(s0)
    sf0 = fe.shortfall(base)
    out = []
    for j, sku in enumerate(pf.sku_ids):
        if sf0[j] <= 1e-9:
            continue
        mapped = [i for i in range(len(s0)) if pf.W[i, j] >= min_weight
                  and not any(r in FIXED_REASONS for r in c.unit_reasons.get(i, []))]
        if not mapped:
            continue
        floor = np.array([c.lo[i] for i in mapped])
        max_cut = np.array([1 - floor[k] / s0[i] if s0[i] > 0 else 0.0 for k, i in enumerate(mapped)])

        def allocation(frac: float, mapped=mapped, max_cut=max_cut) -> np.ndarray:
            s = s0.copy()
            for k, i in enumerate(mapped):
                s[i] = s0[i] * (1 - min(frac, max_cut[k]))
            return s

        def shortfall_at(frac: float, j=j, allocation=allocation) -> float:
            return float(fe.shortfall(fe.state_of(allocation(frac)))[j])

        top = float(max_cut.max()) if len(max_cut) else 0.0
        if top <= 0:
            continue
        if shortfall_at(top) > 1e-9:
            frac = top
        else:
            lo_f, hi_f = 0.0, top
            for _ in range(30):
                mid = 0.5 * (lo_f + hi_f)
                lo_f, hi_f = (mid, hi_f) if shortfall_at(mid) > 1e-9 else (lo_f, mid)
            frac = hi_f
        s = allocation(frac)
        s = np.array([np.floor(v / opt.inc) * opt.inc if abs(v - s0[i]) > 1e-9 else v for i, v in enumerate(s)])
        s = np.maximum(s, c.lo)  # rounding down may cross the floor; the floor itself is feasible
        econ = opt.full.evaluate(s)
        remaining = float(fe.shortfall(fe.state_of(s))[j])
        legs = [{"unit_id": opt.state.units[i].unit_id, "platform": opt.state.units[i].platform,
                 "channel": opt.state.units[i].channel, "campaign_ids": opt.state.units[i].campaign_ids,
                 "budget_id": opt.state.units[i].unit_id, "before": float(s0[i]), "after": float(s[i])}
                for i in mapped if s[i] < s0[i] - 1e-9]
        if not legs:
            continue
        out.append({
            "class": "SAFETY", "status": "REQUIRES_REVIEW", "sku": sku,
            "trigger": "BASELINE_STOCK_DEFICIT" if pf.atp[j] < 0 else "PROJECTED_SHORTFALL",
            "shortfall_before": float(sf0[j]), "remaining_shortfall": remaining,
            "cut_fraction": float(frac), "max_feasible_cut_reached": bool(remaining > 1e-9),
            "legs": legs, "freed_budget": float((s0 - s).sum()), "expected": econ.summary(),
            "inventory_risk_after": {"kind": "PROJECTED_SHORTFALL",
                                     "by_sku": {sku: econ.inventory_risk_by_sku["by_sku"][sku]}},
        })
    return out
