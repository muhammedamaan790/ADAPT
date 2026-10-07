"""Cannibalization (Stage 2, spec §8.1 step 2): T as generated (T59), the flow invariants (a)-(e) with Hypothesis, the
binding-cap cases (T49, T54), pure reallocation, the third-party overlap formula, scale-up monotonicity on net revenue,
and the optimizer's fast evaluation equal to portfolio_economics with T != 0."""

from datetime import datetime

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from adapt.decide.optimizer import Optimizer
from adapt.economics.cannibalization import book, build_T, coefficient_matrix, transfer_matrix
from adapt.economics.portfolio import Portfolio, PortfolioState, SkuState, UnitState

T0 = datetime(2026, 10, 1, 12)
CFG = {"enabled": True, "coefficients": {"same_category_same_channel_stage": 0.30, "same_category": 0.15},
       "max_row_sum": 0.5}


def unit(uid, cats, channel="meta", stage="prospecting", budget=1000.0, roas=2.0, w=None):
    return UnitState(uid, channel, channel.split("_")[0], [uid], budget, 1.0, None, roas, w or {"k": 1.0}, 0.0, 0.0,
                     categories=list(cats), stage=stage)


# ---- T59: T as generated --------------------------------------------------------------------------------------------
def test_t59_generated_T_is_symmetric_zero_diagonal_capped_with_one_alpha():
    hub = [unit("H", ["a", "b", "c", "d"])] + [unit(f"S{i}", [c], "google_search", "search")
                                                for i, c in enumerate("abcd")] + \
        [unit("P", ["a"]), unit("R", ["a"], stage="retargeting"), unit("Z", ["z"])]
    C = coefficient_matrix(hub, CFG["coefficients"])
    T, alpha = transfer_matrix(C, 0.5)
    assert np.array_equal(T, T.T) and not np.diag(T).any()
    assert T.sum(1).max() <= 0.5 + 1e-12
    assert np.allclose(T, alpha * C) and 0 < alpha < 1                  # the hub overlaps many: one global alpha
    i = {u.unit_id: k for k, u in enumerate(hub)}
    assert C[i["H"], i["P"]] == 0.30 and C[i["P"], i["R"]] == 0.15 and C[i["H"], i["S0"]] == 0.15
    assert not C[i["Z"]].any()
    assert build_T(hub, {"enabled": False}) is None and build_T(hub, {}) is None


# ---- flows ----------------------------------------------------------------------------------------------------------
def pair_T(t=0.5):
    return np.array([[0.0, t], [t, 0.0]])


def test_t54_pure_reallocation_with_a_binding_cap_books_zero_in_total():
    # A -10K, B +10K, T = 0.5, R_A(s') = 3K: raw steal B<-A 5K, realized 3K, shortfall_B 2K, recapture B<-A 5K
    dR = np.array([[-10_000.0, 10_000.0]])
    f = book(dR, np.array([[3_000.0, 50_000.0]]), pair_T())
    assert f["booked"][0] == pytest.approx([-13_000.0, 13_000.0])
    assert f["booked"].sum() == pytest.approx(0.0)
    assert f["shortfall"][0, 1] == pytest.approx(2_000.0) and f["realized_steal"][0, 1] == pytest.approx(3_000.0)
    # the audit's case: A -100 / B +100 / R_A(s') = 10 -> booked -110 / +110
    f = book(np.array([[-100.0, 100.0]]), np.array([[10.0, 1e6]]), pair_T())
    assert f["booked"][0] == pytest.approx([-110.0, 110.0])


def test_t54_two_thieves_and_caps_realized_80_shortfall_20_total_cap_independent():
    # units 0, 1 scale up (+100 each) into j (cap 30) and k (cap 50); T_0j = T_1k = 0.5 (symmetric)
    T = np.zeros((4, 4))
    for a, b in ((0, 2), (1, 3)):
        T[a, b] = T[b, a] = 0.5
    dR = np.array([[100.0, 100.0, 0.0, 0.0]])
    capped = book(dR, np.array([[1e6, 1e6, 30.0, 50.0]]), T)
    uncapped = book(dR, np.array([[1e6, 1e6, 1e6, 1e6]]), T)
    assert capped["realized_steal"].sum() == pytest.approx(80.0)
    assert capped["shortfall"].sum() == pytest.approx(20.0)              # the thieves' gains shrink by 20
    assert capped["booked"].sum() == pytest.approx(uncapped["booked"].sum())  # portfolio total cap-independent


def test_t49_three_units_scale_into_one_overlapping_unit():
    T = np.zeros((4, 4))
    for a in range(3):
        T[a, 3] = T[3, a] = 0.15
    dR = np.array([[500.0, 400.0, 300.0, 0.0]])
    R_new = np.array([[1e6, 1e6, 1e6, 100.0]])                          # j has little left to lose
    f = book(dR, R_new, T)
    assert f["stolen_from"][0, 3] <= 100.0 + 1e-9                       # stolen_j <= max(R_j(s'), 0)
    total = dR.sum() - (np.clip(dR, 0, None) * T.sum(1)).sum() + (np.clip(-dR, 0, None) @ T).sum()
    assert f["booked"].sum() == pytest.approx(total)


def test_third_party_overlap_formula():
    # A -x, B +x; A also overlaps C (0.1), B overlaps nothing else: net = (sum_j T_Aj - sum_j T_Bj) x
    T = np.array([[0.0, 0.2, 0.1], [0.2, 0.0, 0.0], [0.1, 0.0, 0.0]])
    x = 1000.0
    f = book(np.array([[-x, x, 0.0]]), np.array([[1e6, 1e6, 1e6]]), T)
    assert f["booked"].sum() == pytest.approx((T[0].sum() - T[1].sum()) * x)


