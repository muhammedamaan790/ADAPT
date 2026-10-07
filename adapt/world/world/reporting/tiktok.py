"""Mock TikTok Business API v1.3 reporting (Stage 2 SIMULATED channel).

GET /tiktok/v1.3/report/integrated/get/?advertiser_id&data_level=AUCTION_AD&start_date&end_date&page&page_size
  -> {"code": 0, "data": {"list": [{"dimensions": {"ad_id", "stat_time_day": "YYYY-MM-DD 00:00:00"},
      "metrics": {"campaign_id", "adgroup_id", "spend", "impressions", "clicks", "complete_payment",
      "total_complete_payment"}}], "page_info": {...}}}
Money in the advertiser currency (USD) as strings; conversions are platform-claimed (incl. view-through).
Listings: /campaign/get/ (budget in USD, BUDGET_MODE_DAY, operation_status), /adgroup/get/, /ad/get/.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from world.accounts import TIKTOK_ADVERTISER_ID, usd_per_inr
from world.mutations.tiktok import tiktok_error
from world.reporting.common import NotSeeded, date_of, not_seeded_response, reportable_days, world_of

router = APIRouter(prefix="/tiktok/v1.3", tags=["mock-tiktok-reporting"])


def _ok(rows: list, page: int, page_size: int) -> JSONResponse:
    total = len(rows)
    chunk = rows[(page - 1) * page_size: page * page_size]
    return JSONResponse({"code": 0, "message": "OK", "request_id": "mock", "data": {
        "list": chunk, "page_info": {"page": page, "page_size": page_size, "total_number": total,
                                     "total_page": max(1, -(-total // page_size))}}})


def _usd(inr: float) -> str:
    return f"{inr * usd_per_inr():.2f}"


@router.get("/report/integrated/get/")
def report(request: Request, advertiser_id: str, start_date: str, end_date: str,
           data_level: str = "AUCTION_AD", page: int = Query(default=1, ge=1),
           page_size: int = Query(default=1000, ge=1, le=1000)) -> JSONResponse:
    if advertiser_id != TIKTOK_ADVERTISER_ID:
        return tiktok_error(400, 40001, "advertiser_id does not exist")
    if data_level != "AUCTION_AD":
        return tiktok_error(400, 40002, "the mock serves data_level AUCTION_AD")
    try:
        store, truth = world_of(request)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    lo, hi = reportable_days(store, truth, date.fromisoformat(start_date), date.fromisoformat(end_date))
    rows = store.read("""SELECT day, campaign_id, adset_id, creative_id, impressions, clicks, spend_inr, conversions,
                                conversion_value_inr FROM fact_ad_creative_daily
                         WHERE platform = 'tiktok' AND day BETWEEN ? AND ? AND impressions > 0
                         ORDER BY day, creative_id""", [lo, hi])
    out = [{"dimensions": {"ad_id": crid, "stat_time_day": f"{date_of(truth, d).isoformat()} 00:00:00"},
            "metrics": {"campaign_id": cid, "adgroup_id": asid, "spend": _usd(sp), "impressions": str(i),
                        "clicks": str(c), "complete_payment": str(conv), "total_complete_payment": _usd(val)}}
           for d, cid, asid, crid, i, c, sp, conv, val in rows]
    return _ok(out, page, page_size)


def _status(store, budget_id: str) -> tuple[float, str]:
    row = store.read("SELECT amount, status FROM budgets_state WHERE platform = 'tiktok' AND budget_id = ?",
                     [budget_id])
    return (row[0][0], row[0][1]) if row else (0.0, "PAUSED")


@router.get("/campaign/get/")
def campaigns(request: Request, advertiser_id: str, page: int = 1, page_size: int = 1000) -> JSONResponse:
    if advertiser_id != TIKTOK_ADVERTISER_ID:
        return tiktok_error(400, 40001, "advertiser_id does not exist")
    try:
        store, truth = world_of(request)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    out = []
    for c in (c for c in truth.catalog.campaigns if c.platform == "tiktok"):
        amount, status = _status(store, c.budget_id)
        out.append({"campaign_id": c.campaign_id, "campaign_name": c.name, "budget": round(amount * usd_per_inr(), 2),
                    "budget_mode": "BUDGET_MODE_DAY", "objective_type": "PRODUCT_SALES",
                    "operation_status": "ENABLE" if status == "ENABLED" else "DISABLE"})
    return _ok(out, page, page_size)


@router.get("/adgroup/get/")
def adgroups(request: Request, advertiser_id: str, page: int = 1, page_size: int = 1000) -> JSONResponse:
    try:
        _, truth = world_of(request)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    ids = {c.campaign_id for c in truth.catalog.campaigns if c.platform == "tiktok"}
    out = [{"adgroup_id": a.adset_id, "campaign_id": a.campaign_id, "adgroup_name": a.audience,
            "operation_status": "ENABLE"} for a in truth.catalog.adsets if a.campaign_id in ids]
    return _ok(out, page, page_size)


@router.get("/ad/get/")
def ads(request: Request, advertiser_id: str, page: int = 1, page_size: int = 1000) -> JSONResponse:
    try:
        _, truth = world_of(request)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    ids = {c.campaign_id for c in truth.catalog.campaigns if c.platform == "tiktok"}
    out = [{"ad_id": cr.creative_id, "adgroup_id": cr.adset_id, "campaign_id": cr.campaign_id,
            "ad_name": cr.headline, "operation_status": "ENABLE"} for cr in truth.catalog.creatives
           if cr.campaign_id in ids]
    return _ok(out, page, page_size)
