"""A4 'done when': seed reproducibility and funnel invariants (T46), plus CRN independence across entities."""

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from world import rng
from world.funnel import (
    FunnelInputs,
    ProspectCapacityError,
    impressions_bought,
    n_max,
    prospect_funnel,
    simulate_campaign_day,
)

SKUS = ("SKU-B", "SKU-A", "SKU-C")


def make_inputs(spend=5_000.0, **kw) -> FunnelInputs:
    base = dict(
        spend=spend, cpm=100.0, ctr=0.02, click_session_rate=0.88, cvr=0.05,
        audience_size=40_000, sku_ids=SKUS, sku_weights=(0.5, 0.3, 0.2), return_rates=(0.1, 0.2, 0.0),
    )
    base.update(kw)
    return FunnelInputs(**base)


POOL = n_max(s_max=50_000, min_cpm=50.0)


# ---- rng ---------------------------------------------------------------------------------------
def test_streams_are_index_addressable_prefixes():
    long = rng.uniforms(42, 3, "C1", "click", 1_000)
    assert np.array_equal(rng.uniforms(42, 3, "C1", "click", 400), long[:400])


def test_streams_differ_by_every_key_component():
    ref = rng.uniforms(42, 3, "C1", "click", 50)
    for args in [(43, 3, "C1", "click"), (42, 4, "C1", "click"), (42, 3, "C2", "click"), (42, 3, "C1", "session")]:
        assert not np.array_equal(rng.uniforms(*args, 50), ref)


def test_key_is_unambiguous_with_separator_characters():
    assert rng.stream_key(1, 2, "a|b", "c") != rng.stream_key(1, 2, "a", "b|c")


# ---- invariants (T46) ------------------------------------------------------------------------------
@settings(max_examples=60, deadline=None)
@given(
    spend=st.floats(0, 20_000),
    ctr=st.floats(0, 1),
    csr=st.floats(0, 1),
    cvr=st.floats(0, 3),
    avail=st.floats(0, 1),
    day=st.integers(0, 365),
)
def test_conditional_chain_invariants(spend, ctr, csr, cvr, avail, day):
    inp = make_inputs(spend=spend, ctr=ctr, click_session_rate=csr, cvr=cvr, availability=avail)
    assert 0.0 <= inp.p_buy <= 1.0
    a = prospect_funnel(7, day, "C1", inp, POOL)
    assert not (a.session & ~a.clicked).any(), "session without click"
    assert not (a.purchased & ~a.session).any(), "purchase without session"
    assert not (a.returned & ~a.purchased).any(), "return without purchase"
    assert ((a.sku_index >= 0) == a.purchased).all(), "SKU drawn only for purchased prospects"
    assert a.clicked.size == impressions_bought(spend, 100.0)


def test_same_seed_state_and_spend_give_identical_outcomes():
    inp = make_inputs()
    assert simulate_campaign_day(42, 10, "C1", inp, POOL) == simulate_campaign_day(42, 10, "C1", inp, POOL)


def test_more_spend_exposes_a_strict_superset_of_the_same_prospects():
    low = prospect_funnel(42, 10, "C1", make_inputs(spend=2_000), POOL)
    high = prospect_funnel(42, 10, "C1", make_inputs(spend=6_000), POOL)
    n = low.clicked.size
    assert high.clicked.size > n
    for name in ("clicked", "session", "purchased", "returned", "sku_index", "user_id"):
        assert np.array_equal(getattr(high, name)[:n], getattr(low, name)), name


def test_other_campaigns_spend_and_call_order_do_not_shift_a_campaign():
    a_first = simulate_campaign_day(42, 10, "A", make_inputs(), POOL)
    simulate_campaign_day(42, 10, "B", make_inputs(spend=30_000), POOL)
    a_again = simulate_campaign_day(42, 10, "A", make_inputs(), POOL)
    assert a_first == a_again


def test_sku_assignment_does_not_depend_on_input_order():
    fwd = simulate_campaign_day(42, 1, "C1", make_inputs(), POOL)
    rev = simulate_campaign_day(
        42, 1, "C1",
        make_inputs(sku_ids=tuple(reversed(SKUS)), sku_weights=(0.2, 0.3, 0.5), return_rates=(0.0, 0.2, 0.1)),
        POOL,
    )
    assert fwd.units_by_sku == rev.units_by_sku


def test_zero_spend_exposes_nothing():
    out = simulate_campaign_day(42, 1, "C1", make_inputs(spend=0), POOL)
    assert (out.impressions, out.clicks, out.purchases, out.reach, out.frequency) == (0, 0, 0, 0, None)


# ---- capacity --------------------------------------------------------------------------------------
def test_n_max_formula():
    assert n_max(s_max=10_000, min_cpm=80) == 187_500  # ceil(1.5 * 1000 * 10000 / 80)


def test_exceeding_the_prospect_pool_is_a_hard_error():
    with pytest.raises(ProspectCapacityError):
        prospect_funnel(42, 1, "C1", make_inputs(spend=10_000), pool_size=99_999)


# ---- rates are what the world says they are ------------------------------------------------------------
def test_aggregate_rates_match_truth_within_sampling_error():
    inp = make_inputs(spend=40_000, ctr=0.03, cvr=0.08)  # 400k impressions
    out = simulate_campaign_day(5, 2, "C1", inp, POOL)
    assert out.clicks / out.impressions == pytest.approx(0.03, rel=0.03)
    assert out.sessions / out.clicks == pytest.approx(0.88, rel=0.02)
    # purchases | sessions ~ Binomial(sessions, p_buy)
    sd = np.sqrt(out.sessions * 0.08 * 0.92)
    assert abs(out.purchases - out.sessions * 0.08) < 4 * sd
    assert sum(out.units_by_sku.values()) == out.purchases
    assert out.units_by_sku.get("SKU-C", 0) > 0 and "SKU-C" not in out.returns_by_sku  # 0% return rate
    assert 1.0 < out.frequency < 20.0


def test_p_buy_is_capped_at_one():
    assert make_inputs(cvr=2.0, price_factor=1.5).p_buy == 1.0


@pytest.mark.parametrize(
    "kw",
    [dict(ctr=1.2), dict(click_session_rate=-0.1), dict(sku_weights=(0.5, 0.5, 0.5)), dict(sku_ids=("A", "A", "B")),
     dict(return_rates=(0.1, 1.5, 0.0)), dict(sku_ids=()), dict(cvr=-0.01)],
)
def test_invalid_inputs_are_rejected(kw):
    with pytest.raises(ValueError):
        make_inputs(**kw)