def test_zero_T_books_the_curve_increment():
    dR = np.array([[-5.0, 7.0, 0.0]])
    assert np.array_equal(book(dR, np.ones_like(dR) * 100, np.zeros((3, 3)))["booked"], dR)


@settings(max_examples=200, deadline=None)
@given(st.integers(2, 6).flatmap(lambda U: st.tuples(
    st.lists(st.floats(-1_000, 1_000), min_size=U, max_size=U),
    st.lists(st.floats(0, 2_000), min_size=U, max_size=U),
    st.lists(st.floats(0, 0.3), min_size=U * U, max_size=U * U))))
def test_flow_invariants_a_to_e(args):
    dR_l, cap_l, c_l = args
    U = len(dR_l)
    C = np.array(c_l).reshape(U, U)
    C = np.triu(C, 1)
    C = C + C.T
    T, alpha = transfer_matrix(C, 0.5)
    dR, R_new = np.array([dR_l]), np.array([cap_l])
    f = book(dR, R_new, T)
    P = np.clip(dR, 0, None)
    S_raw = T * P[0][:, None]                                            # S_raw[i, j] = T[i, j] P_i
    total = dR.sum() - S_raw.sum() + (np.clip(-dR, 0, None) @ T).sum()
    assert f["booked"].sum() == pytest.approx(total, abs=1e-6)            # (a) cap independence
    assert f["shortfall"] + f["realized_steal"] == pytest.approx(f["raw_steal"], abs=1e-9)   # (b)
    assert (f["realized_steal"] <= f["raw_steal"] + 1e-9).all() and (f["realized_steal"] >= -1e-12).all()
    assert (f["stolen_from"][0] <= np.clip(R_new[0], 0, None) + 1e-6).all()  # (c)
    assert (P[0] - S_raw.sum(1) >= -1e-9).all()                            # (d) net new demand >= 0
    assert np.array_equal(T, T.T) and not np.diag(T).any() and T.sum(1).max() <= 0.5 + 1e-12  # (e)


# ---- through portfolio_economics and the optimizer -------------------------------------------------------------------
def curve(uid, budget, beta, K, cats, channel="meta", stage="prospecting", n=30, seed=0, w=None):
    from adapt.predict.curves import CurveArtifact

    p = np.array([beta, K, 1.0, 0.0, 0.0])
    rng = np.random.default_rng(seed)
    draws = np.abs(p * (1 + 0.05 * rng.standard_normal((n, 5)) * np.array([1, 1, 0, 0, 0])))
    art = CurveArtifact(uid, "OK", 1.0, float(budget), float(budget * 2.5), 1.0, 1.0, 0.0, p, draws)
    return UnitState(uid, channel, channel.split("_")[0], [uid], float(budget), 1.0, art, 0.0, w or {"k": 1.0},
                     0.0, 0.0, categories=list(cats), stage=stage)


def state(cannibal: bool, c=0.3):
    units = [curve("A", 1000, 2.0, 1.0, ["a"]), curve("B", 1000, 3.0, 2.0, ["a"], seed=1),
             curve("C", 1000, 2.5, 1.5, ["b"], "google_search", "search", seed=2, w={"k2": 1.0})]
    cfg = {"enabled": cannibal, "coefficients": {"same_category_same_channel_stage": c, "same_category": c / 2},
           "max_row_sum": 0.5}
    return PortfolioState(T0, 7, units, {"k": SkuState("k", 100.0, 40.0, 1e9, 0.0, 0.0),
                                         "k2": SkuState("k2", 100.0, 40.0, 1e9, 0.0, 0.0)}, n_draws=30,
                          cannibalization=cfg)


def test_scale_up_with_more_overlap_never_raises_net_revenue():
    up = {"A": 1200.0, "B": 1200.0}
    vals = [Portfolio(state(True, c)).evaluate(up).delta_net_revenue.mean() for c in (0.0, 0.1, 0.2, 0.3)]
    assert all(b <= a + 1e-9 for a, b in zip(vals, vals[1:], strict=False))
    assert Portfolio(state(False)).evaluate(up).delta_net_revenue.mean() == pytest.approx(vals[0])


def test_zero_change_is_zero_with_cannibalization():
    e = Portfolio(state(True)).evaluate({"A": 1000.0, "B": 1000.0, "C": 1000.0})
    assert np.allclose(e.delta_caa, 0) and np.allclose(e.delta_net_revenue, 0)


def test_fast_evaluation_equals_portfolio_economics_with_cannibalization():
    s = state(True)
    opt = Optimizer(s)
    assert opt.fe.T is not None
    alloc = np.array([1150.0, 850.0, 1100.0])
    fast = opt.fe.dcaa(opt.fe.state_of(alloc))
    full = Portfolio(s, opt.pf.draws).evaluate(alloc).delta_caa
    assert np.allclose(fast, full, rtol=1e-9, atol=1e-6)
    moved = opt.fe.with_move(opt.fe.with_move(opt.fe.state_of(opt.s0), 0, 1150.0), 1, 850.0)
    moved = opt.fe.with_move(moved, 2, 1100.0)
    assert np.allclose(opt.fe.dcaa(moved), full, rtol=1e-9, atol=1e-6)
    assert opt.solve()["status"] == "OK"


def test_cannibalization_is_in_the_exact_fingerprint_and_snapshot():
    from adapt.decide.snapshot import state_from_dict, state_to_dict
    from adapt.economics.state import inputs_manifest

    s = state(True)
    back = state_from_dict(state_to_dict(s))
    assert back.cannibalization == s.cannibalization and back.units[0].categories == ["a"]
    m1, m2 = inputs_manifest(s), inputs_manifest(state(True, c=0.2))
    assert m1["cannibalization"]["T"] != m2["cannibalization"]["T"]
