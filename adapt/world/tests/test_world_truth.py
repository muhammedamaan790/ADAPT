"""A4 truth/seeding: priors, catalog structure, response model, per-seed truth draws, immutable truth file."""

import math
from datetime import date

import duckdb
import numpy as np
import pytest

from world.catalog import build_catalog
from world.priors import benchmarks, load_priors
from world.truth import (
    WorldConfig,
    _solve_audience,
    build_truth,
    expected_purchases,
    frequency,
    impressions_at,
    open_truth,
    spend_for_purchases,
    write_truth,
)


@pytest.fixture(scope="module")
def cfg(backbone_dir, global_ads_csv):
    return WorldConfig(seed=42, backbone_dir=backbone_dir, global_ads_csv=global_ads_csv,
                       history_end_date=date(2026, 9, 30))


@pytest.fixture(scope="module")
def truth(cfg):
    return build_truth(cfg)


# ---- priors ------------------------------------------------------------------------------------------------
def test_priors_use_ecommerce_rows_and_fall_back_when_thin(global_ads_csv):
    pools = load_priors(global_ads_csv)
    assert pools["google_search"].source == "global_ads:E-commerce (n=15)"
    assert pools["meta"].source == "global_ads:E-commerce (n=16)"  # all Meta campaign types pooled
    assert pools["google_video"].source == "benchmarks fallback"  # 3 E-commerce rows < min_prior_rows; SaaS ignored
    assert len(pools["google_video"]) == 1


def test_priors_without_a_file_fall_back_everywhere():
    assert all(p.source == "benchmarks fallback" for p in load_priors(None).values())


# ---- catalog -----------------------------------------------------------------------------------------------
def test_catalog_structure(backbone_dir):
    cat = build_catalog(backbone_dir, history_days=120)
    assert len(cat.categories) == 3 and len(cat.campaigns) == 12  # 4 channels per category
    budgets = cat.budgets()
    shared = [b for b, members in budgets.items() if len(members) > 1]
    assert len(shared) == 1  # the two lowest-ranked categories' Google Video campaigns
    assert {cat.campaign(m).channel for m in budgets[shared[0]]} == {"google_video"}
    for c in cat.campaigns:
        if c.platform == "meta":
            assert c.budget_id == c.campaign_id
    per_adset: dict[str, list] = {}
    for cr in cat.creatives:
        per_adset.setdefault(cr.adset_id, []).append(cr)
    assert all(2 <= len(v) <= 4 for v in per_adset.values())
    assert all(min(cr.launch_day for cr in v) == -120 for v in per_adset.values())  # one creative live from day 1
    for code in ("M_JEANS", "W_DRESSES", "M_SWIM"):
        assert sum(s.mix_weight for s in cat.skus_of(code)) == pytest.approx(1.0)
    fx = benchmarks()["fx_usd_inr"]
    sku = next(s for s in cat.skus if s.sku == "M_JEANS-P2")
    assert sku.unit_price_inr == pytest.approx(90 * fx) and sku.ship_cost_inr == pytest.approx(0.04 * 90 * fx)


def test_catalog_is_seed_independent_and_deterministic(backbone_dir):
    assert build_catalog(backbone_dir) == build_catalog(backbone_dir)


# ---- response model --------------------------------------------------------------------------------------------
def test_frequency_limits_and_monotonicity():
    assert frequency(0, 1000) == 1.0
    assert frequency(1e-6, 1000) == pytest.approx(1.0)
    vals = [frequency(i, 10_000) for i in (100, 1_000, 10_000, 100_000)]
    assert vals == sorted(vals) and vals[-1] == pytest.approx(10.0, rel=1e-3)  # I >> A -> freq ~ I / A


def test_audience_solver_hits_the_target_frequency():
    a = _solve_audience(50_000, 1.8)
    assert frequency(50_000, a) == pytest.approx(1.8, rel=1e-6)


def test_response_is_increasing_concave_and_invertible(truth):
    t = next(iter(truth.campaigns.values()))
    spends = np.linspace(100, 6 * t.s_ref, 40)
    p = np.array([expected_purchases(s, t) for s in spends])
    assert (np.diff(p) > 0).all()
    assert (np.diff(p, 2) < 1e-9).all()  # diminishing returns
    target = expected_purchases(t.s_ref, t)
    assert expected_purchases(spend_for_purchases(target, t), t) == pytest.approx(target, rel=1e-6)
    assert impressions_at(0, t) == 0 and expected_purchases(0, t) == 0


