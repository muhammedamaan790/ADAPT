"""Stage 2 demand + inventory risk + governance: NB2 P(stockout) and method-of-moments dispersion, quantile
non-crossing (property), LightGBM promoted only when it beats seasonal-naive by >= 5% with calibrated coverage,
T61 leakage (data after fit_ts cannot change the fit), champion/challenger non-inferiority and T32 (a worse candidate
is not promoted; rollback restores the previous champion), and the Stage 2 inventory gate / safety candidate."""

from datetime import datetime, timedelta

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from scipy.stats import nbinom

from adapt.core.db import Database
from adapt.decide.optimizer import Optimizer
from adapt.decide.safety import safety_candidates
from adapt.economics.inventory_risk import method_of_moments_r, stockout_probability
from adapt.economics.portfolio import Portfolio, PortfolioState, SkuState, UnitState
from adapt.learn import governance
from adapt.predict import demand

AS_OF = datetime(2026, 10, 1, 12)


# ---- NB2 -------------------------------------------------------------------------------------------------------------
def test_nb2_matches_scipy_and_edge_cases():
    m, avail, r = np.array([12.0, 12.0, 0.0]), np.array([10.4, 30.0, 3.0]), np.array([3.0, 3.0, 3.0])
    p = stockout_probability(m, avail, r)
    assert p[0] == pytest.approx(1 - nbinom.cdf(10, 3.0, 3.0 / 15.0))     # floor(10.4) = 10 units sellable
    assert p[1] < p[0] and p[2] == 0.0                                     # nothing demanded -> no stockout
    assert stockout_probability(np.array([5.0]), np.array([-4.0]), np.array([1e6]))[0] == pytest.approx(
        1 - nbinom.cdf(0, 1e6, 1e6 / (1e6 + 5)))                           # negative stock -> count 0


def test_method_of_moments_dispersion():
    rng = np.random.default_rng(0)
    assert method_of_moments_r(rng.negative_binomial(2, 2 / 7, 20000)) == pytest.approx(2.0, rel=0.1)
    assert method_of_moments_r(rng.poisson(5, 2000)) == 1e6                # Poisson-like -> capped
    assert method_of_moments_r(np.zeros(10)) == 1e6


# ---- governance (spec §10.2, T32) ------------------------------------------------------------------------------------
def test_noninferiority_bootstrap():
    rng = np.random.default_rng(1)
    days = np.repeat(np.arange(28), 5)
    champ = rng.gamma(2.0, 1.0, len(days))
    assert governance.noninferiority(champ * 1.30, champ, days, 2.0)["passed"] is False
    assert governance.noninferiority(champ.copy(), champ, days, 2.0)["passed"] is True
    assert governance.noninferiority(champ * 0.8, champ, days, 2.0)["passed"] is True


def test_t32_worse_candidate_not_promoted_and_rollback(tmp_path):
    db = Database(tmp_path / "ws.duckdb")
    days = np.repeat(np.arange(28), 3)
    err = np.ones(len(days))
    governance.register_champion(db, "m", "v1", AS_OF, "LIGHTGBM", "first", feature_hash="h1")
    ok = governance.consider(db, "m", "v2", AS_OF + timedelta(days=7), "LIGHTGBM", "sha2", "h2", {}, None,
                             {"a_beats_baseline": True, "c_coverage": True},
                             {"candidate": err * 0.9, "champion": err, "days": days}, 1.0)
    assert ok["promoted"] and governance.champion(db, "m")["version"] == "v2"
    bad = governance.consider(db, "m", "v3", AS_OF + timedelta(days=14), "LIGHTGBM", "sha3", "h3", {}, None,
                              {"a_beats_baseline": True, "c_coverage": True},
                              {"candidate": err * 1.5, "champion": err, "days": days}, 1.0)
    assert not bad["promoted"] and "b_noninferiority" in bad["reason"]
    assert governance.champion(db, "m")["version"] == "v2"                 # the champion is retained
    out = governance.rollback(db, "m", "maria", AS_OF + timedelta(days=15))
    assert out == {"model": "m", "retired": "v2", "champion": "v1"} and governance.champion(db, "m")["version"] == "v1"
    roles = {h["version"]: h["role"] for h in governance.history(db, "m")}
    assert roles == {"v1": "champion", "v2": "retired", "v3": "candidate"}
    same = governance.consider(db, "m", "v4", AS_OF + timedelta(days=21), "LIGHTGBM", "sha4", "h1", {}, None,
                               {"a_beats_baseline": True, "c_coverage": True})
    assert same["promoted"] and "same specification" in same["reason"]   # a routine refit of the champion's spec
    db.close()


