"""A3 'done when' (integration): after sync + canonical build, marts reconcile with the world's facts, the
reconciliation gaps (platform over-claiming, GA4 capture) are visible, and the refund estimate is unbiased."""

from datetime import date, datetime

import pytest

from adapt.ingest.sync import run_sync
from adapt.reconcile.build import build_canonical, logical_now

AS_OF = logical_now(date(2026, 10, 1))


def facts(world, sql, params=None):
    return world.app.state.store.read(sql, params)


@pytest.fixture
def canon(world, http, db):
    run_sync(db, http)
    return build_canonical(db, AS_OF)


def test_marts_reconcile_with_world_facts(world, db, canon):
    assert AS_OF == datetime(2026, 10, 1, 12, 0)
    assert all(h["status"] == "GREEN" for h in canon["health"].values()), canon["health"]
    # per campaign: spend and last-click orders equal what the world served and recorded
    w = {r[0]: r[1:] for r in facts(world, """
        SELECT c.campaign_id, sum(c.spend_inr), coalesce(o.n, 0)
        FROM fact_ad_campaign_daily c
        LEFT JOIN (SELECT campaign_id, count(*) n FROM fact_orders WHERE campaign_id IS NOT NULL GROUP BY 1) o
          USING (campaign_id)
        GROUP BY c.campaign_id, o.n""")}
    m = {r[0]: r[1:] for r in db.query("SELECT campaign_id, sum(spend), sum(attributed_orders) "
                                       "FROM marts.campaign_daily GROUP BY 1")}
    assert set(m) == set(w)
    for cid, (spend, orders) in w.items():
        assert m[cid][0] == pytest.approx(spend, rel=1e-4), cid  # Meta goes through USD cents
        assert m[cid][1] == orders, cid
    # brand totals
    n_orders = facts(world, "SELECT count(*) FROM fact_orders")[0][0]
    assert db.query("SELECT sum(orders) FROM marts.brand_daily")[0][0] == n_orders


def test_reconciliation_gaps_are_visible(world, db, canon):
    claimed = dict(facts(world, "SELECT platform, sum(conversions) FROM fact_ad_creative_daily GROUP BY 1"))
    store = dict(facts(world, "SELECT platform, sum(orders) FROM fact_ad_campaign_daily GROUP BY 1"))
    recon = {p: (c, s) for p, c, s in db.query("SELECT platform, sum(platform_conversions), "
                                               "sum(store_attributed_orders) FROM marts.recon_daily GROUP BY 1")}
    for p in ("meta", "google"):
        assert recon[p][0] / recon[p][1] == pytest.approx(claimed[p] / store[p], rel=1e-9)
        assert recon[p][0] / recon[p][1] > 1.0  # platforms over-claim
    ga = db.query("SELECT sum(ga_purchases) / sum(store_attributed_orders) FROM marts.recon_daily")[0][0]
    assert ga == pytest.approx(0.95, abs=0.03)  # GA4 sees ~95% of what the store recorded


def test_refund_estimate_is_unbiased_across_order_ages(world, db, canon):
    true_rate = facts(world, "SELECT avg(returned::INT) FROM fact_orders WHERE day < -40")[0][0]
    for lo, hi in ((date(2026, 9, 23), date(2026, 9, 30)), (date(2026, 9, 1), date(2026, 9, 22))):
        est = db.query("SELECT sum(refund) / sum(subtotal) FROM core.order_items "
                       "WHERE analysis_date BETWEEN ? AND ?", [lo, hi])[0][0]
        assert est == pytest.approx(true_rate, abs=0.025), (lo, hi)


def test_inventory_and_campaign_sku(world, db, canon):
    on_hand = dict(facts(world, "SELECT sku, on_hand FROM fact_erp_daily WHERE day = -1"))
    mart = dict(db.query("SELECT sku, on_hand FROM marts.sku_daily WHERE date = DATE '2026-09-30'"))
    assert mart == on_hand
    for cid, total in db.query("SELECT campaign_id, sum(attribution_weight) FROM core.campaign_sku GROUP BY 1"):
        assert total == pytest.approx(1.0), cid
    unmapped = db.query("SELECT max(attribution_weight) FROM core.campaign_sku WHERE sku = '__unmapped__'")[0][0]
    assert 0 < unmapped < 0.2  # cross-sell exists but stays below MIX_UNCERTAIN
