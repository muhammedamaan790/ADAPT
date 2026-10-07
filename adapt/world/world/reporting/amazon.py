"""Mock Amazon (Stage 2 SIMULATED channel): Sponsored Products v3 reporting + listings, and the SP-API marketplace
orders / returns of the SEPARATE marketplace order population (TheLook orders are never reassigned, spec §1).

GET /amazon/v3/reports/spCampaigns?startDate&endDate    ad-group daily: impressions, clicks, cost, purchases7d,
                                                         sales7d (platform-claimed, incl. view-through) in INR
GET /amazon/v3/reports/spPurchasedProduct?startDate&endDate   (date, campaign, ad group, purchasedAsin = SKU) click-
                                                         attributed purchases7d / sales7d (the SKU mix of SP sales)
GET /amazon/v3/sp/campaigns[?campaignIdFilter]          {"campaigns": [{campaignId, name, state, budget}]}
GET /amazon/v3/sp/adGroups, /amazon/v3/sp/productAds
GET /amazon/orders/v0/orders?CreatedAfter&CreatedBefore&NextToken    marketplace orders (paid + organic), INR
GET /amazon/returns/v0/returns?ReturnedAfter&ReturnedBefore&NextToken
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from world.accounts import AMAZON_CURRENCY, AMAZON_MARKETPLACE
from world.reporting.common import (
    NotSeeded,
    date_of,
    decode_cursor,
    encode_cursor,
    iso_local,
    not_seeded_response,
    reportable_days,
    world_of,
)

router = APIRouter(prefix="/amazon", tags=["mock-amazon-reporting"])
MARKET = ("amazon_sp", "amazon_organic")


def _window(request, start: str | None, end: str | None):
    store, truth = world_of(request)
    lo, hi = reportable_days(store, truth, date.fromisoformat(start[:10]) if start else None,
                             date.fromisoformat(end[:10]) if end else None)
    return store, truth, lo, hi


@router.get("/v3/reports/spCampaigns")
def sp_campaigns(request: Request, startDate: str, endDate: str) -> JSONResponse:
    try:
        store, truth, lo, hi = _window(request, startDate, endDate)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    rows = store.read("""SELECT day, campaign_id, adset_id, sum(impressions), sum(clicks), sum(spend_inr),
                                sum(conversions), sum(conversion_value_inr) FROM fact_ad_creative_daily
                         WHERE platform = 'amazon' AND day BETWEEN ? AND ? AND impressions > 0
                         GROUP BY 1, 2, 3 ORDER BY 1, 2, 3""", [lo, hi])
    return JSONResponse({"currencyCode": AMAZON_CURRENCY, "rows": [
        {"date": date_of(truth, d).isoformat(), "campaignId": cid, "adGroupId": ag, "impressions": int(i),
         "clicks": int(c), "cost": round(sp, 2), "purchases7d": int(p), "sales7d": round(v, 2)}
        for d, cid, ag, i, c, sp, p, v in rows]})


@router.get("/v3/reports/spPurchasedProduct")
def sp_purchased(request: Request, startDate: str, endDate: str) -> JSONResponse:
    try:
        store, truth, lo, hi = _window(request, startDate, endDate)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    rows = store.read("""SELECT day, campaign_id, adset_id, coalesce(sku, 'OTHER-ASSORTED'), count(*),
                                sum(unit_price_inr - discount_inr) FROM fact_orders
                         WHERE channel = 'amazon_sp' AND day BETWEEN ? AND ? GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4""",
                      [lo, hi])
    return JSONResponse({"currencyCode": AMAZON_CURRENCY, "rows": [
        {"date": date_of(truth, d).isoformat(), "campaignId": cid, "adGroupId": ag, "purchasedAsin": asin,
         "purchases7d": int(n), "sales7d": round(v, 2)} for d, cid, ag, asin, n, v in rows]})


@router.get("/v3/sp/campaigns")
def list_campaigns(request: Request, campaignIdFilter: str | None = None) -> JSONResponse:
    try:
        store, truth = world_of(request)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    out = []
    for c in (c for c in truth.catalog.campaigns if c.platform == "amazon"):
        if campaignIdFilter and c.campaign_id not in campaignIdFilter.split(","):
            continue
        row = store.read("SELECT amount, status FROM budgets_state WHERE platform = 'amazon' AND budget_id = ?",
                         [c.budget_id])
        amount, status = (row[0][0], row[0][1]) if row else (0.0, "PAUSED")
        out.append({"campaignId": c.campaign_id, "name": c.name, "state": status, "targetingType": "AUTO",
                    "budget": {"budget": round(amount, 2), "budgetType": "DAILY"}})
    return JSONResponse({"campaigns": out})


@router.get("/v3/sp/adGroups")
def list_ad_groups(request: Request) -> JSONResponse:
    try:
        _, truth = world_of(request)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    ids = {c.campaign_id for c in truth.catalog.campaigns if c.platform == "amazon"}
    return JSONResponse({"adGroups": [{"adGroupId": a.adset_id, "campaignId": a.campaign_id, "name": a.audience,
                                       "state": "ENABLED"} for a in truth.catalog.adsets if a.campaign_id in ids]})


@router.get("/v3/sp/productAds")
def list_product_ads(request: Request) -> JSONResponse:
    try:
        _, truth = world_of(request)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    ids = {c.campaign_id for c in truth.catalog.campaigns if c.platform == "amazon"}
    return JSONResponse({"productAds": [{"adId": cr.creative_id, "adGroupId": cr.adset_id,
                                         "campaignId": cr.campaign_id, "state": "ENABLED"}
                                        for cr in truth.catalog.creatives if cr.campaign_id in ids]})


@router.get("/orders/v0/orders")
def orders(request: Request, CreatedAfter: str | None = None, CreatedBefore: str | None = None,
           NextToken: str | None = None, MaxResultsPerPage: int = Query(default=100, ge=1, le=500)) -> JSONResponse:
    try:
        store, truth, lo, hi = _window(request, CreatedAfter, CreatedBefore)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    after = decode_cursor(NextToken)
    rows = store.read("""SELECT order_id, day, minute, sku, unit_price_inr, discount_inr FROM fact_orders
                         WHERE channel IN ('amazon_sp', 'amazon_organic') AND day BETWEEN ? AND ? AND order_id > ?
                         ORDER BY order_id LIMIT ?""", [lo, hi, after, MaxResultsPerPage])
    out = [{"AmazonOrderId": f"406-{oid}", "PurchaseDate": iso_local(truth, d, m), "OrderStatus": "Shipped",
            "SalesChannel": AMAZON_MARKETPLACE,
            "OrderTotal": {"CurrencyCode": AMAZON_CURRENCY, "Amount": f"{price - disc:.2f}"},
            "OrderItems": [{"OrderItemId": str(oid * 10 + 1), "SellerSKU": sku or "OTHER-ASSORTED",
                            "QuantityOrdered": 1, "ItemPrice": {"CurrencyCode": AMAZON_CURRENCY,
                                                                "Amount": f"{price - disc:.2f}"}}]}
           for oid, d, m, sku, price, disc in rows]
    payload = {"Orders": out}
    if len(rows) == MaxResultsPerPage:
        payload["NextToken"] = encode_cursor(rows[-1][0])
    return JSONResponse({"payload": payload})


@router.get("/returns/v0/returns")
def returns(request: Request, ReturnedAfter: str | None = None, ReturnedBefore: str | None = None,
            NextToken: str | None = None, MaxResultsPerPage: int = Query(default=100, ge=1, le=500)) -> JSONResponse:
    try:
        store, truth, lo, hi = _window(request, ReturnedAfter, ReturnedBefore)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    after = decode_cursor(NextToken)
    rows = store.read("""SELECT order_id, return_day, unit_price_inr - discount_inr FROM fact_orders
                         WHERE channel IN ('amazon_sp', 'amazon_organic') AND returned
                           AND return_day BETWEEN ? AND ? AND order_id > ? ORDER BY order_id LIMIT ?""",
                      [lo, hi, after, MaxResultsPerPage])
    payload = {"Returns": [{"AmazonOrderId": f"406-{oid}", "OrderItemId": str(oid * 10 + 1),
                            "ReturnDate": iso_local(truth, rd, 600),
                            "RefundAmount": {"CurrencyCode": AMAZON_CURRENCY, "Amount": f"{amt:.2f}"}}
                           for oid, rd, amt in rows]}
    if len(rows) == MaxResultsPerPage:
        payload["NextToken"] = encode_cursor(rows[-1][0])
    return JSONResponse({"payload": payload})
