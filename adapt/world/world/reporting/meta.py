"""Mock Meta Marketing API v25.0 reporting (Graph shapes): ad account insights and object listings.

Insights: GET /meta/v25.0/act_{account}/insights?level=campaign|adset|ad&time_range={"since","until"}
          &time_increment=1&fields=...&limit=&after=
Values are strings, money in the account currency (USD), CTR in percent, purchases under `actions` with
action_type offsite_conversion.fb_pixel_purchase (platform-claimed: click-through + view-through).
Reach exists only at campaign level here (it is not additive across ads or days, so it is never summed).
Listings: /act_{account}/campaigns, /adsets, /ads with `fields`.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from world.accounts import META_AD_ACCOUNT_ID, META_CURRENCY, inr_to_meta
from world.mutations.meta import STATE_TO_WIRE, meta_error
from world.reporting.common import (
    NotSeeded,
    date_of,
    decode_cursor,
    encode_cursor,
    not_seeded_response,
    parse_iso_date,
    reportable_days,
    world_of,
)

router = APIRouter(prefix="/meta/v25.0", tags=["mock-meta-reporting"])
PURCHASE_ACTION = "offsite_conversion.fb_pixel_purchase"
DEFAULT_LIMIT, MAX_LIMIT = 25, 500
INSIGHT_FIELDS = {
    "campaign": {"campaign_id", "campaign_name", "impressions", "reach", "frequency", "clicks", "spend", "cpm", "ctr",
                 "actions", "action_values", "account_currency", "date_start", "date_stop"},
    "adset": {"campaign_id", "campaign_name", "adset_id", "adset_name", "impressions", "clicks", "spend", "cpm",
              "ctr", "actions", "action_values", "account_currency", "date_start", "date_stop"},
    "ad": {"campaign_id", "campaign_name", "adset_id", "adset_name", "ad_id", "ad_name", "impressions", "clicks",
           "spend", "cpm", "ctr", "actions", "action_values", "account_currency", "date_start", "date_stop"},
}


def _money(inr: float) -> str:
    return f"{inr_to_meta(inr):.2f}"


def _page(request: Request, rows: list[dict], limit: int, after: str | None) -> JSONResponse:
    start = decode_cursor(after)
    page = rows[start:start + limit]
    end = start + len(page)
    paging: dict = {"cursors": {"before": encode_cursor(start), "after": encode_cursor(end)}}
    if end < len(rows):
        paging["next"] = str(request.url.include_query_params(after=encode_cursor(end)))
    return JSONResponse({"data": page, "paging": paging})


def _check_account(account_id: str) -> JSONResponse | None:
    if account_id != META_AD_ACCOUNT_ID:
        return meta_error(400, 100, f"Unsupported get request. Object with ID 'act_{account_id}' does not exist",
                          False)
    return None


@router.get("/act_{account_id}/insights")
def insights(
    account_id: str,
    request: Request,
    level: str = Query(default="campaign"),
    time_range: str | None = Query(default=None),
    time_increment: str = Query(default="1"),
    fields: str = Query(default="campaign_id,impressions,clicks,spend"),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    after: str | None = Query(default=None),
) -> JSONResponse:
    if (err := _check_account(account_id)) is not None:
        return err
    try:
        store, truth = world_of(request)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    if level not in INSIGHT_FIELDS:
        return meta_error(400, 100, f"level must be one of {sorted(INSIGHT_FIELDS)}", False)
    if time_increment != "1":
        return meta_error(400, 100, "mock supports time_increment=1 (daily rows) only", False)
    wanted = [f.strip() for f in fields.split(",") if f.strip()]
    unknown = [f for f in wanted if f not in INSIGHT_FIELDS[level]]
    if unknown:
        return meta_error(400, 100, f"({unknown[0]}) is not valid for fields param at level {level}", False)
    try:
        tr = json.loads(time_range) if time_range else {}
        since, until = parse_iso_date(tr.get("since")), parse_iso_date(tr.get("until"))
        decode_cursor(after)
    except (ValueError, AttributeError):
        return meta_error(400, 100, "Invalid time_range or cursor", False)
    lo, hi = reportable_days(store, truth, since, until)

    camp = {c.campaign_id: c for c in truth.catalog.campaigns if c.platform == "meta"}
    adsets = {a.adset_id: a for a in truth.catalog.adsets if a.campaign_id in camp}
    ads = {cr.creative_id: cr for cr in truth.catalog.creatives if cr.campaign_id in camp}
    key = {"campaign": "campaign_id", "adset": "adset_id", "ad": "creative_id"}[level]
    agg = store.read(f"""
        SELECT day, {key}, any_value(campaign_id), any_value(adset_id), sum(impressions), sum(clicks),
               sum(spend_inr), sum(conversions), sum(conversion_value_inr)
        FROM fact_ad_creative_daily
        WHERE platform = 'meta' AND day BETWEEN ? AND ?
        GROUP BY day, {key} HAVING sum(impressions) > 0 ORDER BY day, {key}
    """, [lo, hi])
    reach = {}
    if level == "campaign":
        reach = {(d, cid): r for d, cid, r in store.read(
            "SELECT day, campaign_id, reach FROM fact_ad_campaign_daily "
            "WHERE platform = 'meta' AND day BETWEEN ? AND ?", [lo, hi])}
    rows = []
    for day, ident, cid, asid, imps, clicks, spend, conv, value in agg:
        ds = date_of(truth, day).isoformat()
        full = {
            "campaign_id": cid, "campaign_name": camp[cid].name,
            "adset_id": asid, "adset_name": f"{camp[cid].name} | {adsets[asid].audience}" if asid in adsets else None,
            "ad_id": ident if level == "ad" else None,
            "ad_name": f"{ads[ident].format} · {ads[ident].hook}" if level == "ad" else None,
            "impressions": str(int(imps)), "clicks": str(int(clicks)), "spend": _money(spend),
            "cpm": _money(1000 * spend / imps), "ctr": f"{100 * clicks / imps:.6f}",
            "account_currency": META_CURRENCY, "date_start": ds, "date_stop": ds,
        }
        if level == "campaign":
            r = int(reach.get((day, cid), 0))
            full["reach"] = str(r)
            full["frequency"] = f"{imps / r:.6f}" if r else "0"
        if conv:
            full["actions"] = [{"action_type": PURCHASE_ACTION, "value": str(int(conv))}]
            full["action_values"] = [{"action_type": PURCHASE_ACTION, "value": _money(value)}]
        rows.append({f: full[f] for f in wanted if f in full})
    return _page(request, rows, limit, after)


def _campaign_status(store, budget_id: str) -> str:
    rows = store.read("SELECT status FROM budgets_state WHERE platform = 'meta' AND budget_id = ?", [budget_id])
    return STATE_TO_WIRE[rows[0][0]] if rows else "PAUSED"


@router.get("/act_{account_id}/campaigns")
def campaigns(account_id: str, request: Request, fields: str = Query(default="id,name"),
              limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT), after: str | None = None) -> JSONResponse:
    if (err := _check_account(account_id)) is not None:
        return err
    try:
        store, truth = world_of(request)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    budgets = dict(store.read("SELECT budget_id, amount FROM budgets_state WHERE platform = 'meta'"))
    wanted = [f.strip() for f in fields.split(",") if f.strip()]
    rows = []
    for c in sorted((c for c in truth.catalog.campaigns if c.platform == "meta"), key=lambda c: c.campaign_id):
        full = {"id": c.campaign_id, "name": c.name, "status": _campaign_status(store, c.budget_id),
                "daily_budget": str(round(inr_to_meta(budgets.get(c.budget_id, 0.0)) * 100)),
                "objective": "OUTCOME_SALES", "buying_type": "AUCTION"}
        rows.append({f: full[f] for f in wanted if f in full})
    return _page(request, rows, limit, after)


@router.get("/act_{account_id}/adsets")
def adsets(account_id: str, request: Request, fields: str = Query(default="id,name"),
           limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT), after: str | None = None) -> JSONResponse:
    if (err := _check_account(account_id)) is not None:
        return err
    try:
        store, truth = world_of(request)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    camp = {c.campaign_id: c for c in truth.catalog.campaigns if c.platform == "meta"}
    wanted = [f.strip() for f in fields.split(",") if f.strip()]
    rows = []
    for a in sorted((a for a in truth.catalog.adsets if a.campaign_id in camp), key=lambda a: a.adset_id):
        full = {"id": a.adset_id, "name": f"{camp[a.campaign_id].name} | {a.audience}", "campaign_id": a.campaign_id,
                "status": _campaign_status(store, camp[a.campaign_id].budget_id), "audience": a.audience}
        rows.append({f: full[f] for f in wanted if f in full})
    return _page(request, rows, limit, after)


@router.get("/act_{account_id}/ads")
def ads(account_id: str, request: Request, fields: str = Query(default="id,name"),
        limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT), after: str | None = None) -> JSONResponse:
    if (err := _check_account(account_id)) is not None:
        return err
    try:
        store, truth = world_of(request)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    camp = {c.campaign_id: c for c in truth.catalog.campaigns if c.platform == "meta"}
    wanted = [f.strip() for f in fields.split(",") if f.strip()]
    rows = []
    for cr in sorted((cr for cr in truth.catalog.creatives if cr.campaign_id in camp), key=lambda x: x.creative_id):
        full = {"id": cr.creative_id, "name": f"{cr.format} · {cr.hook}", "adset_id": cr.adset_id,
                "campaign_id": cr.campaign_id,
                "status": _campaign_status(store, camp[cr.campaign_id].budget_id),
                "created_time": f"{date_of(truth, cr.launch_day).isoformat()}T00:00:00+0530",
                "creative": {"title": cr.headline, "body": cr.hook,
                             "call_to_action_type": cr.cta.upper().replace(" ", "_"),
                             "object_type": cr.format.upper(),
                             "image_url": f"/assets/creatives/{cr.creative_id}.svg"}}
        rows.append({f: full[f] for f in wanted if f in full})
    return _page(request, rows, limit, after)
