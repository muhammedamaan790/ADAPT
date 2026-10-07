"""Gated synthetic control (Stage 2 ★, spec §7.1 level 3): maths on hand-made panels with a known injected effect, and
the gate contract on a workspace built from those panels (T51: non-positive inputs are never logged)."""

from datetime import date, timedelta

import numpy as np
import pytest

from adapt.core.db import Database
from adapt.diagnose.causal.estimate import ESTIMATED, NOT_ESTIMABLE, causal_config, estimate
from adapt.diagnose.causal.synthetic_control import fit_weights, select_controls, smape, synthetic_control
from adapt.diagnose.evidence import Incident

CFG = causal_config()


def panel(effect: float = 0.0, n_controls: int = 10, days: int = 63, start: int = 56, seed: int = 0,
          placebo_effect: float = 0.0):
    """Log ROAS panel: a shared weekly + trend factor, unit-specific levels and noise. Treated = a mix of three
    controls plus its own level; a multiplicative effect from `start` on (and optionally a fake one 14 days earlier
    that ends at the real start, for the placebo test)."""
    rng = np.random.default_rng(seed)
    t = np.arange(days)
    common = 0.15 * np.sin(2 * np.pi * t / 7) + 0.004 * t + np.cumsum(rng.normal(0, 0.02, days))
    X = np.column_stack([np.log(1.5 + j * 0.3) + (0.6 + 0.08 * j) * common + rng.normal(0, 0.03, days)
                         for j in range(n_controls)])
    y = np.log(2.2) + X[:, [1, 4, 7]].mean(axis=1) - X[:, [1, 4, 7]].mean() + rng.normal(0, 0.02, days)
    y[start:] += np.log(1 + effect)
    if placebo_effect:
        y[start - 14:start] += np.log(1 + placebo_effect)
    return y, X, [f"C{j:02d}" for j in range(n_controls)]


@pytest.mark.parametrize("effect", [-0.20, 0.0, 0.15])
def test_recovers_an_injected_effect_with_calibrated_intervals(effect):
    """Over 20 panels: estimates within a few points of the truth, and the 90% interval covers it in 80-100% of
    panels (measured 92.5% over 40). With effect 0 that coverage IS the placebo false-effect rate (<= ~10-20%)."""
    errs, hits = [], 0
    for seed in range(20):
        y, X, ids = panel(effect, seed=seed)
        sc = synthetic_control(y, X, ids, 56, 7, np.ones(7), CFG, seed=1)
        errs.append(abs(sc.effect_pct - effect))
        hits += sc.ci_lo <= effect <= sc.ci_hi
        assert sc.holdout_smape <= CFG["smape_max"]
    assert np.median(errs) < 0.025 and max(errs) < 0.06
    assert 0.80 <= hits / 20 <= 1.0


def test_weights_are_convex_and_selection_is_top_k_by_correlation_with_id_tiebreak():
    y, X, ids = panel()
    w, lam = fit_weights(y[:21] - y[:21].mean(), X[:21] - X[:21].mean(0), 0.01)
    assert (w >= 0).all() and w.sum() == pytest.approx(1.0) and lam > 0
    twins = np.column_stack([X[:, 0], X[:, 0], -X[:, 2]])         # identical correlations -> the lower id first
    assert select_controls(y[:21], twins[:21], ["B", "A", "C"], 2) == [1, 0]
    assert select_controls(y[:21], twins[:21], ["B", "A", "C"], 3)[-1] == 2  # the anti-correlated series ranks last


def test_smape_is_symmetric_and_bounded():
    assert smape(np.array([100.0]), np.array([100.0])) == 0.0
    assert smape(np.array([100.0]), np.array([0.0])) == pytest.approx(2.0)
    assert smape(np.array([1.0, 2.0]), np.array([2.0, 1.0])) == smape(np.array([2.0, 1.0]), np.array([1.0, 2.0]))


# ---- the gate contract on a workspace --------------------------------------------------------------------------------
START = date(2026, 9, 1)