# ---- truth -------------------------------------------------------------------------------------------------------
def test_truth_is_deterministic_per_seed_and_differs_across_seeds(cfg, truth):
    again = build_truth(cfg)
    assert again.campaigns == truth.campaigns and again.creatives == truth.creatives
    other = build_truth(WorldConfig(**{**cfg.__dict__, "seed": 7}))
    assert other.campaigns != truth.campaigns
    assert [c.campaign_id for c in other.campaigns.values()] == [c.campaign_id for c in truth.campaigns.values()]


def test_reference_roas_is_in_the_survivorship_band(truth):
    lo, hi = benchmarks()["reference_roas_band"]
    rates = truth.category_rates.set_index("category_code")
    checked = 0
    for t in truth.campaigns.values():
        if t.prior_source == "benchmarks fallback":
            continue  # a single parametric row cannot be redrawn; the closest draw is kept (best effort)
        checked += 1
        dem = float(np.mean([truth.demand_index(t.category_code, d) for d in range(-28, 0)]))
        target = rates.loc[t.category_code, f"paid_{t.channel}"] * dem
        price = sum(s.unit_price_inr * s.mix_weight for s in truth.catalog.skus_of(t.category_code))
        roas = target * price / spend_for_purchases(target, t, dem)
        assert lo - 1e-6 <= roas <= hi + 1e-6, (t.campaign_id, roas)
    assert checked >= 6


def test_history_budgets_hit_monthly_paid_targets(truth):
    rates = truth.category_rates.set_index("category_code")
    hb = truth.history_budgets
    budgets = truth.catalog.budgets()
    for budget_id, members in budgets.items():
        for _, row in hb[hb.budget_id == budget_id].iterrows():
            days = range(int(row.from_day), int(row.to_day) + 1)
            for m in members:
                t = truth.campaigns[m]
                dem = float(np.mean([truth.demand_index(t.category_code, d) for d in days]))
                target = rates.loc[t.category_code, f"paid_{t.channel}"] * dem
                got = expected_purchases(row.amount_inr * t.budget_share, t, dem)
                tol = 0.15 if len(members) > 1 else 0.01  # shared split is fixed, rounding to Rs 100
                assert got == pytest.approx(target, rel=tol, abs=0.2), (m, int(row.from_day))
    shared = [ms for ms in budgets.values() if len(ms) > 1][0]
    assert sum(truth.campaigns[m].budget_share for m in shared) == pytest.approx(1.0)


def test_demand_index_shape_and_future_continuation(truth):
    d = truth.demand
    assert d.day.min() == -120 and d.day.max() == -1
    for code, g in d.groupby("category_code"):
        assert g["index"].mean() == pytest.approx(1.0, abs=0.1)
        assert (g["index"] > 0).all()
        future = [truth.demand_index(code, day) for day in range(0, 7)]
        assert np.mean(future) == pytest.approx(truth.future_level[code], rel=0.05)
    # the fixture's demand grows ~50% over the window, so the carried-forward level is above 1
    assert all(level > 1.1 for level in truth.future_level.values())
    # no cliff at day 0: the first live week continues the last two history weeks (weekday pattern included)
    for code in truth.future_level:
        last = np.mean([truth.demand_index(code, day) for day in range(-14, 0)])
        first = np.mean([truth.demand_index(code, day) for day in range(0, 14)])
        assert first == pytest.approx(last, rel=0.10), code


def test_creative_multipliers_average_one_per_campaign(truth):
    by_campaign: dict[str, list[float]] = {}
    for cr in truth.creatives.values():
        by_campaign.setdefault(cr.campaign_id, []).append(cr.ctr_mult)
        assert cr.fatigue_half_life_impressions > 0
    assert all(math.isclose(np.mean(v), 1.0, rel_tol=1e-9) for v in by_campaign.values())


# ---- persistence ----------------------------------------------------------------------------------------------
def test_truth_file_is_written_once_and_opened_read_only(truth, tmp_path):
    path = tmp_path / "sim_truth.duckdb"
    write_truth(truth, path)
    with pytest.raises(FileExistsError):
        write_truth(truth, path)
    con = open_truth(path)
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    assert {"campaign_truth", "creative_truth", "category_truth", "demand_index", "history_budgets", "sku_truth",
            "meta", "gt_incidents"} <= tables
    assert con.execute("SELECT count(*) FROM campaign_truth").fetchone()[0] == 12
    assert con.execute("SELECT count(*) FROM gt_incidents").fetchone()[0] == 0
    with pytest.raises(duckdb.Error):
        con.execute("DELETE FROM campaign_truth")
    con.close()
    write_truth(truth, path, overwrite=True)  # explicit reseed is allowed
