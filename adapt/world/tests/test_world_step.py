"""A4 'done when' for world.step + seeding: seed reproducible, funnel invariants on the facts (T46), inventory
conservation, history calibration, common random numbers across forks (T35), log replay (T55), stockouts."""

from datetime import date

import numpy as np
import pytest

from world.seed import seed_world
from world.state import replay
from world.step import creative_quality, make_store
from world.truth import WorldConfig

SEED = 42


@pytest.fixture(scope="module")
def cfg(backbone_dir, global_ads_csv):
    return WorldConfig(seed=SEED, backbone_dir=backbone_dir, global_ads_csv=global_ads_csv,
                       history_end_date=date(2026, 9, 30))


@pytest.fixture(scope="module")
def world(cfg, tmp_path_factory):
    store, truth = seed_world(cfg, tmp_path_factory.mktemp("w42"))
    yield store, truth
    store.close()


def q(store, sql, params=None):
    return store.read(sql, params)


def test_history_is_complete_and_clock_at_day_zero(world):
    store, truth = world
    assert store.clock() == (SEED, 0)
    days = q(store, "SELECT min(day), max(day), count(DISTINCT day) FROM fact_ad_campaign_daily")[0]
    assert days == (-truth.history_days, -1, truth.history_days)
    n_campaigns = len(truth.catalog.campaigns)
    assert q(store, "SELECT count(*) FROM fact_ad_campaign_daily")[0][0] == n_campaigns * truth.history_days
    # budget edits are in the log as human (history-manager) actions, one per budget per weekly segment
    actors = dict(q(store, "SELECT actor_id, count(*) FROM world_log GROUP BY 1"))
    assert actors["history-manager"] % len(truth.catalog.budgets()) == 0


def test_funnel_invariants_on_daily_facts(world):
    store, _ = world
    bad = q(store, """SELECT count(*) FROM fact_ad_campaign_daily
                      WHERE clicks > impressions OR sessions > clicks OR purchases > sessions OR orders > purchases
                         OR reach > impressions OR spend_inr < 0""")[0][0]
    assert bad == 0
    # creative rows add up to the campaign rows
    diff = q(store, """SELECT count(*) FROM fact_ad_campaign_daily c
                       JOIN (SELECT day, campaign_id, sum(impressions) i, sum(clicks) k, sum(spend_inr) s
                             FROM fact_ad_creative_daily GROUP BY 1, 2) r USING (day, campaign_id)
                       WHERE c.impressions <> r.i OR c.clicks <> r.k OR abs(c.spend_inr - r.s) > 1e-6""")[0][0]
    assert diff == 0
    # every filled paid order comes from a campaign-day with spend
    orphan = q(store, """SELECT count(*) FROM fact_orders o LEFT JOIN fact_ad_campaign_daily c USING (day, campaign_id)
                         WHERE o.campaign_id IS NOT NULL AND (c.spend_inr IS NULL OR c.spend_inr = 0)""")[0][0]
    assert orphan == 0
    paid_orders = q(store, "SELECT count(*) FROM fact_orders WHERE campaign_id IS NOT NULL")[0][0]
    assert paid_orders == q(store, "SELECT sum(orders) FROM fact_ad_campaign_daily")[0][0]


def test_inventory_is_conserved_and_never_negative(world):
    store, _ = world
    assert q(store, "SELECT count(*) FROM fact_erp_daily WHERE on_hand < 0")[0][0] == 0
    broken = q(store, """SELECT count(*) FROM (
            SELECT sku, day, on_hand, receipts, units_sold,
                   lag(on_hand) OVER (PARTITION BY sku ORDER BY day) AS prev
            FROM fact_erp_daily) WHERE prev IS NOT NULL AND on_hand <> prev + receipts - units_sold""")[0][0]
    assert broken == 0
    mismatch = q(store, """SELECT count(*) FROM fact_erp_daily e
            LEFT JOIN (SELECT day, sku, count(*) n FROM fact_orders WHERE sku IS NOT NULL GROUP BY 1, 2) o
            USING (day, sku) WHERE e.units_sold <> coalesce(o.n, 0)""")[0][0]
    assert mismatch == 0


def test_history_paid_volume_is_calibrated(world):
    store, truth = world
    rates = truth.category_rates.set_index("category_code")
    expected = 0.0
    for t in truth.campaigns.values():
        expected += sum(rates.loc[t.category_code, f"paid_{t.channel}"] * truth.demand_index(t.category_code, d)
                        for d in range(-28, 0))
    simulated = q(store, "SELECT sum(purchases) FROM fact_ad_campaign_daily WHERE day >= -28")[0][0]
    assert simulated / expected == pytest.approx(1.0, abs=0.15)