def workspace(tmp_path, y: np.ndarray, X: np.ndarray, ids: list[str], spend: float = 1000.0, orders: float = 20.0,
              treated_zero_day: int | None = None, control_zero: str | None = None) -> Database:
    db = Database(tmp_path / "ws.duckdb")
    days = len(y)
    d0 = START - timedelta(days=days - 7)  # the last 7 days are the post window
    rows = []
    for name, series, ps in [("T", y, "Cat-T")] + [(c, X[:, j], f"Cat-{c}") for j, c in enumerate(ids)]:
        for k in range(days):
            rev = float(np.exp(series[k]) * spend)
            if (name == "T" and k == treated_zero_day) or (name == control_zero and k == 30):
                rev = 0.0
            rows.append((d0 + timedelta(days=k), name, ps, spend, rev, orders, rev * 0.3))

    def build(cur):
        cur.execute("CREATE SCHEMA marts; CREATE SCHEMA core")
        cur.execute("CREATE TABLE marts.campaign_daily (date DATE, campaign_id VARCHAR, product_set VARCHAR, "
                    "spend DOUBLE, attributed_net_revenue DOUBLE, attributed_orders DOUBLE, attributed_cba DOUBLE)")
        cur.executemany("INSERT INTO marts.campaign_daily VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        cur.execute("CREATE TABLE core.campaigns AS SELECT DISTINCT campaign_id, product_set, campaign_id AS budget_id "
                    "FROM marts.campaign_daily")
        cur.execute("CREATE TABLE core.budget_history (entity_id VARCHAR, effective_from DATE, source VARCHAR)")
        cur.execute("CREATE TABLE core.campaign_state_history (entity_id VARCHAR, effective_from DATE, source VARCHAR)")
        cur.execute("CREATE TABLE core.pricing_snapshots (sku VARCHAR, valid_from DATE, price DOUBLE)")
        cur.execute("CREATE TABLE core.campaign_sku (campaign_id VARCHAR, sku VARCHAR, attribution_weight DOUBLE)")

    db.write(build)
    return db


def incident(metric: str = "ROAS") -> Incident:
    return Incident("ANM-T", "meta", ["T"], None, metric, START, START + timedelta(days=6), "DOWN")


def test_estimated_when_every_gate_passes(tmp_path):
    y, X, ids = panel(-0.25)
    db = workspace(tmp_path, y, X, ids)
    out = estimate(db, incident(), materiality_m=5000.0)
    assert out["status"] == ESTIMATED, out["reason"]
    assert out["effect_pct"] == pytest.approx(-0.25, abs=0.05) and out["ci"][1] < 0  # coverage: the panel test
    assert out["inr_translation"] < 0 and "accounting translation" in out["inr_label"]
    assert all(g["passed"] for g in out["gates"].values())
    assert {c["campaign_id"] for c in out["controls"]} <= set(ids) and len(out["controls"]) == CFG["top_k"]
    db.close()


def test_non_positive_treated_series_is_not_estimable_t51(tmp_path):
    y, X, ids = panel(-0.25)
    db = workspace(tmp_path, y, X, ids, treated_zero_day=40)
    out = estimate(db, incident(), 5000.0)
    assert out["status"] == NOT_ESTIMABLE and "non-positive" in out["reason"]
    db.close()


def test_non_positive_control_is_excluded_not_logged_t51(tmp_path):
    y, X, ids = panel(-0.25)
    db = workspace(tmp_path, y, X, ids, control_zero="C03")
    out = estimate(db, incident(), 5000.0)
    assert out["gates"]["controls"]["excluded"]["C03"].startswith("non-positive")
    assert "C03" not in {c["campaign_id"] for c in out.get("controls") or []}
    db.close()


def test_too_few_controls(tmp_path):
    y, X, ids = panel(-0.25)
    db = workspace(tmp_path, y, X[:, :2], ids[:2])
    out = estimate(db, incident(), 5000.0)
    assert out["status"] == NOT_ESTIMABLE and "fewer than 3" in out["reason"]
    db.close()


def test_placebo_catches_a_pre_existing_shift(tmp_path):
    y, X, ids = panel(-0.25, placebo_effect=-0.25)  # the "effect" started 14 days before the incident
    db = workspace(tmp_path, y, X, ids)
    out = estimate(db, incident(), 5000.0)
    assert out["status"] == NOT_ESTIMABLE and not out["gates"]["placebo"]["passed"]
    db.close()


def test_bad_pre_fit_fails_the_smape_gate(tmp_path):
    y, X, ids = panel(-0.25)
    y = y + np.random.default_rng(5).normal(0, 0.5, len(y))  # treated unrelated to any control
    db = workspace(tmp_path, y, X, ids)
    out = estimate(db, incident(), 5000.0)
    assert out["status"] == NOT_ESTIMABLE and not out["gates"]["pre_fit"]["passed"]
    db.close()


def test_short_history_and_other_families(tmp_path):
    y, X, ids = panel(-0.25)
    db = workspace(tmp_path, y[20:], X[20:], ids)  # 36 pre days < 42
    assert "history" in estimate(db, incident(), 5000.0)["reason"]
    assert estimate(db, incident("CTR"), 5000.0)["reason"].startswith("no causal estimator for the CTR family")
    db.close()


def test_few_post_conversions(tmp_path):
    y, X, ids = panel(-0.25)
    db = workspace(tmp_path, y, X, ids, orders=5.0)  # 35 post-period conversions < 50
    out = estimate(db, incident(), 5000.0)
    assert out["status"] == NOT_ESTIMABLE and "post-period conversions" in out["reason"]
    db.close()
