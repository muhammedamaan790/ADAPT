"""Truth-side economics (eval only, spec §22.8): the expected contribution after ads of a budget allocation over a
horizon, computed from the world's HIDDEN truth and current state. Implemented independently of adapt.economics (no
curves, no attribution weights, no bootstrap): it is what the clairvoyant oracle optimises.

For each campaign at daily budget B (its budget's amount x budget_share):
  spend   = B x mean pacing
  CPM     = cpm_at(spend) x E[CPM noise] x the weekday profile          (truth response model, world/truth.py)
  imps    = 1000 spend / CPM;  CTR = base_ctr x served creative quality x frequency^-gamma
  p_buy   = min(1, p_buy x demand^rho x category price factor)          (current prices, scenario-free demand)
  purchases = imps x CTR x click->session x p_buy, split over SKUs by the truth mix (cross-sell -> warehouse)
Unpaid demand per category = truth rates x demand x price factor. Stock rations each SKU's total demand (paid + unpaid)
against on hand + inbound arriving within the horizon (fill rate per SKU, applied to paid and unpaid alike).
Contribution per sold unit = price - COGS - ship - fee x price - return_rate x price.
CAA_H = sum over days of (filled contribution) - spend.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from world.priors import benchmarks
from world.step import WAREHOUSE, creative_quality
from world.truth import Truth, cpm_at, frequency


@dataclass
class WorldSnapshot:
    day: int                                  # the next day to simulate
    budgets: dict[str, float]                 # budget_id -> amount
    cum_impressions: dict[str, int]
    prices: dict[str, float]
    on_hand: dict[str, float]
    inbound: dict[str, tuple[float, int | None]]
    paused: set


def snapshot(store) -> WorldSnapshot:
    day = store.clock()[1]
    return WorldSnapshot(
        day=day,
        budgets={b: float(a) for _p, b, a, s in store.read("SELECT platform, budget_id, amount, status "
                                                             "FROM budgets_state") if s == "ENABLED"},
        cum_impressions=dict(store.read("SELECT creative_id, cum_impressions FROM creative_state")),
        prices=dict(store.read("SELECT sku, price_inr FROM price_state")),
        on_hand={k: float(v) for k, v in store.read("SELECT sku, on_hand FROM inventory_state")},
        inbound={k: (float(q), d) for k, q, d in store.read("SELECT sku, inbound_qty, inbound_day FROM "
                                                             "inventory_state")},
        paused={c for (c,) in store.read("SELECT campaign_id FROM campaign_status WHERE status = 'PAUSED'")})


def _unit_contribution(price: float, cogs: float, ship_pct: float, ship_min: float, fee: float, rr: float) -> float:
    ship = max(ship_min, ship_pct * price)
    return price - cogs - ship - fee * price - rr * price


def expected_caa(truth: Truth, snap: WorldSnapshot, budgets: dict[str, float], horizon: int = 7) -> float:
    bench = benchmarks()
    st, sig = bench["step"], bench["daily_log_sigma"]
    pace = 0.5 * (st["pacing"][0] + st["pacing"][1])
    cpm_noise = math.exp(sig["cpm"] ** 2 / 2)
    rho = bench["demand_on_conversion"]
    costs = bench["unit_costs"]
    cat = truth.catalog
    rates = truth.category_rates.set_index("category_code")
    wh = truth.warehouse
    total = 0.0
    for h in range(horizon):
        d = snap.day + h
        weekly = bench["weekly_cpm_profile"][truth.config.world_date(d).weekday()]
        price_factor = {c.code: sum(s.mix_weight * (snap.prices[s.sku] / s.unit_price_inr) ** truth.elasticity[c.code]
                                    for s in cat.skus_of(c.code)) for c in cat.categories}
        demand_units: dict[str, float] = {}
        paid_spend = 0.0
        for c in cat.campaigns:
            amount = budgets.get(c.budget_id, 0.0)
            if amount <= 0 or c.campaign_id in snap.paused:
                continue
            t = truth.campaigns[c.campaign_id]
            spend = amount * t.budget_share * pace
            cpm = cpm_at(spend, t) * cpm_noise * weekly
            imps = 1000.0 * spend / cpm
            live = [cr for cr in cat.creatives if cr.campaign_id == c.campaign_id and cr.launch_day <= d]
            if not live:
                continue
            q = np.array([creative_quality(truth, cr.creative_id, snap.cum_impressions.get(cr.creative_id, 0))
                          for cr in live])
            w = q ** st["delivery_optimization"]
            served = float((w / w.sum() * q).sum())
            ctr = min(1.0, t.base_ctr * served * frequency(imps, t.audience_size) ** -t.ctr_freq_gamma)
            demand = truth.demand_index(c.category_code, d)
            p_buy = min(1.0, t.p_buy * demand ** rho * price_factor[c.category_code])
            purchases = imps * ctr * t.click_session_rate * p_buy
            paid_spend += spend
            for s in cat.skus_of(c.category_code):
                demand_units[s.sku] = demand_units.get(s.sku, 0.0) + purchases * (1 - t.cross_sell_share) * s.mix_weight
            demand_units[WAREHOUSE] = demand_units.get(WAREHOUSE, 0.0) + purchases * t.cross_sell_share
        for c in cat.categories:
            unpaid = (rates.loc[c.code, "unpaid_email"] + rates.loc[c.code, "unpaid_organic"]) * \
                truth.demand_index(c.code, d) * price_factor[c.code]
            for s in cat.skus_of(c.code):
                demand_units[s.sku] = demand_units.get(s.sku, 0.0) + unpaid * s.mix_weight
        for sku, units in demand_units.items():
            if sku == WAREHOUSE:
                total += units * _unit_contribution(float(wh["price_inr"]), float(wh["cogs_inr"]),
                                                    costs["ship_cost_pct"], costs["ship_cost_min_inr"],
                                                    costs["payment_fee_pct"], float(wh["return_rate"]))
                continue
            s = next(x for x in cat.skus if x.sku == sku)
            q_in, d_in = snap.inbound.get(sku, (0.0, None))
            stock = snap.on_hand.get(sku, 0.0) + (q_in if d_in is not None and d_in < snap.day + horizon else 0.0)
            fill = min(1.0, stock / (units * horizon)) if units > 0 else 1.0  # horizon-level rationing, per day share
            total += units * fill * _unit_contribution(snap.prices[sku], s.unit_cogs_inr, costs["ship_cost_pct"],
                                                       costs["ship_cost_min_inr"], costs["payment_fee_pct"],
                                                       s.return_rate)
        total -= paid_spend
    return float(total)
