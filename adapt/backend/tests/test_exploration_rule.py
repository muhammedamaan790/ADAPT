"""The exploration selection rule (spec §10.3) on a controlled portfolio: one run-keyed joint bootstrap draw, the
argmax of the contribution change under THAT draw among eligible units, the 2% and box caps, and every filter
(touched by the recommendation, policy flags, no usable curve, non-positive margin, MIX_UNCERTAIN, inventory gate,
no gain)."""

from datetime import datetime
from types import SimpleNamespace

import numpy as np

from adapt.decide import exploration

N_DRAWS = 8
AS_OF = datetime(2026, 10, 5, 12)


def make(slopes, *, reasons=None, model=None, cm=None, mix=None, gate_ok=None, budgets=None):
    """U units; under draw d, adding x rupees to unit i changes CAA by slopes[i][d] * x (a linear toy portfolio)."""
    U = len(slopes)
    slopes = np.asarray(slopes, dtype=float)
    s0 = np.array(budgets or [10_000.0] * U)
    units = [SimpleNamespace(unit_id=f"u{i}", platform="meta", channel="meta", campaign_ids=[f"c{i}"],
                             is_shared=False, budget=float(s0[i]), unmapped_share=(mix or [0.0] * U)[i],
                             model_available=(model or [True] * U)[i]) for i in range(U)]
    state = SimpleNamespace(units=units, n_draws=N_DRAWS)

    def evaluate(s):
        d = (slopes * (np.asarray(s) - s0)[:, None]).sum(0)
        return SimpleNamespace(delta_caa=d, summary=lambda: {"E": float(d.mean()), "P10": 0.0, "P50": 0.0,
                                                             "P90": 0.0, "prob_loss": 0.0, "delta_net_revenue": 0.0},
                               inventory_risk_by_sku={"kind": "PROJECTED_SHORTFALL", "by_sku": {}})

    full = SimpleNamespace(evaluate=evaluate, cm=np.array(cm or [0.3] * U))
    fe = SimpleNamespace(state_of=lambda s: {"s": np.asarray(s)},
                         gate_ok=lambda st: all((gate_ok or [True] * U)[i] for i in range(U)
                                                if st["s"][i] > s0[i] + 1e-9))
    c = SimpleNamespace(B=float(s0.sum()) / 0.9, R=0.0, hi=s0 * 1.2, unit_reasons=reasons or {})
    opt = SimpleNamespace(state=state, full=full, fe=fe, c=c, s0=s0, inc=100.0)
    hold = {"status": "OK", "decision_id": "opt-x-R", "allocation": {u.unit_id: u.budget for u in units}}
    return opt, hold


def drawn(as_of=AS_OF) -> int:
    import hashlib

    seed = int(hashlib.sha256(f"explore|{as_of.isoformat()}".encode()).hexdigest()[:8], 16)
    return int(np.random.default_rng(seed).integers(N_DRAWS))


CFG = {"enabled": True, "max_budget_share": 0.02}


def test_argmax_under_the_drawn_draw_with_both_caps():
    d = drawn()
    slopes = np.full((3, N_DRAWS), 0.1)
    slopes[1, d] = 0.9                                  # unit 1 wins only under the drawn draw
    slopes[2, :] = 0.5
    slopes[2, d] = 0.2                                  # unit 2 wins on average, not under the drawn draw
    opt, hold = make(slopes)
    out = exploration.candidate(opt, hold, AS_OF, CFG)
    assert out["legs"][0]["unit_id"] == "u1" and out["exploration"]["draw"] == d
    amount = out["legs"][0]["after"] - out["legs"][0]["before"]
    assert amount == min(np.floor(0.02 * opt.c.B / 100) * 100, 2_000)                       # 2% cap and +-20% box
    assert out["class"] == "EXPLORATION" and out["decision_id"] == "opt-x-X"


def test_disabled_or_no_gain_creates_nothing():
    opt, hold = make(np.full((2, N_DRAWS), 0.5))
    assert exploration.candidate(opt, hold, AS_OF, {**CFG, "enabled": False}) is None
    opt, hold = make(np.full((2, N_DRAWS), -0.1))                                            # every increase loses
    assert exploration.candidate(opt, hold, AS_OF, CFG) is None


def test_every_eligibility_filter():
    d = drawn()
    base = np.full((2, N_DRAWS), 0.1)
    base[0, d] = 0.9                                    # unit 0 would win; each filter must push the choice to u1
    cases = [dict(reasons={0: ["COOLDOWN"]}), dict(model=[False, True]), dict(cm=[0.0, 0.3]),
             dict(mix=[0.3, 0.0]), dict(gate_ok=[False, True])]
    for kw in cases:
        opt, hold = make(base, **kw)
        assert exploration.candidate(opt, hold, AS_OF, CFG)["legs"][0]["unit_id"] == "u1", kw
    opt, hold = make(base)
    hold["allocation"]["u0"] += 500.0                   # the recommendation touches u0: never explored
    assert exploration.candidate(opt, hold, AS_OF, CFG)["legs"][0]["unit_id"] == "u1"
    opt, hold = make(base)
    opt.c.B = float(opt.s0.sum())                       # nothing unallocated: nothing to explore with
    assert exploration.candidate(opt, hold, AS_OF, CFG) is None