# ---- the demand model on a synthetic workspace -----------------------------------------------------------------------
def workspace(tmp_path, weekly: bool, seed: int = 0, days: int = 240) -> Database:
    rng = np.random.default_rng(seed)
    db = Database(tmp_path / "ws.duckdb")
    end = AS_OF.date() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    skus = [(f"K{i}", f"Cat{i % 2}") for i in range(6)]
    rows, prices = [], []
    for i, (sku, _cat) in enumerate(skus):
        base = 6 + 3 * i
        for k in range(days):
            d = start + timedelta(days=k)
            f = (1.0 + (0.9 if d.weekday() >= 5 else -0.3)) if weekly else 1.0
            price_mult = 1.25 if (k // 30) % 2 == 1 else 1.0
            lam = base * f * (price_mult ** -2.0 if weekly else 1.0)
            rows.append((d, sku, float(rng.poisson(lam)), 0.0))
        for k in range(0, days, 30):
            prices.append((sku, start + timedelta(days=k), start + timedelta(days=k + 30),
                           100.0 * (1.25 if (k // 30) % 2 == 1 else 1.0)))

    def build(cur):
        cur.execute("CREATE SCHEMA core; CREATE SCHEMA marts")
        cur.execute("CREATE TABLE core.skus (sku VARCHAR, category VARCHAR, promoted BOOLEAN)")
        cur.executemany("INSERT INTO core.skus VALUES (?, ?, true)", skus)
        cur.execute("CREATE TABLE marts.sku_daily (date DATE, sku VARCHAR, units DOUBLE, cba DOUBLE)")
        cur.executemany("INSERT INTO marts.sku_daily VALUES (?, ?, ?, ?)", rows)
        cur.execute("CREATE TABLE core.pricing_snapshots (sku VARCHAR, valid_from DATE, valid_to DATE, price DOUBLE)")
        cur.executemany("INSERT INTO core.pricing_snapshots VALUES (?, ?, ?, ?)", prices)
        cur.execute("CREATE TABLE marts.campaign_daily (date DATE, campaign_id VARCHAR, spend DOUBLE)")
        cur.execute("CREATE TABLE core.campaign_sku (campaign_id VARCHAR, sku VARCHAR, attribution_weight DOUBLE)")

    db.write(build)
    return db


def test_lightgbm_promoted_when_it_beats_seasonal_naive(tmp_path):
    db = workspace(tmp_path, weekly=True)
    out = demand.fit_demand(db, AS_OF)
    assert out["status"] == "OK", out
    assert out["improvement_vs_naive"] >= 0.05 and 0.70 <= out["coverage_p10_p90"] <= 0.90, out
    assert out["promotion"]["promoted"] and governance.champion(db, "demand")["kind"] == "LIGHTGBM"
    fc = demand.forecast(db, AS_OF, 7)
    assert set(fc) == {f"K{i}" for i in range(6)} and all(v["model"].startswith("lightgbm") for v in fc.values())
    assert all(v["p10_sum"] <= v["baseline_daily"] * 7 <= v["p90_sum"] + 1e-9 for v in fc.values())
    # rollback restores the Stage 1 model, and the forecast follows it
    governance.rollback(db, "demand", "maria", AS_OF)
    assert all(v["model"] == "seasonal_naive" for v in demand.forecast(db, AS_OF, 7).values())
    db.close()


def test_lightgbm_not_promoted_when_criterion_a_fails(tmp_path):
    """Criterion (a) is a hard gate: with a bar no model can clear, the seasonal-naive champion is retained. (On flat
    Poisson demand LightGBM honestly beats the 7-day mean by ~5%: its 28-day-mean feature is less noisy.)"""
    db = workspace(tmp_path, weekly=False)
    out = demand.fit_demand(db, AS_OF, {"min_improvement_vs_naive": 0.5})
    assert out["status"] == "OK" and not out["promotion"]["promoted"], out
    assert governance.champion(db, "demand")["kind"] == "SEASONAL_NAIVE"
    assert all(v["model"] == "seasonal_naive" for v in demand.forecast(db, AS_OF, 7).values())
    db.close()


def test_t61_rows_after_fit_ts_cannot_change_the_fit(tmp_path):
    db = workspace(tmp_path, weekly=True)
    a = demand.fit_demand(db, AS_OF)
    db.write(lambda cur: cur.execute("INSERT INTO marts.sku_daily VALUES (?, 'K0', 999, 0)", [AS_OF.date()]))
    db.write(lambda cur: cur.execute("DELETE FROM models.demand_fits"))
    b = demand.fit_demand(db, AS_OF)
    assert a["wape_p50"] == b["wape_p50"] and a["version"] == b["version"]  # same content-addressed artifact
    db.close()


@settings(max_examples=50, deadline=None)
@given(st.lists(st.floats(-50, 500), min_size=12, max_size=12))
def test_quantiles_never_cross(x):
    class Fake:  # three boosters whose raw outputs cross on purpose
        def __init__(self, k):
            self.k = k

        def predict(self, X):
            return X[:, self.k]

    out = demand.predict([Fake(2), Fake(0), Fake(1)], np.array([x]))
    assert out[0, 0] <= out[0, 1] <= out[0, 2] and out.min() >= 0


# ---- the Stage 2 inventory gate --------------------------------------------------------------------------------------
def unit(uid, budget, beta, w):
    from adapt.predict.curves import CurveArtifact

    p = np.array([beta, 1.0, 1.0, 0.0, 0.0])
    art = CurveArtifact(uid, "OK", 1.0, float(budget), float(budget * 3), 1.0, 1.0, 0.0, p, np.tile(p, (20, 1)))
    return UnitState(uid, "meta", "meta", [uid], float(budget), 1.0, art, 0.0, w, 0.0, 0.0)


def risk_state(available: float, predicate="STOCKOUT_PROBABILITY") -> PortfolioState:
    return PortfolioState(AS_OF, 7, [unit("A", 1000, 4.0, {"k": 1.0}), unit("B", 1000, 4.0, {"k2": 1.0})],
                          {"k": SkuState("k", 100.0, 60.0, available, 5.0, 10.0, available, 4.0),
                           "k2": SkuState("k2", 100.0, 60.0, 1e6, 0.0, 10.0, 1e6, 4.0)}, n_draws=20,
                          inventory_risk={"predicate": predicate, "p_unsafe": 0.3})


def test_stage2_predicate_reports_stockout_probability_and_blocks_scale_up():
    tight = Portfolio(risk_state(60.0)).evaluate({"A": 1000.0, "B": 1000.0})
    entry = tight.inventory_risk_by_sku["by_sku"]["k"]
    assert tight.inventory_risk_by_sku["kind"] == "STOCKOUT_PROBABILITY"
    assert entry["stockout_probability"] > 0.3 and tight.exposure_by_unit["A"] == pytest.approx(1.0)
    opt = Optimizer(risk_state(60.0))
    up = opt.fe.with_move(opt.fe.state_of(opt.s0), 0, 1200.0)
    assert not opt.fe.gate_ok(up)                                          # BLOCK SCALE on the at-risk SKU
    assert opt.fe.gate_ok(opt.fe.with_move(opt.fe.state_of(opt.s0), 1, 1200.0))
    stage1 = Portfolio(risk_state(60.0, "PROJECTED_SHORTFALL")).evaluate({"A": 1000.0})
    assert stage1.inventory_risk_by_sku["kind"] == "PROJECTED_SHORTFALL"   # the stage switch


def test_stage2_safety_candidate_cuts_until_the_risk_is_acceptable():
    opt = Optimizer(risk_state(66.0))
    cands = safety_candidates(opt)
    assert cands and cands[0]["sku"] == "k" and cands[0]["risk_kind"] == "STOCKOUT_PROBABILITY"
    c = cands[0]
    assert c["risk_before"] > 0.3 and c["legs"][0]["after"] < c["legs"][0]["before"]
    assert c["remaining_risk"] <= 0.3 + 1e-6 or c["max_feasible_cut_reached"]
    assert c["shortfall_before"] is None                                   # Stage 1 field, not reused
