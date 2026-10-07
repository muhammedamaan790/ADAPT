"""The brand's external accounts as the mock platforms present them (IDs, currencies, time zones).

The world stores money in INR. Each platform renders it in its account currency, so ADAPT's connectors must
normalise micros, minor units and FX exactly as they would against the real APIs (spec §1 connector contracts).
"""

from __future__ import annotations

from world.priors import benchmarks

META_AD_ACCOUNT_ID = "1029384756"  # rendered as act_1029384756
META_CURRENCY = "USD"
GOOGLE_CURRENCY = "INR"  # matches the real Google Ads test account
STORE_CURRENCY = "INR"
GA4_PROPERTY_ID = "412345678"
BRAND_TIMEZONE = "Asia/Kolkata"
STORE_GST_RATE = 0.12  # apparel GST, charged on top of tax-exclusive prices (ADAPT must strip it)
WAREHOUSE_SKU = "OTHER-ASSORTED"  # store SKU for non-promoted (unmapped) products
TIKTOK_ADVERTISER_ID = "7012345678901234567"  # Stage 2 SIMULATED channels
TIKTOK_CURRENCY = "USD"
TIKTOK_TIMEZONE = "UTC"  # stat_time_day is labelled UTC; the simulation's day grid is shared (stated in the contract)
AMAZON_PROFILE_ID = "3141592653"
AMAZON_CURRENCY = "INR"
AMAZON_MARKETPLACE = "Amazon.in"


def usd_per_inr() -> float:
    return 1.0 / float(benchmarks()["fx_usd_inr"])


def inr_to_meta(amount_inr: float) -> float:
    return amount_inr * usd_per_inr()


def meta_to_inr(amount_usd: float) -> float:
    return amount_usd * float(benchmarks()["fx_usd_inr"])
