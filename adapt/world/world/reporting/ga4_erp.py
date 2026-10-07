"""Mock GA4 Data API runReport and the ERP stock/receipts API.

GA4: POST /ga4/v1beta/properties/{property_id}:runReport
  {"dateRanges":[{"startDate","endDate"}], "dimensions":[{"name"}], "metrics":[{"name"}], "limit", "offset"}
  dimensions: date (YYYYMMDD), sessionSource, sessionMedium, sessionCampaignId ("(not set)" when unpaid)
  metrics: sessions, ecommercePurchases, purchaseRevenue (INR). GA4 records ~95% of sessions and purchases.
ERP:  GET /erp/v1/stock?date=YYYY-MM-DD  end-of-day snapshot (default: last complete day)
      GET /erp/v1/receipts?from=&to=     goods received per SKU per day
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from world.accounts import BRAND_TIMEZONE, GA4_PROPERTY_ID, STORE_CURRENCY
from world.reporting.common import (
    NotSeeded,
    date_of,
    day_of,
    last_complete_date,
    not_seeded_response,
    parse_iso_date,
    reportable_days,
    world_of,
)

router = APIRouter(tags=["mock-ga4-erp"])
DIMENSIONS = {"date": "day", "sessionSource": "source", "sessionMedium": "medium",
              "sessionCampaignId": "coalesce(campaign_id, '(not set)')"}
METRICS = {"sessions": ("sum(sessions)", "TYPE_INTEGER"), "ecommercePurchases": ("sum(purchases)", "TYPE_INTEGER"),
           "purchaseRevenue": ("sum(revenue_inr)", "TYPE_CURRENCY")}


class _Named(BaseModel):
    name: str


class _Range(BaseModel):
    startDate: str
    endDate: str


class RunReport(BaseModel):
    dateRanges: list[_Range] = Field(min_length=1, max_length=1)
    dimensions: list[_Named] = []
    metrics: list[_Named] = Field(min_length=1)
    limit: int = Field(default=10_000, ge=1, le=250_000)
    offset: int = Field(default=0, ge=0)


def _ga_date(value: str, today: date) -> date:
    if value == "today":
        return today
    if value == "yesterday":
        return today - timedelta(days=1)
    if value.endswith("daysAgo"):
        return today - timedelta(days=int(value[:-7]))
    return date.fromisoformat(value)


def _ga_error(message: str) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": {"code": 400, "message": message,
                                                             "status": "INVALID_ARGUMENT"}})


@router.post("/ga4/v1beta/properties/{property_id}:runReport")
def run_report(property_id: str, body: RunReport, request: Request) -> JSONResponse:
    if property_id != GA4_PROPERTY_ID:
        return JSONResponse(status_code=403, content={"error": {"code": 403, "status": "PERMISSION_DENIED",
                                                                 "message": "User does not have access"}})
    try:
        store, truth = world_of(request)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    dims = [d.name for d in body.dimensions]
    mets = [m.name for m in body.metrics]
    bad = [d for d in dims if d not in DIMENSIONS] + [m for m in mets if m not in METRICS]
    if bad:
        return _ga_error(f"Field {bad[0]} is not a valid dimension or metric for this mock")
    today = date_of(truth, store.clock()[1])
    try:
        since = _ga_date(body.dateRanges[0].startDate, today)
        until = _ga_date(body.dateRanges[0].endDate, today)
    except ValueError:
        return _ga_error("Invalid date range")
    lo, hi = reportable_days(store, truth, since, until)
    dim_sql = [DIMENSIONS[d] for d in dims]
    select = ", ".join(dim_sql + [METRICS[m][0] for m in mets])
    group = f"GROUP BY {', '.join(dim_sql)} ORDER BY {', '.join(dim_sql)}" if dims else ""
    rows = store.read(f"SELECT {select} FROM fact_ga_daily WHERE day BETWEEN ? AND ? {group}", [lo, hi])
    if not dims and rows and rows[0][0] is None:
        rows = []
    out = []
    for r in rows[body.offset:body.offset + body.limit]:
        dvals = []
        for d, v in zip(dims, r[:len(dims)], strict=True):
            dvals.append({"value": date_of(truth, v).strftime("%Y%m%d") if d == "date" else str(v)})
        mvals = []
        for m, v in zip(mets, r[len(dims):], strict=True):
            mvals.append({"value": str(int(v)) if METRICS[m][1] == "TYPE_INTEGER" else f"{float(v):.2f}"})
        out.append({"dimensionValues": dvals, "metricValues": mvals})
    return JSONResponse({
        "dimensionHeaders": [{"name": d} for d in dims],
        "metricHeaders": [{"name": m, "type": METRICS[m][1]} for m in mets],
        "rows": out, "rowCount": len(rows),
        "metadata": {"currencyCode": STORE_CURRENCY, "timeZone": BRAND_TIMEZONE},
        "kind": "analyticsData#runReport",
    })


@router.get("/erp/v1/stock")
def stock(request: Request) -> JSONResponse:
    try:
        store, truth = world_of(request)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    requested = parse_iso_date(request.query_params.get("date")) or last_complete_date(store, truth)
    day = day_of(truth, requested)
    lo, hi = reportable_days(store, truth, requested, requested)
    if lo > hi:
        return JSONResponse(status_code=404, content={"detail": f"no complete snapshot for {requested.isoformat()}"})
    rows = store.read("""SELECT sku, on_hand, reserved, inbound_qty, inbound_day FROM fact_erp_daily
                         WHERE day = ? ORDER BY sku""", [day])
    items = [{"sku": sku, "on_hand": int(oh), "reserved": int(res),
              "inbound": ([{"po_number": f"PO-{sku}-{iday}", "quantity": int(iq),
                            "expected_arrival": date_of(truth, iday).isoformat(), "status": "CONFIRMED"}]
                          if iq else [])}
             for sku, oh, res, iq, iday in rows]
    return JSONResponse({"as_of": requested.isoformat(), "location": "DC-01", "items": items})


@router.get("/erp/v1/receipts")
def receipts(request: Request) -> JSONResponse:
    try:
        store, truth = world_of(request)
    except NotSeeded as exc:
        return not_seeded_response(exc)
    lo, hi = reportable_days(store, truth, parse_iso_date(request.query_params.get("from")),
                             parse_iso_date(request.query_params.get("to")))
    rows = store.read("SELECT day, sku, receipts FROM fact_erp_daily WHERE receipts > 0 AND day BETWEEN ? AND ? "
                      "ORDER BY day, sku", [lo, hi])
    return JSONResponse({"receipts": [{"date": date_of(truth, d).isoformat(), "sku": sku, "quantity": int(q),
                                       "location": "DC-01"} for d, sku, q in rows]})
