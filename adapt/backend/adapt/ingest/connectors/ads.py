"""Meta Marketing API v25.0 and Google Ads API v25 connectors.

Meta: ad-level daily insights (USD strings -> INR at the simulation FX), campaign-level reach, and object
snapshots (campaigns with daily_budget in USD cents, ad sets, ads). Google: GAQL ad-level daily metrics
(cost_micros -> INR), and campaign / budget / ad group / ad snapshots. Platform conversions are kept as
platform-claimed numbers (they include view-through); reconciliation happens later (A3).
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pandas as pd

from adapt.ingest.connectors.base import ConnectorResult, SyncContext, fx_to_inr, sources_config

PURCHASE_ACTION = "offsite_conversion.fb_pixel_purchase"
MICROS = 1_000_000


# ---- pure normalisers (unit-tested on native payloads) -------------------------------------------------------
def _action_value(items: list[dict] | None) -> float:
    for a in items or []:
        if a.get("action_type") == PURCHASE_ACTION:
            return float(a["value"])
    return 0.0


def normalize_meta_ad_rows(rows: list[dict], currency: str) -> pd.DataFrame:
    fx = fx_to_inr(currency)
    out = [{
        "date": date.fromisoformat(r["date_start"]), "campaign_id": r["campaign_id"], "adset_id": r["adset_id"],
        "ad_id": r["ad_id"], "impressions": int(r["impressions"]), "clicks": int(r["clicks"]),
        "spend_native": float(r["spend"]), "currency": currency, "fx_rate": fx, "spend_inr": float(r["spend"]) * fx,
        "platform_conversions": int(_action_value(r.get("actions"))),
        "platform_conversion_value_inr": _action_value(r.get("action_values")) * fx,
    } for r in rows]
    return pd.DataFrame(out)


def normalize_meta_campaign_rows(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{"date": date.fromisoformat(r["date_start"]), "campaign_id": r["campaign_id"],
                          "impressions": int(r["impressions"]), "reach": int(r["reach"])} for r in rows])


def normalize_google_ad_rows(results: list[dict]) -> pd.DataFrame:
    out = []
    for r in results:
        m = r["metrics"]
        micros = int(m["costMicros"])
        out.append({
            "date": date.fromisoformat(r["segments"]["date"]), "campaign_id": r["campaign"]["id"],
            "ad_group_id": r["adGroup"]["id"], "ad_id": r["adGroupAd"]["ad"]["id"],
            "impressions": int(m["impressions"]), "clicks": int(m["clicks"]), "cost_micros": micros,
            "cost_inr": micros / MICROS, "platform_conversions": float(m["conversions"]),
            "platform_conversion_value_inr": float(m["conversionsValue"]),
        })
    return pd.DataFrame(out)


# ---- Meta ------------------------------------------------------------------------------------------------------
def _meta_pages(ctx: SyncContext, path: str, params: dict):
    after = None
    while True:
        q = params | ({"after": after} if after else {})
        page = ctx.http.get(path, q)
        ctx.record_page("meta_ads", path, q, page)
        yield page["data"]
        if "next" not in page.get("paging", {}):
            return
        after = page["paging"]["cursors"]["after"]


def sync_meta(ctx: SyncContext, since: date, until: date) -> ConnectorResult:
    cfg = sources_config()["sources"]["meta_ads"]
    base = f"/meta/{cfg['api_version']}/act_{cfg['account_id']}"
    res = ConnectorResult("meta_ads")
    tr = json.dumps({"since": since.isoformat(), "until": until.isoformat()})
    ad_rows: list[dict] = []
    for data in _meta_pages(ctx, f"{base}/insights", {
            "level": "ad", "time_range": tr, "time_increment": "1", "limit": 500,
            "fields": "campaign_id,adset_id,ad_id,date_start,impressions,clicks,spend,actions,action_values"}):
        ad_rows += data
    camp_rows: list[dict] = []
    for data in _meta_pages(ctx, f"{base}/insights", {
            "level": "campaign", "time_range": tr, "time_increment": "1", "limit": 500,
            "fields": "campaign_id,date_start,impressions,reach"}):
        camp_rows += data
    res.rows["stg.meta_ad_daily"] = ctx.upsert(
        "stg.meta_ad_daily", ctx.stamp(normalize_meta_ad_rows(ad_rows, cfg["currency"]), "meta_ads"))
    res.rows["stg.meta_campaign_daily"] = ctx.upsert(
        "stg.meta_campaign_daily", ctx.stamp(normalize_meta_campaign_rows(camp_rows), "meta_ads"))

    fx = fx_to_inr(cfg["currency"])
    snaps = []
    for data in _meta_pages(ctx, f"{base}/campaigns", {"fields": "id,name,status,daily_budget,objective",
                                                       "limit": 500}):
        snaps += [{"entity_type": "campaign", "entity_id": c["id"], "parent_id": None, "campaign_id": c["id"],
                   "budget_id": c["id"], "name": c["name"], "status": c["status"], "channel_type": c["objective"],
                   "budget_amount_inr": int(c["daily_budget"]) / 100 * fx, "budget_is_shared": False,
                   "attributes": json.dumps({"daily_budget_minor": c["daily_budget"], "currency": cfg["currency"]})}
                  for c in data]
    for data in _meta_pages(ctx, f"{base}/adsets", {"fields": "id,name,campaign_id,status,audience", "limit": 500}):
        snaps += [{"entity_type": "adset", "entity_id": a["id"], "parent_id": a["campaign_id"],
                   "campaign_id": a["campaign_id"], "name": a["name"], "status": a["status"],
                   "attributes": json.dumps({"audience": a.get("audience")})} for a in data]
    for data in _meta_pages(ctx, f"{base}/ads", {"fields": "id,name,adset_id,campaign_id,status,created_time,creative",
                                                 "limit": 500}):
        snaps += [{"entity_type": "ad", "entity_id": a["id"], "parent_id": a["adset_id"],
                   "campaign_id": a["campaign_id"], "name": a["name"], "status": a["status"],
                   "attributes": json.dumps({"created_time": a.get("created_time"), "creative": a.get("creative")})}
                  for a in data]
    res.rows["stg.entity_snapshots"] = _upsert_snapshots(ctx, "meta_ads", "meta", snaps)
    res.pages = ctx.pages.get("meta_ads", 0)
    return res


def _upsert_snapshots(ctx: SyncContext, source: str, platform: str, snaps: list[dict]) -> int:
    if not snaps:
        return 0
    df = pd.DataFrame(snaps)
    for col in ("parent_id", "campaign_id", "budget_id", "name", "status", "channel_type", "budget_amount_inr",
                "budget_is_shared", "attributes"):
        if col not in df:
            df[col] = None
    df["snapshot_date"] = ctx.world_date
    df["platform"] = platform
    df = ctx.stamp(df, source, date_col=None)
    df["available_at"] = datetime.combine(ctx.world_date, datetime.min.time())  # observed now
    return ctx.upsert("stg.entity_snapshots", df)


# ---- Google ----------------------------------------------------------------------------------------------------
def _gaql(ctx: SyncContext, path: str, query: str) -> list[dict]:
    results, token = [], None
    while True:
        body = {"query": query} | ({"pageToken": token} if token else {})
        page = ctx.http.post(path, body)
        ctx.record_page("google_ads", path, body, page)
        results += page.get("results", [])
        token = page.get("nextPageToken")
        if not token:
            return results


def sync_google(ctx: SyncContext, since: date, until: date) -> ConnectorResult:
    cfg = sources_config()["sources"]["google_ads"]
    path = f"/google/{cfg['api_version']}/customers/{cfg['customer_id']}/googleAds:search"
    res = ConnectorResult("google_ads")
    ads = _gaql(ctx, path, (
        "SELECT campaign.id, ad_group.id, ad_group_ad.ad.id, segments.date, metrics.impressions, metrics.clicks, "
        "metrics.cost_micros, metrics.conversions, metrics.conversions_value FROM ad_group_ad "
        f"WHERE segments.date BETWEEN '{since.isoformat()}' AND '{until.isoformat()}'"))
    res.rows["stg.google_ad_daily"] = ctx.upsert(
        "stg.google_ad_daily", ctx.stamp(normalize_google_ad_rows(ads), "google_ads"))

    budgets = {r["campaignBudget"]["id"]: r["campaignBudget"] for r in _gaql(ctx, path, (
        "SELECT campaign_budget.id, campaign_budget.amount_micros, campaign_budget.explicitly_shared, "
        "campaign_budget.status FROM campaign_budget"))}
    snaps = [{"entity_type": "budget", "entity_id": b["id"], "budget_id": b["id"], "status": b["status"],
              "budget_amount_inr": int(b["amountMicros"]) / MICROS, "budget_is_shared": bool(b["explicitlyShared"]),
              "attributes": json.dumps({"amount_micros": b["amountMicros"]})} for b in budgets.values()]
    for r in _gaql(ctx, path, ("SELECT campaign.id, campaign.name, campaign.status, campaign.campaign_budget, "
                               "campaign.advertising_channel_type FROM campaign")):
        c = r["campaign"]
        bid = c["campaignBudget"].rsplit("/", 1)[-1]
        b = budgets.get(bid, {})
        snaps.append({"entity_type": "campaign", "entity_id": c["id"], "campaign_id": c["id"], "budget_id": bid,
                      "name": c["name"], "status": c["status"], "channel_type": c["advertisingChannelType"],
                      "budget_amount_inr": int(b["amountMicros"]) / MICROS if b else None,
                      "budget_is_shared": bool(b.get("explicitlyShared", False))})
    for r in _gaql(ctx, path, "SELECT ad_group.id, ad_group.name, ad_group.status, campaign.id FROM ad_group"):
        g = r["adGroup"]
        snaps.append({"entity_type": "adset", "entity_id": g["id"], "parent_id": r["campaign"]["id"],
                      "campaign_id": r["campaign"]["id"], "name": g["name"], "status": g["status"]})
    for r in _gaql(ctx, path, ("SELECT ad_group_ad.ad.id, ad_group_ad.ad.name, ad_group_ad.status, ad_group.id, "
                               "campaign.id FROM ad_group_ad")):
        a = r["adGroupAd"]
        snaps.append({"entity_type": "ad", "entity_id": a["ad"]["id"], "parent_id": r["adGroup"]["id"],
                      "campaign_id": r["campaign"]["id"], "name": a["ad"]["name"], "status": a["status"]})
    res.rows["stg.entity_snapshots"] = _upsert_snapshots(ctx, "google_ads", "google", snaps)
    res.pages = ctx.pages.get("google_ads", 0)
    return res
