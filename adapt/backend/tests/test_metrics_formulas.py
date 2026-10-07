"""B1 'done when': hand-calculated checks, including the return/discount nrpu cases from spec §8.1."""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from adapt.metrics import formulas as f


# ---- hand checks ---------------------------------------------------------------------------
def test_nrpu_with_returns_hand_check():
    # price 100, COGS 50, no discount, ship/fee 0, return rate 20%
    n = f.nrpu(100, 0.0, 0.20)
    assert n == pytest.approx(80.0)
    assert f.units_from_net_revenue(80, n) == pytest.approx(1.0)  # not 0.8
    assert f.unit_contribution(n, cogs=50, ship_cost=0, fee_pct=0) == pytest.approx(30.0)  # not 24


def test_nrpu_with_discount_hand_check():
    assert f.nrpu(100, 0.10, 0.0) == pytest.approx(90.0)


def test_nrpu_with_discount_and_returns():
    # paid 90, expected refund 10% of 90 = 9 -> 81
    assert f.nrpu(100, 0.10, 0.10) == pytest.approx(81.0)


def test_unit_contribution_fee_on_nrpu():
    assert f.unit_contribution(80, cogs=40, ship_cost=5, fee_pct=0.02) == pytest.approx(80 - 40 - 5 - 1.6)


def test_funnel_ratios_hand_check():
    assert f.ctr(50, 10_000).value == pytest.approx(0.005)
    assert f.cpm(2_000, 100_000).value == pytest.approx(20.0)
    assert f.cpc(2_000, 500).value == pytest.approx(4.0)
    assert f.cvr(25, 500).value == pytest.approx(0.05)
    assert f.cpa(2_000, 25).value == pytest.approx(80.0)
    assert f.aov(25_000, 25).value == pytest.approx(1_000.0)
    assert f.roas(25_000, 2_000).value == pytest.approx(12.5)


def test_revenue_chain_hand_check():
    g = f.gmv(1_000, 3)
    nr = f.net_revenue(g, discounts=300, refunds=200)
    cba = f.contribution_before_ads(nr, cogs=1_000, ship_cost=150, payment_fees=50)
    caa = f.contribution_after_ads(cba, ad_spend=400)
    assert (g, nr, cba, caa) == (3_000, 2_500, 1_300, 900)
    assert f.cm_before_ads_pct(cba, nr).value == pytest.approx(0.52)
    assert f.cm_after_ads_pct(caa, nr).value == pytest.approx(0.36)


# ---- denominator contract --------------------------------------------------------------------
@pytest.mark.parametrize(
    "metric",
    [f.ctr(5, 0), f.cpc(10, 0), f.cpm(10, 0), f.cvr(1, 0), f.cpa(10, 0), f.aov(10, 0), f.roas(10, 0),
     f.poas(10, 0), f.mer(10, 0), f.cac(10, 0), f.session_click_ratio(10, 0)],
)
def test_zero_denominator_is_null_with_reason(metric):
    assert metric.value is None and metric.reason == f.ZERO_DENOMINATOR


def test_roas_zero_revenue_is_a_valid_zero():
    m = f.roas(0, 1_000)
    assert m.value == 0.0 and m.reason is None


def test_session_click_ratio_not_capped():
    assert f.session_click_ratio(130, 100).value == pytest.approx(1.3)


# ---- refunds ----------------------------------------------------------------------------------
def test_refund_liability_never_adds_realized_and_expected():
    assert f.line_refund(realized_to_date=10, expected_total=6, matured=False) == (10, "liability_max")
    assert f.line_refund(realized_to_date=2, expected_total=6, matured=False) == (6, "liability_max")
    assert f.line_refund(realized_to_date=2, expected_total=6, matured=True) == (2, "realized")


# ---- inventory --------------------------------------------------------------------------------
def test_days_of_cover_unbounded_and_bands():
    assert f.days_of_cover(10, 0, 0) == f.UNBOUNDED
    assert f.inventory_band(f.UNBOUNDED, on_hand=10) == "EXCESS"
    assert f.inventory_band(f.UNBOUNDED, on_hand=0) == "CRITICAL"
    assert f.inventory_band(f.days_of_cover(20, 2, 9), 20) == "CRITICAL"  # 2 days
    assert f.inventory_band(5, 1) == "LOW"
    assert f.inventory_band(10, 1) == "HEALTHY"
    assert f.inventory_band(30, 1) == "HIGH"
    assert f.inventory_band(60, 1) == "EXCESS"


def test_safety_stock_and_reorder_point():
    ss = f.safety_stock(0.95, sigma_daily=10, lead_time_days=4)
    assert ss == pytest.approx(1.6449 * 10 * 2, rel=1e-3)
    assert f.reorder_point(20, 4, ss) == pytest.approx(80 + ss)


def test_sku_status_and_projected_shortfall():
    # available 100, safety stock 20
    assert f.sku_status(70, 100, 20) == "OK" and f.projected_shortfall(70, 100, 20) == 0
    assert f.sku_status(90, 100, 20) == "AT_RISK" and f.projected_shortfall(90, 100, 20) == 10
    assert f.sku_status(130, 100, 20) == "SHORT" and f.projected_shortfall(130, 100, 20) == 50


def test_atp_keeps_deficit():
    assert f.atp(available=50, safety_stock_units=20, baseline_demand_h=130) == -100


def test_available_units_inbound_confidence():
    assert f.available_units(100, 10, inbound=50, inbound_confidence=0.9) == pytest.approx(135)


# ---- properties ------------------------------------------------------------------------------
money = st.floats(min_value=0, max_value=1e7, allow_nan=False, allow_infinity=False)


@given(price=st.floats(1, 1e5), disc=st.floats(0, 0.9), ret=st.floats(0, 0.9))
def test_nrpu_round_trip(price, disc, ret):
    n = f.nrpu(price, disc, ret)
    assert 0 < n <= price
    assert f.units_from_net_revenue(n * 7, n) == pytest.approx(7)


@given(num=money, den=money)
def test_ratio_never_returns_non_finite(num, den):
    m = f.ratio(num, den)
    assert m.value is None or math.isfinite(m.value)
