"""Inventory page recommendations: deterministic rules on cover, reorder point, inbound and velocity."""

from adapt.api.routers.data import inventory_action

BASE = dict(available=500, inbound=0, days_to_arrival=None, position=500.0, rop=300.0, v28=25.0, change=0.0,
            spend=1000.0, lead=14, excess=45.0)


def act(**kw):
    return inventory_action(**{**BASE, **kw})


def test_at_or_below_the_reorder_point_restocks_first():
    action, reason = act(available=100, position=100.0, change=0.4)
    assert action == "RESTOCK" and "reorder point (300)" in reason and "40% faster" in reason


def test_selling_out_before_inbound_lands_holds_spend_or_expedites():
    assert act(available=50, inbound=600, days_to_arrival=5, position=590.0)[0] == "HOLD_SPEND"  # 2 days of cover
    assert act(available=50, inbound=600, days_to_arrival=5, position=590.0, spend=0.0)[0] == "EXPEDITE"
    action, reason = act(available=0, inbound=600, days_to_arrival=1, position=540.0)
    assert action == "HOLD_SPEND" and reason.startswith("Out of stock; 600 inbound units arrive in 1 day")
    assert "within a day" in act(available=5, inbound=600, days_to_arrival=1, position=545.0)[1]


def test_excess_cover_and_no_demand_are_clearance_candidates():
    assert act(available=2000, position=2000.0)[0] == "CLEAR_EXCESS"  # 80 days > 45
    assert act(v28=0.0, change=None)[0] == "CLEAR_EXCESS"


def test_velocity_scales_with_deep_stock_and_watches_otherwise():
    assert act(available=900, position=900.0, change=0.3)[0] == "SCALE_DEMAND"  # 36 days >= 2 x 14
    assert act(change=0.3)[0] == "WATCH"  # 20 days
    assert act(change=0.2)[0] == "CONTINUE"  # below the 25% alert
