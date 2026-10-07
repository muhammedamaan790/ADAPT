"""Stage 2 SIMULATED channel connectors (spec §1 native formats): TikTok Business API v1.3, Amazon Ads Sponsored
Products v3, Amazon SP-API marketplace orders + returns.

TikTok: ad-level daily report (AUCTION_AD), money in the advertiser currency (USD -> INR at the simulation FX),
stat_time_day labelled UTC (the source date is kept as the analysis date: the simulation shares one day grid; stated
in the connectors contract); campaign budgets live on the campaign (BUDGET_MODE_DAY, USD).
Amazon Ads: ad-group daily cost / sales7d / purchases7d (platform-claimed, INR) and the purchased-product report
(which SKUs the SP sales were); campaign daily budgets (INR). Amazon marketplace: orders and returns of the separate
marketplace population (SellerSKU = our SKU, INR, ex-tax prices), never matched to web sessions or UTMs.
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd

from adapt.ingest.connectors.ads import _upsert_snapshots
from adapt.ingest.connectors.base import ConnectorResult, SyncContext, fx_to_inr, sources_config
from adapt.ingest.connectors.commerce import parse_local_ts


def _tiktok_pages(ctx: SyncContext, path: str, params: dict) -> list[dict]:
    out, page = [], 1
    while True:
        q = params | {"page": page, "page_size": 1000}
        body = ctx.http.get(path, q)
        ctx.record_page("tiktok_ads", path, q, body)
        if body.get("code") != 0:
            from adapt.ingest.http import ConnectorError

            raise ConnectorError(f"TIKTOK_{body.get('code')}", body.get("message", ""))
        out += body["data"]["list"]
        if page >= body["data"]["page_info"]["total_page"]:
            return out
        page += 1


def normalize_tiktok_rows(rows: list[dict], currency: str) -> pd.DataFrame:
    fx = fx_to_inr(currency)
    return pd.DataFrame([{
        "date": date.fromisoformat(r["dimensions"]["stat_time_day"][:10]), "campaign_id": r["metrics"]["campaign_id"],
        "adgroup_id": r["metrics"]["adgroup_id"], "ad_id": r["dimensions"]["ad_id"],
        "impressions": int(r["metrics"]["impressions"]), "clicks": int(r["metrics"]["clicks"]),
        "spend_native": float(r["metrics"]["spend"]), "currency": currency, "fx_rate": fx,
        "spend_inr": float(r["metrics"]["spend"]) * fx, "platform_conversions": int(r["metrics"]["complete_payment"]),
        "platform_conversion_value_inr": float(r["metrics"]["total_complete_payment"]) * fx} for r in rows])


def sync_tiktok(ctx: SyncContext, since: date, until: date) -> ConnectorResult:
    cfg = sources_config()["sources"]["tiktok_ads"]
    base = f"/tiktok/{cfg['api_version']}"
    res = ConnectorResult("tiktok_ads")
    adv = {"advertiser_id": cfg["advertiser_id"]}
    rows = _tiktok_pages(ctx, f"{base}/report/integrated/get/", adv | {
        "data_level": "AUCTION_AD", "start_date": since.isoformat(), "end_date": until.isoformat()})
    res.rows["stg.tiktok_ad_daily"] = ctx.upsert("stg.tiktok_ad_daily",
                                                 ctx.stamp(normalize_tiktok_rows(rows, cfg["currency"]), "tiktok_ads"))
    fx = fx_to_inr(cfg["currency"])
    snaps = [{"entity_type": "campaign", "entity_id": c["campaign_id"], "campaign_id": c["campaign_id"],
              "budget_id": c["campaign_id"], "name": c["campaign_name"],
              "status": "ACTIVE" if c["operation_status"] == "ENABLE" else "PAUSED",
              "channel_type": c["objective_type"], "budget_amount_inr": float(c["budget"]) * fx,
              "budget_is_shared": False, "attributes": json.dumps({"budget_usd": c["budget"],
                                                                   "budget_mode": c["budget_mode"]})}
             for c in _tiktok_pages(ctx, f"{base}/campaign/get/", adv)]
    snaps += [{"entity_type": "adset", "entity_id": a["adgroup_id"], "parent_id": a["campaign_id"],
               "campaign_id": a["campaign_id"], "name": a["adgroup_name"], "status": a["operation_status"],
               "attributes": json.dumps({"audience": a["adgroup_name"]})}
              for a in _tiktok_pages(ctx, f"{base}/adgroup/get/", adv)]
    snaps += [{"entity_type": "ad", "entity_id": a["ad_id"], "parent_id": a["adgroup_id"],
               "campaign_id": a["campaign_id"], "name": a["ad_name"], "status": a["operation_status"]}
              for a in _tiktok_pages(ctx, f"{base}/ad/get/", adv)]
    res.rows["stg.entity_snapshots"] = _upsert_snapshots(ctx, "tiktok_ads", "tiktok", snaps)
    res.pages = ctx.pages.get("tiktok_ads", 0)
    return res


def sync_amazon_ads(ctx: SyncContext, since: date, until: date) -> ConnectorResult:
    cfg = sources_config()["sources"]["amazon_ads"]
    res = ConnectorResult("amazon_ads")
    win = {"startDate": since.isoformat(), "endDate": until.isoformat()}

    def get(path, params=None):
        body = ctx.http.get(path, params or {})
        ctx.record_page("amazon_ads", path, params or {}, body)
        return body

    product_ads = get("/amazon/v3/sp/productAds")["productAds"]
    ad_of_group = {a["adGroupId"]: a["adId"] for a in product_ads}
    rows = get("/amazon/v3/reports/spCampaigns", win)["rows"]
    df = pd.DataFrame([{"date": date.fromisoformat(r["date"]), "campaign_id": r["campaignId"],
                        "ad_group_id": r["adGroupId"], "ad_id": ad_of_group.get(r["adGroupId"]),
                        "impressions": int(r["impressions"]), "clicks": int(r["clicks"]), "cost_inr": float(r["cost"]),
                        "purchases7d": int(r["purchases7d"]), "sales7d_inr": float(r["sales7d"])} for r in rows])
    res.rows["stg.amazon_sp_daily"] = ctx.upsert("stg.amazon_sp_daily", ctx.stamp(df, "amazon_ads"))
    pp = get("/amazon/v3/reports/spPurchasedProduct", win)["rows"]
    dfp = pd.DataFrame([{"date": date.fromisoformat(r["date"]), "campaign_id": r["campaignId"],
                         "ad_group_id": r["adGroupId"], "sku": r["purchasedAsin"], "purchases7d": int(r["purchases7d"]),
                         "sales7d_inr": float(r["sales7d"])} for r in pp])
    res.rows["stg.amazon_purchased_product"] = ctx.upsert("stg.amazon_purchased_product", ctx.stamp(dfp, "amazon_ads"))
    snaps = [{"entity_type": "campaign", "entity_id": c["campaignId"], "campaign_id": c["campaignId"],
              "budget_id": c["campaignId"], "name": c["name"],
              "status": "ACTIVE" if c["state"] == "ENABLED" else "PAUSED", "channel_type": "SPONSORED_PRODUCTS",
              "budget_amount_inr": float(c["budget"]["budget"]), "budget_is_shared": False,
              "attributes": json.dumps({"budget": c["budget"], "currency": cfg["currency"]})}
             for c in get("/amazon/v3/sp/campaigns")["campaigns"]]
    snaps += [{"entity_type": "adset", "entity_id": a["adGroupId"], "parent_id": a["campaignId"],
               "campaign_id": a["campaignId"], "name": a["name"], "status": a["state"],
               "attributes": json.dumps({"audience": a["name"]})} for a in get("/amazon/v3/sp/adGroups")["adGroups"]]
    snaps += [{"entity_type": "ad", "entity_id": a["adId"], "parent_id": a["adGroupId"], "campaign_id": a["campaignId"],
               "name": f"product ad {a['adId']}", "status": a["state"]} for a in product_ads]
    res.rows["stg.entity_snapshots"] = _upsert_snapshots(ctx, "amazon_ads", "amazon", snaps)
    res.pages = ctx.pages.get("amazon_ads", 0)
    return res


def _token_pages(ctx: SyncContext, path: str, params: dict, key: str) -> list[dict]:
    out, token = [], None
    while True:
        q = params | ({"NextToken": token} if token else {})
        body = ctx.http.get(path, q)
        ctx.record_page("amazon_marketplace", path, q, body)
        out += body["payload"][key]
        token = body["payload"].get("NextToken")
        if not token:
            return out


def normalize_amazon_orders(orders: list[dict]) -> pd.DataFrame:
    rows = []
    for o in orders:
        created = parse_local_ts(o["PurchaseDate"])
        for li in o["OrderItems"]:
            price, qty = float(li["ItemPrice"]["Amount"]), int(li["QuantityOrdered"])
            rows.append({"order_id": int(o["AmazonOrderId"].split("-")[-1]), "line_item_id": int(li["OrderItemId"]),
                         "created_at": created, "date": created.date(), "sku": li["SellerSKU"], "qty": qty,
                         "unit_price": price / qty, "line_subtotal_ex_tax": price,
                         "currency": li["ItemPrice"]["CurrencyCode"]})
    return pd.DataFrame(rows)


def sync_amazon_marketplace(ctx: SyncContext, since: date, until: date) -> ConnectorResult:
    res = ConnectorResult("amazon_marketplace")
    win_o = {"CreatedAfter": since.isoformat(), "CreatedBefore": until.isoformat(), "MaxResultsPerPage": 500}
    orders = normalize_amazon_orders(_token_pages(ctx, "/amazon/orders/v0/orders", win_o, "Orders"))
    res.rows["stg.amazon_order_lines"] = ctx.upsert("stg.amazon_order_lines", ctx.stamp(orders, "amazon_marketplace"))
    win_r = {"ReturnedAfter": since.isoformat(), "ReturnedBefore": until.isoformat(), "MaxResultsPerPage": 500}
    rets = _token_pages(ctx, "/amazon/returns/v0/returns", win_r, "Returns")
    df = pd.DataFrame([{"order_id": int(r["AmazonOrderId"].split("-")[-1]), "line_item_id": int(r["OrderItemId"]),
                        "created_at": parse_local_ts(r["ReturnDate"]), "date": parse_local_ts(r["ReturnDate"]).date(),
                        "amount": float(r["RefundAmount"]["Amount"])} for r in rets])
    res.rows["stg.amazon_returns"] = ctx.upsert("stg.amazon_returns", ctx.stamp(df, "amazon_marketplace"))
    res.pages = ctx.pages.get("amazon_marketplace", 0)
    return res

