"""Metrics engine: pure, deterministic functions (spec §5). No I/O, no hidden state.

Denominator contract: a ratio whose denominator is 0 returns Metric(None, "ZERO_DENOMINATOR"),
never 0, inf, or NaN. Exception with meaning: ROAS = 0 when spend > 0 and revenue = 0.
Ratios are always computed from window totals, never as means of daily ratios.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

ZERO_DENOMINATOR = "ZERO_DENOMINATOR"
# Denominators are counts or rupees; anything smaller than this is numerically zero (avoids inf on subnormals).
DENOMINATOR_EPS = 1e-9


@dataclass(frozen=True)
class Metric:
    value: float | None
    reason: str | None = None

    @property
    def is_null(self) -> bool:
        return self.value is None


def ratio(numerator: float, denominator: float, scale: float = 1.0) -> Metric:
    if abs(denominator) < DENOMINATOR_EPS:
        return Metric(None, ZERO_DENOMINATOR)
    value = scale * numerator / denominator
    if not math.isfinite(value):
        raise ValueError(f"non-finite metric: {numerator}/{denominator}")
    return Metric(value)


# ---- funnel / ad efficiency -------------------------------------------------------------
def ctr(clicks: float, impressions: float) -> Metric:
    return ratio(clicks, impressions)


def cpc(spend: float, clicks: float) -> Metric:
    return ratio(spend, clicks)


def cpm(spend: float, impressions: float) -> Metric:
    return ratio(spend, impressions, scale=1000.0)


def cvr(orders: float, clicks: float) -> Metric:
    return ratio(orders, clicks)


def cpa(spend: float, orders: float) -> Metric:
    return ratio(spend, orders)


def aov(net_revenue: float, orders: float) -> Metric:
    return ratio(net_revenue, orders)


def roas(revenue: float, spend: float) -> Metric:
    return ratio(revenue, spend)


def poas(cba: float, spend: float) -> Metric:
    """Attributed contribution before ads per rupee of spend (the single canonical name)."""
    return ratio(cba, spend)


def mer(total_net_revenue: float, total_spend: float) -> Metric:
    return ratio(total_net_revenue, total_spend)


def cac(total_spend: float, new_customers: float) -> Metric:
    return ratio(total_spend, new_customers)


def session_click_ratio(sessions: float, clicks: float) -> Metric:
    """Tracking-health ratio. Deliberately NOT capped at 1 (repeat sessions, window mismatch)."""
    return ratio(sessions, clicks)


def over_attribution(platform_conversions: float, store_attributed_orders: float) -> Metric:
    """Disagreement between platform-claimed and store last-paid-click conversions (not identified overlap)."""
    return ratio(platform_conversions, store_attributed_orders)


# ---- revenue chain (tax excluded everywhere) -----------------------------------------------
def gmv(unit_price: float, qty: float) -> float:
    return unit_price * qty


def net_revenue(gmv_value: float, discounts: float, refunds: float) -> float:
    return gmv_value - discounts - refunds


def contribution_before_ads(net_rev: float, cogs: float, ship_cost: float, payment_fees: float) -> float:
    return net_rev - cogs - ship_cost - payment_fees


def contribution_after_ads(cba: float, ad_spend: float) -> float:
    return cba - ad_spend


def cm_before_ads_pct(cba: float, net_rev: float) -> Metric:
    return ratio(cba, net_rev)


def cm_after_ads_pct(caa: float, net_rev: float) -> Metric:
    return ratio(caa, net_rev)


# ---- unit economics (spec §8.1 step 3) ------------------------------------------------------
def nrpu(price: float, avg_discount_rate: float, return_rate: float) -> float:
    """Expected net revenue per physical unit: price x (1 - discount) minus the expected refund per unit.

    Refunds and discounts are subtracted exactly once, here.
    """
    if not 0 <= avg_discount_rate < 1 or not 0 <= return_rate < 1:
        raise ValueError("discount and return rates must be in [0, 1)")
    paid = price * (1 - avg_discount_rate)
    return paid - return_rate * paid


def unit_contribution(nrpu_value: float, cogs: float, ship_cost: float, fee_pct: float) -> float:
    """Contribution before ads per physical unit, built on nrpu (refunds are NOT subtracted again)."""
    return nrpu_value - cogs - ship_cost - fee_pct * nrpu_value


def units_from_net_revenue(net_rev: float, nrpu_value: float) -> float:
    if nrpu_value <= 0:
        raise ValueError("nrpu must be positive to convert net revenue into units")
    return net_rev / nrpu_value


# ---- refund accounting contract (spec §5) ----------------------------------------------------
RefundBasis = Literal["realized", "liability_max", "expected"]


def line_refund(
    realized_to_date: float, expected_total: float, matured: bool
) -> tuple[float, RefundBasis]:
    """Total refund for an order line. Realized and expected amounts are never added together."""
    if matured:
        return realized_to_date, "realized"
    return max(realized_to_date, expected_total), "liability_max"


# ---- inventory (spec §5, §0.5) -----------------------------------------------------------------
UNBOUNDED = "UNBOUNDED"
InventoryBand = Literal["CRITICAL", "LOW", "HEALTHY", "HIGH", "EXCESS"]
SkuStatus = Literal["OK", "AT_RISK", "SHORT"]


def available_units(on_hand: float, reserved: float, inbound: float = 0.0, inbound_confidence: float = 0.0) -> float:
    return on_hand - reserved + inbound_confidence * inbound


def days_of_cover(on_hand: float, reserved: float, forecast_daily_units: float) -> float | str:
    if forecast_daily_units <= 0:
        return UNBOUNDED
    return (on_hand - reserved) / forecast_daily_units


def inventory_band(cover: float | str, on_hand: float) -> InventoryBand:
    if cover == UNBOUNDED:
        return "EXCESS" if on_hand > 0 else "CRITICAL"
    if cover < 3:
        return "CRITICAL"
    if cover < 7:
        return "LOW"
    if cover < 21:
        return "HEALTHY"
    if cover <= 45:
        return "HIGH"
    return "EXCESS"


def safety_stock(service_level: float, sigma_daily: float, lead_time_days: float) -> float:
    from scipy.stats import norm

    return float(norm.ppf(service_level)) * sigma_daily * math.sqrt(lead_time_days)


def reorder_point(mean_daily_demand: float, lead_time_days: float, ss: float) -> float:
    return mean_daily_demand * lead_time_days + ss


def atp(available: float, safety_stock_units: float, baseline_demand_h: float) -> float:
    """Available to promote; can be negative (a baseline deficit), and is never clipped here."""
    return available - safety_stock_units - baseline_demand_h


def projected_shortfall(projected_units: float, available: float, safety_stock_units: float) -> float:
    return max(projected_units - (available - safety_stock_units), 0.0)


def sku_status(projected_units: float, available: float, safety_stock_units: float) -> SkuStatus:
    """Stage 1 deterministic inventory status. at_risk = status in {AT_RISK, SHORT}."""
    if projected_units <= available - safety_stock_units:
        return "OK"
    if projected_units <= available:
        return "AT_RISK"
    return "SHORT"