def test_seeding_is_reproducible(world, cfg, tmp_path):
    store, _ = world
    again, _ = seed_world(cfg, tmp_path / "again")
    assert again.semantic_state_hash() == store.semantic_state_hash()
    again.close()


def test_replaying_the_log_reproduces_the_state(world, tmp_path):
    store, truth = world
    fresh = make_store(tmp_path / "replay.duckdb", truth)
    replay(store.log(), fresh)
    assert fresh.semantic_state_hash() == store.semantic_state_hash()
    fresh.close()


def test_common_random_numbers_across_forks(world, cfg, tmp_path):
    """Changing one campaign's budget leaves campaigns in other categories exactly unchanged (no shared stock)."""
    store, truth = world
    a = make_store(tmp_path / "a.duckdb", truth)
    b = make_store(tmp_path / "b.duckdb", truth)
    for fork in (a, b):
        replay(store.log(), fork)
    target = next(c for c in truth.catalog.campaigns if c.channel == "google_search")
    amount = q(a, "SELECT amount FROM budgets_state WHERE budget_id = ?", [target.budget_id])[0][0]
    b.commit("set_budget", "fork:boost", "test", {"platform": target.platform, "budget_id": target.budget_id,
                                                  "amount": amount * 1.5})
    for fork in (a, b):
        fork.commit("advance", "fork:adv", "test", {"days": 3})
    sql = ("SELECT campaign_id, impressions, clicks, sessions, orders FROM fact_ad_campaign_daily "
           "WHERE day >= 0 AND campaign_id IN (SELECT campaign_id FROM fact_ad_campaign_daily) ORDER BY 1, day")
    rows_a = {r[0]: r[1:] for r in q(a, sql)}
    rows_b = {r[0]: r[1:] for r in q(b, sql)}
    other = [c.campaign_id for c in truth.catalog.campaigns if c.category_code != target.category_code]
    assert all(rows_a[cid] == rows_b[cid] for cid in other)
    imps = "SELECT sum(impressions) FROM fact_ad_campaign_daily WHERE day >= 0 AND campaign_id = ?"
    assert q(b, imps, [target.campaign_id])[0][0] > q(a, imps, [target.campaign_id])[0][0]
    a.close()
    b.close()


def test_stockout_loses_demand_and_reorders(world, tmp_path):
    store, truth = world
    fork = make_store(tmp_path / "so.duckdb", truth)
    replay(store.log(), fork)
    sku = truth.catalog.skus[0].sku
    fork.commit("set_inventory", "so:zero", "test", {"sku": sku, "on_hand": 0, "cancel_inbound": True})
    fork.commit("advance", "so:adv", "test", {"days": 2})
    assert q(fork, "SELECT count(*) FROM fact_orders WHERE day = 0 AND sku = ?", [sku])[0][0] == 0
    assert q(fork, "SELECT coalesce(sum(units), 0) FROM fact_lost_demand WHERE day = 0 AND sku = ?", [sku])[0][0] > 0
    inbound = q(fork, "SELECT inbound_qty, inbound_day FROM inventory_state WHERE sku = ?", [sku])[0]
    assert inbound[0] > 0 and inbound[1] == 0 + truth.sku_lead_time[sku]  # reordered on day 0
    fork.close()


def test_creative_quality_wears_out_by_half_lives(world):
    _, truth = world
    cid = next(iter(truth.creatives))
    hl = truth.creatives[cid].fatigue_half_life_impressions
    q0 = creative_quality(truth, cid, 0)
    assert creative_quality(truth, cid, int(hl)) == pytest.approx(q0 / 2, rel=1e-3)
    assert creative_quality(truth, cid, int(2 * hl)) == pytest.approx(q0 / 4, rel=1e-3)


def test_platforms_claim_more_than_the_store_sees(world):
    store, _ = world
    rows = dict(q(store, """SELECT c.platform, cr.conv / c.orders FROM
        (SELECT platform, sum(orders) orders FROM fact_ad_campaign_daily GROUP BY 1) c JOIN
        (SELECT platform, sum(conversions) conv FROM fact_ad_creative_daily GROUP BY 1) cr USING (platform)"""))
    assert 1.0 < rows["google"] < rows["meta"]  # Meta claims more view-through than Google
    ga, store_paid = q(store, """SELECT sum(g.purchases), sum(c.orders) FROM fact_ga_daily g
                                 JOIN fact_ad_campaign_daily c USING (day, campaign_id)""")[0]
    assert ga / store_paid == pytest.approx(0.95, abs=0.03)
    returned = q(store, "SELECT avg(returned::INT), count(*) FILTER (WHERE returned AND return_day IS NULL) "
                        "FROM fact_orders")[0]
    assert 0.05 < returned[0] < 0.2 and returned[1] == 0
    assert np.isfinite(returned[0])
